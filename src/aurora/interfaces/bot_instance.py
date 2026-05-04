"""봇 인스턴스 싱글톤 — /start /stop 이 제어할 매매 lifecycle.

BotInstance 가 어댑터 (CcxtClient + MultiTfCache + Executor) 와 strategy 결합.
매 1초 ``_step()`` 호출:
    1. ``cache.step()`` — 봉 경계 시 새 봉 fetch
    2. ``strategy.evaluate_*`` + ``signal.compose_entry`` — 진입 결정
    3. ``executor.update_trailing_sl`` / ``should_close`` / ``close_position``
       — 트레일링 / 청산
    4. 진입 신호 + 무포지션 → ``executor.open_position``

Lifecycle:
    >>> bot = bot_instance.get_instance()
    >>> bot.configure_from_settings()       # settings 기반 일괄 (CcxtClient + 기본 설정)
    >>> # 또는 bot.configure(client=mock_client, symbol=..., ...) — 테스트 inject
    >>> await bot.start()                    # warmup + run_loop
    >>> # 매매 자동 진행
    >>> await bot.stop()                     # cancel + client.close()

호환성 (기존 PR-C/D/F 테스트):
    ``configure*`` 호출 안 한 BotInstance 는 ``start()`` 시 어댑터 생성 X
    → ``_run_loop`` 가 noop 만 (1초 sleep). 기존 lifecycle 테스트 그대로 동작.

담당: 정용우 (영역 위임 받음 2026-05-03 — 어댑터 PR Stage 2E C)
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque

from aurora.config import settings
from aurora.core.risk import TpSlConfig, build_risk_plan
from aurora.core.signal import compose_entry, compose_exit
from aurora.core.strategy import (
    StrategyConfig,
    detect_ema_touch,
    detect_rsi_divergence,
    evaluate_selectable,
)
from aurora.exchange.base import ExchangeClient
from aurora.exchange.data import MultiTfCache
from aurora.exchange.execution import ClosedTrade, Executor
from aurora.interfaces import active_position_store, trades_store

logger = logging.getLogger(__name__)


# 매매 사이클 폴링 주기 — 봉 경계 검출은 cache.step 자체가 처리.
# 1초보다 짧으면 ccxt 호출 빈도 ↑, 더 길면 SL/TP polling 반응 둔해짐.
_LOOP_INTERVAL_SEC = 1.0

# warmup default 봉 수 — 전략 평가에 충분한 history (EMA 480 / RSI Div 등)
_WARMUP_DEFAULTS = {"15m": 200, "1H": 500, "2H": 250, "4H": 500, "1D": 200}

# default 매매 페어 / TF 셋 — configure 안 하면 settings 기반 사용
_DEFAULT_SYMBOL = "BTC/USDT:USDT"
_DEFAULT_TIMEFRAMES = ["15m", "1H", "4H"]

# default 레버리지 — TODO: GUI config 연결 시 settings.leverage 같은 필드 추가 (별도 PR)
_DEFAULT_LEVERAGE = 10

# 지표 트리거 패널 카테고리 (UI 표시 6개) — signal.source 의 prefix 매핑.
# v0.1.14 — 사용자가 "각 지표 현재 어떻게 보고 있나" 한눈에 확인용.
_INDICATOR_CATEGORIES: list[str] = ["EMA", "RSI", "BB", "MA", "Ichimoku", "Harmonic"]
_SOURCE_PREFIX_MAP: dict[str, str] = {
    "ema_": "EMA",
    "rsi_": "RSI",
    "bollinger_": "BB",
    "ma_cross_": "MA",
    "ichimoku_": "Ichimoku",
    "harmonic_": "Harmonic",
}


def _categorize_source(source: str) -> str | None:
    """signal.source ("ema_touch_200" 등) → UI 카테고리 ("EMA")."""
    for prefix, cat in _SOURCE_PREFIX_MAP.items():
        if source.startswith(prefix):
            return cat
    return None  # 매핑 없는 source — UI 표시 X (예: 2468 internal)


class BotInstance:
    """봇 lifecycle — start/stop + 매매 사이클 (단일 페어).

    호환성: 인자 없이 생성 가능 (기존 PR-C 시그니처). configure() 호출
    안 하면 어댑터 X → start() 시 noop loop. 매매하려면 configure*.
    """

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None

        # 어댑터 / 설정 — configure 시점 lazy 생성
        self._client: ExchangeClient | None = None
        self._cache: MultiTfCache | None = None
        self._executor: Executor | None = None

        # configure_from_settings 흔적 — stop 후 재 start 시 자동 reconfigure 트리거.
        # Why: stop() 이 client/cache/executor 를 None 으로 정리 (ccxt session 누수 방지)
        # 하므로, 사용자가 GUI ▶ 시작 → ■ 중지 → ▶ 시작 사이클 시 두 번째 start 가
        # noop loop 로 진입해 포지션 표시가 사라지는 버그 fix.
        # configure(수동 inject)는 False 로 덮어 써서 mock 테스트가 영향 안 받게 함.
        self._auto_configured = False

        # 외부 포지션 (사용자가 직접 거래소 화면에서 연 포지션) 감지 상태.
        # Why: 사용자 수동 포지션이 마진 거의 다 잡고 있으면 Aurora 가 자기 포지션
        # 없다고 판단하고 진입 시도 → InsufficientFunds 무한 루프 (v0.1.4 검증).
        # Fix: 진입 직전 fetch_position 으로 거래소 측 포지션 확인 → 외부 포지션 있으면
        # 진입 skip + 1회 WARNING (반복 로그 방지). 외부 포지션 사라지면 자동 reset.
        self._external_position: bool = False
        self._external_position_warned: bool = False

        # 지표 트리거 상태 — 매 _step 마지막 평가 결과 보존 (UI 대시보드 표시용 v0.1.14).
        # 형식: {"EMA": "long" | "short" | None, "RSI": ..., "BB": ..., ...}
        self._last_indicator_status: dict[str, str | None] = {}

        # 거래내역 (v0.1.20) — close_position 마다 ClosedTrade 추가 (rolling 100).
        # GUI "거래내역" 표 표시 + Telegram 알림 + PnL 카드 데이터 source.
        # v0.1.25: 디스크에서 영속화된 record 복원 → 봇 재시작 후 표 / 통계 유지.
        self._closed_trades: deque[ClosedTrade] = deque(maxlen=100)
        try:
            for t in trades_store.load():
                self._closed_trades.append(t)  # deque maxlen 자동 — 100 초과는 buffer drop
        except Exception as e:  # noqa: BLE001 — 시작 차단 방지 (디스크 손상 등)
            logger.warning("trades_store.load 실패 (in-memory 만으로 시작): %s", e)

        self._symbol: str = _DEFAULT_SYMBOL
        self._timeframes: list[str] = list(_DEFAULT_TIMEFRAMES)
        self._strategy_config: StrategyConfig = StrategyConfig()
        self._tpsl_config: TpSlConfig = TpSlConfig()
        self._risk_pct: float = 0.01
        self._full_seed: bool = False
        self._leverage: int = _DEFAULT_LEVERAGE

    # ============================================================
    # property — 외부 read-only 접근
    # ============================================================

    @property
    def running(self) -> bool:
        return self._running

    @property
    def has_position(self) -> bool:
        """현재 포지션 보유 여부 — UI 대시보드 / API status 표시용."""
        return self._executor.has_position if self._executor else False

    @property
    def is_configured(self) -> bool:
        """configure* 호출 후 어댑터 생성 가능 상태."""
        return self._client is not None

    @property
    def client(self) -> ExchangeClient | None:
        """어댑터 read-only 접근 — ``/status`` 의 ``get_equity()`` 등 외부 조회용."""
        return self._client

    @property
    def external_position_detected(self) -> bool:
        """외부 포지션 (사용자가 거래소 화면에서 직접 연 포지션) 감지 여부.

        True 면 Aurora 가 진입 skip 중. UI 알림 표시 / API 응답 노출용.
        """
        return self._external_position

    def _record_closed(self, closed: ClosedTrade) -> None:
        """ClosedTrade 메모리 buffer + 디스크 영속화 통합 (v0.1.25).

        매 close_position 직후 호출. ``trades_store.save`` 는 atomic + 실패 시 silent
        (warn) — 디스크 이슈로 매매 lifecycle 차단되지 않게.

        v0.1.26: 청산 후 active_position 도 동기화 — 잔여 0 = clear, partial = 새 state save.
        """
        self._closed_trades.append(closed)
        try:
            trades_store.save(list(self._closed_trades))
        except Exception as e:  # noqa: BLE001 — 매매 사이클 보호
            logger.warning("trades_store.save 실패 (in-memory 유지): %s", e)
        self._persist_active()

    def _persist_active(self) -> None:
        """현재 Executor state 를 디스크에 영속화 (v0.1.26).

        잔여 0 / has_position=False → clear. 활성 포지션 있으면 save (현재 plan + remaining + tp_hits).
        매 진입 / partial 청산 / 외부 청산 감지 / start 복원 후 호출.
        """
        try:
            if self._executor is None or not self._executor.has_position:
                active_position_store.clear()
                return
            active_position_store.save(
                plan=self._executor._plan,
                symbol=self._symbol,
                triggered_by=self._executor._triggered_by,
                opened_at_ts=self._executor._opened_at_ts,
                remaining_qty=self._executor._remaining_qty,
                tp_hits=self._executor._tp_hits,
            )
        except Exception as e:  # noqa: BLE001 — 매매 사이클 보호
            logger.warning("active_position_store sync 실패: %s", e)

    @property
    def closed_trades(self) -> list:
        """거래내역 list — 최근 100개 (rolling). UI "거래내역" 표 / PnL 카드 데이터.

        Returns:
            list[ClosedTrade] — 신→구 (가장 최근 trade 가 마지막).
        """
        return list(self._closed_trades)

    @property
    def last_indicator_status(self) -> dict[str, str | None]:
        """매 _step 마지막 평가 결과 — UI 지표 트리거 패널 표시용 (v0.1.14).

        형식: ``{"EMA": "long"|"short"|None, "RSI": ..., "BB": ..., "MA": ...,
                "Ichimoku": ..., "Harmonic": ...}``
        None = 신호 없음 (중립).
        """
        return dict(self._last_indicator_status)

    # ============================================================
    # configure — 외부 inject 또는 settings 기반 자동
    # ============================================================

    def configure(
        self,
        client: ExchangeClient,
        *,
        symbol: str | None = None,
        timeframes: list[str] | None = None,
        strategy_config: StrategyConfig | None = None,
        tpsl_config: TpSlConfig | None = None,
        risk_pct: float | None = None,
        full_seed: bool | None = None,
        leverage: int | None = None,
    ) -> None:
        """매매 사이클 시작 전 어댑터/설정 명시.

        Args:
            client: ``ExchangeClient`` (CcxtClient 등) — 외부 inject. 테스트는 mock 사용.
            symbol: ccxt 표준 (default ``"BTC/USDT:USDT"``).
            timeframes: 멀티 TF 리스트 (default ``["15m","1H","4H"]``).
            strategy_config / tpsl_config: 전략·TP/SL 설정 (default = dataclass 기본).
            risk_pct: 거래당 risk 비율 (default 0.01 = 1%).
            full_seed: 풀시드 모드 (default False).
            leverage: 레버리지 배율 (default 10).

        Raises:
            RuntimeError: 봇 실행 중 호출 시 (stop 후 configure).
        """
        if self._running:
            raise RuntimeError("BotInstance running 중 configure 불가 — stop 후 호출")

        self._client = client
        # 수동 inject — auto-reconfigure 비활성. mock 테스트가 stop→start 시 새 ccxt
        # 만들지 않게 보장 (configure_from_settings 가 다시 True 로 set).
        self._auto_configured = False
        if symbol is not None:
            self._symbol = symbol
        if timeframes is not None:
            self._timeframes = list(timeframes)
        if strategy_config is not None:
            self._strategy_config = strategy_config
        if tpsl_config is not None:
            self._tpsl_config = tpsl_config
        if risk_pct is not None:
            self._risk_pct = risk_pct
        if full_seed is not None:
            self._full_seed = full_seed
        if leverage is not None:
            self._leverage = leverage

    def configure_from_settings(self) -> None:
        """``aurora.config.settings`` + ``config_store`` 결합 자동 configure.

        결합 우선순위:
            - 거래소·API 키·demo 플래그 = ``settings`` (.env, 보안)
            - 페어 / TF / 레버리지 / risk_pct / full_seed / use_* = ``config_store``
              (GUI 에서 사용자가 변경 가능)
            - 양쪽 다 미명시 시 default (BTC/USDT:USDT, [15m,1H,4H], 10x, 1%)

        Note:
            API 키는 항상 ``settings`` (.env) 에서만. ``config_store`` JSON 평문에
            저장 X — 보안 정책 (Phase 1 단순화).

        Raises:
            RuntimeError: 봇 실행 중 호출 시.
        """
        # 함수 내부 import — bot_instance 모듈 로드 시 의존성 비용 회피
        from aurora.exchange.ccxt_client import CcxtClient
        from aurora.exchange.team_aliases import resolve_alias
        from aurora.interfaces import config_store

        # GUI 에서 저장한 값 (옵션 — 없으면 default)
        cfg = config_store.load() or {}

        # API 키 결정 — alias 우선, 매핑 실패 시 .env fallback (testing 단계 단순화)
        # Why: 사용자가 GUI 에 "장수" 같은 nickname 입력하면 즉시 매핑 → 실 키 lookup.
        # cleanup 후엔 본 분기 제거 + .env 단일 path 복원 (data/team_aliases.json 메타 참조).
        alias = cfg.get("bybit_alias", "")
        resolved = resolve_alias(alias) if alias else None
        if resolved is not None:
            api_key, api_secret = resolved
            logger.info("BotInstance: alias '%s' resolved → Bybit Demo 키 적용", alias)
        else:
            api_key = settings.bybit_api_key
            api_secret = settings.bybit_api_secret
            if alias:
                logger.warning(
                    "BotInstance: alias '%s' 매핑 없음 — .env fallback", alias,
                )

        client = CcxtClient(
            exchange_id=cfg.get("default_exchange", settings.default_exchange),
            api_key=api_key,
            api_secret=api_secret,
            demo=settings.bybit_demo,
        )

        # StrategyConfig 의 use_* 토글 cfg 에서 반영
        strategy_cfg = StrategyConfig()
        for key in ("use_bollinger", "use_ma_cross", "use_harmonic", "use_ichimoku"):
            if key in cfg:
                setattr(strategy_cfg, key, bool(cfg[key]))

        self.configure(
            client=client,
            symbol=cfg.get("primary_symbol", _DEFAULT_SYMBOL),
            timeframes=cfg.get("timeframes", list(_DEFAULT_TIMEFRAMES)),
            strategy_config=strategy_cfg,
            leverage=cfg.get("leverage", _DEFAULT_LEVERAGE),
            risk_pct=cfg.get("risk_pct", 0.01),
            full_seed=cfg.get("full_seed", False),
        )
        # 자동 configure 흔적 — stop → start 사이클 시 재호출 트리거
        self._auto_configured = True

    # ============================================================
    # lifecycle — start / stop
    # ============================================================

    async def start(self) -> None:
        """매매 lifecycle 시작 — configure 됐으면 warmup + 매매 loop, 아니면 noop loop.

        호환성: 기존 PR-C 테스트는 configure 없이 start() 호출 → cache/executor
        생성 X, _run_loop 가 1초 sleep 만 (lifecycle flag 만 검증).

        자동 reconfigure: ``configure_from_settings`` 으로 자동 configure 됐던 봇이
        stop 으로 client 정리된 후 재 start 될 때 자동으로 재 configure 호출.
        ``configure(수동 inject)`` 케이스는 영향 X.
        """
        if self._running:
            logger.warning("BotInstance.start: 이미 실행 중")
            return

        # stop → start 사이클 후 client None + 자동 configure 이력 있으면 재호출
        if self._client is None and self._auto_configured:
            try:
                self.configure_from_settings()
                logger.info("BotInstance.start: auto-reconfigured (stop 후 재시작)")
            except Exception as e:  # noqa: BLE001 — 실패해도 noop loop 로 진입
                logger.warning("BotInstance.start: auto-reconfigure 실패 (%s) — noop", e)
                self._auto_configured = False  # 다음 시도 안 함

        # configure 됐으면 어댑터 lazy 생성 + warmup
        if self._client is not None:
            self._cache = MultiTfCache(self._client, self._symbol, self._timeframes)
            # Executor 가 이미 보존되어 있으면 새 client 만 주입 (포지션 state 살림).
            # 처음 start 거나 reset_for_test 후엔 None → 신규 생성.
            if self._executor is not None:
                self._executor.set_client(self._client)
                logger.info(
                    "BotInstance.start: Executor state 보존 (has_position=%s) + client 재주입",
                    self._executor.has_position,
                )
            else:
                self._executor = Executor(self._client, self._symbol, self._tpsl_config)
            warmup_lookback = {
                tf: _WARMUP_DEFAULTS.get(tf, 500) for tf in self._timeframes
            }
            await self._cache.warmup(warmup_lookback)
            # v0.1.26: 영속화된 활성 포지션 복원 시도 — .exe 종료 후 자기 포지션 잊지 않게.
            await self._restore_active_position()
            logger.info(
                "BotInstance.start: configured (symbol=%s, tfs=%s, leverage=%dx)",
                self._symbol, self._timeframes, self._leverage,
            )
        else:
            logger.info("BotInstance.start: noop (configure 미호출 — lifecycle only)")

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("BotInstance: 시작")

    async def _restore_active_position(self) -> None:
        """봇 시작 시 영속화된 활성 포지션 복원 (v0.1.26).

        흐름:
            1. ``active_position_store.load()`` — 영속 plan 있으면 dict 반환
            2. 없으면 (첫 실행 / 정상 종료 후) noop
            3. 거래소 측 ``fetch_position`` 으로 정합성 검증
                - 거래소 X → 사용자가 직접 청산했음 → ``clear()`` + noop
                - symbol/direction 불일치 → 외부 변경 → ``clear()`` + noop
                - qty 큰 차이 (>10%) → 외부 추가/부분 청산 → ``clear()`` + noop (안전 측)
                - 정합 → ``Executor.restore_plan()`` 호출 + 봇이 자기 포지션으로 인식

        이미 ``_executor.has_position`` 이면 (stop→start 사이클, _plan 보존) skip.
        paper 모드 = skip (실 거래소 호출 안 하는 정책).
        """
        if settings.run_mode == "paper":
            return
        if self._client is None or self._executor is None:
            return
        if self._executor.has_position:
            # Executor state 가 살아있는 stop→start 사이클 — 복원 불필요 (이미 메모리)
            return

        saved = active_position_store.load()
        if saved is None:
            return

        plan = active_position_store.reconstruct_plan(saved.get("plan", {}))
        if plan is None:
            active_position_store.clear()
            return

        saved_symbol = saved.get("symbol", "")
        if saved_symbol != self._symbol:
            logger.info(
                "재시작: 영속 포지션 심볼(%s) != 현재(%s) → clear",
                saved_symbol, self._symbol,
            )
            active_position_store.clear()
            return

        try:
            actual = await self._client.fetch_position(self._symbol)
        except Exception as e:  # noqa: BLE001 — UI 안전, 시작 차단 방지
            logger.warning("재시작 복원: fetch_position 실패 (%s) — 다음 step 재시도", e)
            return

        if actual is None:
            logger.info("재시작: 영속 포지션 거래소엔 없음 → 외부 청산 — clear")
            active_position_store.clear()
            return

        if actual.side != plan.direction:
            logger.warning(
                "재시작: 영속 방향(%s) != 거래소(%s) — 외부 변경, 복원 skip",
                plan.direction, actual.side,
            )
            active_position_store.clear()
            return

        saved_remaining = float(saved.get("remaining_qty", plan.position.coin_amount))
        # qty 10% 이상 차이 = 외부 추가/부분 청산 의심. 작은 차이는 부동소수 + 거래소
        # 반올림 허용 (Bybit 0.001 단위 등).
        denom = max(saved_remaining, 1e-9)
        if abs(actual.qty - saved_remaining) / denom > 0.1:
            logger.warning(
                "재시작: 영속 qty(%.6f) != 거래소(%.6f) — 차이 큼, 복원 skip",
                saved_remaining, actual.qty,
            )
            active_position_store.clear()
            return

        # 모든 검증 통과 — 복원
        self._executor.restore_plan(
            plan=plan,
            triggered_by=list(saved.get("triggered_by", [])),
            opened_at_ts=int(saved.get("opened_at_ts", 0)),
            remaining_qty=actual.qty,  # 거래소 실제 qty 사용 (소량 변동 흡수)
            tp_hits=int(saved.get("tp_hits", 0)),
        )
        logger.info(
            "재시작: 활성 포지션 복원 — %s %s qty=%.6f sl=%.2f tp_hits=%d (영속 plan)",
            self._symbol, plan.direction, actual.qty,
            plan.sl_price, saved.get("tp_hits", 0),
        )
        # 거래소 실제 qty 로 살짝 보정됐으니 다시 save (state 동기화)
        self._persist_active()

    async def stop(self) -> None:
        """매매 lifecycle 중지 — task cancel + client cleanup."""
        if not self._running:
            logger.warning("BotInstance.stop: 이미 중지됨")
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # client 정리 — async ccxt 세션만 close. Executor state (_plan / SL / TP)
        # 는 보존하여 stop → start 사이클 시 자기 포지션을 잊지 않음.
        # Why: 이전엔 stop 시 _executor=None 처리 → 재 start 시 새 Executor → _plan=None
        # → has_position=False → 진입 시도 → InsufficientFunds 무한 루프 (v0.1.5 기록).
        # Fix: Executor 보존, start 시 새 client 를 set_client 로 주입 (v0.1.6).
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                logger.exception("BotInstance.stop: client.close() 실패 (무시)")
            self._client = None
            self._cache = None
            # self._executor 는 보존 — 자기 포지션 (_plan) 살림

        logger.info("BotInstance: 중지")

    # ============================================================
    # 내부 — 매매 사이클
    # ============================================================

    async def _run_loop(self) -> None:
        """봇 메인 loop — 매 1초 ``_step()`` 호출.

        예외 발생 시 stack trace 로깅 후 loop 계속 (운영 안정성). cancel 만 전파.
        """
        while self._running:
            try:
                await self._step()
            except asyncio.CancelledError:
                raise  # cancel 은 stop() 에서 정상 종료 처리
            except Exception:
                logger.exception("BotInstance loop error — 다음 step 까지 대기")
            await asyncio.sleep(_LOOP_INTERVAL_SEC)

    async def _step(self) -> None:
        """1 step — fetch / 트레일링 / 청산 / 진입 검사.

        configure 안 됐으면 즉시 return (noop).
        """
        if self._cache is None or self._executor is None or self._client is None:
            return

        # 1. 새 봉 fetch (봉 경계 시점만 실 호출)
        df_by_tf = await self._cache.step()

        # 2. 현재가 (가장 빠른 TF 의 마지막 close)
        primary_tf = self._timeframes[0]
        primary_df = df_by_tf.get(primary_tf)
        if primary_df is None or primary_df.empty:
            return
        current_price = float(primary_df["close"].iloc[-1])

        # 3. 활성 포지션 — 트레일링 + 청산 + REVERSE 검사 (진입 평가는 분기 별)
        # Aurora 정책 (페어당 1개) — 보유 중엔 SL/TP/REVERSE 청산만, 신규 진입 X.
        if self._executor.has_position:
            # 3-1. 거래소 측 sync — 사용자 직접 청산 / liquidation 감지 (v0.1.7).
            # paper 모드는 fetch_position 항상 None 반환 (정책) → sync skip
            # (v0.1.9 fix: 안 그러면 paper 진입 직후 즉시 reset 무한 루프).
            if settings.run_mode != "paper":
                actual = await self._client.fetch_position(self._symbol)
                if actual is None:
                    logger.info(
                        "외부 청산 감지: %s 봇 자기 포지션 거래소 측 사라짐 — state reset",
                        self._symbol,
                    )
                    self._executor.reset_position()
                    # v0.1.26: 영속 데이터도 같이 clear — 다음 시작 시 안 떠올리게
                    self._persist_active()
                    return  # 다음 step 부터 진입 평가 분기 진입

                # 3-2. qty sync — 사용자 직접 추가 진입 / 부분 청산 인지 (v0.1.8).
                # Why: 봇 기록 _remaining_qty 와 거래소 실제 qty 가 다르면 SL 청산 시
                # reduce_only 위반 또는 잔여 위험 노출. 보수적으로 min 으로 갱신.
                if abs(actual.qty - self._executor._remaining_qty) > 1e-6:
                    logger.warning(
                        "qty 불일치: 봇 %.4f / 거래소 %.4f → min(봇, 거래소) 로 보수 갱신",
                        self._executor._remaining_qty, actual.qty,
                    )
                    self._executor._remaining_qty = min(
                        self._executor._remaining_qty, actual.qty,
                    )
                    if self._executor._remaining_qty <= 1e-9:
                        # 거래소 측 0 (사용자 전량 청산) — reset
                        self._executor.reset_position()
                        return

            # 3-3. 트레일링 SL 갱신 + tp_hits 카운트
            prev_tp_hits = self._executor._tp_hits
            await self._executor.update_trailing_sl(current_price)

            # 3-4. TP 단계별 부분 청산 (v0.1.8) — tp_hits 변화 감지.
            # tp_hits 0→1: 25% 청산, 1→2: 25%, 2→3: 25%, 3→4: tp_full (should_close).
            # Why: 백테스트 엔진과 라이브 동작 일치 (PF 괴리 방지).
            if self._executor._tp_hits > prev_tp_hits and self._executor.has_position:
                new_tp_idx = self._executor._tp_hits - 1  # 새로 도달한 단계 (0-indexed)
                allocations = self._tpsl_config.tp_allocations
                # 마지막 단계는 tp_full 이 처리 — partial 은 마지막 직전까지만
                if 0 <= new_tp_idx < len(allocations) - 1:
                    entry_qty = self._executor._plan.position.coin_amount
                    partial_qty = min(
                        entry_qty * (allocations[new_tp_idx] / 100.0),
                        self._executor._remaining_qty,
                    )
                    if partial_qty > 1e-9:
                        try:
                            _, closed = await self._executor.close_position(
                                qty=partial_qty, reason="tp_partial",
                            )
                            self._record_closed(closed)
                            logger.info(
                                "TP%d 부분 청산: qty=%.6f (allocation=%.1f%%)",
                                new_tp_idx + 1, partial_qty, allocations[new_tp_idx],
                            )
                        except RuntimeError as e:
                            logger.warning("TP 부분 청산 실패: %s", e)

            # 3-5. SL / tp_full 청산 (포지션 살아있으면)
            if self._executor.has_position:
                reason = self._executor.should_close(current_price)
                if reason is not None:
                    _, closed = await self._executor.close_position(reason=reason)
                    self._record_closed(closed)
                    return

            # 3-6. REVERSE 신호 (v0.1.8) — 보유 중 반대 방향 신호 → 청산.
            # Why: 백테스트 D-24 정합 (engine.py L353). 라이브 누락 시 PF 괴리.
            if self._executor.has_position:
                rev_signals = []
                rev_signals.extend(detect_ema_touch(df_by_tf, self._strategy_config))
                df_1h = df_by_tf.get("1H")
                if df_1h is not None and not df_1h.empty:
                    rev_signals.extend(
                        detect_rsi_divergence(df_1h, self._strategy_config),
                    )
                rev_signals.extend(
                    evaluate_selectable(
                        df_by_tf, self._strategy_config, symbol=self._symbol,
                    ),
                )
                cur_dir = self._executor._plan.direction
                if compose_exit(cur_dir, rev_signals):
                    logger.info("REVERSE 신호 (%s) → 청산 (다음 step 진입 평가)", cur_dir)
                    _, closed = await self._executor.close_position(reason="reverse")
                    self._record_closed(closed)
            return

        # 4. 외부 포지션 detect (v0.1.9 — 신호 평가 전으로 위치 변경)
        # Why: 이전엔 신호 평가 후 진입 직전에 detect → 신호 없을 때마다 flag reset
        # → 다음 신호 발생 시 또 WARNING 출력 (반복 로그). 신호 평가 전 detect 하면
        # flag 가 외부 포지션 자체 변화에만 반응 → WARNING 1회 보장.
        # paper 모드는 fetch_position 항상 None 정책 → sync skip.
        if settings.run_mode != "paper":
            external = await self._client.fetch_position(self._symbol)
            if external is not None:
                if not self._external_position_warned:
                    logger.warning(
                        "외부 포지션 감지: %s %s qty=%.4f entry=%.2f — Aurora 진입 skip "
                        "(사용자가 직접 청산하면 자동 매매 재개)",
                        self._symbol, external.side, external.qty, external.entry_price,
                    )
                    self._external_position_warned = True
                self._external_position = True
                return  # 외부 포지션 있으면 진입 평가 X
            # 외부 포지션 사라짐 → flag reset + 진입 평가 진행
            if self._external_position:
                logger.info("외부 포지션 사라짐 — Aurora 자동 매매 재개")
            self._external_position = False
            self._external_position_warned = False

        # 5. 진입 신호 평가 (무포지션)
        signals = []
        signals.extend(detect_ema_touch(df_by_tf, self._strategy_config))
        df_1h = df_by_tf.get("1H")
        if df_1h is not None and not df_1h.empty:
            signals.extend(detect_rsi_divergence(df_1h, self._strategy_config))
        signals.extend(
            evaluate_selectable(df_by_tf, self._strategy_config, symbol=self._symbol),
        )

        # 지표 트리거 상태 갱신 (v0.1.14, v0.1.18 4-state 확장).
        # 값: "long" / "short" (활성) / "neutral" (대기) / "disabled" (Selectable 꺼짐).
        # Fixed 지표 (EMA/RSI) 는 항상 enabled. Selectable 4 종은 use_* 토글에 따라.
        cfg = self._strategy_config
        disabled_map = {
            "BB": not cfg.use_bollinger,
            "MA": not cfg.use_ma_cross,
            "Ichimoku": not cfg.use_ichimoku,
            "Harmonic": not cfg.use_harmonic,
        }
        self._last_indicator_status = {
            cat: ("disabled" if disabled_map.get(cat, False) else "neutral")
            for cat in _INDICATOR_CATEGORIES
        }
        for sig in signals:
            cat = _categorize_source(sig.source)
            if cat is not None and self._last_indicator_status[cat] != "disabled":
                self._last_indicator_status[cat] = sig.direction.value

        decision = compose_entry(signals)

        if not decision.enter or decision.direction is None:
            return

        # 6. 진입 실행 — equity 조회 + RiskPlan 산출
        balance = await self._client.get_equity()
        plan = build_risk_plan(
            entry_price=current_price,
            direction=decision.direction.value,
            leverage=self._leverage,
            equity_usd=balance.total_usd,
            config=self._tpsl_config,
            risk_pct=self._risk_pct,
            full_seed=self._full_seed,
        )
        await self._executor.open_position(plan, triggered_by=decision.triggered_by)
        logger.info(
            "BotInstance: 진입 — %s %s qty=%.6f (triggered_by=%s, score=%.2f)",
            self._symbol, decision.direction.value, plan.position.coin_amount,
            decision.triggered_by, decision.score,
        )
        # v0.1.26: 진입 직후 영속화 — .exe 종료 시 잊지 않게
        self._persist_active()


# ============================================================
# 싱글톤 — 기존 API 그대로 (PR-C 호환)
# ============================================================

_instance: BotInstance | None = None


def get_instance() -> BotInstance:
    """싱글톤 접근자 — 첫 호출 시 lazy 생성."""
    global _instance
    if _instance is None:
        _instance = BotInstance()
    return _instance


def reset_for_test() -> None:
    """테스트 격리용 — 싱글톤 초기화."""
    global _instance
    _instance = None
