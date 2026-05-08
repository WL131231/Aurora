"""Phase 3 Dashboard Series — 14D 시계열 합본 (v0.1.115).

거래소별 ``ExchangeSeriesProvider`` 등록 → 매 fetch 시 병렬 호출 + cache.
``DashboardFlowAggregator`` (snapshot, 60초 cache) 측 별개 — 시계열 측 5분 cache
박힘 (daily 봉 측 빨리 안 변함, API 부담 ↓).

합본 정책:
- price_close: OI 가중 평균 (큰 거래소 영향 ↑)
- perp_cvd: 거래소별 (taker_buy - taker_sell) 측 봉 단위 sum → cumulative
- oi_usd: sum (None 제외)
- funding_rate: OI 가중 평균
- taker_delta_usd: sum (buy - sell)
- ls_global_timeline: OI 가중 평균
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import aiohttp

from aurora.market.exchanges.series_base import (
    ExchangeSeries,
    ExchangeSeriesProvider,
)
from aurora.timeouts import (
    DASHBOARD_SERIES_PROVIDER_TIMEOUT_SEC,
    make_dashboard_series_session_timeout,
)

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SEC = 300  # 5분 — daily 봉 측 빨리 안 변함
_DAY_MS = 86_400_000


@dataclass(slots=True)
class SeriesPoint:
    """합본 시계열 1점."""

    ts_ms: int
    value: float | None


@dataclass(slots=True)
class DashboardSeries:
    """5 거래소 14D 시계열 합본."""

    coin: str
    days: int
    fetched_at_ms: int
    exchanges: list[str] = field(default_factory=list)
    # 합본 시계열
    price_close: list[SeriesPoint] = field(default_factory=list)
    perp_cvd: list[SeriesPoint] = field(default_factory=list)        # cumulative
    spot_cvd: list[SeriesPoint] = field(default_factory=list)        # v0.1.116+ 박힘 (현 빈)
    oi_usd: list[SeriesPoint] = field(default_factory=list)
    funding_rate: list[SeriesPoint] = field(default_factory=list)
    taker_delta_usd: list[SeriesPoint] = field(default_factory=list)
    ls_global_timeline: list[SeriesPoint] = field(default_factory=list)
    # per-exchange (debug / 거래소별 표기)
    per_exchange: dict[str, ExchangeSeries] = field(default_factory=dict)

    @classmethod
    def from_series_list(
        cls, coin: str, days: int, series_list: list[ExchangeSeries],
    ) -> DashboardSeries:
        """거래소 시계열 list → 합본 (day_ms 정렬, OI 가중 평균 등)."""
        # day_ms × exchange → SeriesBar 추출
        all_days: set[int] = set()
        for s in series_list:
            for b in s.bars:
                all_days.add(b.ts_ms)
        sorted_days = sorted(all_days)

        # 거래소별 day_ms → bar lookup
        bars_by_ex: dict[str, dict[int, object]] = {}
        for s in series_list:
            bars_by_ex[s.exchange] = {b.ts_ms: b for b in s.bars}

        price_close: list[SeriesPoint] = []
        perp_cvd: list[SeriesPoint] = []
        oi_usd: list[SeriesPoint] = []
        funding_rate: list[SeriesPoint] = []
        taker_delta: list[SeriesPoint] = []
        ls_timeline: list[SeriesPoint] = []

        cumulative_cvd = 0.0
        cvd_seen = False

        for day in sorted_days:
            # 해당 day 측 거래소별 bar list
            day_bars = []
            for s in series_list:
                bar = bars_by_ex.get(s.exchange, {}).get(day)
                if bar is not None:
                    day_bars.append((s.exchange, bar))

            # price_close — OI 가중 평균
            price_val = _weighted_avg(day_bars, "close", "oi_usd")
            price_close.append(SeriesPoint(ts_ms=day, value=price_val))

            # taker_delta — sum (buy - sell)
            delta_sum: float | None = None
            for _, bar in day_bars:
                buy = getattr(bar, "taker_buy_usd", None)
                sell = getattr(bar, "taker_sell_usd", None)
                if buy is None or sell is None:
                    continue
                delta_sum = (delta_sum or 0.0) + (buy - sell)
            taker_delta.append(SeriesPoint(ts_ms=day, value=delta_sum))

            # perp_cvd — cumulative sum 측 delta
            if delta_sum is not None:
                cumulative_cvd += delta_sum
                cvd_seen = True
            perp_cvd.append(
                SeriesPoint(ts_ms=day, value=cumulative_cvd if cvd_seen else None),
            )

            # oi_usd — sum
            oi_vals = [
                getattr(bar, "oi_usd", None)
                for _, bar in day_bars
                if getattr(bar, "oi_usd", None) is not None
            ]
            oi_sum = sum(oi_vals) if oi_vals else None
            oi_usd.append(SeriesPoint(ts_ms=day, value=oi_sum))

            # funding_rate — OI 가중 평균
            funding_val = _weighted_avg(day_bars, "funding_rate_avg", "oi_usd")
            funding_rate.append(SeriesPoint(ts_ms=day, value=funding_val))

            # ls_global_timeline — OI 가중 평균
            ls_val = _weighted_avg(day_bars, "ls_ratio_global", "oi_usd")
            ls_timeline.append(SeriesPoint(ts_ms=day, value=ls_val))

        return cls(
            coin=coin,
            days=days,
            fetched_at_ms=int(time.time() * 1000),
            exchanges=[s.exchange for s in series_list],
            price_close=price_close,
            perp_cvd=perp_cvd,
            spot_cvd=[],
            oi_usd=oi_usd,
            funding_rate=funding_rate,
            taker_delta_usd=taker_delta,
            ls_global_timeline=ls_timeline,
            per_exchange={s.exchange: s for s in series_list},
        )


def _weighted_avg(
    day_bars: list[tuple[str, object]], field_name: str, weight_field: str,
) -> float | None:
    """day_bars 측 ``field_name`` 측 ``weight_field`` 가중 평균 (None 제외).

    가중치 측 모두 None / 0 측 단순 평균.
    """
    num = 0.0
    den = 0.0
    simple_vals: list[float] = []
    for _, bar in day_bars:
        val = getattr(bar, field_name, None)
        if val is None:
            continue
        simple_vals.append(val)
        weight = getattr(bar, weight_field, None)
        if weight is not None and weight > 0:
            num += val * weight
            den += weight
    if den > 0:
        return num / den
    if simple_vals:
        return sum(simple_vals) / len(simple_vals)
    return None


class DashboardSeriesAggregator:
    """거래소 series provider 등록 + cache (5분) + 병렬 fetch."""

    def __init__(
        self,
        providers: list[ExchangeSeriesProvider],
        cache_ttl_sec: int = _DEFAULT_TTL_SEC,
    ) -> None:
        self._providers = list(providers)
        self._ttl = cache_ttl_sec
        self._cache: dict[tuple[str, int], DashboardSeries] = {}
        self._cache_ts: dict[tuple[str, int], float] = {}

    @property
    def exchange_names(self) -> list[str]:
        return [p.EXCHANGE_NAME for p in self._providers]

    async def fetch(self, coin: str, days: int = 14) -> DashboardSeries:
        """coin 측 모든 거래소 시계열 병렬 fetch (cache hit 시 즉시 반환)."""
        key = (coin, days)
        last = self._cache_ts.get(key, 0.0)
        if last > 0 and time.time() - last < self._ttl and key in self._cache:
            return self._cache[key]

        async def _fetch_with_timeout(provider, sess) -> ExchangeSeries:
            try:
                return await asyncio.wait_for(
                    provider.fetch_series(sess, coin, days),
                    timeout=DASHBOARD_SERIES_PROVIDER_TIMEOUT_SEC,
                )
            except TimeoutError:
                logger.warning(
                    "DashboardSeries %s.%s timeout (%.0fs)",
                    provider.EXCHANGE_NAME, coin,
                    DASHBOARD_SERIES_PROVIDER_TIMEOUT_SEC,
                )
                return ExchangeSeries(
                    exchange=provider.EXCHANGE_NAME,
                    symbol=provider.symbol_for(coin),
                    coin=coin,
                    days=days,
                    errors=[f"fetch: timeout {DASHBOARD_SERIES_PROVIDER_TIMEOUT_SEC}s"],
                )

        timeout = make_dashboard_series_session_timeout()
        async with aiohttp.ClientSession(timeout=timeout) as session:
            results = await asyncio.gather(
                *[_fetch_with_timeout(p, session) for p in self._providers],
                return_exceptions=True,
            )

        series_list: list[ExchangeSeries] = []
        for prov, r in zip(self._providers, results, strict=True):
            if isinstance(r, Exception):
                logger.warning(
                    "DashboardSeries %s.%s 실패: %s",
                    prov.EXCHANGE_NAME, coin, r,
                )
                series_list.append(ExchangeSeries(
                    exchange=prov.EXCHANGE_NAME,
                    symbol=prov.symbol_for(coin),
                    coin=coin,
                    days=days,
                    errors=[f"fetch: {r!r}"],
                ))
            else:
                series_list.append(r)

        flow = DashboardSeries.from_series_list(coin, days, series_list)
        self._cache[key] = flow
        self._cache_ts[key] = time.time()
        logger.info(
            "DashboardSeries %s/%dD 박힘: %d 거래소, %d 봉 합본",
            coin, days, len(series_list), len(flow.price_close),
        )
        return flow


# ============================================================
# 모듈 싱글톤
# ============================================================

_singleton: DashboardSeriesAggregator | None = None


def get_series_aggregator() -> DashboardSeriesAggregator:
    """싱글톤 series aggregator — 등록 거래소 5개."""
    global _singleton
    if _singleton is None:
        from aurora.market.exchanges.binance_series import BinanceSeriesProvider
        from aurora.market.exchanges.bitget_series import BitgetSeriesProvider
        from aurora.market.exchanges.bybit_series import BybitSeriesProvider
        from aurora.market.exchanges.hyperliquid_series import (
            HyperliquidSeriesProvider,
        )
        from aurora.market.exchanges.okx_series import OkxSeriesProvider
        _singleton = DashboardSeriesAggregator([
            BinanceSeriesProvider(),
            BybitSeriesProvider(),
            OkxSeriesProvider(),
            BitgetSeriesProvider(),
            HyperliquidSeriesProvider(),
        ])
    return _singleton


def reset_for_test() -> None:
    """테스트 격리 — 싱글톤 reset."""
    global _singleton
    _singleton = None


__all__ = [
    "DashboardSeries",
    "DashboardSeriesAggregator",
    "SeriesPoint",
    "get_series_aggregator",
    "reset_for_test",
]
