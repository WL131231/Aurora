"""Phase 3 Dashboard Ratios — 5단 L/S Ratio 합본 (v0.1.115).

기존 ``DashboardFlow`` (snapshot 60초 cache) 측 derivation — 별도 fetch X.

5 segments:
1. WHALE NOTIONAL — 5분 윈도우 큰 거래 측 buy/sell USD 비중 (Binance 만)
2. WHALE ACCOUNTS — 5분 윈도우 큰 거래 buy/sell 개수 비중 (Binance 만)
3. TOP NOTIONAL   — top trader position 비율 → long_pct (Binance/OKX 합본 가중 평균)
4. TOP ACCOUNTS   — top trader account 비율 → long_pct (Binance 만)
5. GLOBAL ACCOUNTS — global account 비율 → long_pct (4 거래소 OI 가중 평균)

ratio → long_pct 환산: ``long_pct = ratio / (1 + ratio)`` (ratio = long/short).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from aurora.market.dashboard_flow import DashboardFlow


@dataclass(slots=True)
class LSRSegment:
    """5단 L/S ratio 1개."""

    label: str                                    # "WHALE NOTIONAL" 등
    long_pct: float | None                        # 0..1
    short_pct: float | None                       # 0..1 = 1 - long_pct
    source_exchanges: list[str] = field(default_factory=list)
    sample_size: int | None = None                # whale 측 거래 개수, accounts 측 None


@dataclass(slots=True)
class DashboardRatios:
    """coin 측 5단 L/S segment."""

    coin: str
    fetched_at_ms: int
    segments: list[LSRSegment]


def _ratio_to_long_pct(ratio: float | None) -> float | None:
    """L/S ratio → long_pct 환산. None / <=0 → None."""
    if ratio is None or ratio <= 0:
        return None
    return ratio / (1.0 + ratio)


def compute_ratios(flow: DashboardFlow) -> DashboardRatios:
    """``DashboardFlow`` snapshot → 5단 segment derivation."""
    segments: list[LSRSegment] = []

    # 1. WHALE NOTIONAL
    whale_buy = flow.total_whale_buy_5m_usd
    whale_sell = flow.total_whale_sell_5m_usd
    whale_buy_total = (whale_buy or 0) + (whale_sell or 0)
    whale_notional_long = (
        (whale_buy / whale_buy_total) if whale_buy_total > 0 and whale_buy is not None
        else None
    )
    whale_sources = [
        s.exchange for s in flow.snapshots
        if s.whale_buy_5m_usd is not None or s.whale_sell_5m_usd is not None
    ]
    segments.append(LSRSegment(
        label="WHALE NOTIONAL",
        long_pct=whale_notional_long,
        short_pct=(1.0 - whale_notional_long) if whale_notional_long is not None else None,
        source_exchanges=whale_sources,
        sample_size=flow.total_whale_count_5m,
    ))

    # 2. WHALE ACCOUNTS — v0.1.115 측 buy/sell 분리 X (`whale_count_5m` 측 합)
    # placeholder — long_pct 측 NOTIONAL 측 동일 박힘 (실제 분리 시 추후 fix)
    segments.append(LSRSegment(
        label="WHALE ACCOUNTS",
        long_pct=whale_notional_long,
        short_pct=(1.0 - whale_notional_long) if whale_notional_long is not None else None,
        source_exchanges=whale_sources,
        sample_size=flow.total_whale_count_5m,
    ))

    # 3. TOP NOTIONAL — top trader position 측 ratio
    top_pos_long = _ratio_to_long_pct(flow.avg_ls_ratio_top_position)
    top_pos_sources = [
        s.exchange for s in flow.snapshots
        if s.ls_ratio_top_position is not None
    ]
    segments.append(LSRSegment(
        label="TOP NOTIONAL",
        long_pct=top_pos_long,
        short_pct=(1.0 - top_pos_long) if top_pos_long is not None else None,
        source_exchanges=top_pos_sources,
        sample_size=None,
    ))

    # 4. TOP ACCOUNTS — top trader account 측 ratio
    top_acc_long = _ratio_to_long_pct(flow.avg_ls_ratio_top_account)
    top_acc_sources = [
        s.exchange for s in flow.snapshots
        if s.ls_ratio_top_account is not None
    ]
    segments.append(LSRSegment(
        label="TOP ACCOUNTS",
        long_pct=top_acc_long,
        short_pct=(1.0 - top_acc_long) if top_acc_long is not None else None,
        source_exchanges=top_acc_sources,
        sample_size=None,
    ))

    # 5. GLOBAL ACCOUNTS — global L/S ratio 측 OI 가중 평균
    global_long = _ratio_to_long_pct(flow.avg_ls_ratio_global)
    global_sources = [
        s.exchange for s in flow.snapshots
        if s.ls_ratio_global is not None
    ]
    segments.append(LSRSegment(
        label="GLOBAL ACCOUNTS",
        long_pct=global_long,
        short_pct=(1.0 - global_long) if global_long is not None else None,
        source_exchanges=global_sources,
        sample_size=None,
    ))

    return DashboardRatios(
        coin=flow.coin,
        fetched_at_ms=int(time.time() * 1000),
        segments=segments,
    )


__all__ = ["DashboardRatios", "LSRSegment", "compute_ratios"]
