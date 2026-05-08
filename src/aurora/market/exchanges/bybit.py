"""Bybit V5 public market data fetcher (v0.1.88).

Public endpoints (API 키 X):
- ``/v5/market/tickers?category=linear&symbol=BTCUSDT``
  → price, 24h pct, turnover, openInterest, openInterestValue (USD), fundingRate
- ``/v5/market/account-ratio?category=linear&symbol=BTCUSDT&period=5min``
  → buyRatio / sellRatio (account 기준 L-S ratio)

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
_HTTP_TIMEOUT = make_exchange_timeout()  # v0.1.98: central config
_LS_PERIOD = "5min"


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

        results = await asyncio.gather(
            self._fetch_ticker(session, symbol),
            self._fetch_ls_ratio(session, symbol),
            return_exceptions=True,
        )
        ticker, ls = results

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
