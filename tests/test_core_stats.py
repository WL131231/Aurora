"""aurora.core.stats — 거래 결과 통계 계산 단위 테스트 (v0.1.24).

UI 대시보드 `결과 통계` 6 카드용 stats 함수. 백테스트용 ``aurora.backtest.stats`` 와는
별개 (그쪽은 BacktestStats / TradeRecord — 본 함수와 다른 input/output).

핵심 검증:
    - 빈 입력 → 모두 0
    - win/loss 카운트 + 승률
    - cumulative pnl + 평균 ROI
    - MDD% (peak-to-trough)
    - Sharpe (per-trade ROI 기반, rf=0)
    - 평균 보유 시간 (분)
"""

from __future__ import annotations

from dataclasses import dataclass

from aurora.core.stats import TradeStats, compute_stats


@dataclass(slots=True)
class _FakeTrade:
    """compute_stats Protocol 만족 — 4 attribute 만 필요."""

    pnl_usd: float
    roi_pct: float
    opened_at_ts: int
    closed_at_ts: int


def _trade(
    pnl: float = 0.0,
    roi: float = 0.0,
    opened_ms: int = 0,
    closed_ms: int = 0,
) -> _FakeTrade:
    return _FakeTrade(pnl_usd=pnl, roi_pct=roi, opened_at_ts=opened_ms, closed_at_ts=closed_ms)


# ============================================================
# 기본 케이스
# ============================================================


def test_empty_returns_all_zeros():
    """빈 trade 리스트 → 모든 메트릭 0."""
    s = compute_stats([])
    assert s == TradeStats(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def test_single_trade_no_sharpe():
    """단일 trade — n<2 라 sharpe 0, 나머지는 정상."""
    trades = [_trade(pnl=10.0, roi=2.0, opened_ms=1000, closed_ms=61000)]  # 60초 보유
    s = compute_stats(trades)
    assert s.total_trades == 1
    assert s.win_count == 1
    assert s.loss_count == 0
    assert s.win_rate_pct == 100.0
    assert s.cumulative_pnl_usd == 10.0
    assert s.avg_roi_pct == 2.0
    assert s.sharpe_ratio == 0.0   # n<2 정의 X
    assert s.avg_hold_minutes == 1.0  # 60초 = 1분


# ============================================================
# Win / Loss 카운팅
# ============================================================


def test_win_loss_count_and_rate():
    """승률 = wins / total × 100. 0 PnL 은 어느 쪽도 X."""
    trades = [
        _trade(pnl=10.0, roi=1.0),
        _trade(pnl=-5.0, roi=-0.5),
        _trade(pnl=0.0, roi=0.0),
        _trade(pnl=20.0, roi=2.0),
    ]
    s = compute_stats(trades)
    assert s.total_trades == 4
    assert s.win_count == 2
    assert s.loss_count == 1
    assert s.win_rate_pct == 50.0   # 2/4


def test_cumulative_pnl_sum():
    trades = [_trade(pnl=10.0), _trade(pnl=-3.0), _trade(pnl=7.5)]
    s = compute_stats(trades)
    assert s.cumulative_pnl_usd == 14.5


# ============================================================
# MDD (peak-to-trough)
# ============================================================


def test_mdd_normal_drawdown():
    """누적: 10 → 30 → 20 → 25 → 15. peak=30, trough=15. DD=15. MDD% = 15/30 × 100 = 50."""
    trades = [
        _trade(pnl=10.0),
        _trade(pnl=20.0),    # 누적 30 (peak)
        _trade(pnl=-10.0),   # 누적 20
        _trade(pnl=5.0),     # 누적 25
        _trade(pnl=-10.0),   # 누적 15 (trough)
    ]
    s = compute_stats(trades)
    assert s.cumulative_pnl_usd == 15.0
    assert abs(s.max_drawdown_pct - 50.0) < 1e-6


def test_mdd_zero_when_only_losses():
    """모두 손실 — peak 0 이하 → MDD% 정의 불가 → 0."""
    trades = [_trade(pnl=-5.0), _trade(pnl=-10.0)]
    s = compute_stats(trades)
    assert s.max_drawdown_pct == 0.0


def test_mdd_zero_when_monotonic_growth():
    """누적 손실 없음 (peak 만 갱신) → MDD 0."""
    trades = [_trade(pnl=10.0), _trade(pnl=20.0), _trade(pnl=5.0)]
    s = compute_stats(trades)
    assert s.max_drawdown_pct == 0.0


# ============================================================
# Sharpe ratio
# ============================================================


def test_sharpe_zero_when_zero_variance():
    """모든 ROI 동일 → 분산 0 → sharpe 0."""
    trades = [_trade(roi=2.0), _trade(roi=2.0), _trade(roi=2.0)]
    s = compute_stats(trades)
    assert s.sharpe_ratio == 0.0


def test_sharpe_positive_when_avg_positive():
    """ROI 평균 > 0 → sharpe > 0."""
    trades = [
        _trade(roi=3.0),
        _trade(roi=1.0),
        _trade(roi=2.0),
    ]
    s = compute_stats(trades)
    assert s.sharpe_ratio > 0
    # mean=2, var=(1²+(-1)²+0²)/2=1, std=1, sharpe=2/1=2
    assert abs(s.sharpe_ratio - 2.0) < 1e-6


# ============================================================
# 평균 보유
# ============================================================


def test_avg_hold_minutes():
    """ms 차이 → 분 변환."""
    trades = [
        _trade(opened_ms=0, closed_ms=60_000),         # 1분
        _trade(opened_ms=0, closed_ms=120_000),        # 2분
        _trade(opened_ms=0, closed_ms=180_000),        # 3분
    ]
    s = compute_stats(trades)
    assert s.avg_hold_minutes == 2.0
