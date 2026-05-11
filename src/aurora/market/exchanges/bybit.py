"""Bybit V5 public market data fetcher (v0.1.88 + v0.3.1 Whale).

Public endpoints (API 키 X):
- ``/v5/market/tickers?category=linear&symbol=BTCUSDT``
- ``/v5/market/account-ratio?category=linear&symbol=BTCUSDT&period=5min``
- v0.3.1: ``/v5/market/recent-trade?category=linear&symbol=BTCUSDT&limit=1000`` — Whale notional

Bybit V5 측 top trader 별도 endpoint X — ls_ratio_top_position / top_account 측 None.
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from aurora.market.exchanges.base import ExchangeMarketData, ExchangeSnapshot
from aurora.timeouts import make_exchange_timeout

logger = logging.getLogger(__name__)

_BYBIT_BASE = "https://api.bybit.com"
_HTTP_TIMEOUT = make_exchange_timeout()
_LS_PERIOD = "5min"
# v0.3.1: Whale 측 Binance 측 동일 정합 박음 — ≥ $100K, 5분 윈도우
_WHALE_THRESHOLD_USD = 100_000.0
_WHALE_WINDOW_MS = 5 * 60 * 1000


class BybitMarketData(ExchangeMarketData):
    """Bybit V5 linear (USDT-perp) public endpoint 시장 자료."""

    EXCHANGE_NAME = "bybit"

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

        # v0.3.1: Whale recent trades 측 측 추가 (Binance 측 동일 정합).
        results = await asyncio.gather(
            self._fetch_ticker(session, symbol),
            self._fetch_ls_ratio(session, symbol),
            self._fetch_recent_trades(session, symbol),
            return_exceptions=True,
        )
        ticker, ls, recent_trades = results

        # Tickers — price / 24h / OI / funding 모두 한 응답
        if isinstance(ticker, Exception):
            snap.errors.append(f"ticker: {ticker}")
        elif isinstance(ticker, dict):
            try:
                snap.price = float(ticker.get("lastPrice", "0") or 0)
                # price24hPcnt 박힘 = 0.015 (1.5%) 형식 — % 표기로 변환
                pct = float(ticker.get("price24hPcnt", "0") or 0)
                snap.price_24h_change_pct = pct * 100.0
                snap.volume_24h_usd = float(ticker.get("turnover24h", "0") or 0)
                snap.oi_usd = float(ticker.get("openInterestValue", "0") or 0)
                snap.funding_rate = float(ticker.get("fundingRate", "0") or 0)
            except (TypeError, ValueError) as e:
                snap.errors.append(f"ticker calc: {e}")

        # L-S ratio (account)
        if isinstance(ls, Exception):
            snap.errors.append(f"ls: {ls}")
        elif isinstance(ls, dict):
            try:
                buy = float(ls.get("buyRatio", "0") or 0)
                sell = float(ls.get("sellRatio", "0") or 0)
                snap.long_account_pct = buy
                snap.short_account_pct = sell
                if sell > 0:
                    snap.ls_ratio_global = buy / sell
            except (TypeError, ValueError, ZeroDivisionError) as e:
                snap.errors.append(f"ls calc: {e}")

        # v0.3.1: Whale notional — recent trades 측 5분 윈도우 측 ≥ $100K 합산
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
                    ts = int(t.get("time", 0) or 0)
                    if ts < window_start:
                        continue
                    price = float(t.get("price", 0) or 0)
                    size = float(t.get("size", 0) or 0)
                    notional = price * size
                    if notional < _WHALE_THRESHOLD_USD:
                        continue
                    count += 1
                    # Bybit side: "Buy" = taker buy, "Sell" = taker sell
                    if t.get("side") == "Buy":
                        buy_sum += notional
                    else:
                        sell_sum += notional
                snap.whale_buy_5m_usd = buy_sum
                snap.whale_sell_5m_usd = sell_sum
                snap.whale_count_5m = count
            except (TypeError, ValueError) as e:
                snap.errors.append(f"recent_trades calc: {e}")

        if snap.errors:
            logger.debug("Bybit snapshot %s 부분 실패: %s", symbol, "; ".join(snap.errors))
        return snap

    # ============================================================
    # endpoint sub-methods
    # ============================================================

    async def _get_json(
        self, session: aiohttp.ClientSession, path: str, params: dict,
    ) -> dict:
        url = f"{_BYBIT_BASE}{path}"
        async with session.get(url, params=params, timeout=_HTTP_TIMEOUT) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _fetch_ticker(self, session, symbol: str) -> dict | None:
        """V5 tickers — result.list[0] 추출."""
        data = await self._get_json(
            session, "/v5/market/tickers",
            {"category": "linear", "symbol": symbol},
        )
        if not isinstance(data, dict):
            return None
        if data.get("retCode") != 0:
            raise RuntimeError(f"bybit ret={data.get('retCode')} msg={data.get('retMsg')}")
        rows = (data.get("result") or {}).get("list") or []
        return rows[0] if rows else None

    async def _fetch_recent_trades(self, session, symbol: str) -> list:
        """v0.3.1: V5 recent-trade — 1000 trades 측 Whale 측 input.

        Response: result.list = [{ price, size, side("Buy"/"Sell"), time(ms), ... }]
        """
        data = await self._get_json(
            session, "/v5/market/recent-trade",
            {"category": "linear", "symbol": symbol, "limit": 1000},
        )
        if not isinstance(data, dict):
            return []
        if data.get("retCode") != 0:
            raise RuntimeError(f"bybit ret={data.get('retCode')} msg={data.get('retMsg')}")
        return (data.get("result") or {}).get("list") or []

    async def _fetch_ls_ratio(self, session, symbol: str) -> dict | None:
        """V5 account-ratio — buyRatio / sellRatio."""
        data = await self._get_json(
            session, "/v5/market/account-ratio",
            {"category": "linear", "symbol": symbol, "period": _LS_PERIOD, "limit": 1},
        )
        if not isinstance(data, dict):
            return None
        if data.get("retCode") != 0:
            raise RuntimeError(f"bybit ret={data.get('retCode')} msg={data.get('retMsg')}")
        rows = (data.get("result") or {}).get("list") or []
        return rows[-1] if rows else None
