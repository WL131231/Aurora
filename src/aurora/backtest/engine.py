"""백테스트 엔진 — Walk-forward 시뮬 + RiskPlan 기반 진입·청산.

PR-2 산출 OHLCV (1 분봉 parquet) 를 1 분 단위로 ``MultiTfAggregator`` 에 공급해
멀티 TF 닫힘 이벤트마다 ``core.strategy`` 신호 평가 + ``compose_entry`` 합산 +
``build_risk_plan`` 으로 RiskPlan 산출. 매 1 분봉 SL/TP 도달 / 트레일링 갱신 /
연속 손절·MDD 가드. 차용: ``replay_engine.py`` 시뮬 골격 (DESIGN.md §6.2 10단계)
+ ``adaptive_backtest.py`` outer 루프. 본 모듈은 골격 — Group 2 에서 시뮬 본문
구현. trade 기록은 ``TradeRecord`` 누적 후 ``stats.compute_session_stats`` 위임.

상세 spec: ``src/aurora/backtest/DESIGN.md`` §5.2 + §6.2.

담당: ChoYoon
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from aurora.backtest.cost import (
    apply_costs,
    apply_slippage,
    slip_pct,
)
from aurora.backtest.replay import MultiTfAggregator
from aurora.backtest.stats import TradeRecord
from aurora.core.indicators import atr_wilder
from aurora.core.risk import (
    RiskPlan,
    TpSlConfig,
    TpSlMode,
    build_risk_plan,
    sl_pct_for_leverage,
    tp_pct_4_levels_for_leverage,
    update_trailing_sl,
)
from aurora.core.signal import compose_entry, compose_exit
from aurora.core.strategy import (
    Direction,
    EntrySignal,
    Regime,
    StrategyConfig,
    classify_regime,
    detect_ema_touch,
    detect_rsi_divergence,
    evaluate_selectable,
)

# 모듈 logger — 0 거래 / 영구 정지 / 강제 청산 등 비치명 경고용
logger = logging.getLogger(__name__)


# ============================================================
# 백테스트 설정 — 사용자 노출 파라미터 + 가드 임계값
# ============================================================


@dataclass(slots=True)
class BacktestConfig:
    """백테스트 설정 — 시뮬 파라미터 + 가드 임계값.

    ``initial_capital`` / ``leverage`` / ``risk_pct`` 는 D-8 (장수 동의) 사용자
    노출. ``max_dd_stop_pct`` / ``consec_sl_pause_*`` 는 차용 코드
    (``replay_engine.py`` L44/L49/L50) 가드 그대로. ``timeframes`` 는
    ``MultiTfAggregator`` 가 집계할 TF — Fixed (EMA 200/480) 멀티 TF 평가 +
    1H RSI Divergence 정합.

    Attributes:
        symbol: 거래 페어 (예: ``"BTCUSDT"``).
        timeframes: 집계 TF 목록 (``replay.py`` ``TF_MINUTES`` 키).
        initial_capital: 시드 USD (시뮬 시작 잔고).
        leverage: 레버리지 (Aurora 정책 10~50x).
        risk_pct: 거래당 최대 손실 비율 (R 기반 size, D-8). 디폴트 1%.
        max_dd_stop_pct: 시드 대비 누적 손실이 이 비율 도달 시 영구 정지
            (replay L44 차용, 시드 -15%). 거래 사이클 강제 종료 가드.
        consec_sl_pause_threshold: 연속 SL N 회 도달 시 일시 정지 (replay L49).
        consec_sl_pause_minutes: 일시 정지 시간 (분). replay L50 의 ``480 ×
            3분봉 = 24h`` → Aurora 1 분봉 기준 ``1440`` 으로 단위 변환 (인터벌
            의존 제거). pause 동안 진입 게이트 차단, 보유 포지션의 청산은 정상
            동작.
        risk_config: ``TpSlConfig`` (mode + tp/sl pcts + trailing). ``None`` 이면
            ``BacktestEngine.__init__`` 시점에 ``sl_pct_for_leverage`` /
            ``tp_pct_4_levels_for_leverage`` (v0.1.13 신규, D-3 등간격 4 분할)
            그래디언트로 자동 산출 → ``self._risk_config`` 박음 (config 인스턴스
            mutate X — 외부 재사용 안전).
        strategy_config: ``StrategyConfig`` (EMA / RSI Div / Selectable 지표
            on/off + 파라미터). ``None`` 이면 ``__init__`` 시점에
            ``StrategyConfig()`` 디폴트 (Fixed only) 자동 산출 → ``self.
            _strategy_config`` 박음 (D-8 사용자 노출 패턴 정합).
    """

    symbol: str = "BTCUSDT"
    timeframes: list[str] = field(
        default_factory=lambda: ["1H", "2H", "4H", "1D", "1W"],
    )
    initial_capital: float = 10_000.0
    leverage: int = 10
    risk_pct: float = 0.01            # 1R = 시드의 1% (replay L24, D-8)
    max_dd_stop_pct: float = 0.15     # 시드 -15% 영구 정지 (replay L44)
    consec_sl_pause_threshold: int = 2    # 연속 SL 2 회 (replay L49)
    consec_sl_pause_minutes: int = 1440   # 정지 24 h (replay L50, 단위 변환)
    risk_config: TpSlConfig | None = None       # __init__ 자동 산출 분기
    strategy_config: StrategyConfig | None = None  # __init__ 디폴트 자동


# ============================================================
# 보유 포지션 — 진입~청산 사이의 ephemeral 상태
# ============================================================


@dataclass(slots=True)
class Position:
    """보유 포지션 추적 상태 — 진입 시 생성, 청산 시 ``None`` 으로 리셋.

    페어당 1 포지션 정책 (``CLAUDE.md`` ``max_positions_per_pair=1``) → engine
    내 ``self.position: Position | None`` 단일 인스턴스 (D-20). ``tp_hits`` 는
    도달한 TP 단계 수 (counter, 0~4) — ``update_trailing_sl(tp_hits: int, ...)``
    입력 정합 (D-21). ``highest/lowest_since_entry`` 는 ``PERCENT_BELOW_*``
    트레일링 모드 입력 (DESIGN F-9).

    Attributes:
        plan: 진입 시 산출한 ``RiskPlan`` (entry_price/direction/sl_price/
            tp_prices[4]/position/trailing_mode).
        entry_ts: 진입 시각 (ms epoch). ``TradeRecord.entry_ts`` 와 동일 값.
        current_sl: 현재 SL 가격 — ``update_trailing_sl`` 갱신 결과 (단방향
            보장). 진입 직후엔 ``plan.sl_price`` 와 동일.
        size_pct: 진입 시점 ``margin_usd / equity`` 비율 — ``cost.apply_costs``
            인자 정합 (replay 패턴 차용). 진입 시 lock + 닫힌 청산 시 잔여 비율
            산출 기준 (default 없음 — 0 나눗셈 위험으로 명시 인자 강제).
        tp_hits: 도달한 TP 단계 수 (0~4). 0 이면 미도달, 4 면 전부 도달
            (full close 분기).
        highest_since_entry: 진입 후 최고가 (롱 트레일링용).
        lowest_since_entry: 진입 후 최저가 (숏 트레일링용).
        regime: 진입 시점 4H 시장 국면 (D-5, Issue #110). ``classify_regime``
            산출값 박힘 — ``_close`` / ``_partial_close`` 가 ``TradeRecord.regime``
            에 전파. 4H 미닫힘 진입 시 ``Regime.UNKNOWN`` (디폴트).
    """

    plan: RiskPlan
    entry_ts: int
    current_sl: float
    size_pct: float
    tp_hits: int = 0
    highest_since_entry: float = 0.0
    lowest_since_entry: float = 0.0
    regime: Regime = Regime.UNKNOWN


# ============================================================
# 엔진 본체 — 1 분봉 단위 시뮬 + trade 기록
# ============================================================


class BacktestEngine:
    """단일 페어 백테스트 엔진 — 1 분봉 ``DataFrame`` → ``list[TradeRecord]``.

    DESIGN.md §6.2 10 단계 흐름 그대로:

        1. ``aggregator.step`` → 닫힘 dict
        2-4. 보유 포지션 트레일링 갱신 + SL/TP 체크 + high/low 갱신
        5. 닫힌 TF 없으면 early return
        6-7. MDD / pause 가드
        8. 닫힌 TF 들 ``get_df`` → ``strategy.evaluate``
        9. ``compose_entry`` → ``CompositeDecision``
        10. ``decision.enter`` 시 ``build_risk_plan`` + ``_open``

    Group 1 (본 PR) — 골격만 (``run`` 본문은 ``NotImplementedError``).
    Group 2 — ``step()`` 10 단계 본 구현. Group 3 — 테스트 + 통합 시나리오.

    aggregator 인스턴스는 ``run()`` 내부에서 신규 생성 (D-22, 상태 격리).
    ``equity_curve`` 는 engine 미추적 — ``stats.compute_session_stats`` 가
    ``trades`` 에서 사후 재계산 (D-23 DRY).
    """

    def __init__(self, config: BacktestConfig) -> None:
        """엔진 초기화 — 시드 / 가드 카운터 / trade 누적 컨테이너.

        ``risk_config`` / ``strategy_config`` 가 ``None`` 이면 본 시점에 자동
        산출 → ``self._risk_config`` / ``self._strategy_config`` 박음. config
        인스턴스는 mutate X (외부 재사용 안전, leverage 다른 두 엔진 같은
        config 공유 가능).

        Args:
            config: 시뮬 파라미터 + 가드 임계값.
        """
        self.config = config

        # risk_config 자동 산출 — sl_pct_for_leverage + tp_pct_4_levels_for_leverage
        # (v0.1.13 신규, D-3 등간격 4 분할). config mutate X 보장 위해 self.
        # _risk_config 별도 박음.
        if config.risk_config is not None:
            self._risk_config: TpSlConfig = config.risk_config
        else:
            self._risk_config = TpSlConfig(
                mode=TpSlMode.FIXED_PCT,
                fixed_tp_pcts=tp_pct_4_levels_for_leverage(config.leverage),
                fixed_sl_pct=sl_pct_for_leverage(config.leverage),
            )

        # strategy_config 디폴트 — None 시 StrategyConfig() (Fixed only)
        self._strategy_config: StrategyConfig = (
            config.strategy_config if config.strategy_config is not None
            else StrategyConfig()
        )

        # 시드 추적 — peak_balance 는 _update_peak / _check_max_dd 에서 갱신
        self.balance: float = config.initial_capital
        self.peak_balance: float = config.initial_capital

        # 보유 포지션 (단일, 페어당 1 정책, D-20)
        self.position: Position | None = None

        # 가드 상태 — replay_engine L257-261 차용
        self.stopped: bool = False    # MDD 도달 시 True (영구, run 종료까지)
        self.pause_bars: int = 0      # 연속 SL pause 잔여 분 (1m × N)
        self.consec_sl: int = 0       # 연속 SL 카운트 (TP1+ 도달 시 reset)

        # 결과 누적 — stats.compute_session_stats 입력
        self.trades: list[TradeRecord] = []

        # 슬리피지 산출용 — step() 진입점에서 매 1m 봉 갱신 (replay L257-260)
        self._last_high: float = 0.0
        self._last_low: float = 0.0
        self._last_close: float = 0.0

        # D-5 regime — step() 8 단계 (4H 닫힘 시) 갱신 + _open() 시점 Position.regime 박음.
        # 4H 미닫힘 상태로 진입하는 경우 디폴트 UNKNOWN 박힘 (분류 미산출 자연 처리).
        self._last_regime: Regime = Regime.UNKNOWN

    # ─────────────────────────────────────────────
    # public API — Group 2 본문 구현 자리
    # ─────────────────────────────────────────────

    def run(self, df_1m: pd.DataFrame) -> list[TradeRecord]:
        """1 분봉 ``DataFrame`` 통째 받아 시뮬 + trade 기록 반환.

        ``MultiTfAggregator`` 신규 생성 (D-22 상태 격리, run 호출별 독립) +
        매 1m 봉마다 ``self.step(bar, aggregator)`` 호출 + 마지막 봉 도달 시
        보유 포지션 강제 청산 (``_force_close_at_end``).

        Args:
            df_1m: PR-2 산출 parquet 로드 결과. ``DatetimeIndex`` (ms epoch
                기반) + OHLCV 컬럼. ``df_1m.iterrows()`` 자연 호출 가능 형태.
                ``timestamp`` 변환은 ``step()`` 진입점 1 줄 (``ts_ms =
                int(bar_1m.name.value // 10**6)``, D-26).

        Returns:
            ``TradeRecord`` 리스트 — ``stats.compute_session_stats`` 입력.
            0 거래 세션도 빈 리스트 정상 반환.
        """
        # aggregator 신규 생성 — run 별 독립 (D-22)
        aggregator = MultiTfAggregator(timeframes=self.config.timeframes)

        last_close = 0.0
        last_ts = 0
        for ts, bar in df_1m.iterrows():
            self.step(bar, aggregator)
            last_close = float(bar["close"])
            last_ts = int(ts.value // 10**6)

        # 마지막 봉 미청산 포지션 → FORCE_END 강제 청산 (adaptive L376-391)
        if last_ts > 0:
            self._force_close_at_end(last_close=last_close, last_ts=last_ts)

        return self.trades

    def step(self, bar_1m: pd.Series, aggregator: MultiTfAggregator) -> None:
        """1 분봉 1 개 처리 — DESIGN.md §6.2 10 단계 흐름.

        보유 분기 (트레일링 + SL/TP 체크 + high/low 갱신) → 닫힌 TF early
        return → MDD 가드 → 신호 평가 (3 함수 합본) → REVERSE 분기 (D-24)
        또는 신규 진입 (build_risk_plan + _open). pause 카운터는 본 메서드
        진입점에서 매 1m 1 회 감소 (replay L347-348, 1m unit 정합).

        Args:
            bar_1m: 1 분봉 Series — ``.name`` = ``pd.Timestamp`` 인덱스 (ms
                epoch 변환 가능), 컬럼 ``open/high/low/close`` (volume 옵션).
            aggregator: ``run()`` 가 신규 생성한 ``MultiTfAggregator`` 인스턴스.
                ``step()`` 별 인자 주입 — 단위 테스트 친화 + 상태 격리 (D-22).

        Note:
            동일 1m 에 close + open 금지 (D-20 단일 포지션 정책) — REVERSE
            분기 후 즉시 return, 다음 1m 부터 신규 진입 가능. ATR 모드 + 4H
            미닫힘 시 진입 skip (D-4 정합 — ``build_risk_plan(atr=None)`` 호출
            시 ValueError 회피).
        """
        # 0a. _last_OHLC 갱신 — slip 산출 입력 (현재 봉 변동성 기준)
        self._last_high = float(bar_1m["high"])
        self._last_low = float(bar_1m["low"])
        self._last_close = float(bar_1m["close"])
        ts_ms = int(bar_1m.name.value // 10**6)            # D-26

        # 0b. 매 1m 가드 — pause 카운터 1 감소 (replay L347-348, 1m unit 정합)
        # Why: 닫힌 TF 분기 뒤로 옮기면 단위 의미 깨짐 (1H 닫힘 시만 감소 → 1440
        #      이 60일이 됨). docstring contract "매 1 분봉 호출" 우선.
        self._tick_pause()

        # 1. aggregator step — 닫힘 dict
        closed = aggregator.step(bar_1m)

        # 2-4. 보유 포지션 분기 — 트레일링 갱신 + SL/TP 체크 + high/low 갱신
        if self.position is not None:
            p = self.position
            # 2. 트레일링 SL 갱신 (5 모드 + OFF, 단방향 보장은 risk.py 내부)
            p.current_sl = update_trailing_sl(
                current_sl=p.current_sl,
                plan=p.plan,
                config=self._risk_config,
                tp_hits=p.tp_hits,
                highest_since_entry=p.highest_since_entry,
                lowest_since_entry=p.lowest_since_entry,
            )
            # 3. SL/TP 도달 체크 + gap-fill (단계 1·2 _check_exits)
            record = self._check_exits(
                ts_ms=ts_ms,
                open_=float(bar_1m["open"]),
                high=self._last_high,
                low=self._last_low,
            )
            # 4. 청산 X 시 high/low 갱신 — 다음 봉 트레일링 입력
            if record is None and self.position is not None:
                self.position.highest_since_entry = max(
                    self.position.highest_since_entry, self._last_high,
                )
                self.position.lowest_since_entry = min(
                    self.position.lowest_since_entry, self._last_low,
                )

        # 5. 닫힌 TF 추출 — 어떤 TF 도 닫힘 X 면 신호 평가 skip (early return)
        closed_tfs = [tf for tf, b in closed.items() if b is not None]
        if not closed_tfs:
            return

        # 6. MDD 영구 정지 가드 (글로벌)
        self._check_max_dd()

        # 7. df_by_tf + signals — 3 함수 합본 (C1 정합, DESIGN §6.2 8 단계)
        # core.strategy.evaluate() 통합 함수 부재 → detect_ema_touch +
        # detect_rsi_divergence (1H) + evaluate_selectable 직접 호출.
        df_by_tf = {tf: aggregator.get_df(tf) for tf in closed_tfs}
        sc = self._strategy_config
        signals: list[EntrySignal] = list(detect_ema_touch(df_by_tf, sc))
        if "1H" in df_by_tf:
            signals.extend(detect_rsi_divergence(df_by_tf["1H"], sc))
        signals.extend(
            evaluate_selectable(df_by_tf, sc, symbol=self.config.symbol),
        )

        # 7b. D-5 regime 갱신 — 4H 닫힘 시만 (Issue #110, 정책 spec 8 단계 위치).
        # 4H 미닫힘 시 self._last_regime 보존 (이전 4H 닫힘 분류값 유지). 진입 직전
        # _open() 시점에 Position.regime 박힘 → _close/_partial_close 가 TradeRecord
        # 로 전파. VOLATILE 시 신호 평가 skip 등 액션은 별도 후속 (보충 의견 F1).
        if "4H" in df_by_tf:
            self._last_regime = classify_regime(df_by_tf["4H"])

        # 8. 보유 중 분기 — REVERSE 체크 (D-24, compose_exit 활용)
        # Why: signal.is_reverse_signal 부재 — compose_exit(direction, signals)
        # 이 내부에서 compose_entry 호출 후 반대 방향 비교. 동일 1m close+open
        # 금지 (D-20) → REVERSE 후 즉시 return.
        if self.position is not None:
            cur_dir = Direction(self.position.plan.direction)
            if compose_exit(cur_dir, signals):
                self._close(
                    fill=self._last_close, ts_ms=ts_ms, reason="REVERSE",
                )
            return

        # 9. 신규 진입 가드 — stopped (영구 정지) / pause_bars / decision
        if self.stopped or self.pause_bars > 0:
            return
        decision = compose_entry(signals)
        if not decision.enter or decision.direction is None:
            return

        # 10. ATR 모드 + 4H 미닫힘 → 진입 skip (D-4 정합, sanity [22])
        cfg = self._risk_config
        if cfg.mode == TpSlMode.ATR and "4H" not in df_by_tf:
            return
        atr_value = (
            float(atr_wilder(df_by_tf["4H"]).iloc[-1])
            if cfg.mode == TpSlMode.ATR else None
        )

        # 11. build_risk_plan + _open (1m close 진입, gap-fill X — Q4 잠정안)
        plan = build_risk_plan(
            entry_price=self._last_close,
            direction=decision.direction.value,
            leverage=self.config.leverage,
            equity_usd=self.balance,
            config=cfg,
            atr=atr_value,
            risk_pct=self.config.risk_pct,                 # D-8 사용자 노출
            full_seed=False,
            min_seed_pct=0.40,                             # D-1 정책
        )
        self._open(plan=plan, ts_ms=ts_ms)

    # ─────────────────────────────────────────────
    # 헬퍼 함수 — Group 2 본문 구현 자리 (시그니처만 박음)
    # ─────────────────────────────────────────────

    def _open(self, plan: RiskPlan, ts_ms: int) -> None:
        """포지션 진입 — ``Position`` 생성 + ``self.position`` 설정.

        진입 슬리피지는 ``step()`` 진입 분기에서 ``plan.entry_price`` 에 사전
        반영. 진입 수수료는 청산 시점 ``cost.apply_costs`` 가 왕복 (2×) 으로
        차감 (replay 패턴 차용 — 청산 시 ``2 × fee × notional``).

        Raises:
            RuntimeError: 이미 보유 중일 때 (단일 포지션 정책 D-20 위반 — 호출자
                ``step()`` 의 진입 분기 가드 누락 신호).
        """
        if self.position is not None:
            raise RuntimeError(
                f"이미 보유 포지션 있음 (entry_ts={self.position.entry_ts}) — "
                f"step() 진입 분기 가드 누락",
            )
        # size_pct 진입 lock — 단일 포지션 정책상 entry~close 사이 balance 변동 X
        # (cost.apply_costs 인자 정합, replay 패턴 차용).
        size_pct = plan.position.margin_usd / self.balance
        # PERCENT_BELOW_HIGHEST 트레일링은 진입 직후부터 활성화 — 초기값을
        # entry_price 로 박아 단방향 갱신 보장.
        self.position = Position(
            plan=plan,
            entry_ts=ts_ms,
            current_sl=plan.sl_price,
            size_pct=size_pct,
            tp_hits=0,
            highest_since_entry=plan.entry_price,
            lowest_since_entry=plan.entry_price,
            regime=self._last_regime,
        )

    def _close(self, fill: float, ts_ms: int, reason: str) -> TradeRecord:
        """포지션 전체 청산 — ``TradeRecord`` 생성·누적 + 잔고 갱신 + 카운터
        분기 (D-2 / D-25).

        reason 매핑 (DESIGN.md §11 D-2 동기):

            ====================  ====================  ==========================
            Reason                consec_sl 카운트      분류
            ====================  ====================  ==========================
            TP4                   reset (= 0)           익절 (마지막 단계)
            SL                    ++ (임계 시 pause)    시장 강제 (가격 도달)
            BE / REVERSE          유지                  봇 능동 청산
            FORCE_END             유지                  백테스트 강제 종료
            ====================  ====================  ==========================

        근거: SL 만 시장 강제, 그 외는 봇 능동 판단 — 본질 다름.

        잔여 청산 비율: ``size_pct × (1 − Σalloc[0..tp_hits−1] / 100)``
        (TP4 / SL after TP-N 모두 정합). 강제청산 한도:
        ``lev_pnl = max(lev_pnl, −size × leverage)`` (replay L985 차용 — 시드
        마이너스 방지).

        Args:
            fill: 슬리피지 미적용 체결가 (gap-fill 결정된 raw 가격).
            ts_ms: 청산 시각 (ms epoch).
            reason: 청산 사유 — D-25 매핑 7 개 중 하나
                (``"SL"`` / ``"TP4"`` / ``"BE"`` / ``"REVERSE"`` / ``"FORCE_END"``).

        Returns:
            생성된 ``TradeRecord`` (engine.trades 에도 append).

        Raises:
            RuntimeError: 보유 포지션 없음 (``_check_exits`` / ``_force_close_at_end``
                호출 가드 누락 신호).
        """
        if self.position is None:
            raise RuntimeError("청산할 포지션 없음 — _close 호출 가드 누락")
        # __init__ 시점 산출 보장 — None 가드 불필요 (CLAUDE.md trust internal guarantees)
        cfg = self._risk_config

        p = self.position
        plan = p.plan

        # 슬리피지 (exit) — 항상 불리한 방향
        slip = slip_pct(self._last_high, self._last_low, self._last_close)
        exit_price = apply_slippage(fill, plan.direction, side="exit", slip=slip)

        # raw pnl (방향 부호)
        sign = 1.0 if plan.direction == "long" else -1.0
        raw_pnl_pct = (exit_price - plan.entry_price) / plan.entry_price * sign

        # 잔여 size_pct (TP4 / SL after TP-N 모두 정합)
        consumed = sum(cfg.tp_allocations[: p.tp_hits]) / 100.0
        remaining_size_pct = p.size_pct * (1.0 - consumed)

        # cost 적용 + 강제청산 한도 (replay L985)
        lev_pnl, _fee_loss = apply_costs(raw_pnl_pct, remaining_size_pct, plan.leverage)
        lev_pnl = max(lev_pnl, -remaining_size_pct * plan.leverage)
        self.balance *= 1.0 + lev_pnl
        self._update_peak()
        self._check_max_dd()

        # consec_sl 분기 (D-2 매핑 표)
        if reason == "SL":
            self.consec_sl += 1
            if self.consec_sl >= self.config.consec_sl_pause_threshold:
                self.pause_bars = self.config.consec_sl_pause_minutes
                self.consec_sl = 0
        elif reason == "TP4":
            self.consec_sl = 0
        # BE / REVERSE / FORCE_END: 카운트 유지 (D-2)

        # TradeRecord 생성
        sl_distance = abs(plan.entry_price - plan.sl_price)
        if sl_distance == 0:
            # Why: silent fallback X — 추후 통계에서 r_multiple=0 trade 식별 +
            # RiskPlan 산출 단계 역추적 단서 (장수 권장).
            logger.warning(
                "r_multiple 산출 시 sl_distance=0 — RiskPlan 점검 필요 (entry_ts=%d)",
                p.entry_ts,
            )
            r_multiple = 0.0
        else:
            r_multiple = (exit_price - plan.entry_price) / sl_distance * sign

        duration_min = max(0, (ts_ms - p.entry_ts) // 60_000)
        trade = TradeRecord(
            entry_price=plan.entry_price,
            entry_ts=p.entry_ts,
            exit_price=exit_price,
            exit_ts=ts_ms,
            direction=plan.direction,
            leverage=float(plan.leverage),
            pnl=lev_pnl,
            r_multiple=r_multiple,
            duration_minutes=int(duration_min),
            regime=str(p.regime),
        )
        self.trades.append(trade)
        self.position = None
        return trade

    def _partial_close(
        self, idx: int, fill: float, ts_ms: int,
    ) -> TradeRecord:
        """TP n 단계 분할 익절 — ``alloc[idx]`` 비율만 청산 + ``tp_hits`` ++ +
        ``update_trailing_sl`` 위임.

        idx 매핑: ``0`` = TP1, ``1`` = TP2, ``2`` = TP3. ``idx == 3`` (TP4 = full
        close) 는 ``_close(reason="TP4")`` 책임 (호출자 ``_check_exits`` 분기).

        consec_sl reset (TP1+ 도달 = D-2 익절 카테고리). 트레일링 갱신은 단방향
        보장 (``risk.update_trailing_sl`` 내부).

        Args:
            idx: TP 단계 인덱스 (0~2).
            fill: 슬리피지 미적용 체결가.
            ts_ms: 청산 시각 (ms epoch).

        Returns:
            ``TradeRecord`` (engine.trades 에도 append).

        Raises:
            ValueError: ``idx >= 3`` (TP4 는 ``_close`` 책임 — 호출자 분기 누락).
            RuntimeError: 보유 포지션 없음 (``_check_exits`` 호출 가드 누락 신호).
        """
        if self.position is None:
            raise RuntimeError("부분 청산할 포지션 없음 — _partial_close 호출 가드 누락")
        if idx >= 3:
            raise ValueError(
                f"_partial_close idx={idx} 잘못 — TP4 (idx=3) 는 "
                f"_close(reason='TP4') 책임",
            )
        # __init__ 시점 산출 보장 — None 가드 불필요 (CLAUDE.md trust internal guarantees)
        cfg = self._risk_config
        p = self.position
        plan = p.plan

        slip = slip_pct(self._last_high, self._last_low, self._last_close)
        exit_price = apply_slippage(fill, plan.direction, side="exit", slip=slip)

        sign = 1.0 if plan.direction == "long" else -1.0
        raw_pnl_pct = (exit_price - plan.entry_price) / plan.entry_price * sign

        # chunk size_pct (이번 단계만)
        alloc_pct = cfg.tp_allocations[idx] / 100.0
        chunk_size_pct = p.size_pct * alloc_pct

        lev_pnl, _fee_loss = apply_costs(raw_pnl_pct, chunk_size_pct, plan.leverage)
        lev_pnl = max(lev_pnl, -chunk_size_pct * plan.leverage)   # 강제청산 한도
        self.balance *= 1.0 + lev_pnl
        self._update_peak()

        # tp_hits ++ + consec_sl reset (D-2 익절 카테고리)
        p.tp_hits += 1
        self.consec_sl = 0

        # 트레일링 갱신 위임 — 단방향 보장은 risk.py 내부
        p.current_sl = update_trailing_sl(
            current_sl=p.current_sl,
            plan=plan,
            config=cfg,
            tp_hits=p.tp_hits,
            highest_since_entry=p.highest_since_entry,
            lowest_since_entry=p.lowest_since_entry,
        )

        # TradeRecord (partial — 청산된 chunk 만 기록)
        sl_distance = abs(plan.entry_price - plan.sl_price)
        if sl_distance == 0:
            logger.warning(
                "r_multiple 산출 시 sl_distance=0 — RiskPlan 점검 필요 (entry_ts=%d)",
                p.entry_ts,
            )
            r_multiple = 0.0
        else:
            r_multiple = (exit_price - plan.entry_price) / sl_distance * sign

        duration_min = max(0, (ts_ms - p.entry_ts) // 60_000)
        trade = TradeRecord(
            entry_price=plan.entry_price,
            entry_ts=p.entry_ts,
            exit_price=exit_price,
            exit_ts=ts_ms,
            direction=plan.direction,
            leverage=float(plan.leverage),
            pnl=lev_pnl,
            r_multiple=r_multiple,
            duration_minutes=int(duration_min),
            regime=str(p.regime),
        )
        self.trades.append(trade)
        return trade

    def _check_exits(
        self,
        ts_ms: int,
        open_: float,
        high: float,
        low: float,
    ) -> TradeRecord | None:
        """매 1 분봉 SL/TP 도달 검사 — gap-fill 적용 + 4 단계 분할 일반화.

        우선순위: SL > TP (replay L437 ``# SL 우선 (보수적): low가 SL 통과``
        그대로 차용). gap-fill: open 이 레벨 통과 시 더 불리한 가격에 체결
        (replay L443-445 / L451-453 / L460-462 패턴 차용).

        한 봉 1 청산 한계 (replay 패턴 그대로): 한 봉에 high 가 TP1+TP2+TP3
        모두 도달 시에도 첫 hit (TP1) 만 처리 + 다음 봉 이월. for 루프 무 +
        early return — 슬립/cost 복잡도 회피. 백테스트는 보수적 (TP 들이 늦게
        산출됨).

        BE 판정: ``tp_hits >= 1 + SL 도달`` → reason ``"BE"`` (D-2 카테고리 정합 —
        봇 능동 청산 카운트 유지). trailing OFF 라도 동일 분류 (replay 컨벤션).

        Args:
            ts_ms: 현재 1 분봉 ms epoch.
            open_: 1 분봉 open.
            high: 1 분봉 high.
            low: 1 분봉 low.

        Note:
            close 는 매개변수에서 제외 — slip 산출은 ``self._last_close``
            (``step()`` 진입점 L283-285 갱신) 사용. 장수+WooJae 합의 2026-05-04.

        Returns:
            청산 발생 시 ``TradeRecord``, 미청산이면 ``None``.
        """
        if self.position is None:
            return None
        p = self.position
        plan = p.plan

        if plan.direction == "long":
            # SL 우선 (보수적): low 가 SL 통과
            if low <= p.current_sl:
                fill = (
                    min(open_, p.current_sl)
                    if open_ <= p.current_sl
                    else p.current_sl
                )
                reason = "BE" if p.tp_hits >= 1 else "SL"
                return self._close(fill=fill, ts_ms=ts_ms, reason=reason)
            # TP 단계 — 미달 단계부터 순차 (한 봉 1 청산)
            for idx in range(p.tp_hits, 4):
                tp_price = plan.tp_prices[idx]
                if high >= tp_price:
                    fill = max(open_, tp_price) if open_ >= tp_price else tp_price
                    if idx == 3:
                        return self._close(fill=fill, ts_ms=ts_ms, reason="TP4")
                    return self._partial_close(idx=idx, fill=fill, ts_ms=ts_ms)
            return None

        # SHORT — 부등호 반대
        if high >= p.current_sl:
            fill = (
                max(open_, p.current_sl)
                if open_ >= p.current_sl
                else p.current_sl
            )
            reason = "BE" if p.tp_hits >= 1 else "SL"
            return self._close(fill=fill, ts_ms=ts_ms, reason=reason)
        for idx in range(p.tp_hits, 4):
            tp_price = plan.tp_prices[idx]
            if low <= tp_price:
                fill = min(open_, tp_price) if open_ <= tp_price else tp_price
                if idx == 3:
                    return self._close(fill=fill, ts_ms=ts_ms, reason="TP4")
                return self._partial_close(idx=idx, fill=fill, ts_ms=ts_ms)
        return None

    def _update_peak(self) -> None:
        """``peak_balance`` 갱신 — ``_check_max_dd`` 와 한 쌍 (replay L554-560).

        ``self.balance > self.peak_balance`` 이면 갱신. MDD 가드는
        ``_check_max_dd`` 가 시드 기준으로 별도 산출.
        """
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance

    def _check_max_dd(self) -> bool:
        """시드 대비 누적 손실 ≥ ``max_dd_stop_pct`` 시 ``stopped=True`` 영구
        정지 가드 (replay L558-560 차용).

        Returns:
            정지 발동 여부 (``True`` 면 진입 게이트 차단). 한 번 ``True`` 면
            ``run`` 종료까지 영구 (보유 포지션은 정상 청산 후 신규 진입 X).
        """
        initial = self.config.initial_capital
        dd = (initial - self.balance) / initial
        # Why: 한 번 정지 후에도 매 1m 호출되므로 WARNING 중복 방지 가드.
        if dd >= self.config.max_dd_stop_pct and not self.stopped:
            logger.warning(
                "MDD %.2f%% 도달 (시드 %.2f → %.2f) — 영구 정지",
                dd * 100, initial, self.balance,
            )
            self.stopped = True
        return self.stopped

    def _tick_pause(self) -> None:
        """``pause_bars`` 카운터 1 감소 (매 1 분봉 호출, replay L347-348).

        ``pause_bars > 0`` 이면 진입 게이트 차단 (DESIGN §6.2 7 단계).
        보유 포지션 청산은 차단 X (생존 우선 정책 — pause 중에도 SL/TP 정상
        체결).
        """
        if self.pause_bars > 0:
            self.pause_bars -= 1

    def _force_close_at_end(self, last_close: float, last_ts: int) -> None:
        """``run()`` 마지막 봉 도달 시 보유 포지션 강제 청산 — reason
        ``"FORCE_END"`` (D-25 신설, adaptive L376-391 차용).

        시뮬 종료 시 미청산 포지션이 통계 누락되지 않도록 마지막 close 가격에
        시장가 청산. consec_sl 카운트 유지 (D-2 BE/REVERSE/FORCE_END 카테고리
        정합 — 봇 능동 청산).
        """
        if self.position is None:
            return
        self._close(fill=last_close, ts_ms=last_ts, reason="FORCE_END")
