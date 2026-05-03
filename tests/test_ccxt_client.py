"""ccxt_client.py 단위 테스트 — mock 기반, 외부 네트워크 X.

pytest-asyncio 자동 모드 (pyproject.toml asyncio_mode=auto) 활용.
ccxt async 인스턴스를 ``unittest.mock.AsyncMock`` 으로 패치.

검증 카테고리:
    - 인스턴스 생성 옵션 (Bybit perpetual options + Demo Trading)
    - paper 모드 분기 (place_order / set_leverage / cancel_all 가짜 응답)
    - fetch_ohlcv 변환 (ccxt row → DataFrame)
    - fetch_position / get_positions (ccxt position → Aurora Position)
    - get_equity (ccxt balance → Aurora Balance)
    - close() lifecycle

담당: ChoYoon (어댑터 PR 위임 받음, 2026-05-03)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from aurora.exchange.base import Balance, Order
from aurora.exchange.ccxt_client import CcxtClient

# ============================================================
# 인스턴스 생성 — DESIGN.md §3.1 옵션 검증
# ============================================================


def _make_client(exchange_id: str = "bybit", demo: bool = True) -> CcxtClient:
    """테스트용 CcxtClient — async 인스턴스를 MagicMock 으로 대체."""
    with patch("aurora.exchange.ccxt_client.ccxt_async") as mock_async:
        # ex_class = getattr(ccxt_async, exchange_id) → MagicMock
        mock_ex_instance = MagicMock()
        mock_ex_instance.fetch_ohlcv = AsyncMock(return_value=[])
        mock_ex_instance.fetch_positions = AsyncMock(return_value=[])
        mock_ex_instance.fetch_balance = AsyncMock(return_value={})
        mock_ex_instance.create_order = AsyncMock(return_value={})
        mock_ex_instance.set_leverage = AsyncMock(return_value=None)
        mock_ex_instance.cancel_all_orders = AsyncMock(return_value=None)
        mock_ex_instance.load_time_difference = AsyncMock(return_value=None)
        mock_ex_instance.close = AsyncMock(return_value=None)
        mock_ex_instance.enableDemoTrading = MagicMock(return_value=None)

        mock_ex_class = MagicMock(return_value=mock_ex_instance)
        setattr(mock_async, exchange_id, mock_ex_class)

        client = CcxtClient(
            exchange_id=exchange_id,  # type: ignore[arg-type]
            api_key="test-key",
            api_secret="test-secret",
            demo=demo,
        )
        # 테스트가 mock 호출 검증할 수 있게 노출
        client._mock_ex = mock_ex_instance        # type: ignore[attr-defined]
        client._mock_class = mock_ex_class        # type: ignore[attr-defined]
        return client


def test_constructor_bybit_perpetual_options():
    """Bybit 인스턴스 생성 시 swap + linear + recvWindow=60000 옵션 적용."""
    client = _make_client(exchange_id="bybit")
    # 인스턴스 생성 호출 인자 검증
    call_args = client._mock_class.call_args        # type: ignore[attr-defined]
    config = call_args.args[0] if call_args.args else call_args.kwargs.get("config", {})
    options = config["options"]
    assert options["defaultType"] == "swap"
    assert options["defaultSubType"] == "linear"   # USDT-margined 명시 (PR-2 패턴)
    assert options["recvWindow"] == 60000          # Windows clock skew
    assert options["adjustForTimeDifference"] is True
    assert config["enableRateLimit"] is True


def test_constructor_bybit_demo_enabled():
    """demo=True + bybit 일 때 enableDemoTrading 호출."""
    client = _make_client(exchange_id="bybit", demo=True)
    client._mock_ex.enableDemoTrading.assert_called_once_with(True)  # type: ignore[attr-defined]


def test_constructor_bybit_demo_disabled():
    """demo=False 일 때 enableDemoTrading 호출 X."""
    client = _make_client(exchange_id="bybit", demo=False)
    client._mock_ex.enableDemoTrading.assert_not_called()  # type: ignore[attr-defined]


# ============================================================
# fetch_ohlcv — DataFrame 변환
# ============================================================


@pytest.mark.asyncio
async def test_fetch_ohlcv_converts_rows_to_df():
    """ccxt row list → DatetimeIndex DataFrame 변환."""
    client = _make_client()
    client._mock_ex.fetch_ohlcv = AsyncMock(return_value=[  # type: ignore[attr-defined]
        [1700000000000, 100.0, 110.0, 95.0, 105.0, 1000.0],
        [1700003600000, 105.0, 108.0, 102.0, 107.0, 800.0],
    ])

    df = await client.fetch_ohlcv("BTC/USDT:USDT", "1H", limit=2)

    assert isinstance(df.index, pd.DatetimeIndex)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2
    assert df.iloc[0]["close"] == 105.0
    assert df.iloc[1]["volume"] == 800.0


@pytest.mark.asyncio
async def test_fetch_ohlcv_empty_response_returns_empty_df():
    """빈 응답 — 빈 DataFrame (호출자가 안전하게 처리 가능)."""
    client = _make_client()
    df = await client.fetch_ohlcv("BTC/USDT:USDT", "1H", limit=10)
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


@pytest.mark.asyncio
async def test_fetch_ohlcv_normalizes_aurora_tf_to_ccxt():
    """Aurora 포맷 timeframe → ccxt 포맷 변환 후 호출."""
    client = _make_client()
    await client.fetch_ohlcv("BTC/USDT:USDT", "1H", limit=10)
    call_args = client._mock_ex.fetch_ohlcv.call_args  # type: ignore[attr-defined]
    # ccxt 포맷 = 소문자
    assert call_args.args[1] == "1h"


# ============================================================
# Position / Balance
# ============================================================


@pytest.mark.asyncio
async def test_get_equity_parses_usdt_balance():
    """ccxt balance.USDT → Aurora Balance dataclass."""
    client = _make_client()
    client._mock_ex.fetch_balance = AsyncMock(return_value={  # type: ignore[attr-defined]
        "USDT": {"total": 8663.43, "free": 5000.0, "used": 3663.43},
    })
    balance = await client.get_equity()
    assert isinstance(balance, Balance)
    assert balance.total_usd == 8663.43
    assert balance.free_usd == 5000.0
    assert balance.used_usd == 3663.43


@pytest.mark.asyncio
async def test_get_equity_handles_missing_usdt():
    """USDT 키 없는 응답 — 0 으로 안전 fallback."""
    client = _make_client()
    client._mock_ex.fetch_balance = AsyncMock(return_value={})  # type: ignore[attr-defined]
    balance = await client.get_equity()
    assert balance.total_usd == 0.0
    assert balance.free_usd == 0.0
    assert balance.used_usd == 0.0


@pytest.mark.asyncio
async def test_get_positions_filters_zero_contracts():
    """contracts=0 인 row 는 필터링 (close 된 포지션 응답 방어).

    Note: paper 모드는 빈 리스트 즉시 반환이므로 demo 모드 명시 필요
    (CI 환경 .env 부재 → default "paper" 회귀 보호).
    """
    with patch("aurora.exchange.ccxt_client.settings") as mock_settings:
        mock_settings.run_mode = "demo"
        client = _make_client()
        client._mock_ex.fetch_positions = AsyncMock(return_value=[  # type: ignore[attr-defined]
            {"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.5,
             "entryPrice": 50000, "leverage": 10, "unrealizedPnl": 100, "marginMode": "isolated"},
            {"symbol": "ETH/USDT:USDT", "side": "short", "contracts": 0,  # close 됨
             "entryPrice": 0, "leverage": 1, "unrealizedPnl": 0, "marginMode": "isolated"},
        ])
        positions = await client.get_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "BTC/USDT:USDT"
        assert positions[0].side == "long"
        assert positions[0].qty == 0.5


@pytest.mark.asyncio
async def test_fetch_position_returns_none_when_no_open():
    """open contract 없으면 None — demo 모드 (실 fetch_positions 호출) 검증.

    Note: paper 모드 None 반환은 별도 ``test_paper_mode_fetch_position_returns_none``.
    """
    with patch("aurora.exchange.ccxt_client.settings") as mock_settings:
        mock_settings.run_mode = "demo"
        client = _make_client()
        client._mock_ex.fetch_positions = AsyncMock(return_value=[  # type: ignore[attr-defined]
            {"symbol": "BTC/USDT:USDT", "contracts": 0},
        ])
        pos = await client.fetch_position("BTC/USDT:USDT")
        assert pos is None


# ============================================================
# Paper 모드 — DESIGN.md §3.2 / E-3
# ============================================================


@pytest.mark.asyncio
async def test_paper_mode_place_order_returns_fake():
    """paper 모드 = 실 호출 X, 가짜 'filled' Order 반환."""
    with patch("aurora.exchange.ccxt_client.settings") as mock_settings:
        mock_settings.run_mode = "paper"
        client = _make_client()
        order = await client.place_order(
            "BTC/USDT:USDT", side="buy", qty=0.1, price=None,
        )
        assert isinstance(order, Order)
        assert order.status == "filled"
        assert order.order_id.startswith("paper-")
        # 거래소 호출 검증 — create_order 안 불려야
        client._mock_ex.create_order.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_paper_mode_set_leverage_skipped():
    """paper 모드 = set_leverage 노출 X."""
    with patch("aurora.exchange.ccxt_client.settings") as mock_settings:
        mock_settings.run_mode = "paper"
        client = _make_client()
        await client.set_leverage("BTC/USDT:USDT", 10)
        client._mock_ex.set_leverage.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_paper_mode_fetch_position_returns_none():
    """paper 모드 = 항상 None (실 fetch_positions 호출 X)."""
    with patch("aurora.exchange.ccxt_client.settings") as mock_settings:
        mock_settings.run_mode = "paper"
        client = _make_client()
        result = await client.fetch_position("BTC/USDT:USDT")
        assert result is None
        client._mock_ex.fetch_positions.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_paper_mode_get_positions_returns_empty():
    """paper 모드 = 빈 리스트."""
    with patch("aurora.exchange.ccxt_client.settings") as mock_settings:
        mock_settings.run_mode = "paper"
        client = _make_client()
        result = await client.get_positions()
        assert result == []


# ============================================================
# Demo / Live 모드 실제 호출
# ============================================================


@pytest.mark.asyncio
async def test_demo_mode_place_order_calls_ccxt():
    """demo 모드 = 실 create_order 호출."""
    with patch("aurora.exchange.ccxt_client.settings") as mock_settings:
        mock_settings.run_mode = "demo"
        client = _make_client()
        client._mock_ex.create_order = AsyncMock(return_value={  # type: ignore[attr-defined]
            "id": "12345", "symbol": "BTC/USDT:USDT", "amount": 0.1,
            "price": None, "status": "filled", "timestamp": 1700000000000,
        })
        order = await client.place_order(
            "BTC/USDT:USDT", side="buy", qty=0.1, price=None,
        )
        assert order.order_id == "12345"
        assert order.status == "filled"
        client._mock_ex.create_order.assert_called_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_set_leverage_arg_order():
    """ccxt set_leverage(leverage, symbol) — 인자 순서 반대 검증 (회귀 보호)."""
    with patch("aurora.exchange.ccxt_client.settings") as mock_settings:
        mock_settings.run_mode = "demo"
        client = _make_client()
        await client.set_leverage("BTC/USDT:USDT", 10)
        client._mock_ex.set_leverage.assert_called_once_with(10, "BTC/USDT:USDT")  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_place_order_reduce_only_param():
    """reduce_only=True 시 ccxt params 에 reduceOnly=True 전달."""
    with patch("aurora.exchange.ccxt_client.settings") as mock_settings:
        mock_settings.run_mode = "demo"
        client = _make_client()
        client._mock_ex.create_order = AsyncMock(return_value={"id": "1"})  # type: ignore[attr-defined]
        await client.place_order(
            "BTC/USDT:USDT", side="sell", qty=0.1, reduce_only=True,
        )
        call_args = client._mock_ex.create_order.call_args  # type: ignore[attr-defined]
        params = call_args.args[5]
        assert params == {"reduceOnly": True}


# ============================================================
# Lifecycle — _ensure_init / close
# ============================================================


@pytest.mark.asyncio
async def test_ensure_init_called_once():
    """첫 호출 시 load_time_difference, 그 후 호출은 skip."""
    with patch("aurora.exchange.ccxt_client.settings") as mock_settings:
        mock_settings.run_mode = "demo"
        client = _make_client()
        await client.get_equity()
        await client.get_equity()
        assert client._mock_ex.load_time_difference.call_count == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_close_calls_ccxt_close():
    """close() → ccxt async 인스턴스 close (httpx 세션 정리)."""
    client = _make_client()
    await client.close()
    client._mock_ex.close.assert_called_once()  # type: ignore[attr-defined]
