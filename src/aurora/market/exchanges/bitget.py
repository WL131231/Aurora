"""Bitget V2 mix (futures) public market data fetcher (v0.1.89).

Public endpoints (API 키 X):
- ``/api/v2/mix/market/ticker?productType=usdt-futures&symbol=BTCUSDT``
  → lastPr / change24h / quoteVolume / holdingAmount (OI in coin) / fundingRate
- ``/api/v2/mix/market/account-long-short?productType=usdt-futures&symbol=BTCUSDT&period=5m``
  → longAccountRatio / shortAccountRatio (account 기준)

Bitget 측 top trader 별도 endpoint X — ls_ratio_top_* 는 None 유지.
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from aurora.market.exchanges.base import ExchangeMarketData, ExchangeSnapshot
from aurora.timeouts import make_exchange_timeout

logger = logging.getLogger(__name__)

_BITGET_BASE = "https://api.bitget.com"
_HTTP_TIMEOUT = make_exchange_timeout()
_LS_PERIOD = "5m"
_PRODUCT_TYPE = "usdt-futures"
# v0.3.1: Whale 측 Binance 측 동일 정합
_WHALE_THRESHOLD_USD = 100_000.0
_WHALE_WINDOW_MS = 5 * 60 * 1000


class BitgetMarketData(ExchangeMarketData):
    """Bitget V2 USDT-futures public endpoint 시장 자료."""

    EXCHANGE_NAME = "bitget"

    async def fetch_snapshot(
        self,
        session: aiohttp.ClientSession,
        coin: str,
    ) -> ExchangeSnapshot:
        symbol = self.symbol_for(coin)
        snap = ExchangeSnapshot(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            fetched_at_ms=int(time.time() * 1000),
        )

        # v0.3.1: recent fills 측 추가 (Whale notional)
        results = await asyncio.gather(
            self._fetch_ticker(session, symbol),
            self._fetch_ls(session, symbol),
            self._fetch_recent_trades(session, symbol),
            return_exceptions=True,
        )
        ticker, ls, recent_trades = results

        # Ticker — price / 24h / OI / funding 모두 박힘
        if isinstance(ticker, Exception):
            snap.errors.append(f"ticker: {ticker}")
        elif isinstance(ticker, dict):
            try:
                snap.price = float(ticker.get("lastPr", "0") or 0)
                # change24h 박힘 = "0.015" 형식 (1.5%)
                pct = float(ticker.get("change24h", "0") or 0)
                snap.price_24h_change_pct = pct * 100.0
                snap.volume_24h_usd = float(ticker.get("quoteVolume", "0") or 0)
                # holdingAmount = OI in coin (BTC) — × price = USD notional
                holding = float(ticker.get("holdingAmount", "0") or 0)
                if snap.price > 0:
                    snap.oi_usd = holding * snap.price
                snap.funding_rate = float(ticker.get("fundingRate", "0") or 0)
            except (TypeError, ValueError) as e:
                snap.errors.append(f"ticker calc: {e}")

        # L-S ratio (account)
        if isinstance(ls, Exception):
            snap.errors.append(f"ls: {ls}")
        elif isinstance(ls, dict):
            try:
                long_ratio = float(ls.get("longAccountRatio", "0") or 0)
                short_ratio = float(ls.get("shortAccountRatio", "0") or 0)
                snap.long_account_pct = long_ratio
                snap.short_account_pct = short_ratio
                if short_ratio > 0:
                    snap.ls_ratio_global = long_ratio / short_ratio
            except (TypeError, ValueError, ZeroDivisionError) as e:
                snap.errors.append(f"ls calc: {e}")

        # v0.3.1: Whale notional — recent fills 측 5분 윈도우 측 ≥ $100K 합산
        snap.whale_threshold_usd = _WHALE_THRESHOLD_USD
        now_ms = int(time.time() * 1000)
        window_start = now_ms - _WHALE_WINDOW_MS
        if isinstance(recent_trades, Exception):
            snap.errors.append(f"recent_trades: {recent_trades}")
        elif isinstance(recent_trades, list):
            try:
                buy_sum = 0.0
                sell_sum = 0.0
                count = 0
                for t in recent_trades:
                    ts = int(t.get("ts", 0) or 0)
                    if ts < window_start:
                        continue
                    price = float(t.get("price", 0) or 0)
                    size = float(t.get("size", 0) or 0)
                    notional = price * size
                    if notional < _WHALE_THRESHOLD_USD:
                        continue
                    count += 1
                    # Bitget side: "buy"/"sell"
                    if t.get("side") == "buy":
                        buy_sum += notional
                    else:
                        sell_sum += notional
                snap.whale_buy_5m_usd = buy_sum
                snap.whale_sell_5m_usd = sell_sum
                snap.whale_count_5m = count
            except (TypeError, ValueError) as e:
                snap.errors.append(f"recent_trades calc: {e}")

        if snap.errors:
            logger.debug("Bitget snapshot %s 부분 실패: %s", symbol, "; ".join(snap.errors))
        return snap

    async def _fetch_recent_trades(self, session, symbol: str) -> list:
        """v0.3.1: V2 fills-history — 100 recent fills.

        Response: data = [{ tradeId, price, size, side("buy"/"sell"), ts(ms), ... }]
        """
        data = await self._get_json(
            session, "/api/v2/mix/market/fills",
            {"productType": _PRODUCT_TYPE, "symbol": symbol, "limit": "100"},
        )
        if data.get("code") != "00000":
            raise RuntimeError(f"bitget code={data.get('code')} msg={data.get('msg')}")
        return data.get("data") or []

    # ============================================================
    # endpoint sub-methods
    # ============================================================

    async def _get_json(
        self, session: aiohttp.ClientSession, path: str, params: dict,
    ) -> dict:
        url = f"{_BITGET_BASE}{path}"
        async with session.get(url, params=params, timeout=_HTTP_TIMEOUT) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _fetch_ticker(self, session, symbol: str) -> dict | None:
        data = await self._get_json(
            session, "/api/v2/mix/market/ticker",
            {"productType": _PRODUCT_TYPE, "symbol": symbol},
        )
        if data.get("code") != "00000":
            raise RuntimeError(f"bitget code={data.get('code')} msg={data.get('msg')}")
        rows = data.get("data") or []
        return rows[0] if rows else None

    async def _fetch_ls(self, session, symbol: str) -> dict | None:
        data = await self._get_json(
            session, "/api/v2/mix/market/account-long-short",
            {"productType": _PRODUCT_TYPE, "symbol": symbol, "period": _LS_PERIOD},
        )
        if data.get("code") != "00000":
            raise RuntimeError(f"bitget code={data.get('code')} msg={data.get('msg')}")
        rows = data.get("data") or []
        # rows = [{ts, longAccountRatio, shortAccountRatio}] — 가장 최근
        if isinstance(rows, list) and rows:
            return rows[-1]
        return None
