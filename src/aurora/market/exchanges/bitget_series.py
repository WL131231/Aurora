"""Bitget V2 mix (futures) 14D 시계열 fetcher (v0.1.115).

Public endpoints (API 키 X):
- ``/api/v2/mix/market/candles?productType=usdt-futures&granularity=1D&limit=14`` — kline
- ``/api/v2/mix/market/history-fund-rate?symbol=&pageSize=100`` — funding history
- ``/api/v2/mix/market/account-long-short?period=1d&limit=14`` — LSR (account)

OI history endpoint 측 v2 doc 측 미박힘 — bars[i].oi_usd 측 None, ``errors`` 측 사유 박음.
Top trader / taker volume / spot 측 endpoint X — None.
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from aurora.market.exchanges.binance_series import _floor_day_ms
from aurora.market.exchanges.series_base import (
    ExchangeSeries,
    ExchangeSeriesProvider,
    SeriesBar,
)
from aurora.timeouts import make_exchange_timeout

logger = logging.getLogger(__name__)

_BITGET_BASE = "https://api.bitget.com"
_HTTP_TIMEOUT = make_exchange_timeout()
_PRODUCT_TYPE = "usdt-futures"
_DAY_MS = 86_400_000


class BitgetSeriesProvider(ExchangeSeriesProvider):
    """Bitget V2 USDT-futures 14D 시계열."""

    EXCHANGE_NAME = "bitget"

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

        # OI history 측 v2 미지원 — 빈 list 측 placeholder
        series.errors.append("oi_hist: not supported (Bitget v2)")
        series.errors.append("taker_vol: not supported (Bitget v2)")

        results = await asyncio.gather(
            self._fetch_candles(session, symbol, days),
            self._fetch_funding(session, symbol, days),
            self._fetch_lsr(session, symbol, days),
            return_exceptions=True,
        )
        candles, funding, lsr = results

        bars: dict[int, SeriesBar] = {}

        # 1) candles — Bitget 측 [ts, o, h, l, c, baseVol, quoteVol]
        if isinstance(candles, Exception):
            series.errors.append(f"candles: {candles}")
        elif isinstance(candles, list):
            for k in candles:
                try:
                    open_ms = int(k[0])
                    day = _floor_day_ms(open_ms)
                    bar = bars.setdefault(day, SeriesBar(ts_ms=day))
                    bar.open = float(k[1])
                    bar.high = float(k[2])
                    bar.low = float(k[3])
                    bar.close = float(k[4])
                    if len(k) >= 7:
                        bar.volume_usd = float(k[6] or 0)
                except (TypeError, ValueError, IndexError) as e:
                    series.errors.append(f"candle parse: {e}")
                    break

        # 2) funding — list 측 fundingTime / fundingRate (8h)
        if isinstance(funding, Exception):
            series.errors.append(f"funding: {funding}")
        elif isinstance(funding, list):
            day_buckets: dict[int, list[float]] = {}
            for row in funding:
                try:
                    ts = int(row.get("fundingTime", 0))
                    rate = float(row.get("fundingRate", 0) or 0)
                    day = _floor_day_ms(ts)
                    day_buckets.setdefault(day, []).append(rate)
                except (TypeError, ValueError) as e:
                    series.errors.append(f"funding parse: {e}")
                    break
            for day, rates in day_buckets.items():
                if not rates:
                    continue
                bar = bars.setdefault(day, SeriesBar(ts_ms=day))
                bar.funding_rate_avg = sum(rates) / len(rates)

        # 3) LSR — list 측 [{ts, longAccountRatio, shortAccountRatio}]
        if isinstance(lsr, Exception):
            series.errors.append(f"lsr: {lsr}")
        elif isinstance(lsr, list):
            for row in lsr:
                try:
                    ts = int(row.get("ts", 0))
                    day = _floor_day_ms(ts)
                    bar = bars.setdefault(day, SeriesBar(ts_ms=day))
                    long_ratio = float(row.get("longAccountRatio", 0) or 0)
                    short_ratio = float(row.get("shortAccountRatio", 0) or 0)
                    if short_ratio > 0:
                        bar.ls_ratio_global = long_ratio / short_ratio
                except (TypeError, ValueError, ZeroDivisionError) as e:
                    series.errors.append(f"lsr parse: {e}")
                    break

        sorted_bars = sorted(bars.values(), key=lambda b: b.ts_ms)
        series.bars = sorted_bars[-days:]

        if series.errors:
            logger.debug(
                "Bitget series %s 부분: %s",
                symbol, "; ".join(series.errors[:3]),
            )
        return series

    # ============================================================

    async def _get_json(
        self, session: aiohttp.ClientSession, path: str, params: dict,
    ) -> dict:
        url = f"{_BITGET_BASE}{path}"
        async with session.get(url, params=params, timeout=_HTTP_TIMEOUT) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _fetch_candles(self, session, symbol: str, days: int) -> list:
        # Bitget 측 startTime / endTime 측 ms — 14d 박음
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - (days + 1) * _DAY_MS
        data = await self._get_json(
            session, "/api/v2/mix/market/candles",
            {
                "productType": _PRODUCT_TYPE, "symbol": symbol,
                "granularity": "1D", "limit": str(days),
                "startTime": str(start_ms), "endTime": str(end_ms),
            },
        )
        if data.get("code") != "00000":
            raise RuntimeError(f"bitget code={data.get('code')} msg={data.get('msg')}")
        return data.get("data") or []

    async def _fetch_funding(self, session, symbol: str, days: int) -> list:
        # 8h × 3 × N
        page_size = min(100, days * 3 + 10)
        data = await self._get_json(
            session, "/api/v2/mix/market/history-fund-rate",
            {
                "productType": _PRODUCT_TYPE, "symbol": symbol,
                "pageSize": str(page_size),
            },
        )
        if data.get("code") != "00000":
            raise RuntimeError(f"bitget code={data.get('code')} msg={data.get('msg')}")
        return data.get("data") or []

    async def _fetch_lsr(self, session, symbol: str, days: int) -> list:
        data = await self._get_json(
            session, "/api/v2/mix/market/account-long-short",
            {
                "productType": _PRODUCT_TYPE, "symbol": symbol,
                "period": "1d", "limit": str(days),
            },
        )
        if data.get("code") != "00000":
            raise RuntimeError(f"bitget code={data.get('code')} msg={data.get('msg')}")
        return data.get("data") or []


__all__ = ["BitgetSeriesProvider"]
