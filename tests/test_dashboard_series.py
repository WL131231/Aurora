"""Phase 3 Dashboard Series 단위 테스트 (v0.1.115).

- DashboardSeries.from_series_list — 합산 / 가중 평균 / cumulative CVD
- DashboardSeriesAggregator — cache TTL
- BinanceSeriesProvider — endpoint mock
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from aurora.market.exchanges.binance_series import BinanceSeriesProvider
from aurora.market.exchanges.series_base import (
    ExchangeSeries,
    ExchangeSeriesProvider,
    SeriesBar,
)
from aurora.market.series_aggregator import (
    DashboardSeries,
    DashboardSeriesAggregator,
)

_DAY_MS = 86_400_000


# ============================================================
# DashboardSeries.from_series_list — 합산 / 가중 평균
# ============================================================


def _bar(day_idx: int, **kw: Any) -> SeriesBar:
    """day_idx 측 day_ms = day_idx × _DAY_MS."""
    return SeriesBar(ts_ms=day_idx * _DAY_MS, **kw)


def test_from_series_list_oi_sum_per_day() -> None:
    """oi_usd 합본 측 거래소별 sum (None 제외)."""
    s_a = ExchangeSeries(
        exchange="a", symbol="X", coin="BTC", days=2,
        bars=[_bar(1, oi_usd=10.0), _bar(2, oi_usd=20.0)],
    )
    s_b = ExchangeSeries(
        exchange="b", symbol="X", coin="BTC", days=2,
        bars=[_bar(1, oi_usd=5.0), _bar(2, oi_usd=None)],
    )
    ds = DashboardSeries.from_series_list("BTC", 2, [s_a, s_b])
    assert ds.oi_usd[0].value == 15.0
    assert ds.oi_usd[1].value == 20.0


def test_from_series_list_price_weighted_by_oi() -> None:
    """price_close 합본 측 OI 가중 평균."""
    s_a = ExchangeSeries(
        exchange="a", symbol="X", coin="BTC", days=1,
        bars=[_bar(1, close=100.0, oi_usd=9.0)],
    )
    s_b = ExchangeSeries(
        exchange="b", symbol="X", coin="BTC", days=1,
        bars=[_bar(1, close=200.0, oi_usd=1.0)],
    )
    ds = DashboardSeries.from_series_list("BTC", 1, [s_a, s_b])
    # (100 × 9 + 200 × 1) / 10 = 110
    assert ds.price_close[0].value == pytest.approx(110.0)


def test_from_series_list_perp_cvd_cumulative() -> None:
    """perp_cvd 측 (taker_buy - taker_sell) 합 측 봉 단위 누적."""
    s_a = ExchangeSeries(
        exchange="a", symbol="X", coin="BTC", days=3,
        bars=[
            _bar(1, taker_buy_usd=100.0, taker_sell_usd=50.0),
            _bar(2, taker_buy_usd=80.0, taker_sell_usd=120.0),
            _bar(3, taker_buy_usd=200.0, taker_sell_usd=100.0),
        ],
    )
    ds = DashboardSeries.from_series_list("BTC", 3, [s_a])
    # day1 delta=+50, day2 delta=-40, day3 delta=+100
    assert ds.taker_delta_usd[0].value == pytest.approx(50.0)
    assert ds.taker_delta_usd[1].value == pytest.approx(-40.0)
    assert ds.taker_delta_usd[2].value == pytest.approx(100.0)
    # cumulative: 50, 10, 110
    assert ds.perp_cvd[0].value == pytest.approx(50.0)
    assert ds.perp_cvd[1].value == pytest.approx(10.0)
    assert ds.perp_cvd[2].value == pytest.approx(110.0)


def test_from_series_list_cvd_none_until_first_data() -> None:
    """taker buy/sell 측 None 측 day 측 perp_cvd 측 None (cvd_seen=False)."""
    s_a = ExchangeSeries(
        exchange="a", symbol="X", coin="BTC", days=2,
        bars=[
            _bar(1, close=100.0),  # taker None
            _bar(2, close=110.0, taker_buy_usd=50.0, taker_sell_usd=20.0),
        ],
    )
    ds = DashboardSeries.from_series_list("BTC", 2, [s_a])
    assert ds.perp_cvd[0].value is None
    assert ds.perp_cvd[1].value == pytest.approx(30.0)


def test_from_series_list_empty_list_returns_empty_series() -> None:
    """빈 거래소 list → 빈 시계열."""
    ds = DashboardSeries.from_series_list("BTC", 14, [])
    assert ds.price_close == []
    assert ds.oi_usd == []
    assert ds.exchanges == []


def test_from_series_list_per_exchange_passthrough() -> None:
    """per_exchange dict 측 거래소별 ExchangeSeries 유지."""
    s_a = ExchangeSeries(
        exchange="binance", symbol="BTCUSDT", coin="BTC", days=1,
        bars=[_bar(1, close=100.0)], errors=["x: y"],
    )
    ds = DashboardSeries.from_series_list("BTC", 1, [s_a])
    assert "binance" in ds.per_exchange
    assert ds.per_exchange["binance"].errors == ["x: y"]


# ============================================================
# DashboardSeriesAggregator — cache
# ============================================================


class _FakeSeriesProvider(ExchangeSeriesProvider):
    """call count 추적 mock series provider."""

    EXCHANGE_NAME = "fake"

    def __init__(self) -> None:
        self.call_count = 0

    async def fetch_series(
        self, session: Any, coin: str, days: int = 14,
    ) -> ExchangeSeries:
        self.call_count += 1
        return ExchangeSeries(
            exchange=self.EXCHANGE_NAME,
            symbol=f"{coin}USDT",
            coin=coin,
            days=days,
            bars=[_bar(1, close=100.0, oi_usd=1_000_000.0)],
        )


@pytest.mark.asyncio
async def test_series_aggregator_cache_hit_skips_fetch() -> None:
    """cache TTL 안 → fetch 재호출 X."""
    provider = _FakeSeriesProvider()
    agg = DashboardSeriesAggregator([provider], cache_ttl_sec=300)
    ds1 = await agg.fetch("BTC", 14)
    ds2 = await agg.fetch("BTC", 14)
    assert provider.call_count == 1
    assert ds1 is ds2


@pytest.mark.asyncio
async def test_series_aggregator_cache_miss_after_ttl() -> None:
    """TTL 만료 → 재 fetch."""
    provider = _FakeSeriesProvider()
    agg = DashboardSeriesAggregator([provider], cache_ttl_sec=0)
    await agg.fetch("BTC", 14)
    await agg.fetch("BTC", 14)
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_series_aggregator_separate_cache_per_coin() -> None:
    """BTC 측 cache hit 측 ETH 영향 X (key = (coin, days))."""
    provider = _FakeSeriesProvider()
    agg = DashboardSeriesAggregator([provider], cache_ttl_sec=300)
    await agg.fetch("BTC", 14)
    await agg.fetch("ETH", 14)
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_series_aggregator_provider_exception_returns_empty() -> None:
    """provider raise → 빈 ExchangeSeries + errors 박힘 (UI 안전)."""

    class _BrokenProvider(ExchangeSeriesProvider):
        EXCHANGE_NAME = "broken"

        async def fetch_series(
            self, session: Any, coin: str, days: int = 14,
        ) -> ExchangeSeries:
            raise RuntimeError("boom")

    agg = DashboardSeriesAggregator([_BrokenProvider()])
    ds = await agg.fetch("BTC", 14)
    # 합본 측 빈 (모든 거래소 실패)
    assert ds.price_close == []
    assert "broken" in ds.per_exchange
    assert ds.per_exchange["broken"].errors


# ============================================================
# BinanceSeriesProvider — endpoint mock
# ============================================================


def _binance_series_mock_responses() -> dict[str, Any]:
    """Binance 5 endpoint mock — 2 일치 봉 (단순화)."""
    day1 = 1714003200000  # 2024-04-25 UTC 00:00
    day2 = day1 + _DAY_MS

    # kline = [openTime, o, h, l, c, vol, closeTime, quoteVol, trades,
    #         takerBuyVol, takerBuyQuoteVol, ignore]
    return {
        "/fapi/v1/klines": [
            [day1, "80000", "81000", "79500", "80500", "100",
             day1 + _DAY_MS - 1, "8000000000", 1000, "60", "4800000000", "0"],
            [day2, "80500", "82000", "80000", "81500", "120",
             day2 + _DAY_MS - 1, "9700000000", 1100, "70", "5700000000", "0"],
        ],
        "/fapi/v1/fundingRate": [
            {"fundingTime": day1 + 0, "fundingRate": "0.00010", "symbol": "BTCUSDT"},
            {"fundingTime": day1 + 28800000, "fundingRate": "0.00012", "symbol": "BTCUSDT"},
            {"fundingTime": day1 + 57600000, "fundingRate": "0.00014", "symbol": "BTCUSDT"},
            {"fundingTime": day2 + 0, "fundingRate": "0.00020", "symbol": "BTCUSDT"},
        ],
        "/futures/data/openInterestHist": [
            {"timestamp": day1, "sumOpenInterest": "100000",
             "sumOpenInterestValue": "8000000000"},
            {"timestamp": day2, "sumOpenInterest": "110000",
             "sumOpenInterestValue": "8800000000"},
        ],
        "/futures/data/globalLongShortAccountRatio": [
            {"timestamp": day1, "longShortRatio": "1.45",
             "longAccount": "0.59", "shortAccount": "0.41"},
            {"timestamp": day2, "longShortRatio": "1.50",
             "longAccount": "0.60", "shortAccount": "0.40"},
        ],
        "/futures/data/takerlongshortRatio": [
            {"timestamp": day1, "buySellRatio": "1.10",
             "buyVol": "55000000000", "sellVol": "50000000000"},
            {"timestamp": day2, "buySellRatio": "1.20",
             "buyVol": "60000000000", "sellVol": "50000000000"},
        ],
    }


def _make_binance_mock_session(responses: dict[str, Any]) -> MagicMock:
    """aiohttp.ClientSession.get path → response mock."""
    session = MagicMock()

    def _get(url: str, params=None, timeout=None):
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
async def test_binance_series_happy_path() -> None:
    """5 endpoint 정상 → 2 일봉 박힘 + 모든 필드."""
    provider = BinanceSeriesProvider()
    session = _make_binance_mock_session(_binance_series_mock_responses())

    series = await provider.fetch_series(session, "BTC", days=14)

    assert series.exchange == "binance"
    assert series.symbol == "BTCUSDT"
    assert len(series.bars) == 2
    bar1 = series.bars[0]
    assert bar1.close == pytest.approx(80500.0)
    assert bar1.volume_usd == pytest.approx(8_000_000_000.0)
    assert bar1.taker_buy_usd == pytest.approx(4_800_000_000.0)
    assert bar1.taker_sell_usd == pytest.approx(8_000_000_000.0 - 4_800_000_000.0)
    assert bar1.oi_usd == pytest.approx(8_000_000_000.0)
    assert bar1.ls_ratio_global == pytest.approx(1.45)
    # v0.3.4: 1H bucket forward-fill — bar1 (day1 00:00) 측 ts <= +1h funding 중 latest
    # = day1+0 = 0.00010 (day1+8h, day1+16h 측 박혀 늦은 hour bucket 박힘)
    assert bar1.funding_rate_avg == pytest.approx(0.00010, rel=1e-3)


@pytest.mark.asyncio
async def test_binance_series_partial_endpoint_failure_isolated() -> None:
    """OI hist endpoint 만 raise → 다른 필드 정상, errors 박힘."""
    provider = BinanceSeriesProvider()
    responses = _binance_series_mock_responses()

    session = MagicMock()

    def _get(url: str, params=None, timeout=None):
        path = url.replace("https://fapi.binance.com", "")
        ctx = AsyncMock()
        resp = MagicMock()
        if path == "/futures/data/openInterestHist":
            resp.raise_for_status = MagicMock(side_effect=RuntimeError("500 server"))
        else:
            resp.raise_for_status = MagicMock()
        resp.json = AsyncMock(return_value=responses[path])
        ctx.__aenter__.return_value = resp
        ctx.__aexit__.return_value = None
        return ctx
    session.get = _get

    series = await provider.fetch_series(session, "BTC", days=14)

    assert len(series.bars) == 2
    # OI 측 None
    assert series.bars[0].oi_usd is None
    # 다른 필드 정상
    assert series.bars[0].close is not None
    assert series.bars[0].ls_ratio_global is not None
    # 에러 박힘
    assert any("oi_hist" in e for e in series.errors)


# ============================================================
# _weighted_avg — 가중 평균 헬퍼 단위 테스트
# ============================================================

from types import SimpleNamespace  # noqa: E402

from aurora.market.series_aggregator import _weighted_avg  # noqa: E402


def _wt_bar(value: float | None, weight: float | None) -> object:
    """_weighted_avg 테스트용 더미 bar 객체 — val / wt 속성."""
    return SimpleNamespace(val=value, wt=weight)


def test_weighted_avg_all_weights_set() -> None:
    """가중치 전부 있음 → 가중 평균 반환."""
    bars = [
        ("binance", _wt_bar(10.0, 100.0)),
        ("okx", _wt_bar(20.0, 300.0)),
    ]
    # (10×100 + 20×300) / (100+300) = 7000 / 400 = 17.5
    result = _weighted_avg(bars, "val", "wt")
    assert result == pytest.approx(17.5)


def test_weighted_avg_no_weights_simple_mean() -> None:
    """가중치 모두 None → 단순 평균."""
    bars = [
        ("binance", _wt_bar(10.0, None)),
        ("okx", _wt_bar(20.0, None)),
        ("bybit", _wt_bar(30.0, None)),
    ]
    result = _weighted_avg(bars, "val", "wt")
    assert result == pytest.approx(20.0)


def test_weighted_avg_zero_weight_treated_as_no_weight() -> None:
    """weight=0 → 분모에 미포함 → 단순 평균 경로."""
    bars = [
        ("binance", _wt_bar(10.0, 0.0)),
        ("okx", _wt_bar(20.0, 0.0)),
    ]
    result = _weighted_avg(bars, "val", "wt")
    assert result == pytest.approx(15.0)


def test_weighted_avg_all_values_none_returns_none() -> None:
    """모든 val 이 None → None 반환."""
    bars = [
        ("binance", _wt_bar(None, 100.0)),
        ("okx", _wt_bar(None, 200.0)),
    ]
    result = _weighted_avg(bars, "val", "wt")
    assert result is None


def test_weighted_avg_partial_none_skips_none_bars() -> None:
    """일부 val=None 인 bar 는 스킵하고 나머지로 평균."""
    bars = [
        ("binance", _wt_bar(None, 100.0)),
        ("okx", _wt_bar(40.0, 200.0)),
    ]
    # None bar 제외 → 40.0 × 200 / 200 = 40.0
    result = _weighted_avg(bars, "val", "wt")
    assert result == pytest.approx(40.0)


def test_weighted_avg_empty_bars_returns_none() -> None:
    """빈 day_bars → None."""
    assert _weighted_avg([], "val", "wt") is None
