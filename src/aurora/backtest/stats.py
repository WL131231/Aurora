"""백테스트 통계 — 거래 기록·요약·R-multiple·drawdown 산출.

PR-2 산출 OHLCV 와 BacktestEngine 시뮬 결과를 받아 통계 dataclass 산출 +
R-multiple / drawdown / 실효 R 비율 (장수 §11 D-1) 헬퍼 제공. cost.py 와
함께 백테스트 손익 정합성 + 결과 평가의 한 쌍.

상세 spec: ``src/aurora/backtest/DESIGN.md`` §5.2 + §11 D-1.

담당: ChoYoon
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from aurora.backtest.cost import Direction
from aurora.core.risk import RiskPlan

# ============================================================
# 거래 기록 — 단일 trade 결과 + R-multiple 평가 단위
# ============================================================


@dataclass(slots=True)
class TradeRecord:
    """단일 거래 기록 — 진입·청산·손익·R-multiple·시장 국면 metadata.

    timestamp 는 ms epoch (int64) — PR-2 parquet 산출물 정합 (DESIGN.md §2).
    R-multiple 은 ``(exit - entry) / sl_distance × 방향`` 부호 (compute_r_multiples 산출).

    Attributes:
        entry_price: 슬리피지 반영 진입가.
        entry_ts: 진입 시각 (ms epoch).
        exit_price: 슬리피지 반영 청산가.
        exit_ts: 청산 시각 (ms epoch).
        direction: 포지션 방향 (cost.py ``Literal["long","short"]`` 재사용 —
            ``core.strategy.Direction`` StrEnum value 자연 통과).
        leverage: 레버리지 (Aurora 정책 10~50x).
        pnl: 레버리지·수수료 반영 net PnL 비율 (시드 대비, ``cost.apply_costs`` 산출 lev_pnl).
        r_multiple: R-multiple — sl_distance 단위 손익 평가.
        duration_minutes: 보유 시간 (분).
        regime: 시장 국면 metadata (예: ``"TREND_UP"`` / ``"RANGE"``). 없으면 ``None``.
    """

    entry_price: float
    entry_ts: int
    exit_price: float
    exit_ts: int
    direction: Direction
    leverage: float
    pnl: float
    r_multiple: float
    duration_minutes: int
    regime: str | None = None


# ============================================================
# 백테스트 요약 — 세션 단위 통계 + 실효 R 비율 (D-1)
# ============================================================


@dataclass(slots=True)
class BacktestStats:
    """백테스트 세션 단위 요약 통계.

    DESIGN.md §5.2 spec — total_trades / win_rate / mdd / sharpe / expectancy /
    equity_curve / total_pnl / fee_paid. ``effective_r_pct`` 는 §11 D-1 (장수 동의)
    실효 R 비율 선택 필드 — Stage 1B 단순화 단계에서 None default, 후속 PR 에서 산출.

    Attributes:
        total_trades: 총 거래 수.
        win_rate: 승률 (0.0~1.0).
        mdd: Maximum Drawdown (peak-to-trough max %, 양수 표기).
        sharpe: Sharpe ratio (annualized).
        expectancy: 평균 R-multiple (1 거래당 기대 R).
        equity_curve: 시드 대비 누적 잔고 곡선 (0 거래 세션은 빈 list).
        total_pnl: 총 net PnL 비율.
        fee_paid: 누적 수수료 손실 비율.
        effective_r_pct: 실효 R 비율 (D-1) — None 이면 미산출. 산출 시 ``list[float]``.
    """

    total_trades: int
    win_rate: float
    mdd: float
    sharpe: float
    expectancy: float
    equity_curve: list[float]
    total_pnl: float
    fee_paid: float
    effective_r_pct: list[float] | None = None


# 모듈 logger — 0 거래 세션 등 비치명 경고용 (D-11 패턴)
logger = logging.getLogger(__name__)


# ============================================================
# 공개 함수 — 세션 통계 산출
# ============================================================


def compute_session_stats(trades: list[TradeRecord]) -> BacktestStats:
    """거래 기록 → 세션 단위 통계 산출.

    DESIGN.md §5.2 spec — win_rate / mdd / sharpe / expectancy / equity_curve /
    total_pnl / fee_paid 산출. ``effective_r_pct`` 는 §11 D-1 단순화 단계로 None.

    공식:

    - ``win_rate``     = 양수 PnL 거래 / 전체 (0.0~1.0)
    - ``mdd``          = ``compute_drawdown(equity_curve)``
    - ``sharpe``       = ``mean(pnl) / std(pnl)`` raw — N<2 또는 std=0 시 0.0
                         (annualization 후속 PR — Stage 1B 단순화)
    - ``expectancy``   = ``mean(r_multiple)``
    - ``equity_curve`` = 시드 1.0 시작 복리 누적 (``curve[i] = curve[i-1] × (1+pnl_i)``)
    - ``total_pnl``    = ``curve[-1] - 1.0``
    - ``fee_paid``     = 0.0 placeholder (Stage 1B 단순화 — Stage 1C BacktestEngine
                         통합 시 채움)

    Args:
        trades: 단일 세션 내 trade 기록 리스트.

    Returns:
        ``BacktestStats`` 인스턴스.
        0 거래 세션 (``trades=[]``) 은 모든 필드 0.0 / 빈 list 로 반환 + WARNING 로그.
    """
    if not trades:
        logger.warning("0 거래 세션 — BacktestStats 빈 값 반환")
        return BacktestStats(
            total_trades=0, win_rate=0.0, mdd=0.0, sharpe=0.0, expectancy=0.0,
            equity_curve=[], total_pnl=0.0, fee_paid=0.0,
        )

    n = len(trades)
    pnls = [t.pnl for t in trades]
    r_mults = [t.r_multiple for t in trades]

    # equity curve — 시드 1.0 시작 복리 누적
    equity_curve: list[float] = [1.0]
    for pnl in pnls:
        equity_curve.append(equity_curve[-1] * (1 + pnl))

    wins = sum(1 for p in pnls if p > 0)
    win_rate = wins / n

    # Sharpe — N<2 시 std 산출 불가 → 0.0 fallback
    mean_pnl = sum(pnls) / n
    if n < 2:
        sharpe = 0.0
    else:
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (n - 1)
        std_pnl = variance ** 0.5
        sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0.0

    return BacktestStats(
        total_trades=n,
        win_rate=win_rate,
        mdd=compute_drawdown(equity_curve),
        sharpe=sharpe,
        expectancy=sum(r_mults) / n,
        equity_curve=equity_curve,
        total_pnl=equity_curve[-1] - 1.0,
        fee_paid=0.0,             # Stage 1B 단순화 — Stage 1C 에서 채움
        effective_r_pct=None,     # D-1 단순화 — 후속 PR 산출
    )


# ============================================================
# 공개 함수 — R-multiple 산출 (RiskPlan 결합)
# ============================================================


def compute_r_multiples(
    trades: list[TradeRecord], plans: list[RiskPlan],
) -> list[float]:
    """trade 별 R-multiple 산출 — sl_distance 단위 손익 평가.

    공식:

        ``r_multiple = (exit_price - entry_price) / sl_distance × sign``

    여기서 ``sign`` 은 long +1 / short -1, ``sl_distance = |entry - sl_price|``
    (RiskPlan 에서 인라인 계산 — RiskPlan 에 sl_distance 필드 X).

    Args:
        trades: trade 기록 리스트.
        plans: 각 trade 에 대응하는 RiskPlan (인덱스 일치 + 길이 일치 필수).

    Returns:
        R-multiple 리스트 (``len(trades)`` 와 동일).

    Raises:
        ValueError: ``len(trades) != len(plans)`` 또는 ``sl_distance == 0``
            (silent 전파 차단 — RiskPlan 산출 버그 즉시 발견).
    """
    if len(trades) != len(plans):
        raise ValueError(
            f"trades 와 plans 길이 불일치: trades={len(trades)}, plans={len(plans)}",
        )
    out: list[float] = []
    for trade, plan in zip(trades, plans, strict=True):
        sl_distance = abs(plan.entry_price - plan.sl_price)
        if sl_distance == 0:
            raise ValueError(
                f"sl_distance == 0 (entry={plan.entry_price}, sl={plan.sl_price}) — "
                f"RiskPlan 산출 검토 필요",
            )
        # Why: long 은 +1, short 은 -1 부호 (방향 반전 시 R-multiple 부호 반전)
        sign = 1.0 if trade.direction == "long" else -1.0
        out.append((trade.exit_price - trade.entry_price) / sl_distance * sign)
    return out


# ============================================================
# 공개 함수 — Maximum Drawdown
# ============================================================


def compute_drawdown(equity_curve: list[float]) -> float:
    """Maximum Drawdown — peak-to-trough 최대 낙폭 (양수 비율 표기).

    공식 (차용: replay_engine L1021-1028):

        ``peak = curve[0]``
        ``for v in curve: peak = max(peak, v); dd = (peak - v) / peak; mdd = max(mdd, dd)``

    Args:
        equity_curve: 시드 정규화 누적 잔고 곡선.

    Returns:
        MDD (양수, 0.0 이상 — 정상 시뮬은 0.0~1.0, 음수 시드 도달 시 1.0 초과 가능 (비정상 신호)).
        빈 곡선은 ``0.0`` 반환.
        ``peak <= 0`` 시점은 dd 산출 0.0 (음수 시드/전손 방어).
    """
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    mdd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        # peak <= 0 (음수 시드 / 전손) 시 ZeroDivisionError 방어
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > mdd:
            mdd = dd
    return mdd
