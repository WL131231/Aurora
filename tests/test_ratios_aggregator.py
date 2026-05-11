"""market.ratios_aggregator — compute_ratios / _ratio_to_long_pct 단위 테스트."""

from __future__ import annotations

from aurora.market.dashboard_flow import DashboardFlow
from aurora.market.ratios_aggregator import (
    _ratio_to_long_pct,
    compute_ratios,
)


# ============================================================
# _ratio_to_long_pct
# ============================================================


def test_ratio_to_long_pct_none_returns_none() -> None:
    assert _ratio_to_long_pct(None) is None


def test_ratio_to_long_pct_zero_or_negative_returns_none() -> None:
    assert _ratio_to_long_pct(0.0) is None
    assert _ratio_to_long_pct(-1.0) is None


def test_ratio_to_long_pct_one_returns_half() -> None:
    """ratio=1 → long_pct = 0.5 (롱=숏 동률)."""
    result = _ratio_to_long_pct(1.0)
    assert result is not None
    assert abs(result - 0.5) < 1e-9


def test_ratio_to_long_pct_formula() -> None:
    """ratio=3 → 3/(1+3) = 0.75."""
    result = _ratio_to_long_pct(3.0)
    assert result is not None
    assert abs(result - 0.75) < 1e-9


# ============================================================
# compute_ratios — 5단 segment 생성
# ============================================================


def _empty_flow(coin: str = "BTC") -> DashboardFlow:
    return DashboardFlow(coin=coin, fetched_at_ms=0)


def test_compute_ratios_returns_five_segments() -> None:
    """항상 5단 segment 반환."""
    ratios = compute_ratios(_empty_flow())
    assert len(ratios.segments) == 5


def test_compute_ratios_segment_labels() -> None:
    """segment 레이블 순서 — WHALE NOTIONAL → GLOBAL ACCOUNTS."""
    ratios = compute_ratios(_empty_flow())
    labels = [s.label for s in ratios.segments]
    assert labels == [
        "WHALE NOTIONAL",
        "WHALE ACCOUNTS",
        "TOP NOTIONAL",
        "TOP ACCOUNTS",
        "GLOBAL ACCOUNTS",
    ]


def test_compute_ratios_all_none_when_no_data() -> None:
    """데이터 없는 flow → 모든 segment long_pct = None."""
    ratios = compute_ratios(_empty_flow())
    for seg in ratios.segments:
        assert seg.long_pct is None
        assert seg.short_pct is None


def test_compute_ratios_whale_notional_long_pct() -> None:
    """whale buy=700k, sell=300k → long_pct=0.7."""
    flow = DashboardFlow(
        coin="BTC",
        fetched_at_ms=0,
        total_whale_buy_5m_usd=700_000.0,
        total_whale_sell_5m_usd=300_000.0,
    )
    ratios = compute_ratios(flow)
    whale = next(s for s in ratios.segments if s.label == "WHALE NOTIONAL")
    assert whale.long_pct is not None
    assert abs(whale.long_pct - 0.7) < 1e-9
    assert abs(whale.short_pct - 0.3) < 1e-9


def test_compute_ratios_top_notional_from_avg_ratio() -> None:
    """avg_ls_ratio_top_position=3 → TOP NOTIONAL long_pct=0.75."""
    flow = DashboardFlow(
        coin="BTC",
        fetched_at_ms=0,
        avg_ls_ratio_top_position=3.0,
    )
    ratios = compute_ratios(flow)
    top = next(s for s in ratios.segments if s.label == "TOP NOTIONAL")
    assert top.long_pct is not None
    assert abs(top.long_pct - 0.75) < 1e-9


def test_compute_ratios_coin_preserved_and_ts_is_current() -> None:
    """coin 보존 + fetched_at_ms 는 현재 시각 (time.time() 기반)."""
    import time
    before = int(time.time() * 1000)
    flow = DashboardFlow(coin="ETH", fetched_at_ms=0)
    ratios = compute_ratios(flow)
    after = int(time.time() * 1000)
    assert ratios.coin == "ETH"
    assert before <= ratios.fetched_at_ms <= after
