"""OKX V5 public market data fetcher (v0.1.88).

Public endpoints (API 키 X):
- ``/api/v5/market/ticker?instId=BTC-USDT-SWAP`` — price / 24h volume
- ``/api/v5/public/open-interest?instType=SWAP&instId=BTC-USDT-SWAP``
  → oi (contracts), oiCcy (in BTC) — USD 환산은 oiCcy × price
- ``/api/v5/public/funding-rate?instId=BTC-USDT-SWAP`` — fundingRate
- ``/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy=BTC&period=5m``
  → [ts, ratio] — global L-S account ratio
- ``/api/v5/rubik/stat/contracts/long-short-position-ratio?ccy=BTC&period=5m``
  → [ts, ratio] — top trader L-S position ratio
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from aurora.market.exchanges.base import ExchangeMarketData, ExchangeSnapshot
from aurora.timeouts import make_exchange_timeout

logger = logging.getLogger(__name__)

_OKX_BASE = "https://www.okx.com"
_HTTP_TIMEOUT = make_exchange_timeout()  # v0.1.98: central config
_LS_PERIOD = "5m"


class OkxMarketData(ExchangeMarketData):
    """OKX V5 SWAP (USDT linear perpetual) public endpoint 시장 자료."""

    EXCHANGE_NAME = "okx"

    def symbol_for(self, coin: str) -> str:
        # OKX perp = "BTC-USDT-SWAP"
        return f"{coin}-USDT-SWAP"

    async def fetch_snapshot(
        self,
        session: aiohttp.ClientSession,
        coin: str,
    ) -> ExchangeSnapshot:
        inst_id = self.symbol_for(coin)
        snap = ExchangeSnapshot(
            exchange=self.EXCHANGE_NAME,
            symbol=inst_id,
            fetched_at_ms=int(time.time() * 1000),
        )

        results = await asyncio.gather(
            self._fetch_ticker(session, inst_id),
            self._fetch_oi(session, inst_id),
            self._fetch_funding(session, inst_id),
            self._fetch_ls_account(session, coin),
            self._fetch_ls_top_position(session, coin),
            return_exceptions=True,
        )
        ticker, oi_row, funding, ls_acc, ls_top_pos = results

        # Ticker — price / 24h volume
        if isinstance(ticker, Exception):
            snap.errors.append(f"ticker: {ticker}")
        elif isinstance(ticker, dict):
            try:
                snap.price = float(ticker.get("last", "0") or 0)
                open_24h = float(ticker.get("open24h", "0") or 0)
                if open_24h > 0:
                    snap.price_24h_change_pct = (snap.price - open_24h) / open_24h * 100.0
                # volCcy24h = quote currency (USDT) 거래량
                snap.volume_24h_usd = float(ticker.get("volCcy24h", "0") or 0)
            except (TypeError, ValueError) as e:
                snap.errors.append(f"ticker calc: {e}")

        # OI — oiCcy (BTC) × price = USD notional
        if isinstance(oi_row, Exception):
            snap.errors.append(f"oi: {oi_row}")
        elif isinstance(oi_row, dict):
            try:
                oi_ccy = float(oi_row.get("oiCcy", "0") or 0)
                if snap.price is not None and snap.price > 0:
                    snap.oi_usd = oi_ccy * snap.price
            except (TypeError, ValueError) as e:
                snap.errors.append(f"oi calc: {e}")

        # Funding rate
        if isinstance(funding, Exception):
            snap.errors.append(f"funding: {funding}")
        elif isinstance(funding, dict):
            try:
                snap.funding_rate = float(funding.get("fundingRate", "0") or 0)
            except (TypeError, ValueError) as e:
                snap.errors.append(f"funding calc: {e}")

        # L-S Account (global)
        if isinstance(ls_acc, Exception):
            snap.errors.append(f"ls_acc: {ls_acc}")
        elif isinstance(ls_acc, list) and ls_acc:
            try:
                # row = [ts, ratio]
                snap.ls_ratio_global = float(ls_acc[-1][1])
                # OKX 측 long_account_pct / short_account_pct 분리 X — None 유지
            except (TypeError, ValueError, IndexError) as e:
                snap.errors.append(f"ls_acc calc: {e}")

        # L-S Top Position
        if isinstance(ls_top_pos, Exception):
            snap.errors.append(f"ls_top_pos: {ls_top_pos}")
        elif isinstance(ls_top_pos, list) and ls_top_pos:
            try:
                snap.ls_ratio_top_position = float(ls_top_pos[-1][1])
            except (TypeError, ValueError, IndexError) as e:
                snap.errors.append(f"ls_top_pos calc: {e}")

        if snap.errors:
            logger.debug("OKX snapshot %s 부분 실패: %s", inst_id, "; ".join(snap.errors))
        return snap

    # ============================================================
    # endpoint sub-methods
    # ============================================================

    async def _get_json(
        self, session: aiohttp.ClientSession, path: str, params: dict,
    ) -> dict:
        url = f"{_OKX_BASE}{path}"
        async with session.get(url, params=params, timeout=_HTTP_TIMEOUT) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _fetch_ticker(self, session, inst_id: str) -> dict | None:
        data = await self._get_json(
            session, "/api/v5/market/ticker", {"instId": inst_id},
        )
        if data.get("code") != "0":
            raise RuntimeError(f"okx code={data.get('code')} msg={data.get('msg')}")
        rows = data.get("data") or []
        return rows[0] if rows else None

    async def _fetch_oi(self, session, inst_id: str) -> dict | None:
        data = await self._get_json(
            session, "/api/v5/public/open-interest",
            {"instType": "SWAP", "instId": inst_id},
        )
        if data.get("code") != "0":
            raise RuntimeError(f"okx code={data.get('code')} msg={data.get('msg')}")
        rows = data.get("data") or []
        return rows[0] if rows else None

    async def _fetch_funding(self, session, inst_id: str) -> dict | None:
        data = await self._get_json(
            session, "/api/v5/public/funding-rate", {"instId": inst_id},
        )
        if data.get("code") != "0":
            raise RuntimeError(f"okx code={data.get('code')} msg={data.get('msg')}")
        rows = data.get("data") or []
        return rows[0] if rows else None

    async def _fetch_ls_account(self, session, coin: str) -> list:
        data = await self._get_json(
            session, "/api/v5/rubik/stat/contracts/long-short-account-ratio",
            {"ccy": coin, "period": _LS_PERIOD},
        )
        if data.get("code") != "0":
            raise RuntimeError(f"okx code={data.get('code')} msg={data.get('msg')}")
        return data.get("data") or []

    async def _fetch_ls_top_position(self, session, coin: str) -> list:
        data = await self._get_json(
            session, "/api/v5/rubik/stat/contracts/long-short-position-ratio",
            {"ccy": coin, "period": _LS_PERIOD},
        )
        if data.get("code") != "0":
            raise RuntimeError(f"okx code={data.get('code')} msg={data.get('msg')}")
        return data.get("data") or []
