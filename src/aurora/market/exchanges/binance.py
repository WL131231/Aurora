"""Binance Futures (USDM) 시장 데이터 fetcher (v0.1.87).

Public endpoints (API 키 X):
- ``/fapi/v1/openInterest`` — OI (BTC contracts; mark price 곱해서 USD notional 환산)
- ``/fapi/v1/premiumIndex`` — funding rate + mark price
- ``/fapi/v1/ticker/24hr`` — 24h 가격 / 거래량
- ``/futures/data/globalLongShortAccountRatio?period=5m`` — global L/S ratio
- ``/futures/data/topLongShortPositionRatio?period=5m`` — top trader (position 기준)
- ``/futures/data/topLongShortAccountRatio?period=5m`` — top trader (account 기준)

Rate limit: weight-based, 일반 endpoint 1~10 weight, 1200/min 제한. 6 endpoint × 1
coin = 매 fetch ~6 weight, 60초 주기 박음 → 60 weight/min (5%).
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from aurora.market.exchanges.base import ExchangeMarketData, ExchangeSnapshot

logger = logging.getLogger(__name__)

_FAPI_BASE = "https://fapi.binance.com"
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=8)
_LS_PERIOD = "5m"  # globalLongShortAccountRatio / topLongShortPositionRatio period


class BinanceMarketData(ExchangeMarketData):
    """Binance Futures (USDM) public endpoint 측 시장 자료."""

    EXCHANGE_NAME = "binance"

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

        # 6 endpoint 병렬 fetch — 부분 실패 격리 (return_exceptions=True)
        results = await asyncio.gather(
            self._fetch_oi(session, symbol),
            self._fetch_premium_index(session, symbol),
            self._fetch_ticker_24h(session, symbol),
            self._fetch_ls_global(session, symbol),
            self._fetch_ls_top_position(session, symbol),
            self._fetch_ls_top_account(session, symbol),
            return_exceptions=True,
        )
        oi_btc, premium, ticker, ls_global, ls_top_pos, ls_top_acc = results

        # OI — BTC contracts → USD notional (mark price 곱)
        if isinstance(oi_btc, Exception):
            snap.errors.append(f"oi: {oi_btc}")
        elif isinstance(premium, Exception):
            snap.errors.append(f"premium: {premium}")
        else:
            mark_price = premium.get("markPrice")
            try:
                if oi_btc is not None and mark_price is not None:
                    snap.oi_usd = float(oi_btc) * float(mark_price)
            except (TypeError, ValueError) as e:
                snap.errors.append(f"oi calc: {e}")

        # Funding rate
        if not isinstance(premium, Exception) and isinstance(premium, dict):
            try:
                snap.funding_rate = float(premium.get("lastFundingRate", "0") or 0)
                snap.price = float(premium.get("markPrice", "0") or 0)
            except (TypeError, ValueError) as e:
                snap.errors.append(f"funding: {e}")

        # 24h ticker
        if isinstance(ticker, Exception):
            snap.errors.append(f"ticker24h: {ticker}")
        elif isinstance(ticker, dict):
            try:
                snap.price_24h_change_pct = float(ticker.get("priceChangePercent", "0") or 0)
                snap.volume_24h_usd = float(ticker.get("quoteVolume", "0") or 0)
                if snap.price is None:
                    snap.price = float(ticker.get("lastPrice", "0") or 0)
            except (TypeError, ValueError) as e:
                snap.errors.append(f"ticker calc: {e}")

        # L/S Global (account)
        if isinstance(ls_global, Exception):
            snap.errors.append(f"ls_global: {ls_global}")
        elif isinstance(ls_global, list) and ls_global:
            row = ls_global[-1]  # 가장 최근 봉
            try:
                snap.ls_ratio_global = float(row.get("longShortRatio"))
                snap.long_account_pct = float(row.get("longAccount"))
                snap.short_account_pct = float(row.get("shortAccount"))
            except (TypeError, ValueError) as e:
                snap.errors.append(f"ls_global calc: {e}")

        # Top trader (position)
        if isinstance(ls_top_pos, Exception):
            snap.errors.append(f"ls_top_pos: {ls_top_pos}")
        elif isinstance(ls_top_pos, list) and ls_top_pos:
            row = ls_top_pos[-1]
            try:
                snap.ls_ratio_top_position = float(row.get("longShortRatio"))
            except (TypeError, ValueError) as e:
                snap.errors.append(f"ls_top_pos calc: {e}")

        # Top trader (account)
        if isinstance(ls_top_acc, Exception):
            snap.errors.append(f"ls_top_acc: {ls_top_acc}")
        elif isinstance(ls_top_acc, list) and ls_top_acc:
            row = ls_top_acc[-1]
            try:
                snap.ls_ratio_top_account = float(row.get("longShortRatio"))
            except (TypeError, ValueError) as e:
                snap.errors.append(f"ls_top_acc calc: {e}")

        if snap.errors:
            logger.debug("Binance snapshot %s 부분 실패: %s", symbol, "; ".join(snap.errors))
        return snap

    # ============================================================
    # endpoint 별 sub-method (각자 raise 가능 — gather 가 격리)
    # ============================================================

    async def _get_json(
        self, session: aiohttp.ClientSession, path: str, params: dict,
    ):
        url = f"{_FAPI_BASE}{path}"
        async with session.get(url, params=params, timeout=_HTTP_TIMEOUT) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _fetch_oi(self, session, symbol: str) -> str | None:
        """OI (BTC contracts) — string 반환 (호출자가 USD notional 환산)."""
        data = await self._get_json(session, "/fapi/v1/openInterest", {"symbol": symbol})
        return data.get("openInterest") if isinstance(data, dict) else None

    async def _fetch_premium_index(self, session, symbol: str) -> dict:
        return await self._get_json(session, "/fapi/v1/premiumIndex", {"symbol": symbol})

    async def _fetch_ticker_24h(self, session, symbol: str) -> dict:
        return await self._get_json(session, "/fapi/v1/ticker/24hr", {"symbol": symbol})

    async def _fetch_ls_global(self, session, symbol: str) -> list:
        return await self._get_json(
            session, "/futures/data/globalLongShortAccountRatio",
            {"symbol": symbol, "period": _LS_PERIOD, "limit": 1},
        )

    async def _fetch_ls_top_position(self, session, symbol: str) -> list:
        return await self._get_json(
            session, "/futures/data/topLongShortPositionRatio",
            {"symbol": symbol, "period": _LS_PERIOD, "limit": 1},
        )

    async def _fetch_ls_top_account(self, session, symbol: str) -> list:
        return await self._get_json(
            session, "/futures/data/topLongShortAccountRatio",
            {"symbol": symbol, "period": _LS_PERIOD, "limit": 1},
        )
