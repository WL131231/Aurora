"""Phase 3 Dashboard Ratios 단위 테스트 (v0.1.115).

5단 L/S segment derivation 측 ``DashboardFlow`` snapshot → ratios 변환 verify.
"""

from __future__ import annotations

import pytest

from aurora.market.dashboard_flow import DashboardFlow
from aurora.market.exchanges.base import ExchangeSnapshot
from aurora.market.ratios_aggregator import compute_ratios


def _flow_with(snaps: list[ExchangeSnapshot]) -> DashboardFlow:
    """ExchangeSnapshot list → DashboardFlow (합산 박힘)."""
    return DashboardFlow.from_snapshots("BTC", snaps)


# ============================================================
# 5단 segments — happy path
# ============================================================


def test_compute_ratios_returns_5_segments_in_order() -> None:
    """label 측 5개, 순서 fixed."""
    flow = _flow_with([
        ExchangeSnapshot(exchange="x", symbol="X", fetched_at_ms=0),
    ])
    ratios = compute_ratios(flow)
    assert len(ratios.segments) == 5
    labels = [s.label for s in ratios.segments]
    assert labels == [
        "WHALE NOTIONAL", "WHALE ACCOUNTS",
        "TOP NOTIONAL", "TOP ACCOUNTS", "GLOBAL ACCOUNTS",
    ]


def test_compute_ratios_global_long_pct_from_ratio() -> None:
    """global L/S ratio 1.5 → long_pct = 0.6 (= 1.5 / 2.5)."""
    snaps = [
        ExchangeSnapshot(
            exchange="binance", symbol="BTCUSDT", fetched_at_ms=0,
            oi_usd=1_000_000.0, ls_ratio_global=1.5,
        ),
    ]
    flow = _flow_with(snaps)
    ratios = compute_ratios(flow)
    global_seg = next(s for s in ratios.segments if s.label == "GLOBAL ACCOUNTS")
    assert global_seg.long_pct == pytest.approx(0.6)
    assert global_seg.short_pct == pytest.approx(0.4)
    assert "binance" in global_seg.source_exchanges


def test_compute_ratios_top_position_long_pct() -> None:
    """top position ratio 0.85 → long_pct = 0.459 (= 0.85 / 1.85)."""
    snaps = [
        ExchangeSnapshot(
            exchange="binance", symbol="BTCUSDT", fetched_at_ms=0,
            oi_usd=1_000_000.0, ls_ratio_top_position=0.85,
        ),
    ]
    flow = _flow_with(snaps)
    ratios = compute_ratios(flow)
    top_seg = next(s for s in ratios.segments if s.label == "TOP NOTIONAL")
    assert top_seg.long_pct == pytest.approx(0.85 / 1.85)


def test_compute_ratios_whale_notional_from_buy_sell_sum() -> None:
    """whale_buy=600K + sell=400K → long_pct = 0.6."""
    snaps = [
        ExchangeSnapshot(
            exchange="binance", symbol="BTCUSDT", fetched_at_ms=0,
            whale_buy_5m_usd=600_000.0,
            whale_sell_5m_usd=400_000.0,
            whale_count_5m=10,
        ),
    ]
    flow = _flow_with(snaps)
    ratios = compute_ratios(flow)
    whale_seg = next(s for s in ratios.segments if s.label == "WHALE NOTIONAL")
    assert whale_seg.long_pct == pytest.approx(0.6)
    assert whale_seg.sample_size == 10
    assert "binance" in whale_seg.source_exchanges


# ============================================================
# None / 미지원 거래소
# ============================================================


def test_compute_ratios_none_when_no_data() -> None:
    """avg_ls_ratio_top_position 측 None → segment.long_pct None."""
    snaps = [
        ExchangeSnapshot(exchange="x", symbol="X", fetched_at_ms=0),
    ]
    flow = _flow_with(snaps)
    ratios = compute_ratios(flow)
    top_seg = next(s for s in ratios.segments if s.label == "TOP NOTIONAL")
    assert top_seg.long_pct is None
    assert top_seg.short_pct is None
    assert top_seg.source_exchanges == []


def test_compute_ratios_zero_ratio_returns_none() -> None:
    """ratio=0 (남자 사람 X) → long_pct None (divide-by-zero 방지)."""
    snaps = [
        ExchangeSnapshot(
            exchange="x", symbol="X", fetched_at_ms=0,
            oi_usd=1_000_000.0, ls_ratio_global=0.0,
        ),
    ]
    flow = _flow_with(snaps)
    ratios = compute_ratios(flow)
    global_seg = next(s for s in ratios.segments if s.label == "GLOBAL ACCOUNTS")
    assert global_seg.long_pct is None


def test_compute_ratios_source_exchanges_only_with_data() -> None:
    """ls_ratio_global 측 박힌 거래소 만 source 박힘."""
    snaps = [
        ExchangeSnapshot(
            exchange="binance", symbol="X", fetched_at_ms=0,
            oi_usd=1_000_000.0, ls_ratio_global=1.5,
        ),
        ExchangeSnapshot(
            exchange="hyperliquid", symbol="X", fetched_at_ms=0,
            oi_usd=500_000.0, ls_ratio_global=None,  # HL 측 미지원
        ),
    ]
    flow = _flow_with(snaps)
    ratios = compute_ratios(flow)
    global_seg = next(s for s in ratios.segments if s.label == "GLOBAL ACCOUNTS")
    assert global_seg.source_exchanges == ["binance"]
