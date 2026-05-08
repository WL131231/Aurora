"""OKX V5 14D 시계열 fetcher (v0.1.115).

Public endpoints (API 키 X):
- ``/api/v5/market/candles?bar=1D&limit=14`` — daily candle (8 columns)
- ``/api/v5/public/funding-rate-history?instId=&limit=400`` — funding history (8h)
- ``/api/v5/rubik/stat/contracts/open-interest-volume?ccy=BTC&period=1D`` — OI hist
- ``/api/v5/rubik/stat/contracts/long-short-account-ratio?period=1D&limit=14`` — LSR
- ``/api/v5/rubik/stat/taker-volume?ccy=BTC&instType=CONTRACTS&period=1D`` — taker
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

_OKX_BASE = "https://www.okx.com"
_HTTP_TIMEOUT = make_exchange_timeout()


class OkxSeriesProvider(ExchangeSeriesProvider):
    """OKX V5 SWAP (USDT linear perp) 14D 시계열."""

    EXCHANGE_NAME = "okx"

    def symbol_for(self, coin: str) -> str:
        return f"{coin}-USDT-SWAP"

    async def fetch_series(
        self,
        session: aiohttp.ClientSession,
        coin: str,
        days: int = 14,
    ) -> ExchangeSeries:
        inst_id = self.symbol_for(coin)
        series = ExchangeSeries(
            exchange=self.EXCHANGE_NAME,
            symbol=inst_id,
            coin=coin,
            days=days,
        )

        results = await asyncio.gather(
            self._fetch_candles(session, inst_id, days),
            self._fetch_funding(session, inst_id, days),
            self._fetch_oi_hist(session, coin, days),
            self._fetch_lsr(session, coin, days),
            self._fetch_taker(session, coin, days),
            return_exceptions=True,
        )
        candles, funding, oi_hist, lsr, taker = results

        bars: dict[int, SeriesBar] = {}

        # 1) candles — [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
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
                    # volCcyQuote (USDT) — 일부 구버전 측 7번 idx 비어있을 수 있음
                    if len(k) >= 8:
                        bar.volume_usd = float(k[7] or 0)
                except (TypeError, ValueError, IndexError) as e:
                    series.errors.append(f"candle parse: {e}")
                    break

        # 2) funding history — list 측 fundingTime / fundingRate (8h)
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

        # 3) OI history — [ts, oi_ccy, oi_usd]
        if isinstance(oi_hist, Exception):
            series.errors.append(f"oi_hist: {oi_hist}")
        elif isinstance(oi_hist, list):
            for row in oi_hist:
                try:
                    ts = int(row[0])
                    day = _floor_day_ms(ts)
                    bar = bars.setdefault(day, SeriesBar(ts_ms=day))
                    bar.oi_usd = float(row[2] or 0)
                except (TypeError, ValueError, IndexError) as e:
                    series.errors.append(f"oi parse: {e}")
                    break

        # 4) LSR — [ts, ratio]
        if isinstance(lsr, Exception):
            series.errors.append(f"lsr: {lsr}")
        elif isinstance(lsr, list):
            for row in lsr:
                try:
                    ts = int(row[0])
                    day = _floor_day_ms(ts)
                    bar = bars.setdefault(day, SeriesBar(ts_ms=day))
                    bar.ls_ratio_global = float(row[1] or 0)
                except (TypeError, ValueError, IndexError) as e:
                    series.errors.append(f"lsr parse: {e}")
                    break

        # 5) taker volume — [ts, sellVol, buyVol]
        if isinstance(taker, Exception):
            series.errors.append(f"taker: {taker}")
        elif isinstance(taker, list):
            for row in taker:
                try:
                    ts = int(row[0])
                    day = _floor_day_ms(ts)
                    bar = bars.setdefault(day, SeriesBar(ts_ms=day))
                    sell_vol = float(row[1] or 0)
                    buy_vol = float(row[2] or 0)
                    if bar.close is not None:
                        bar.taker_buy_usd = buy_vol * bar.close
                        bar.taker_sell_usd = sell_vol * bar.close
                except (TypeError, ValueError, IndexError) as e:
                    series.errors.append(f"taker parse: {e}")
                    break

        sorted_bars = sorted(bars.values(), key=lambda b: b.ts_ms)
        series.bars = sorted_bars[-days:]

        if series.errors:
            logger.debug(
                "OKX series %s 부분 실패: %s",
                inst_id, "; ".join(series.errors[:3]),
            )
        return series

    # ============================================================

    async def _get_json(
        self, session: aiohttp.ClientSession, path: str, params: dict,
    ) -> dict:
        url = f"{_OKX_BASE}{path}"
        async with session.get(url, params=params, timeout=_HTTP_TIMEOUT) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _fetch_candles(self, session, inst_id: str, days: int) -> list:
        data = await self._get_json(
            session, "/api/v5/market/candles",
            {"instId": inst_id, "bar": "1D", "limit": days},
        )
        if data.get("code") != "0":
            raise RuntimeError(f"okx code={data.get('code')} msg={data.get('msg')}")
        return data.get("data") or []

    async def _fetch_funding(self, session, inst_id: str, days: int) -> list:
        # 8h × 3 × N
        limit = min(400, days * 3 + 10)
        data = await self._get_json(
            session, "/api/v5/public/funding-rate-history",
            {"instId": inst_id, "limit": limit},
        )
        if data.get("code") != "0":
            raise RuntimeError(f"okx code={data.get('code')} msg={data.get('msg')}")
        return data.get("data") or []

    async def _fetch_oi_hist(self, session, coin: str, days: int) -> list:
        data = await self._get_json(
            session, "/api/v5/rubik/stat/contracts/open-interest-volume",
            {"ccy": coin, "period": "1D"},
        )
        if data.get("code") != "0":
            raise RuntimeError(f"okx code={data.get('code')} msg={data.get('msg')}")
        rows = data.get("data") or []
        # 최신 N 개 (rubik 측 desc 박혀있음)
        return rows[:days]

    async def _fetch_lsr(self, session, coin: str, days: int) -> list:
        data = await self._get_json(
            session, "/api/v5/rubik/stat/contracts/long-short-account-ratio",
            {"ccy": coin, "period": "1D"},
        )
        if data.get("code") != "0":
            raise RuntimeError(f"okx code={data.get('code')} msg={data.get('msg')}")
        rows = data.get("data") or []
        return rows[:days]

    async def _fetch_taker(self, session, coin: str, days: int) -> list:
        data = await self._get_json(
            session, "/api/v5/rubik/stat/taker-volume",
            {"ccy": coin, "instType": "CONTRACTS", "period": "1D"},
        )
        if data.get("code") != "0":
            raise RuntimeError(f"okx code={data.get('code')} msg={data.get('msg')}")
        rows = data.get("data") or []
        return rows[:days]


__all__ = ["OkxSeriesProvider"]
