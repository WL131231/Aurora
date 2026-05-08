"""Binance Futures (USDM) 14D 시계열 fetcher (v0.1.115).

Phase 3 Dashboard view 측 신규 사양 — 14 일봉 + funding + OI + LSR + taker delta.
``BinanceMarketData`` (snapshot) 측 별개. base URL / timeout 측 재사용.

Public endpoints (API 키 X):
- ``/fapi/v1/klines?interval=1d&limit=14`` — kline 14봉
- ``/fapi/v1/fundingRate?symbol=&limit=42`` — 8h × 3 × 14d funding history (1d agg)
- ``/futures/data/openInterestHist?period=1d&limit=14`` — OI history
- ``/futures/data/globalLongShortAccountRatio?period=1d&limit=14`` — LSR global
- ``/futures/data/takerlongshortRatio?period=1d&limit=14`` — taker buy/sell

Top trader LSR (position/account) 측 daily history 측 endpoint 별도 — v0.1.115 측
range 절감 위해 global 만 박음 (top 측 5단 ratio 측 snapshot 측 처리).
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


def _floor_day_ms(ts_ms: int) -> int:
    """ts_ms 측 UTC 00:00 으로 floor."""
    return (ts_ms // _DAY_MS) * _DAY_MS


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

        # day_ms → SeriesBar
        bars: dict[int, SeriesBar] = {}

        # 1) klines — base + price + volume + taker buy
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
                    bar.volume_usd = float(k[7])      # quote volume
                    bar.taker_buy_usd = float(k[10])  # taker buy quote vol
                    if bar.volume_usd is not None and bar.taker_buy_usd is not None:
                        bar.taker_sell_usd = bar.volume_usd - bar.taker_buy_usd
                except (TypeError, ValueError, IndexError) as e:
                    series.errors.append(f"kline parse: {e}")
                    break

        # 2) funding history — 8h × 3 × N day → daily 평균
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

        # 3) OI history
        if isinstance(oi_hist, Exception):
            series.errors.append(f"oi_hist: {oi_hist}")
        elif isinstance(oi_hist, list):
            for row in oi_hist:
                try:
                    ts = int(row.get("timestamp", 0))
                    day = _floor_day_ms(ts)
                    bar = bars.setdefault(day, SeriesBar(ts_ms=day))
                    bar.oi_usd = float(row.get("sumOpenInterestValue", 0) or 0)
                except (TypeError, ValueError) as e:
                    series.errors.append(f"oi parse: {e}")
                    break

        # 4) LSR global
        if isinstance(lsr_global, Exception):
            series.errors.append(f"lsr_global: {lsr_global}")
        elif isinstance(lsr_global, list):
            for row in lsr_global:
                try:
                    ts = int(row.get("timestamp", 0))
                    day = _floor_day_ms(ts)
                    bar = bars.setdefault(day, SeriesBar(ts_ms=day))
                    bar.ls_ratio_global = float(row.get("longShortRatio", 0) or 0)
                except (TypeError, ValueError) as e:
                    series.errors.append(f"lsr_global parse: {e}")
                    break

        # 5) Taker buy/sell ratio — kline 측 taker_buy 측 perp, 이건 보강 (futures 데이터)
        # buyVol / sellVol 측 endpoint 측 base 단위 (BTC) — kline taker_buy_quote 측 우선.
        # 보조 박힘 (kline 측 taker_buy 비어있을 때만).
        if isinstance(taker_lsr, Exception):
            series.errors.append(f"taker_lsr: {taker_lsr}")
        elif isinstance(taker_lsr, list):
            for row in taker_lsr:
                try:
                    ts = int(row.get("timestamp", 0))
                    day = _floor_day_ms(ts)
                    bar = bars.setdefault(day, SeriesBar(ts_ms=day))
                    if bar.taker_buy_usd is None and bar.close is not None:
                        buy_vol = float(row.get("buyVol", 0) or 0)
                        sell_vol = float(row.get("sellVol", 0) or 0)
                        bar.taker_buy_usd = buy_vol * bar.close
                        bar.taker_sell_usd = sell_vol * bar.close
                except (TypeError, ValueError) as e:
                    series.errors.append(f"taker_lsr parse: {e}")
                    break

        # 정렬 + 마지막 N 개
        sorted_bars = sorted(bars.values(), key=lambda b: b.ts_ms)
        series.bars = sorted_bars[-days:]

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
        return await self._get_json(
            session, "/fapi/v1/klines",
            {"symbol": symbol, "interval": "1d", "limit": days},
        )

    async def _fetch_funding(self, session, symbol: str, days: int) -> list:
        # 8h funding × 3/day × days + buffer
        limit = min(1000, days * 3 + 10)
        return await self._get_json(
            session, "/fapi/v1/fundingRate",
            {"symbol": symbol, "limit": limit},
        )

    async def _fetch_oi_hist(self, session, symbol: str, days: int) -> list:
        return await self._get_json(
            session, "/futures/data/openInterestHist",
            {"symbol": symbol, "period": "1d", "limit": days},
        )

    async def _fetch_lsr_global(self, session, symbol: str, days: int) -> list:
        return await self._get_json(
            session, "/futures/data/globalLongShortAccountRatio",
            {"symbol": symbol, "period": "1d", "limit": days},
        )

    async def _fetch_taker_lsr(self, session, symbol: str, days: int) -> list:
        return await self._get_json(
            session, "/futures/data/takerlongshortRatio",
            {"symbol": symbol, "period": "1d", "limit": days},
        )


__all__ = ["BinanceSeriesProvider", "_DAY_MS", "_floor_day_ms"]
