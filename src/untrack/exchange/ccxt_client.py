"""ccxt 기반 통합 어댑터 — Bybit / OKX / Binance.

ccxt가 거래소 차이를 흡수하므로 한 클래스로 멀티 거래소 지원.

담당: 팀원 B
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from untrack.exchange.base import ExchangeClient, Order, Position


class CcxtClient(ExchangeClient):
    """ccxt 기반 거래소 어댑터.

    Args:
        exchange_id: "bybit" | "okx" | "binance"
        api_key, api_secret: 거래소 API 키
        passphrase: OKX 전용
        testnet: 테스트넷 여부
    """

    def __init__(
        self,
        exchange_id: Literal["bybit", "okx", "binance"],
        api_key: str,
        api_secret: str,
        passphrase: str = "",
        testnet: bool = False,
    ) -> None:
        self.name = exchange_id
        # TODO(B): ccxt.async_support 인스턴스 생성
        # 거래소별 특이사항:
        #   - Bybit: defaultType='swap' (USDT 무기한)
        #   - OKX: passphrase 필수
        #   - Binance: defaultType='future'
        raise NotImplementedError

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
    ) -> pd.DataFrame:
        """OHLCV 캔들 → DataFrame.

        반환 컬럼: ['open', 'high', 'low', 'close', 'volume']
        인덱스: pandas DatetimeIndex (UTC)
        """
        # TODO(B)
        raise NotImplementedError

    async def fetch_position(self, symbol: str) -> Position | None:
        # TODO(B)
        raise NotImplementedError

    async def place_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        qty: float,
        price: float | None = None,
        reduce_only: bool = False,
    ) -> Order:
        # TODO(B): paper 모드에서는 실제 호출 없이 가짜 Order 반환
        raise NotImplementedError

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        # TODO(B)
        raise NotImplementedError

    async def cancel_all(self, symbol: str) -> None:
        # TODO(B)
        raise NotImplementedError
