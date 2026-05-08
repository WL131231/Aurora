"""Hyperliquid Info API public market data fetcher (v0.1.89).

Hyperliquid 측 단일 POST endpoint 측 다양한 type 박힘:
- ``POST /info`` body=``{"type": "metaAndAssetCtxs"}``
  → ``[meta, assetCtxs]`` 리턴. ``meta.universe`` = 자산 list, ``assetCtxs[i]`` = i 번째 자산
    측 ``{markPx, prevDayPx, dayBaseVlm, dayNtlVlm, openInterest, funding}``.

Hyperliquid 측 retail vs pro / top trader L-S ratio 별도 X — ls_ratio_* 측 None 유지.

OI 단위: ``openInterest`` 는 base coin (BTC) 단위 → × markPx = USD notional.
funding: 1시간 단위 표기 (Binance/Bybit 측 8시간 단위). UI 표기 정합 위해 그대로
박음 — 직접 비교 시 사용자 인지 박힘.
"""

from __future__ import annotations

import logging
import time

import aiohttp

from aurora.market.exchanges.base import ExchangeMarketData, ExchangeSnapshot
from aurora.timeouts import make_exchange_timeout

logger = logging.getLogger(__name__)

_HL_BASE = "https://api.hyperliquid.xyz"
_HTTP_TIMEOUT = make_exchange_timeout()  # v0.1.98: central config


class HyperliquidMarketData(ExchangeMarketData):
    """Hyperliquid Info API 시장 자료."""

    EXCHANGE_NAME = "hyperliquid"

    def symbol_for(self, coin: str) -> str:
        # Hyperliquid 측 coin 자체가 symbol (BTC, ETH).
        return coin

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

        try:
            data = await self._post_info(session, {"type": "metaAndAssetCtxs"})
        except Exception as e:  # noqa: BLE001
            snap.errors.append(f"metaAndAssetCtxs: {e}")
            return snap

        # data = [meta, assetCtxs]
        if not isinstance(data, list) or len(data) < 2:
            snap.errors.append(f"unexpected response shape: {type(data).__name__}")
            return snap

        meta, asset_ctxs = data[0], data[1]
        universe = (meta or {}).get("universe", []) if isinstance(meta, dict) else []

        # coin 측 index 찾기
        idx = None
        for i, asset in enumerate(universe):
            if isinstance(asset, dict) and asset.get("name") == coin:
                idx = i
                break
        if idx is None or idx >= len(asset_ctxs):
            snap.errors.append(f"coin {coin} not in universe")
            return snap

        ctx = asset_ctxs[idx]
        if not isinstance(ctx, dict):
            snap.errors.append(f"ctx[{idx}] not dict: {type(ctx).__name__}")
            return snap

        try:
            mark_px = float(ctx.get("markPx") or 0)
            prev_px = float(ctx.get("prevDayPx") or 0)
            day_ntl_vlm = float(ctx.get("dayNtlVlm") or 0)  # USD notional volume
            oi_coin = float(ctx.get("openInterest") or 0)
            funding = float(ctx.get("funding") or 0)

            snap.price = mark_px if mark_px > 0 else None
            if prev_px > 0 and mark_px > 0:
                snap.price_24h_change_pct = (mark_px - prev_px) / prev_px * 100.0
            snap.volume_24h_usd = day_ntl_vlm if day_ntl_vlm > 0 else None
            if oi_coin > 0 and mark_px > 0:
                snap.oi_usd = oi_coin * mark_px
            snap.funding_rate = funding
        except (TypeError, ValueError) as e:
            snap.errors.append(f"ctx calc: {e}")

        if snap.errors:
            logger.debug("Hyperliquid snapshot %s 부분 실패: %s", symbol, "; ".join(snap.errors))
        return snap

    # ============================================================
    # POST /info — body 별 dispatch
    # ============================================================

    async def _post_info(
        self, session: aiohttp.ClientSession, body: dict,
    ):
        url = f"{_HL_BASE}/info"
        async with session.post(url, json=body, timeout=_HTTP_TIMEOUT) as resp:
            resp.raise_for_status()
            return await resp.json()
