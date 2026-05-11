"""Binance Futures (USDM) 14D 시계열 fetcher (v0.3.4: 1H interval).

Phase 3 Dashboard view 측 신규 사양 — 14일 × 24h = 336 봉 (1H 단위) + funding + OI
+ LSR + taker delta. ``BinanceMarketData`` (snapshot) 측 별개.

Public endpoints (API 키 X):
- ``/fapi/v1/klines?interval=1h&limit=336`` — 1H kline 336봉
- ``/fapi/v1/fundingRate?symbol=&limit=42`` — 8h × 3 × 14d funding (hour bucket fwd-fill)
- ``/futures/data/openInterestHist?period=1h&limit=336`` — OI 1H history
- ``/futures/data/globalLongShortAccountRatio?period=1h&limit=336`` — LSR global 1H
- ``/futures/data/takerlongshortRatio?period=1h&limit=336`` — taker buy/sell 1H

v0.3.4: daily → 1H 박음 (사용자 요청 — 참고자료 1H 라인 정합).
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from aurora.market.exchanges.series_base import (
    ExchangeSeries,
    ExchangeSeriesProvider,
    SeriesBar,
)
from aurora.timeouts import make_exchange_timeout

logger = logging.getLogger(__name__)

_FAPI_BASE = "https://fapi.binance.com"
_HTTP_TIMEOUT = make_exchange_timeout()
_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000


def _floor_day_ms(ts_ms: int) -> int:
    """ts_ms 측 UTC 00:00 으로 floor."""
    return (ts_ms // _DAY_MS) * _DAY_MS


def _floor_hour_ms(ts_ms: int) -> int:
    """ts_ms 측 1H 단위로 floor (v0.3.4)."""
    return (ts_ms // _HOUR_MS) * _HOUR_MS


class BinanceSeriesProvider(ExchangeSeriesProvider):
    """Binance Futures (USDM) 14D 시계열 fetcher."""

    EXCHANGE_NAME = "binance"

    async def fetch_series(
        self,
        session: aiohttp.ClientSession,
        coin: str,
        days: int = 14,
    ) -> ExchangeSeries:
        symbol = self.symbol_for(coin)
        series = ExchangeSeries(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            coin=coin,
            days=days,
        )

        # 5 endpoint 병렬 fetch — 부분 실패 격리
        results = await asyncio.gather(
            self._fetch_klines(session, symbol, days),
            self._fetch_funding(session, symbol, days),
            self._fetch_oi_hist(session, symbol, days),
            self._fetch_lsr_global(session, symbol, days),
            self._fetch_taker_lsr(session, symbol, days),
            return_exceptions=True,
        )
        klines, funding, oi_hist, lsr_global, taker_lsr = results

        # hour_ms → SeriesBar (v0.3.4: 1H bucket)
        bars: dict[int, SeriesBar] = {}

        # 1) klines — base + price + volume + taker buy (1H bar)
        if isinstance(klines, Exception):
            series.errors.append(f"klines: {klines}")
        elif isinstance(klines, list):
            for k in klines:
                try:
                    open_ms = int(k[0])
                    hour = _floor_hour_ms(open_ms)
                    bar = bars.setdefault(hour, SeriesBar(ts_ms=hour))
                    bar.open = float(k[1])
                    bar.high = float(k[2])
                    bar.low = float(k[3])
                    bar.close = float(k[4])
                    bar.volume_usd = float(k[7])      # quote volume
                    bar.taker_buy_usd = float(k[10])  # taker buy quote vol
                    if bar.volume_usd is not None and bar.taker_buy_usd is not None:
                        bar.taker_sell_usd = bar.volume_usd - bar.taker_buy_usd
                except (TypeError, ValueError, IndexError) as e:
                    series.errors.append(f"kline parse: {e}")
                    break

        # 2) funding history — 8h × 3/day funding → 1H bucket forward-fill
        # (8h funding rate 측 8 시간 동안 적용 — hour bucket마다 가장 최근 rate 박음)
        if isinstance(funding, Exception):
            series.errors.append(f"funding: {funding}")
        elif isinstance(funding, list):
            funding_points: list[tuple[int, float]] = []
            for row in funding:
                try:
                    ts = int(row.get("fundingTime", 0))
                    rate = float(row.get("fundingRate", 0) or 0)
                    funding_points.append((ts, rate))
                except (TypeError, ValueError) as e:
                    series.errors.append(f"funding parse: {e}")
                    break
            funding_points.sort(key=lambda x: x[0])
            # forward-fill — bar hour → 가장 최근 funding 박음
            for hour, bar in bars.items():
                latest_rate: float | None = None
                for ts, rate in funding_points:
                    if ts <= hour + _HOUR_MS:
                        latest_rate = rate
                    else:
                        break
                if latest_rate is not None:
                    bar.funding_rate_avg = latest_rate

        # 3) OI history (1H period)
        if isinstance(oi_hist, Exception):
            series.errors.append(f"oi_hist: {oi_hist}")
        elif isinstance(oi_hist, list):
            for row in oi_hist:
                try:
                    ts = int(row.get("timestamp", 0))
                    hour = _floor_hour_ms(ts)
                    bar = bars.setdefault(hour, SeriesBar(ts_ms=hour))
                    bar.oi_usd = float(row.get("sumOpenInterestValue", 0) or 0)
                except (TypeError, ValueError) as e:
                    series.errors.append(f"oi parse: {e}")
                    break

        # 4) LSR global (1H period)
        if isinstance(lsr_global, Exception):
            series.errors.append(f"lsr_global: {lsr_global}")
        elif isinstance(lsr_global, list):
            for row in lsr_global:
                try:
                    ts = int(row.get("timestamp", 0))
                    hour = _floor_hour_ms(ts)
                    bar = bars.setdefault(hour, SeriesBar(ts_ms=hour))
                    bar.ls_ratio_global = float(row.get("longShortRatio", 0) or 0)
                except (TypeError, ValueError) as e:
                    series.errors.append(f"lsr_global parse: {e}")
                    break

        # 5) Taker buy/sell ratio (1H period) — kline taker_buy_quote 측 우선, 보조 박힘.
        if isinstance(taker_lsr, Exception):
            series.errors.append(f"taker_lsr: {taker_lsr}")
        elif isinstance(taker_lsr, list):
            for row in taker_lsr:
                try:
                    ts = int(row.get("timestamp", 0))
                    hour = _floor_hour_ms(ts)
                    bar = bars.setdefault(hour, SeriesBar(ts_ms=hour))
                    if bar.taker_buy_usd is None and bar.close is not None:
                        buy_vol = float(row.get("buyVol", 0) or 0)
                        sell_vol = float(row.get("sellVol", 0) or 0)
                        bar.taker_buy_usd = buy_vol * bar.close
                        bar.taker_sell_usd = sell_vol * bar.close
                except (TypeError, ValueError) as e:
                    series.errors.append(f"taker_lsr parse: {e}")
                    break

        # 정렬 + 마지막 N 개 (1H × days × 24 박힘)
        sorted_bars = sorted(bars.values(), key=lambda b: b.ts_ms)
        series.bars = sorted_bars[-(days * 24):]

        if series.errors:
            logger.debug(
                "Binance series %s 부분 실패: %s",
                symbol, "; ".join(series.errors[:3]),
            )
        return series

    # ============================================================
    # endpoint sub-methods
    # ============================================================

    async def _get_json(
        self, session: aiohttp.ClientSession, path: str, params: dict,
    ):
        url = f"{_FAPI_BASE}{path}"
        async with session.get(url, params=params, timeout=_HTTP_TIMEOUT) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _fetch_klines(self, session, symbol: str, days: int) -> list:
        # v0.3.4: 1H × days × 24, max 500 (Binance public limit)
        limit = min(500, days * 24)
        return await self._get_json(
            session, "/fapi/v1/klines",
            {"symbol": symbol, "interval": "1h", "limit": limit},
        )

    async def _fetch_funding(self, session, symbol: str, days: int) -> list:
        # 8h funding × 3/day × days + buffer
        limit = min(1000, days * 3 + 10)
        return await self._get_json(
            session, "/fapi/v1/fundingRate",
            {"symbol": symbol, "limit": limit},
        )

    async def _fetch_oi_hist(self, session, symbol: str, days: int) -> list:
        # v0.3.4: 1H period × days × 24, max 500
        limit = min(500, days * 24)
        return await self._get_json(
            session, "/futures/data/openInterestHist",
            {"symbol": symbol, "period": "1h", "limit": limit},
        )

    async def _fetch_lsr_global(self, session, symbol: str, days: int) -> list:
        limit = min(500, days * 24)
        return await self._get_json(
            session, "/futures/data/globalLongShortAccountRatio",
            {"symbol": symbol, "period": "1h", "limit": limit},
        )

    async def _fetch_taker_lsr(self, session, symbol: str, days: int) -> list:
        limit = min(500, days * 24)
        return await self._get_json(
            session, "/futures/data/takerlongshortRatio",
            {"symbol": symbol, "period": "1h", "limit": limit},
        )


__all__ = ["BinanceSeriesProvider", "_DAY_MS", "_HOUR_MS", "_floor_day_ms", "_floor_hour_ms"]
