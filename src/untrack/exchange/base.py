"""거래소 추상 인터페이스 — 모든 어댑터가 따라야 할 프로토콜.

담당: 팀원 B
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import pandas as pd


@dataclass(slots=True)
class Order:
    """주문 결과."""

    order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    price: float | None  # None이면 시장가
    status: str
    timestamp_ms: int


@dataclass(slots=True)
class Position:
    """포지션 정보."""

    symbol: str
    side: Literal["long", "short"]
    qty: float
    entry_price: float
    leverage: int
    unrealized_pnl: float
    margin_mode: Literal["isolated", "cross"]


class ExchangeClient(Protocol):
    """거래소 어댑터 인터페이스."""

    name: str  # "bybit", "okx", "binance"

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
    ) -> pd.DataFrame:
        """OHLCV 캔들 가져오기."""
        ...

    async def fetch_position(self, symbol: str) -> Position | None:
        """현재 포지션 조회."""
        ...

    async def place_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        qty: float,
        price: float | None = None,
        reduce_only: bool = False,
    ) -> Order:
        """주문 전송."""
        ...

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        """레버리지 설정."""
        ...

    async def cancel_all(self, symbol: str) -> None:
        """전체 주문 취소."""
        ...
