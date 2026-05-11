"""Hyperliquid 14D 시계열 fetcher (v0.3.4: 1H interval).

POST endpoints (API 키 X):
- ``POST /info {"type":"candleSnapshot","req":{"coin","interval":"1h",...}}``
  → 1H candle 336봉
- ``POST /info {"type":"fundingHistory","coin","startTime"}``
  → 1h 단위 funding history — 1H bucket 정합 박힘 (forward-fill 불필요)

OI history / LSR / taker 측 미지원 — bars[i].oi_usd / ls_ratio_global 측 None,
``errors`` 측 사유 박음.
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from aurora.market.exchanges.binance_series import _floor_hour_ms
from aurora.market.exchanges.series_base import (
    ExchangeSeries,
    ExchangeSeriesProvider,
    SeriesBar,
)
from aurora.timeouts import make_exchange_timeout

logger = logging.getLogger(__name__)

_HL_BASE = "https://api.hyperliquid.xyz"
_HTTP_TIMEOUT = make_exchange_timeout()
_DAY_MS = 86_400_000


class HyperliquidSeriesProvider(ExchangeSeriesProvider):
    """Hyperliquid 14D 시계열."""

    EXCHANGE_NAME = "hyperliquid"

    def symbol_for(self, coin: str) -> str:
        return coin  # HL 측 coin 자체

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

        # OI history / LSR 측 HL 측 미지원
        series.errors.append("oi_hist: not supported (Hyperliquid)")
        series.errors.append("lsr: not supported (Hyperliquid)")
        series.errors.append("taker_vol: not supported (Hyperliquid)")

        end_ms = int(time.time() * 1000)
        start_ms = end_ms - (days + 1) * _DAY_MS

        results = await asyncio.gather(
            self._fetch_candles(session, coin, start_ms, end_ms),
            self._fetch_funding(session, coin, start_ms),
            return_exceptions=True,
        )
        candles, funding = results

        bars: dict[int, SeriesBar] = {}

        # 1) candles — list 측 [{t (open ms), o, h, l, c, v, n}] (1H)
        if isinstance(candles, Exception):
            series.errors.append(f"candles: {candles}")
        elif isinstance(candles, list):
            for k in candles:
                try:
                    open_ms = int(k.get("t", 0))
                    hour = _floor_hour_ms(open_ms)
                    bar = bars.setdefault(hour, SeriesBar(ts_ms=hour))
                    bar.open = float(k.get("o") or 0)
                    bar.high = float(k.get("h") or 0)
                    bar.low = float(k.get("l") or 0)
                    bar.close = float(k.get("c") or 0)
                    # v 측 base volume (BTC) — × close = USD
                    base_vol = float(k.get("v") or 0)
                    if bar.close is not None:
                        bar.volume_usd = base_vol * bar.close
                except (TypeError, ValueError, AttributeError) as e:
                    series.errors.append(f"candle parse: {e}")
                    break

        # 2) funding — list 측 [{time, fundingRate, premium}] (1h 단위, 1H bucket 직접 매핑)
        if isinstance(funding, Exception):
            series.errors.append(f"funding: {funding}")
        elif isinstance(funding, list):
            for row in funding:
                try:
                    ts = int(row.get("time", 0))
                    rate = float(row.get("fundingRate", 0) or 0)
                    hour = _floor_hour_ms(ts)
                    bar = bars.setdefault(hour, SeriesBar(ts_ms=hour))
                    bar.funding_rate_avg = rate
                except (TypeError, ValueError) as e:
                    series.errors.append(f"funding parse: {e}")
                    break

        sorted_bars = sorted(bars.values(), key=lambda b: b.ts_ms)
        series.bars = sorted_bars[-(days * 24):]

        if series.errors:
            logger.debug(
                "Hyperliquid series %s 부분: %s",
                symbol, "; ".join(series.errors[:3]),
            )
        return series

    # ============================================================

    async def _post_info(
        self, session: aiohttp.ClientSession, body: dict,
    ):
        url = f"{_HL_BASE}/info"
        async with session.post(url, json=body, timeout=_HTTP_TIMEOUT) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _fetch_candles(
        self, session, coin: str, start_ms: int, end_ms: int,
    ) -> list:
        # v0.3.4: interval=1h 박음 — 14d × 24 = 336 봉
        body = {
            "type": "candleSnapshot",
            "req": {
                "coin": coin, "interval": "1h",
                "startTime": start_ms, "endTime": end_ms,
            },
        }
        return await self._post_info(session, body)

    async def _fetch_funding(self, session, coin: str, start_ms: int) -> list:
        body = {"type": "fundingHistory", "coin": coin, "startTime": start_ms}
        return await self._post_info(session, body)


__all__ = ["HyperliquidSeriesProvider"]
