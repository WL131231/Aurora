"""거래 결과 통계 계산 — 청산된 trade 리스트 → 6개 핵심 메트릭 (v0.1.22).

UI 대시보드 `결과 통계` 6 카드 (총 거래 / 승률 / 누적 수익률 / 최대 DD / 샤프 / 평균 보유)
와 `/stats` API 응답을 위한 순수 계산 함수.

- in-memory deque (`BotInstance._closed_trades`) 기반 — 영속화는 v0.1.23 별도.
- 확장: backtest 엔진도 같은 함수 사용 가능 (trade 객체만 호환되면 OK).

설계 노트:
    - **MDD%**: cumulative pnl 곡선의 peak-to-trough. peak 가 0 이하면 "% 정의 불가"
      → 0 으로 fallback (UI 에서 "—" 표시 결정).
    - **샤프**: per-trade ROI% 기준 단순 (rf=0). 시간 가중 X — 거래 빈도 일정 가정 Phase 1.
      n<2 면 std 정의 안 됨 → 0.
    - **평균 보유**: ms → 분.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol


class _TradeLike(Protocol):
    """``compute_stats`` 가 요구하는 최소 attribute 셋.

    ``ClosedTrade`` (봇 자기 거래) 와 ``ClosedPosition`` (거래소 history) 모두 호환.
    duck-typing 정합 — 두 source 합쳐서 한 통계 산출 가능.
    """

    pnl_usd: float
    roi_pct: float
    opened_at_ts: int
    closed_at_ts: int


@dataclass(slots=True, frozen=True)
class TradeStats:
    """6 카드 + 보조 (win/loss count, cumulative pnl) 메트릭 묶음."""

    total_trades: int
    win_count: int
    loss_count: int
    win_rate_pct: float            # 0 ~ 100
    cumulative_pnl_usd: float      # 누적 PnL (USDT)
    avg_roi_pct: float             # 거래당 평균 ROI%
    max_drawdown_pct: float        # 최대 DD% (0 ~ 100, 양수)
    sharpe_ratio: float            # per-trade Sharpe (rf=0)
    avg_hold_minutes: float        # 평균 보유 (분)


def compute_stats(trades: Iterable[_TradeLike]) -> TradeStats:
    """청산 trade 리스트 → ``TradeStats``.

    빈 리스트는 모두 0 으로 채운 record 반환 (UI 에서 "—" 처리).
    """
    items = list(trades)
    n = len(items)
    if n == 0:
        return TradeStats(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    pnls = [t.pnl_usd for t in items]
    rois = [t.roi_pct for t in items]
    holds_min = [(t.closed_at_ts - t.opened_at_ts) / 60_000.0 for t in items]

    win_count = sum(1 for p in pnls if p > 0)
    loss_count = sum(1 for p in pnls if p < 0)
    win_rate_pct = (win_count / n) * 100.0

    cumulative_pnl_usd = sum(pnls)
    avg_roi_pct = sum(rois) / n

    # MDD% — cumulative pnl 곡선 peak-to-trough.
    # peak 0 이하 (계속 손실 중) 면 % 정의 불가 → 0.
    cum = 0.0
    peak = 0.0
    max_dd_usd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = peak - cum
        max_dd_usd = max(max_dd_usd, dd)
    max_drawdown_pct = (max_dd_usd / peak * 100.0) if peak > 0 else 0.0

    # Sharpe — per-trade ROI%, rf=0. n<2 면 std 정의 X → 0.
    if n >= 2:
        mean_roi = avg_roi_pct
        var_roi = sum((r - mean_roi) ** 2 for r in rois) / (n - 1)
        std_roi = var_roi ** 0.5
        sharpe = (mean_roi / std_roi) if std_roi > 0 else 0.0
    else:
        sharpe = 0.0

    avg_hold_minutes = sum(holds_min) / n

    return TradeStats(
        total_trades=n,
        win_count=win_count,
        loss_count=loss_count,
        win_rate_pct=win_rate_pct,
        cumulative_pnl_usd=cumulative_pnl_usd,
        avg_roi_pct=avg_roi_pct,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=sharpe,
        avg_hold_minutes=avg_hold_minutes,
    )
