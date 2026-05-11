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
_HTTP_TIMEOUT = make_exchange_timeout()
# v0.3.1: Whale 측 Binance 측 동일 정합
_WHALE_THRESHOLD_USD = 100_000.0
_WHALE_WINDOW_MS = 5 * 60 * 1000


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

        # v0.3.1: Whale notional — recentTrades 측 5분 윈도우 측 ≥ $100K 합산
        snap.whale_threshold_usd = _WHALE_THRESHOLD_USD
        try:
            trades_data = await self._post_info(session, {"type": "recentTrades", "coin": coin})
        except Exception as e:  # noqa: BLE001
            snap.errors.append(f"recentTrades: {e}")
            trades_data = None
        if isinstance(trades_data, list):
            try:
                now_ms = int(time.time() * 1000)
                window_start = now_ms - _WHALE_WINDOW_MS
                buy_sum = 0.0
                sell_sum = 0.0
                count = 0
                for t in trades_data:
                    ts = int(t.get("time", 0) or 0)
                    if ts < window_start:
                        continue
                    price = float(t.get("px", 0) or 0)
                    sz = float(t.get("sz", 0) or 0)
                    notional = price * sz
                    if notional < _WHALE_THRESHOLD_USD:
                        continue
                    count += 1
                    # HL side: "B" = taker buy, "A" = taker sell (Ask filled)
                    if t.get("side") == "B":
                        buy_sum += notional
                    else:
                        sell_sum += notional
                snap.whale_buy_5m_usd = buy_sum
                snap.whale_sell_5m_usd = sell_sum
                snap.whale_count_5m = count
            except (TypeError, ValueError) as e:
                snap.errors.append(f"recentTrades calc: {e}")

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
