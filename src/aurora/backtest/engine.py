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

from aurora.backtest.cost import Direction, apply_costs, apply_slippage, slip_pct
from aurora.backtest.stats import TradeRecord
from aurora.core.risk import RiskPlan, TpSlConfig, update_trailing_sl

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
            ``run()`` 진입 시점에 ``sl_pct_for_leverage`` /
            ``tp_pct_range_for_leverage`` 그래디언트 + D-3 등간격 분할로 자동
            산출 (Group 2 에서 채움).
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
    risk_config: TpSlConfig | None = None  # Group 2 자동 산출 분기


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
    """

    plan: RiskPlan
    entry_ts: int
    current_sl: float
    size_pct: float
    tp_hits: int = 0
    highest_since_entry: float = 0.0
    lowest_since_entry: float = 0.0


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

        Args:
            config: 시뮬 파라미터 + 가드 임계값.
        """
        self.config = config

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

    # ─────────────────────────────────────────────
    # public API — Group 2 본문 구현 자리
    # ─────────────────────────────────────────────

    def run(self, df_1m: pd.DataFrame) -> list[TradeRecord]:
        """1 분봉 ``DataFrame`` 통째 받아 시뮬 + trade 기록 반환.

        Args:
            df_1m: PR-2 산출 parquet 로드 결과. ``DatetimeIndex`` (ms epoch
                기반) + OHLCV 컬럼. ``df_1m.iterrows()`` 자연 호출 가능 형태.
                ``timestamp`` 변환은 진입점 1줄 (``ts_ms = int(bar_1m.name.value
                // 10**6)``, D-26).

        Returns:
            ``TradeRecord`` 리스트 — ``stats.compute_session_stats`` 입력.
            0 거래 세션도 빈 리스트 정상 반환.

        Raises:
            NotImplementedError: Group 1 골격 단계. Group 2 본문 구현 후 제거.
        """
        del df_1m  # Group 2 본문에서 활용 — 골격 단계 명시적 무시
        raise NotImplementedError("Group 2 — step() 10 단계 본 구현 예정")

    # ─────────────────────────────────────────────
    # 헬퍼 함수 — Group 2 본문 구현 자리 (시그니처만 박음)
    # ─────────────────────────────────────────────

    def _to_record_direction(self, direction: str) -> Direction:
        """direction 이중 표준 격리 — ``RiskPlan`` (소문자) → ``TradeRecord``
        (대문자) 변환을 1 곳에 격리 (D-19).

        ``core`` 영역 ``Direction StrEnum value="long"/"short"`` ↔ ``backtest``
        ``cost.Direction Literal["LONG","SHORT"]`` 정합. ``cost`` / ``stats``
        Stage 1B 변경 X (회귀 비용 0). 본질 정합화는 후속 PR (장수 ping 답변
        후 결정).
        """
        # Why: Literal 반환 타입 narrowing 위해 if-elif-raise 패턴.
        # `return norm` (str) 은 mypy/pyright 가 좁혀주지 않음.
        norm = direction.upper()
        if norm == "LONG":
            return "LONG"
        if norm == "SHORT":
            return "SHORT"
        raise ValueError(
            f"잘못된 direction: {direction!r} "
            f"(예상: 'long'/'short' 또는 'LONG'/'SHORT')",
        )

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
            RuntimeError: 보유 포지션 X 또는 ``risk_config`` 미설정.
        """
        if self.position is None:
            raise RuntimeError("청산할 포지션 없음 — _close 호출 가드 누락")
        cfg = self.config.risk_config
        if cfg is None:
            raise RuntimeError(
                "risk_config 미설정 — step() 진입점에서 build_risk_plan 호출 시 설정 필요",
            )

        p = self.position
        plan = p.plan

        # 슬리피지 (exit) — 항상 불리한 방향
        direction_upper = self._to_record_direction(plan.direction)
        slip = slip_pct(self._last_high, self._last_low, self._last_close)
        exit_price = apply_slippage(fill, direction_upper, side="exit", slip=slip)

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
            direction=direction_upper,
            leverage=float(plan.leverage),
            pnl=lev_pnl,
            r_multiple=r_multiple,
            duration_minutes=int(duration_min),
            regime=None,
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
            RuntimeError: ``self.position is None`` / ``risk_config is None``.
        """
        if self.position is None:
            raise RuntimeError("부분 청산할 포지션 없음 — _partial_close 호출 가드 누락")
        if idx >= 3:
            raise ValueError(
                f"_partial_close idx={idx} 잘못 — TP4 (idx=3) 는 "
                f"_close(reason='TP4') 책임",
            )
        cfg = self.config.risk_config
        if cfg is None:
            raise RuntimeError(
                "risk_config 미설정 — step() 진입점에서 build_risk_plan 호출 시 설정 필요",
            )
        p = self.position
        plan = p.plan

        direction_upper = self._to_record_direction(plan.direction)
        slip = slip_pct(self._last_high, self._last_low, self._last_close)
        exit_price = apply_slippage(fill, direction_upper, side="exit", slip=slip)

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
            direction=direction_upper,
            leverage=float(plan.leverage),
            pnl=lev_pnl,
            r_multiple=r_multiple,
            duration_minutes=int(duration_min),
            regime=None,
        )
        self.trades.append(trade)
        return trade

    def _check_exits(
        self,
        ts_ms: int,
        open_: float,
        high: float,
        low: float,
        close: float,
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
            close: 1 분봉 close (의도적으로 매개변수 — 호출자 OHLC 4 인자 일관성.
                slip 산출은 ``self._last_*`` 사용, ``step()`` 진입점에서 갱신).

        Returns:
            청산 발생 시 ``TradeRecord``, 미청산이면 ``None``.
        """
        del close   # 함수 내부 미사용 — slip 은 self._last_* 사용 (step 갱신)
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
