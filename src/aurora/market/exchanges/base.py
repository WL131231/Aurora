"""거래소별 시장 데이터 fetcher 베이스 (v0.1.87).

Phase 3 dashboard view — 5 거래소 (Binance / Bybit / OKX / Bitget / Hyperliquid)
의 OI / Funding / L-S Ratio / Top Trader Ratio 자료 합본.

각 거래소 구현은 ``ExchangeMarketData`` ABC 를 상속 + ``fetch_snapshot()`` 구현.
``DashboardFlowAggregator`` 가 등록된 거래소 측 병렬 fetch + 합본 박음.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import aiohttp


@dataclass(slots=True)
class ExchangeSnapshot:
    """거래소별 시장 자료 1개 (한 시점 snapshot).

    None = 해당 거래소 측 미지원 / fetch 실패. 합본 측 None 은 평균 계산 제외.
    """

    exchange: str                        # "binance" / "bybit" / "okx" / "bitget" / "hyperliquid"
    symbol: str                          # 거래소별 표기 (BTCUSDT / BTCUSDT-PERP 등)
    fetched_at_ms: int                   # snapshot 시각 (ms epoch)
    # OI (notional USD)
    oi_usd: float | None = None          # Open Interest in USD notional
    # Funding rate (next funding period 비율, 0.0001 = 0.01%)
    funding_rate: float | None = None
    # 24h 가격 / 거래량
    price: float | None = None
    price_24h_change_pct: float | None = None  # +/- %
    volume_24h_usd: float | None = None
    # Long-Short ratio (account-based, global)
    ls_ratio_global: float | None = None       # long_account / short_account
    long_account_pct: float | None = None      # 0..1
    short_account_pct: float | None = None
    # Top trader long-short ratio (position 기준)
    ls_ratio_top_position: float | None = None
    # Top trader long-short ratio (account 기준)
    ls_ratio_top_account: float | None = None
    # 에러 메시지 (UI debug 용)
    errors: list[str] = field(default_factory=list)


class ExchangeMarketData(ABC):
    """거래소별 시장 자료 fetcher ABC (v0.1.87).

    구현체는 ``EXCHANGE_NAME`` (str), ``symbol_for(coin)`` (str → str), ``fetch_snapshot``
    박음. ``fetch_snapshot`` 안 측 endpoint 별 sub-method 분리해 부분 실패 격리.
    """

    EXCHANGE_NAME: str = "<override>"

    def symbol_for(self, coin: str) -> str:
        """coin (BTC / ETH 등) → 거래소별 perpetual symbol.

        기본 = ``f"{coin}USDT"`` (Binance / Bybit / OKX 등 통상). Hyperliquid 같은
        별도 표기 거래소는 override.
        """
        return f"{coin}USDT"

    @abstractmethod
    async def fetch_snapshot(
        self,
        session: aiohttp.ClientSession,
        coin: str,
    ) -> ExchangeSnapshot:
        """주어진 coin 의 시장 자료 한 시점 snapshot fetch.

        Args:
            session: aiohttp.ClientSession (호출자가 lifecycle 관리).
            coin: ``"BTC"`` / ``"ETH"`` 등 — ``symbol_for(coin)`` 으로 변환.

        Returns:
            ``ExchangeSnapshot`` — 부분 실패 시 해당 필드만 None + ``errors`` 에 기록.
            전체 실패해도 ExchangeSnapshot 자체는 항상 반환 (UI 표시 본질).
        """
        ...
