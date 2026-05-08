"""Phase 3 Dashboard Flow — 거래소 5개 시장 자료 합본 (v0.1.87+).

거래소별 ``ExchangeMarketData`` 등록 → 매 fetch 시 병렬 호출 + cache.
v0.1.87 측 Binance 1개 박힘. v0.1.88 (Bybit, OKX) / v0.1.89 (Bitget, Hyperliquid)
순차 박힘. v0.1.90 측 Whale Notional 별도.

cache TTL = 60 초 — Binance L/S ratio period=5m 갱신 빈도 정합 + UI 폴링 (15초)
부담 완화. 봇 cache hit 측 즉시 응답.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import aiohttp

from aurora.market.exchanges.base import ExchangeMarketData, ExchangeSnapshot

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SEC = 60
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)


@dataclass(slots=True)
class DashboardFlow:
    """모든 등록 거래소 측 snapshot 합본 + 합계.

    합계 (sum/avg) 측 None 제외 (부분 거래소 실패 시 나머지로 계산).
    UI 측 거래소별 segment + 합계 둘 다 표시.
    """

    coin: str                                                # "BTC" 등
    fetched_at_ms: int
    snapshots: list[ExchangeSnapshot] = field(default_factory=list)
    # 합계
    total_oi_usd: float | None = None                        # 거래소 합산
    total_volume_24h_usd: float | None = None
    avg_funding_rate: float | None = None                    # None 제외 평균
    avg_ls_ratio_global: float | None = None
    avg_ls_ratio_top_position: float | None = None
    avg_ls_ratio_top_account: float | None = None
    # avg 가중치 = 거래소 OI 비중 (큰 거래소 영향 큼). OI 미상 시 단순 평균.
    # v0.1.90: Whale notional 합 (5분 윈도우, 거래소별 None 제외)
    total_whale_buy_5m_usd: float | None = None
    total_whale_sell_5m_usd: float | None = None
    total_whale_count_5m: int | None = None

    @classmethod
    def from_snapshots(
        cls, coin: str, snaps: list[ExchangeSnapshot],
    ) -> DashboardFlow:
        """snapshot 리스트 → 합계 계산 박힘.

        - total_oi / total_volume = sum (None 제외)
        - avg_funding / avg_ls_* = OI 가중 평균 (OI 미상 시 단순 평균)
        """
        oi_vals = [s.oi_usd for s in snaps if s.oi_usd is not None]
        vol_vals = [s.volume_24h_usd for s in snaps if s.volume_24h_usd is not None]
        total_oi = sum(oi_vals) if oi_vals else None
        total_vol = sum(vol_vals) if vol_vals else None

        def _weighted(field_name: str) -> float | None:
            num = 0.0
            den = 0.0
            simple_vals: list[float] = []
            for s in snaps:
                val = getattr(s, field_name, None)
                if val is None:
                    continue
                simple_vals.append(val)
                if s.oi_usd is not None and s.oi_usd > 0:
                    num += val * s.oi_usd
                    den += s.oi_usd
            if den > 0:
                return num / den
            if simple_vals:
                return sum(simple_vals) / len(simple_vals)
            return None

        # v0.1.90: Whale notional 합 (None 제외 sum)
        whale_buy_vals = [s.whale_buy_5m_usd for s in snaps if s.whale_buy_5m_usd is not None]
        whale_sell_vals = [s.whale_sell_5m_usd for s in snaps if s.whale_sell_5m_usd is not None]
        whale_count_vals = [s.whale_count_5m for s in snaps if s.whale_count_5m is not None]

        return cls(
            coin=coin,
            fetched_at_ms=int(time.time() * 1000),
            snapshots=list(snaps),
            total_oi_usd=total_oi,
            total_volume_24h_usd=total_vol,
            avg_funding_rate=_weighted("funding_rate"),
            avg_ls_ratio_global=_weighted("ls_ratio_global"),
            avg_ls_ratio_top_position=_weighted("ls_ratio_top_position"),
            avg_ls_ratio_top_account=_weighted("ls_ratio_top_account"),
            total_whale_buy_5m_usd=sum(whale_buy_vals) if whale_buy_vals else None,
            total_whale_sell_5m_usd=sum(whale_sell_vals) if whale_sell_vals else None,
            total_whale_count_5m=sum(whale_count_vals) if whale_count_vals else None,
        )


class DashboardFlowAggregator:
    """거래소 등록 + cache + 병렬 fetch 박음.

    Lifecycle:
        >>> agg = DashboardFlowAggregator([BinanceMarketData()])
        >>> flow = await agg.fetch("BTC")
        >>> for s in flow.snapshots:
        >>>     print(s.exchange, s.oi_usd)
    """

    def __init__(
        self,
        providers: list[ExchangeMarketData],
        cache_ttl_sec: int = _DEFAULT_TTL_SEC,
    ) -> None:
        self._providers = list(providers)
        self._ttl = cache_ttl_sec
        self._cache: dict[str, DashboardFlow] = {}
        self._cache_ts: dict[str, float] = {}

    @property
    def exchange_names(self) -> list[str]:
        return [p.EXCHANGE_NAME for p in self._providers]

    async def fetch(self, coin: str) -> DashboardFlow:
        """coin 측 모든 거래소 snapshot 병렬 fetch (cache hit 시 즉시 반환).

        모든 거래소 실패 시에도 빈 ``DashboardFlow`` 반환 (UI 측 빈 카드 표시).
        """
        # cache hit
        last = self._cache_ts.get(coin, 0.0)
        if last > 0 and time.time() - last < self._ttl and coin in self._cache:
            return self._cache[coin]

        # 새 fetch — 모든 거래소 병렬
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            results = await asyncio.gather(
                *[p.fetch_snapshot(session, coin) for p in self._providers],
                return_exceptions=True,
            )

        snaps: list[ExchangeSnapshot] = []
        for prov, r in zip(self._providers, results, strict=True):
            if isinstance(r, Exception):
                logger.warning(
                    "DashboardFlow %s.%s fetch 실패: %s", prov.EXCHANGE_NAME, coin, r,
                )
                # 빈 snapshot 박음 (errors 에 사유)
                snaps.append(ExchangeSnapshot(
                    exchange=prov.EXCHANGE_NAME,
                    symbol=prov.symbol_for(coin),
                    fetched_at_ms=int(time.time() * 1000),
                    errors=[f"fetch: {r!r}"],
                ))
            else:
                snaps.append(r)

        flow = DashboardFlow.from_snapshots(coin, snaps)
        self._cache[coin] = flow
        self._cache_ts[coin] = time.time()
        logger.info(
            "DashboardFlow %s 박힘: %d 거래소 (OI 합 %.1fM USD)",
            coin, len(snaps),
            (flow.total_oi_usd or 0) / 1e6,
        )
        return flow


# ============================================================
# 모듈 싱글톤 — API 엔드포인트가 사용 (봇 가동 무관 항상 사용 가능)
# ============================================================

_singleton: DashboardFlowAggregator | None = None


def get_aggregator() -> DashboardFlowAggregator:
    """싱글톤 aggregator — 등록 거래소는 v0.1.87+ 순차 박힘.

    v0.1.87: Binance
    v0.1.88: + Bybit, OKX
    v0.1.89: + Bitget, Hyperliquid (5/5 완성)
    """
    global _singleton
    if _singleton is None:
        from aurora.market.exchanges import (
            BinanceMarketData,
            BitgetMarketData,
            BybitMarketData,
            HyperliquidMarketData,
            OkxMarketData,
        )
        _singleton = DashboardFlowAggregator([
            BinanceMarketData(),
            BybitMarketData(),
            OkxMarketData(),
            BitgetMarketData(),
            HyperliquidMarketData(),
        ])
    return _singleton


def reset_for_test() -> None:
    """테스트 격리 — 싱글톤 reset."""
    global _singleton
    _singleton = None
