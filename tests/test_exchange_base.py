"""exchange/base.py — Balance dataclass + ExchangeClient Protocol 단위 테스트.

dataclass 필드 / 타입 / 기본 동작 + Protocol 충족 검증 (mock client).

담당: ChoYoon (exchange 영역) — 어댑터 PR 한정 위임 받음 (2026-05-03)
"""

from __future__ import annotations

from typing import Literal

import pandas as pd
import pytest

from aurora.exchange.base import (
    Balance,
    ExchangeClient,
    Order,
    Position,
)

# ============================================================
# Balance dataclass — Stage 2A 신설 (DESIGN.md §4)
# ============================================================


def test_balance_dataclass_creation():
    """Balance 생성 + 필드 접근 — total/free/used USDT."""
    b = Balance(total_usd=10000.0, free_usd=8000.0, used_usd=2000.0)
    assert b.total_usd == 10000.0
    assert b.free_usd == 8000.0
    assert b.used_usd == 2000.0


def test_balance_slots_no_extra_attrs():
    """slots=True — 임의 속성 추가 차단 (메모리 안정성)."""
    b = Balance(total_usd=100.0, free_usd=100.0, used_usd=0.0)
    with pytest.raises(AttributeError):
        b.unknown_field = 42  # type: ignore[attr-defined]


def test_balance_consistency_check_caller_responsibility():
    """Balance 자체는 total = free + used 강제 X — 호출자가 보장.

    Note: 거래소 응답이 일시적 불일치 가능 (rate limit / async race) → 어댑터
    측에서 보정 또는 호출자가 처리. dataclass 단계에선 raw 값 그대로 보존.
    """
    # 의도적 불일치 — 어댑터 raw 응답 시나리오
    b = Balance(total_usd=100.0, free_usd=50.0, used_usd=30.0)  # 합 = 80 ≠ total 100
    assert b.total_usd == 100.0
    # 검증 로직은 호출자 책임 (어댑터 디버깅 시점에만)


# ============================================================
# ExchangeClient Protocol — Mock 충족 검증
# ============================================================


class _MockClient:
    """ExchangeClient Protocol 만족하는 최소 mock — Protocol structural typing 검증용.

    실제 어댑터 (ccxt_client.py) 가 Stage 2B 에서 본 구현. 본 mock 은 Protocol
    충족 여부만 검증.
    """

    name: str = "mock"

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
    ) -> pd.DataFrame:
        return pd.DataFrame()

    async def fetch_position(self, symbol: str) -> Position | None:
        return None

    async def get_positions(self) -> list[Position]:
        return []

    async def get_equity(self) -> Balance:
        return Balance(total_usd=0.0, free_usd=0.0, used_usd=0.0)

    async def place_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        qty: float,
        price: float | None = None,
        reduce_only: bool = False,
    ) -> Order:
        return Order(
            order_id="mock-1", symbol=symbol, side=side, qty=qty,
            price=price, status="filled", timestamp_ms=0,
        )

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        return None

    async def cancel_all(self, symbol: str) -> None:
        return None

    async def fetch_closed_positions(
        self,
        since_ms: int | None = None,
        limit: int = 200,
    ) -> list:
        return []


def test_mock_client_satisfies_protocol():
    """_MockClient 가 ExchangeClient Protocol 만족 (structural typing).

    Why: Protocol 신설 메서드 (get_positions / get_equity) 누락 시 컴파일/런타임 X
    하지만 호출자가 기대하는 인터페이스 누락 — 본 테스트가 회귀 보호.
    """
    client: ExchangeClient = _MockClient()  # 타입 할당 가능 = Protocol 만족
    assert client.name == "mock"


@pytest.mark.asyncio
async def test_mock_get_equity_returns_balance():
    """get_equity() Balance 반환 — Stage 2A 신설 메서드 동작 검증."""
    client = _MockClient()
    balance = await client.get_equity()
    assert isinstance(balance, Balance)
    assert balance.total_usd == 0.0


@pytest.mark.asyncio
async def test_mock_get_positions_returns_list():
    """get_positions() list[Position] 반환 — Stage 2A 신설 메서드 동작 검증."""
    client = _MockClient()
    positions = await client.get_positions()
    assert isinstance(positions, list)
    assert len(positions) == 0
