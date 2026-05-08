"""Phase 3 Dashboard Flow 단위 테스트 (v0.1.87).

- ExchangeSnapshot 합산 / 가중 평균 회귀
- DashboardFlowAggregator cache TTL
- BinanceMarketData fetch_snapshot — aiohttp mock
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from aurora.market.dashboard_flow import DashboardFlow, DashboardFlowAggregator
from aurora.market.exchanges import BinanceMarketData
from aurora.market.exchanges.base import ExchangeMarketData, ExchangeSnapshot

# ============================================================
# DashboardFlow.from_snapshots — 합산 / 평균
# ============================================================


def test_from_snapshots_sums_oi_and_volume() -> None:
    """OI / 24h volume 합 박힘 (None 제외)."""
    snaps = [
        ExchangeSnapshot(
            exchange="binance", symbol="BTCUSDT", fetched_at_ms=0,
            oi_usd=10_000_000.0, volume_24h_usd=50_000_000.0,
        ),
        ExchangeSnapshot(
            exchange="bybit", symbol="BTCUSDT", fetched_at_ms=0,
            oi_usd=8_000_000.0, volume_24h_usd=40_000_000.0,
        ),
        ExchangeSnapshot(
            exchange="okx", symbol="BTCUSDT", fetched_at_ms=0,
            oi_usd=None,  # 미상 — 합산 제외
            volume_24h_usd=20_000_000.0,
        ),
    ]
    flow = DashboardFlow.from_snapshots("BTC", snaps)
    assert flow.total_oi_usd == 18_000_000.0
    assert flow.total_volume_24h_usd == 110_000_000.0


def test_from_snapshots_weighted_avg_funding_by_oi() -> None:
    """avg_funding_rate = OI 가중 평균 (큰 거래소 영향 큼)."""
    snaps = [
        ExchangeSnapshot(
            exchange="binance", symbol="BTCUSDT", fetched_at_ms=0,
            oi_usd=9_000_000.0, funding_rate=0.0001,
        ),
        ExchangeSnapshot(
            exchange="bybit", symbol="BTCUSDT", fetched_at_ms=0,
            oi_usd=1_000_000.0, funding_rate=0.001,
        ),
    ]
    flow = DashboardFlow.from_snapshots("BTC", snaps)
    # 가중 = (0.0001 × 9M + 0.001 × 1M) / 10M = 0.00019
    assert flow.avg_funding_rate is not None
    assert abs(flow.avg_funding_rate - 0.00019) < 1e-9


def test_from_snapshots_simple_avg_when_no_oi() -> None:
    """OI 모두 미상 → 단순 평균 fallback."""
    snaps = [
        ExchangeSnapshot(
            exchange="a", symbol="X", fetched_at_ms=0,
            oi_usd=None, ls_ratio_global=1.0,
        ),
        ExchangeSnapshot(
            exchange="b", symbol="X", fetched_at_ms=0,
            oi_usd=None, ls_ratio_global=2.0,
        ),
    ]
    flow = DashboardFlow.from_snapshots("BTC", snaps)
    assert flow.avg_ls_ratio_global == 1.5


def test_from_snapshots_all_none_returns_none() -> None:
    """모든 거래소 측 funding 미상 → avg_funding None."""
    snaps = [
        ExchangeSnapshot(
            exchange="a", symbol="X", fetched_at_ms=0, oi_usd=1.0,
        ),
    ]
    flow = DashboardFlow.from_snapshots("BTC", snaps)
    assert flow.avg_funding_rate is None


# ============================================================
# DashboardFlowAggregator — cache TTL
# ============================================================


class _FakeProvider(ExchangeMarketData):
    """call count 추적 mock provider."""

    EXCHANGE_NAME = "fake"

    def __init__(self) -> None:
        self.call_count = 0

    async def fetch_snapshot(self, session: Any, coin: str) -> ExchangeSnapshot:
        self.call_count += 1
        return ExchangeSnapshot(
            exchange=self.EXCHANGE_NAME,
            symbol=f"{coin}USDT",
            fetched_at_ms=int(time.time() * 1000),
            oi_usd=1_000_000.0,
        )


@pytest.mark.asyncio
async def test_aggregator_cache_hit_skips_fetch() -> None:
    """cache TTL 안 = provider.fetch 재호출 X."""
    provider = _FakeProvider()
    agg = DashboardFlowAggregator([provider], cache_ttl_sec=60)

    flow1 = await agg.fetch("BTC")
    flow2 = await agg.fetch("BTC")
    assert provider.call_count == 1  # cache hit — 두 번째 호출엔 X
    assert flow1 is flow2


@pytest.mark.asyncio
async def test_aggregator_cache_miss_after_ttl() -> None:
    """TTL 만료 = provider.fetch 다시 호출."""
    provider = _FakeProvider()
    agg = DashboardFlowAggregator([provider], cache_ttl_sec=0)  # 즉시 만료

    await agg.fetch("BTC")
    await agg.fetch("BTC")
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_aggregator_provider_exception_returns_empty_snapshot() -> None:
    """provider.fetch 가 raise 해도 빈 snapshot 박힘 (UI 안전)."""

    class _BrokenProvider(ExchangeMarketData):
        EXCHANGE_NAME = "broken"

        async def fetch_snapshot(self, session: Any, coin: str) -> ExchangeSnapshot:
            raise RuntimeError("boom")

    agg = DashboardFlowAggregator([_BrokenProvider()])
    flow = await agg.fetch("BTC")
    assert len(flow.snapshots) == 1
    assert flow.snapshots[0].exchange == "broken"
    assert flow.snapshots[0].errors  # 에러 메시지 박힘
    # 합 = None (모든 fetch 실패)
    assert flow.total_oi_usd is None


# ============================================================
# BinanceMarketData — endpoint mock
# ============================================================


def _binance_mock_responses() -> dict[str, Any]:
    """Binance 6 endpoint mock 응답 — fixture."""
    return {
        "/fapi/v1/openInterest":           {"symbol": "BTCUSDT", "openInterest": "100.5", "time": 1},
        "/fapi/v1/premiumIndex":           {"symbol": "BTCUSDT", "markPrice": "80000.0",
                                            "lastFundingRate": "0.00012"},
        "/fapi/v1/ticker/24hr":            {"symbol": "BTCUSDT", "priceChangePercent": "1.5",
                                            "quoteVolume": "5000000000", "lastPrice": "80000"},
        "/futures/data/globalLongShortAccountRatio":
            [{"longShortRatio": "1.45", "longAccount": "0.59", "shortAccount": "0.41",
              "timestamp": 1, "symbol": "BTCUSDT"}],
        "/futures/data/topLongShortPositionRatio":
            [{"longShortRatio": "0.85", "longAccount": "0.46", "shortAccount": "0.54",
              "timestamp": 1, "symbol": "BTCUSDT"}],
        "/futures/data/topLongShortAccountRatio":
            [{"longShortRatio": "1.10", "longAccount": "0.52", "shortAccount": "0.48",
              "timestamp": 1, "symbol": "BTCUSDT"}],
    }


def _make_mock_session(responses: dict[str, Any]) -> MagicMock:
    """aiohttp.ClientSession.get 측 path 별 응답 dispatch mock."""
    session = MagicMock()

    def _get(url: str, params=None, timeout=None):
        # url = "https://fapi.binance.com/fapi/v1/openInterest" 형태 → path 추출
        path = "/" + url.split("//", 1)[-1].split("/", 1)[-1]
        # path = "fapi.binance.com/..." → fix
        path = url.replace("https://fapi.binance.com", "")

        ctx = AsyncMock()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = AsyncMock(return_value=responses[path])
        ctx.__aenter__.return_value = resp
        ctx.__aexit__.return_value = None
        return ctx

    session.get = _get
    return session


@pytest.mark.asyncio
async def test_binance_fetch_snapshot_happy_path() -> None:
    """6 endpoint 모두 정상 응답 → snapshot 박힘 (OI / funding / L-S 모두)."""
    binance = BinanceMarketData()
    session = _make_mock_session(_binance_mock_responses())

    snap = await binance.fetch_snapshot(session, "BTC")

    assert snap.exchange == "binance"
    assert snap.symbol == "BTCUSDT"
    # OI = 100.5 contracts × 80000 USD = 8_040_000
    assert snap.oi_usd == pytest.approx(8_040_000.0)
    assert snap.funding_rate == pytest.approx(0.00012)
    assert snap.price == pytest.approx(80000.0)
    assert snap.price_24h_change_pct == pytest.approx(1.5)
    assert snap.volume_24h_usd == pytest.approx(5_000_000_000.0)
    assert snap.ls_ratio_global == pytest.approx(1.45)
    assert snap.ls_ratio_top_position == pytest.approx(0.85)
    assert snap.ls_ratio_top_account == pytest.approx(1.10)
    assert snap.errors == []


@pytest.mark.asyncio
async def test_binance_partial_failure_isolated() -> None:
    """1 endpoint 실패 (ls_top_position raise) → 나머지 필드 정상 박힘."""
    binance = BinanceMarketData()

    # mock 측 ls_top_position 만 raise — 나머지 정상
    responses = _binance_mock_responses()

    session = MagicMock()
    def _get(url: str, params=None, timeout=None):
        path = url.replace("https://fapi.binance.com", "")
        ctx = AsyncMock()
        resp = MagicMock()
        if path == "/futures/data/topLongShortPositionRatio":
            resp.raise_for_status = MagicMock(side_effect=RuntimeError("429 rate limit"))
        else:
            resp.raise_for_status = MagicMock()
        resp.json = AsyncMock(return_value=responses[path])
        ctx.__aenter__.return_value = resp
        ctx.__aexit__.return_value = None
        return ctx
    session.get = _get

    snap = await binance.fetch_snapshot(session, "BTC")

    # 정상 필드 박힘
    assert snap.oi_usd is not None
    assert snap.funding_rate is not None
    assert snap.ls_ratio_global is not None
    # 실패 필드 None + 에러 박힘
    assert snap.ls_ratio_top_position is None
    assert any("ls_top_pos" in e for e in snap.errors)
