"""거래소별 14D 시계열 fetcher 베이스 (v0.1.115).

Phase 3 Dashboard view 측 신규 사양 — 14일치 daily 봉 + funding + OI + LSR
시계열 fetch 박힘. 기존 ``ExchangeMarketData`` (snapshot 단일 시점) 측 별개.

각 거래소 구현 측 ``ExchangeSeriesProvider`` 상속 + ``fetch_series()`` 박음.
``DashboardSeriesAggregator`` 측 등록 거래소 측 병렬 fetch + 합본 박음.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import aiohttp


@dataclass(slots=True)
class SeriesBar:
    """1 봉 (daily 기본). ``ts_ms`` = bar open time (UTC 00:00).

    None = 해당 거래소 측 미지원 / fetch 실패. 합본 측 None 측 평균 계산 제외.
    """

    ts_ms: int
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume_usd: float | None = None       # quote volume USD (perp)
    taker_buy_usd: float | None = None    # taker buy quote vol
    taker_sell_usd: float | None = None   # taker sell quote vol (= volume - buy)
    oi_usd: float | None = None           # 봉 끝 시점 OI USD
    funding_rate_avg: float | None = None  # 봉 안 funding rate 평균 (8h × 3 → 1d)
    ls_ratio_global: float | None = None        # global account L/S
    ls_ratio_top_position: float | None = None  # top trader (position 기준)
    ls_ratio_top_account: float | None = None   # top trader (account 기준)


@dataclass(slots=True)
class ExchangeSeries:
    """거래소 1개 측 14D 시계열 (봉 list)."""

    exchange: str                                    # "binance" 등
    symbol: str                                      # "BTCUSDT" 등
    coin: str                                        # "BTC" 등
    days: int                                        # 14
    bars: list[SeriesBar] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # endpoint 별 부분 실패 사유


class ExchangeSeriesProvider(ABC):
    """거래소별 14D 시계열 fetcher ABC.

    구현 측 ``EXCHANGE_NAME`` (str), ``symbol_for(coin)`` (override 가능), ``fetch_series``
    박음. ``fetch_series`` 안 측 endpoint 측 sub-method 분리해 부분 실패 격리.
    """

    EXCHANGE_NAME: str = "<override>"

    def symbol_for(self, coin: str) -> str:
        """coin (BTC / ETH 등) → 거래소별 perpetual symbol.

        기본 = ``f"{coin}USDT"`` — Hyperliquid 같은 별도 표기 거래소 측 override.
        """
        return f"{coin}USDT"

    @abstractmethod
    async def fetch_series(
        self,
        session: aiohttp.ClientSession,
        coin: str,
        days: int = 14,
    ) -> ExchangeSeries:
        """14D 봉 + funding + OI + LSR 시계열 fetch.

        Args:
            session: aiohttp.ClientSession (호출자 lifecycle).
            coin: ``"BTC"`` / ``"ETH"`` 등 — ``symbol_for(coin)`` 변환.
            days: 봉 개수 (기본 14).

        Returns:
            ``ExchangeSeries`` — 부분 실패 시 해당 필드 None + ``errors`` 기록.
            전체 실패 측 빈 ``bars`` + errors 박힘 (UI 측 표기 본질).
        """
        ...
