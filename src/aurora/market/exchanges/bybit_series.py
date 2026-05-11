"""Bybit V5 14D 시계열 fetcher (v0.3.4: 1H interval).

Public endpoints (API 키 X):
- ``/v5/market/kline?category=linear&interval=60&limit=336`` — 1H kline
- ``/v5/market/funding/history?category=linear&limit=50`` — funding (forward-fill)
- ``/v5/market/open-interest?category=linear&intervalTime=1h&limit=336`` — OI 1H
- ``/v5/market/account-ratio?category=linear&period=1h&limit=336`` — LSR 1H

Bybit V5 측 top trader 측 history endpoint X — ls_ratio_top_* 측 None 유지.
Taker buy/sell 측 endpoint X — None (kline 측 분리 X).
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from aurora.market.exchanges.binance_series import _HOUR_MS, _floor_hour_ms
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

        # 1) klines — V5 list = [start_ms, open, high, low, close, volume, turnover] (1H bar)
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
                    bar.volume_usd = float(k[6])  # turnover (quote)
                except (TypeError, ValueError, IndexError) as e:
                    series.errors.append(f"kline parse: {e}")
                    break

        # 2) funding — 8h 단위 funding → 1H bucket forward-fill (8h 동안 적용)
        if isinstance(funding, Exception):
            series.errors.append(f"funding: {funding}")
        elif isinstance(funding, list):
            funding_points: list[tuple[int, float]] = []
            for row in funding:
                try:
                    ts = int(row.get("fundingRateTimestamp", 0))
                    rate = float(row.get("fundingRate", 0) or 0)
                    funding_points.append((ts, rate))
                except (TypeError, ValueError) as e:
                    series.errors.append(f"funding parse: {e}")
                    break
            funding_points.sort(key=lambda x: x[0])
            for hour, bar in bars.items():
                latest_rate: float | None = None
                for ts, rate in funding_points:
                    if ts <= hour + _HOUR_MS:
                        latest_rate = rate
                    else:
                        break
                if latest_rate is not None:
                    bar.funding_rate_avg = latest_rate

        # 3) OI history — list 측 [{openInterest, timestamp}] (1H)
        if isinstance(oi_hist, Exception):
            series.errors.append(f"oi_hist: {oi_hist}")
        elif isinstance(oi_hist, list):
            for row in oi_hist:
                try:
                    ts = int(row.get("timestamp", 0))
                    hour = _floor_hour_ms(ts)
                    bar = bars.setdefault(hour, SeriesBar(ts_ms=hour))
                    # openInterest 측 contracts (BTC) — close 곱 USD 환산
                    oi_btc = float(row.get("openInterest", 0) or 0)
                    if bar.close is not None:
                        bar.oi_usd = oi_btc * bar.close
                except (TypeError, ValueError) as e:
                    series.errors.append(f"oi parse: {e}")
                    break

        # 4) LSR (account) — list 측 buyRatio/sellRatio + timestamp (1H)
        if isinstance(lsr, Exception):
            series.errors.append(f"lsr: {lsr}")
        elif isinstance(lsr, list):
            for row in lsr:
                try:
                    ts = int(row.get("timestamp", 0))
                    hour = _floor_hour_ms(ts)
                    bar = bars.setdefault(hour, SeriesBar(ts_ms=hour))
                    buy = float(row.get("buyRatio", 0) or 0)
                    sell = float(row.get("sellRatio", 0) or 0)
                    if sell > 0:
                        bar.ls_ratio_global = buy / sell
                except (TypeError, ValueError, ZeroDivisionError) as e:
                    series.errors.append(f"lsr parse: {e}")
                    break

        sorted_bars = sorted(bars.values(), key=lambda b: b.ts_ms)
        series.bars = sorted_bars[-(days * 24):]

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
        # v0.3.4: interval=60 (1H), limit max 1000
        limit = min(1000, days * 24)
        data = await self._get_json(
            session, "/v5/market/kline",
            {"category": "linear", "symbol": symbol, "interval": "60", "limit": limit},
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
        # v0.3.4: intervalTime=1h, limit max 200
        limit = min(200, days * 24)
        data = await self._get_json(
            session, "/v5/market/open-interest",
            {
                "category": "linear", "symbol": symbol,
                "intervalTime": "1h", "limit": limit,
            },
        )
        if data.get("retCode") != 0:
            raise RuntimeError(f"bybit ret={data.get('retCode')} msg={data.get('retMsg')}")
        return (data.get("result") or {}).get("list") or []

    async def _fetch_lsr(self, session, symbol: str, days: int) -> list:
        # v0.3.4: period=1h, limit max 500
        limit = min(500, days * 24)
        data = await self._get_json(
            session, "/v5/market/account-ratio",
            {"category": "linear", "symbol": symbol, "period": "1h", "limit": limit},
        )
        if data.get("retCode") != 0:
            raise RuntimeError(f"bybit ret={data.get('retCode')} msg={data.get('retMsg')}")
        return (data.get("result") or {}).get("list") or []


__all__ = ["BybitSeriesProvider"]
