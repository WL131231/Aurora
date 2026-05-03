"""거래소 추상 인터페이스 — 모든 어댑터가 따라야 할 프로토콜.

본 모듈은 거래소 어댑터(ccxt_client.py 등)가 구현해야 할 ``ExchangeClient``
Protocol 과 도메인 dataclass (``Order`` / ``Position`` / ``Balance``) 를 정의.

설계 원칙:
    - 거래소 차이 (Bybit / OKX / Binance) 는 어댑터가 흡수
    - 호출자(``BotInstance`` / ``Executor``) 는 Protocol 만 의존 (테스트 mock 용이)
    - 모든 메서드 ``async`` (실 거래소 호출은 I/O bound)

담당: ChoYoon (exchange 영역) — 어댑터 PR 한정 위임 받음 (DESIGN.md §10, 2026-05-03)
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


@dataclass(slots=True)
class Balance:
    """계정 자본금 — DESIGN.md §4 (옵션 a 어댑터 PR 신설).

    Phase 1 = USDT 단일 자산 기준. 다중 자산은 Phase 3 에서 확장.

    Attributes:
        total_usd: 전체 자본금 (USDT). ``free_usd + used_usd`` 와 일치.
        free_usd:  사용 가능 (마진 안 묶인 자본).
        used_usd:  현재 묶여있는 마진 (열린 포지션의 마진 합).
    """

    total_usd: float
    free_usd: float
    used_usd: float


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
        """단일 페어 포지션 조회."""
        ...

    async def get_positions(self) -> list[Position]:
        """모든 페어 포지션 조회 — 대시보드 / multi-pair 운영용.

        DESIGN.md §4 옵션 a 어댑터 PR 신설. 빈 리스트 반환 가능.
        """
        ...

    async def get_equity(self) -> Balance:
        """계정 자본금 조회 — 대시보드 잔고 표시 + 포지션 사이즈 계산 입력.

        DESIGN.md §4 옵션 a 어댑터 PR 신설. ``run_mode='paper'`` 시
        config 의 가짜 시드 반환 (어댑터 구현 측 결정).
        """
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
