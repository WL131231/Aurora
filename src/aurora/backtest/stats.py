"""백테스트 통계 — 승률, MDD, Sharpe, 손익비 등.

담당: 팀원 C
"""

from __future__ import annotations

from dataclasses import dataclass

from aurora.backtest.engine import TradeRecord


@dataclass(slots=True)
class BacktestStats:
    """백테스트 요약."""

    total_trades: int
    win_rate: float
    avg_pnl_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    profit_factor: float
    total_pnl_usd: float
    avg_hold_minutes: float

    long_count: int
    short_count: int
    long_win_rate: float
    short_win_rate: float


def compute_stats(trades: list[TradeRecord]) -> BacktestStats:
    """거래 기록 → 통계."""
    # TODO(C)
    raise NotImplementedError


def to_summary_text(stats: BacktestStats) -> str:
    """통계를 사람이 읽기 좋은 텍스트로 (Telegram 리포트용)."""
    # TODO(C)
    raise NotImplementedError
