"""Bybit V5 14D 시계열 fetcher (v0.1.115).

Public endpoints (API 키 X):
- ``/v5/market/kline?category=linear&interval=D&limit=14`` — daily kline
- ``/v5/market/funding/history?category=linear&limit=200`` — funding history
- ``/v5/market/open-interest?category=linear&intervalTime=1d&limit=14`` — OI hist
- ``/v5/market/account-ratio?category=linear&period=1d&limit=14`` — LSR (account)

Bybit V5 측 top trader 측 history endpoint X — ls_ratio_top_* 측 None 유지.
Taker buy/sell 측 endpoint X — None (kline 측 분리 X).
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from aurora.market.exchanges.binance_series import _floor_day_ms
from aurora.market.exchanges.series_base import (
    ExchangeSeries,
    ExchangeSeriesProvider,
    SeriesBar,
)
from aurora.timeouts import make_exchange_timeout

logger = logging.getLogger(__name__)

_BYBIT_BASE = "https://api.bybit.com"
_HTTP_TIMEOUT = make_exchange_timeout()


class BybitSeriesProvider(ExchangeSeriesProvider):
    """Bybit V5 linear (USDT-perp) 14D 시계열."""

    EXCHANGE_NAME = "bybit"

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

        results = await asyncio.gather(
            self._fetch_klines(session, symbol, days),
            self._fetch_funding(session, symbol, days),
            self._fetch_oi_hist(session, symbol, days),
            self._fetch_lsr(session, symbol, days),
            return_exceptions=True,
        )
        klines, funding, oi_hist, lsr = results

        bars: dict[int, SeriesBar] = {}

        # 1) klines — V5 list = [start_ms, open, high, low, close, volume, turnover]
        if isinstance(klines, Exception):
            series.errors.append(f"klines: {klines}")
        elif isinstance(klines, list):
            for k in klines:
                try:
                    open_ms = int(k[0])
                    day = _floor_day_ms(open_ms)
                    bar = bars.setdefault(day, SeriesBar(ts_ms=day))
                    bar.open = float(k[1])
                    bar.high = float(k[2])
                    bar.low = float(k[3])
                    bar.close = float(k[4])
                    bar.volume_usd = float(k[6])  # turnover (quote)
                except (TypeError, ValueError, IndexError) as e:
                    series.errors.append(f"kline parse: {e}")
                    break

        # 2) funding — list 측 fundingRateTimestamp / fundingRate
        if isinstance(funding, Exception):
            series.errors.append(f"funding: {funding}")
        elif isinstance(funding, list):
            day_buckets: dict[int, list[float]] = {}
            for row in funding:
                try:
                    ts = int(row.get("fundingRateTimestamp", 0))
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

        # 3) OI history — list 측 [{openInterest, timestamp}]
        if isinstance(oi_hist, Exception):
            series.errors.append(f"oi_hist: {oi_hist}")
        elif isinstance(oi_hist, list):
            for row in oi_hist:
                try:
                    ts = int(row.get("timestamp", 0))
                    day = _floor_day_ms(ts)
                    bar = bars.setdefault(day, SeriesBar(ts_ms=day))
                    # openInterest 측 contracts (BTC) — close 곱 USD 환산
                    oi_btc = float(row.get("openInterest", 0) or 0)
                    if bar.close is not None:
                        bar.oi_usd = oi_btc * bar.close
                except (TypeError, ValueError) as e:
                    series.errors.append(f"oi parse: {e}")
                    break

        # 4) LSR (account) — list 측 buyRatio/sellRatio + timestamp
        if isinstance(lsr, Exception):
            series.errors.append(f"lsr: {lsr}")
        elif isinstance(lsr, list):
            for row in lsr:
                try:
                    ts = int(row.get("timestamp", 0))
                    day = _floor_day_ms(ts)
                    bar = bars.setdefault(day, SeriesBar(ts_ms=day))
                    buy = float(row.get("buyRatio", 0) or 0)
                    sell = float(row.get("sellRatio", 0) or 0)
                    if sell > 0:
                        bar.ls_ratio_global = buy / sell
                except (TypeError, ValueError, ZeroDivisionError) as e:
                    series.errors.append(f"lsr parse: {e}")
                    break

        sorted_bars = sorted(bars.values(), key=lambda b: b.ts_ms)
        series.bars = sorted_bars[-days:]

        if series.errors:
            logger.debug(
                "Bybit series %s 부분 실패: %s",
                symbol, "; ".join(series.errors[:3]),
            )
        return series

    # ============================================================

    async def _get_json(
        self, session: aiohttp.ClientSession, path: str, params: dict,
    ) -> dict:
        url = f"{_BYBIT_BASE}{path}"
        async with session.get(url, params=params, timeout=_HTTP_TIMEOUT) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _fetch_klines(self, session, symbol: str, days: int) -> list:
        data = await self._get_json(
            session, "/v5/market/kline",
            {"category": "linear", "symbol": symbol, "interval": "D", "limit": days},
        )
        if data.get("retCode") != 0:
            raise RuntimeError(f"bybit ret={data.get('retCode')} msg={data.get('retMsg')}")
        return (data.get("result") or {}).get("list") or []

    async def _fetch_funding(self, session, symbol: str, days: int) -> list:
        # 8h funding × 3/day × N
        limit = min(200, days * 3 + 10)
        data = await self._get_json(
            session, "/v5/market/funding/history",
            {"category": "linear", "symbol": symbol, "limit": limit},
        )
        if data.get("retCode") != 0:
            raise RuntimeError(f"bybit ret={data.get('retCode')} msg={data.get('retMsg')}")
        return (data.get("result") or {}).get("list") or []

    async def _fetch_oi_hist(self, session, symbol: str, days: int) -> list:
        data = await self._get_json(
            session, "/v5/market/open-interest",
            {
                "category": "linear", "symbol": symbol,
                "intervalTime": "1d", "limit": days,
            },
        )
        if data.get("retCode") != 0:
            raise RuntimeError(f"bybit ret={data.get('retCode')} msg={data.get('retMsg')}")
        return (data.get("result") or {}).get("list") or []

    async def _fetch_lsr(self, session, symbol: str, days: int) -> list:
        data = await self._get_json(
            session, "/v5/market/account-ratio",
            {"category": "linear", "symbol": symbol, "period": "1d", "limit": days},
        )
        if data.get("retCode") != 0:
            raise RuntimeError(f"bybit ret={data.get('retCode')} msg={data.get('retMsg')}")
        return (data.get("result") or {}).get("list") or []


__all__ = ["BybitSeriesProvider"]
