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
from aurora.market.exchanges import (
    BinanceMarketData,
    BitgetMarketData,
    BybitMarketData,
    HyperliquidMarketData,
    OkxMarketData,
)
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
        "/fapi/v1/aggTrades":  # v0.1.90 — happy path 측 빈 list (whale 0건)
            [],
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


# ============================================================
# BybitMarketData (v0.1.88) — V5 endpoint mock
# ============================================================


def _make_dispatch_session(base_url: str, path_to_response: dict[str, Any]) -> MagicMock:
    """url 측 path 매핑 dispatch — base_url 제거 후 path lookup."""
    session = MagicMock()
    def _get(url: str, params=None, timeout=None):
        path = url.replace(base_url, "")
        ctx = AsyncMock()
        resp = MagicMock()
        if path in path_to_response and isinstance(path_to_response[path], Exception):
            resp.raise_for_status = MagicMock(side_effect=path_to_response[path])
            resp.json = AsyncMock(return_value={})
        else:
            resp.raise_for_status = MagicMock()
            resp.json = AsyncMock(return_value=path_to_response.get(path, {}))
        ctx.__aenter__.return_value = resp
        ctx.__aexit__.return_value = None
        return ctx
    session.get = _get
    return session


@pytest.mark.asyncio
async def test_bybit_fetch_snapshot_happy_path() -> None:
    """Bybit V5 — tickers + account-ratio 두 endpoint 정상."""
    bybit = BybitMarketData()
    session = _make_dispatch_session("https://api.bybit.com", {
        "/v5/market/tickers": {
            "retCode": 0,
            "result": {"list": [{
                "symbol": "BTCUSDT",
                "lastPrice": "80000.0",
                "price24hPcnt": "0.015",   # 1.5%
                "turnover24h": "3000000000",
                "openInterest": "10000",
                "openInterestValue": "800000000",  # USD notional
                "fundingRate": "0.00008",
            }]},
        },
        "/v5/market/account-ratio": {
            "retCode": 0,
            "result": {"list": [{
                "buyRatio": "0.55", "sellRatio": "0.45", "timestamp": "1",
            }]},
        },
    })

    snap = await bybit.fetch_snapshot(session, "BTC")
    assert snap.exchange == "bybit"
    assert snap.symbol == "BTCUSDT"
    assert snap.price == pytest.approx(80000.0)
    assert snap.price_24h_change_pct == pytest.approx(1.5)
    assert snap.volume_24h_usd == pytest.approx(3_000_000_000.0)
    assert snap.oi_usd == pytest.approx(800_000_000.0)
    assert snap.funding_rate == pytest.approx(0.00008)
    # ls_ratio = 0.55 / 0.45
    assert snap.ls_ratio_global == pytest.approx(0.55 / 0.45)
    assert snap.long_account_pct == pytest.approx(0.55)
    # Bybit V5 측 top trader endpoint X — None 유지
    assert snap.ls_ratio_top_position is None
    assert snap.errors == []


@pytest.mark.asyncio
async def test_bybit_ret_code_nonzero_raises_in_subfetch() -> None:
    """retCode != 0 = 부분 실패 (해당 필드만 None + errors 박힘)."""
    bybit = BybitMarketData()
    session = _make_dispatch_session("https://api.bybit.com", {
        "/v5/market/tickers": {
            "retCode": 0,
            "result": {"list": [{
                "lastPrice": "80000", "price24hPcnt": "0",
                "turnover24h": "0", "openInterestValue": "0", "fundingRate": "0",
            }]},
        },
        "/v5/market/account-ratio": {
            "retCode": 10001, "retMsg": "param error",
        },
    })

    snap = await bybit.fetch_snapshot(session, "BTC")
    assert snap.price == pytest.approx(80000.0)  # ticker 측 성공
    assert snap.ls_ratio_global is None
    assert any("ls" in e for e in snap.errors)


# ============================================================
# OkxMarketData (v0.1.88) — V5 endpoint mock
# ============================================================


@pytest.mark.asyncio
async def test_okx_fetch_snapshot_happy_path() -> None:
    """OKX V5 — 5 endpoint 정상 (ticker / OI / funding / L-S acc / L-S top)."""
    okx = OkxMarketData()
    session = _make_dispatch_session("https://www.okx.com", {
        "/api/v5/market/ticker": {
            "code": "0",
            "data": [{
                "instId": "BTC-USDT-SWAP",
                "last": "80000",
                "open24h": "78818.18",  # +1.5%
                "volCcy24h": "1500000000",
            }],
        },
        "/api/v5/public/open-interest": {
            "code": "0",
            "data": [{"oi": "10000", "oiCcy": "5000.0"}],  # 5000 BTC
        },
        "/api/v5/public/funding-rate": {
            "code": "0",
            "data": [{"fundingRate": "0.00010"}],
        },
        "/api/v5/rubik/stat/contracts/long-short-account-ratio": {
            "code": "0",
            "data": [["1700000000000", "1.20"]],
        },
        "/api/v5/rubik/stat/contracts/long-short-position-ratio": {
            "code": "0",
            "data": [["1700000000000", "1.05"]],
        },
    })

    snap = await okx.fetch_snapshot(session, "BTC")
    assert snap.exchange == "okx"
    assert snap.symbol == "BTC-USDT-SWAP"
    assert snap.price == pytest.approx(80000.0)
    # 24h pct = (80000 - 78818.18) / 78818.18 × 100 ≈ 1.5
    assert snap.price_24h_change_pct == pytest.approx(1.5, rel=1e-3)
    # OI = 5000 × 80000 = 400_000_000
    assert snap.oi_usd == pytest.approx(400_000_000.0)
    assert snap.funding_rate == pytest.approx(0.00010)
    assert snap.ls_ratio_global == pytest.approx(1.20)
    assert snap.ls_ratio_top_position == pytest.approx(1.05)
    assert snap.errors == []


@pytest.mark.asyncio
async def test_okx_oi_skipped_when_price_missing() -> None:
    """ticker 실패 → price None → OI USD 환산 skip (oiCcy 만 받아도 None)."""
    okx = OkxMarketData()
    session = _make_dispatch_session("https://www.okx.com", {
        "/api/v5/market/ticker": {
            "code": "1", "msg": "broken",
        },
        "/api/v5/public/open-interest": {
            "code": "0",
            "data": [{"oi": "10000", "oiCcy": "5000.0"}],
        },
        "/api/v5/public/funding-rate": {"code": "0", "data": [{"fundingRate": "0"}]},
        "/api/v5/rubik/stat/contracts/long-short-account-ratio": {"code": "0", "data": []},
        "/api/v5/rubik/stat/contracts/long-short-position-ratio": {"code": "0", "data": []},
    })

    snap = await okx.fetch_snapshot(session, "BTC")
    assert snap.price is None
    assert snap.oi_usd is None  # price 없어서 환산 skip
    assert any("ticker" in e for e in snap.errors)


# ============================================================
# BitgetMarketData (v0.1.89) — V2 mix endpoint mock
# ============================================================


@pytest.mark.asyncio
async def test_bitget_fetch_snapshot_happy_path() -> None:
    """Bitget V2 — ticker + L-S 둘 정상."""
    bitget = BitgetMarketData()
    session = _make_dispatch_session("https://api.bitget.com", {
        "/api/v2/mix/market/ticker": {
            "code": "00000",
            "data": [{
                "symbol": "BTCUSDT",
                "lastPr": "80000",
                "change24h": "0.015",     # 1.5%
                "quoteVolume": "2000000000",
                "holdingAmount": "8000",  # 8000 BTC
                "fundingRate": "0.00009",
            }],
        },
        "/api/v2/mix/market/account-long-short": {
            "code": "00000",
            "data": [{
                "ts": "1", "longAccountRatio": "0.62", "shortAccountRatio": "0.38",
            }],
        },
    })

    snap = await bitget.fetch_snapshot(session, "BTC")
    assert snap.exchange == "bitget"
    assert snap.symbol == "BTCUSDT"
    assert snap.price == pytest.approx(80000.0)
    assert snap.price_24h_change_pct == pytest.approx(1.5)
    assert snap.volume_24h_usd == pytest.approx(2_000_000_000.0)
    # OI = 8000 × 80000 = 640_000_000
    assert snap.oi_usd == pytest.approx(640_000_000.0)
    assert snap.funding_rate == pytest.approx(0.00009)
    assert snap.ls_ratio_global == pytest.approx(0.62 / 0.38)
    assert snap.long_account_pct == pytest.approx(0.62)
    # Bitget 측 top trader endpoint X
    assert snap.ls_ratio_top_position is None
    assert snap.errors == []


# ============================================================
# HyperliquidMarketData (v0.1.89) — Info API POST mock
# ============================================================


def _make_hl_session(meta_response: Any) -> MagicMock:
    """Hyperliquid POST /info dispatch — body type 별."""
    session = MagicMock()
    def _post(url: str, json=None, timeout=None):
        ctx = AsyncMock()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if isinstance(meta_response, Exception):
            resp.raise_for_status = MagicMock(side_effect=meta_response)
        resp.json = AsyncMock(return_value=meta_response)
        ctx.__aenter__.return_value = resp
        ctx.__aexit__.return_value = None
        return ctx
    session.post = _post
    return session


@pytest.mark.asyncio
async def test_hyperliquid_fetch_snapshot_happy_path() -> None:
    """Hyperliquid Info API — metaAndAssetCtxs 정상."""
    hl = HyperliquidMarketData()
    session = _make_hl_session([
        {"universe": [
            {"name": "BTC", "szDecimals": 5, "maxLeverage": 50},
            {"name": "ETH", "szDecimals": 4, "maxLeverage": 50},
        ]},
        [
            {  # BTC ctx (idx=0)
                "markPx": "80000",
                "prevDayPx": "78818.18",   # +1.5% 이상 이내
                "dayNtlVlm": "500000000",  # USD notional
                "openInterest": "1000",    # 1000 BTC
                "funding": "0.00001",
            },
            {  # ETH ctx (idx=1)
                "markPx": "3000",
                "prevDayPx": "2950",
                "dayNtlVlm": "100000000",
                "openInterest": "10000",
                "funding": "0.00002",
            },
        ],
    ])

    snap = await hl.fetch_snapshot(session, "BTC")
    assert snap.exchange == "hyperliquid"
    assert snap.symbol == "BTC"
    assert snap.price == pytest.approx(80000.0)
    assert snap.price_24h_change_pct == pytest.approx(1.5, rel=1e-3)
    assert snap.volume_24h_usd == pytest.approx(500_000_000.0)
    # OI = 1000 × 80000 = 80_000_000
    assert snap.oi_usd == pytest.approx(80_000_000.0)
    assert snap.funding_rate == pytest.approx(0.00001)
    # L-S ratio 측 Hyperliquid X — None
    assert snap.ls_ratio_global is None
    assert snap.errors == []


@pytest.mark.asyncio
async def test_hyperliquid_coin_not_in_universe() -> None:
    """coin 측 universe 안 없음 → errors 박힘 + 모든 필드 None."""
    hl = HyperliquidMarketData()
    session = _make_hl_session([
        {"universe": [{"name": "ETH"}]},  # BTC 없음
        [{"markPx": "3000", "prevDayPx": "2950", "dayNtlVlm": "0",
          "openInterest": "0", "funding": "0"}],
    ])

    snap = await hl.fetch_snapshot(session, "BTC")
    assert snap.price is None
    assert snap.oi_usd is None
    assert any("not in universe" in e for e in snap.errors)


@pytest.mark.asyncio
async def test_hyperliquid_post_raises_returns_empty() -> None:
    """POST /info raise → snapshot.errors 박힘 + 모든 필드 None."""
    hl = HyperliquidMarketData()
    session = _make_hl_session(RuntimeError("503 service unavailable"))

    snap = await hl.fetch_snapshot(session, "BTC")
    assert snap.price is None
    assert snap.errors  # 에러 메시지 박힘


# ============================================================
# v0.1.90: Whale Notional — Binance aggTrades 측 큰 거래 필터
# ============================================================


@pytest.mark.asyncio
async def test_binance_whale_notional_filtering() -> None:
    """≥ $100K 큰 거래만 합산 — buy / sell 분리 (m=true → sell, m=false → buy)."""
    binance = BinanceMarketData()

    # mock 측 aggTrades 응답에 큰 거래 / 작은 거래 섞어 박음
    big_buy = {"p": "80000", "q": "2.5", "T": 1, "m": False}    # $200K BUY
    big_sell = {"p": "80000", "q": "1.5", "T": 2, "m": True}    # $120K SELL
    small_buy = {"p": "80000", "q": "0.1", "T": 3, "m": False}  # $8K (skip)
    big_buy2 = {"p": "80000", "q": "5.0", "T": 4, "m": False}   # $400K BUY

    base_responses = _binance_mock_responses()
    base_responses["/fapi/v1/aggTrades"] = [big_buy, big_sell, small_buy, big_buy2]

    session = MagicMock()
    def _get(url: str, params=None, timeout=None):
        path = url.replace("https://fapi.binance.com", "")
        ctx = AsyncMock()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = AsyncMock(return_value=base_responses[path])
        ctx.__aenter__.return_value = resp
        ctx.__aexit__.return_value = None
        return ctx
    session.get = _get

    snap = await binance.fetch_snapshot(session, "BTC")

    # threshold 박힘
    assert snap.whale_threshold_usd == pytest.approx(100_000.0)
    # 큰 거래 3개 (small_buy 제외)
    assert snap.whale_count_5m == 3
    # 매수 = 200K + 400K = 600K, 매도 = 120K
    assert snap.whale_buy_5m_usd == pytest.approx(600_000.0)
    assert snap.whale_sell_5m_usd == pytest.approx(120_000.0)


def test_dashboard_flow_aggregates_whale_total() -> None:
    """DashboardFlow.from_snapshots — whale 합 박힘 (None 제외)."""
    snaps = [
        ExchangeSnapshot(
            exchange="binance", symbol="BTCUSDT", fetched_at_ms=0,
            whale_buy_5m_usd=500_000.0, whale_sell_5m_usd=200_000.0,
            whale_count_5m=4, whale_threshold_usd=100_000.0,
        ),
        ExchangeSnapshot(
            exchange="bybit", symbol="BTCUSDT", fetched_at_ms=0,
            # whale 측 None — 합산 제외
        ),
    ]
    flow = DashboardFlow.from_snapshots("BTC", snaps)
    assert flow.total_whale_buy_5m_usd == pytest.approx(500_000.0)
    assert flow.total_whale_sell_5m_usd == pytest.approx(200_000.0)
    assert flow.total_whale_count_5m == 4


def test_dashboard_flow_whale_total_none_when_all_none() -> None:
    """모든 거래소 측 whale None → total_whale_* 도 None."""
    snaps = [
        ExchangeSnapshot(exchange="a", symbol="X", fetched_at_ms=0),
        ExchangeSnapshot(exchange="b", symbol="X", fetched_at_ms=0),
    ]
    flow = DashboardFlow.from_snapshots("BTC", snaps)
    assert flow.total_whale_buy_5m_usd is None
    assert flow.total_whale_sell_5m_usd is None
    assert flow.total_whale_count_5m is None
