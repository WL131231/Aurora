"""BotInstance 단위 테스트 — lifecycle + configure + 매매 사이클.

기존 (PR-C) 5 케이스: lifecycle / 싱글톤 / 호환성
신규 (Stage 2E C): configure / _step 매매 분기 / property 노출
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from aurora.exchange.base import Balance, Order
from aurora.interfaces import bot_instance


@pytest.fixture(autouse=True)
def _reset() -> None:
    bot_instance.reset_for_test()


# ============================================================
# 기존 PR-C 5 케이스 — 호환성 보존
# ============================================================


@pytest.mark.asyncio
async def test_start_stop_lifecycle() -> None:
    bot = bot_instance.get_instance()
    assert not bot.running
    await bot.start()
    assert bot.running
    await bot.stop()
    assert not bot.running


@pytest.mark.asyncio
async def test_double_start_warns() -> None:
    bot = bot_instance.get_instance()
    await bot.start()
    await bot.start()  # 두 번째는 무시
    assert bot.running
    await bot.stop()


@pytest.mark.asyncio
async def test_double_stop_warns() -> None:
    bot = bot_instance.get_instance()
    await bot.stop()  # 이미 중지 상태
    assert not bot.running


def test_get_instance_returns_singleton() -> None:
    a = bot_instance.get_instance()
    b = bot_instance.get_instance()
    assert a is b


def test_reset_for_test_clears_singleton() -> None:
    a = bot_instance.get_instance()
    bot_instance.reset_for_test()
    b = bot_instance.get_instance()
    assert a is not b


# ============================================================
# configure — 신규 (Stage 2E C)
# ============================================================


def _make_mock_client(*, ohlcv_rows: list | None = None) -> MagicMock:
    """매매 사이클 검증용 mock client — 모든 어댑터 메서드 AsyncMock."""
    client = MagicMock()
    rows = ohlcv_rows if ohlcv_rows is not None else []
    if rows:
        df = pd.DataFrame(
            rows,
            columns=["timestamp_ms", "open", "high", "low", "close", "volume"],
        )
        df.index = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
        df = df[["open", "high", "low", "close", "volume"]]
    else:
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    client.fetch_ohlcv = AsyncMock(return_value=df)
    client.fetch_position = AsyncMock(return_value=None)
    client.fetch_positions = AsyncMock(return_value=[])
    client.get_positions = AsyncMock(return_value=[])
    client.get_equity = AsyncMock(
        return_value=Balance(total_usd=10000.0, free_usd=10000.0, used_usd=0.0),
    )
    client.place_order = AsyncMock(
        return_value=Order(
            order_id="test-1", symbol="BTC/USDT:USDT", side="buy", qty=0.001,
            price=None, status="filled", timestamp_ms=0,
        ),
    )
    client.set_leverage = AsyncMock(return_value=None)
    client.cancel_all = AsyncMock(return_value=None)
    client.close = AsyncMock(return_value=None)
    return client


def test_is_configured_initially_false() -> None:
    """configure 호출 전 — is_configured False."""
    bot = bot_instance.get_instance()
    assert bot.is_configured is False
    assert bot.has_position is False


def test_configure_sets_client_and_options() -> None:
    """configure(client, ...) — 어댑터 + 설정 inject."""
    bot = bot_instance.get_instance()
    client = _make_mock_client()
    bot.configure(
        client=client,
        symbol="ETH/USDT:USDT",
        timeframes=["1H", "4H"],
        leverage=20,
        risk_pct=0.02,
    )
    assert bot.is_configured
    assert bot._symbol == "ETH/USDT:USDT"
    assert bot._timeframes == ["1H", "4H"]
    assert bot._leverage == 20
    assert bot._risk_pct == 0.02


@pytest.mark.asyncio
async def test_configure_blocked_during_running() -> None:
    """running 중 configure → RuntimeError (도중 inject 차단)."""
    bot = bot_instance.get_instance()
    await bot.start()
    try:
        with pytest.raises(RuntimeError, match="running 중 configure"):
            bot.configure(client=_make_mock_client())
    finally:
        await bot.stop()


# ============================================================
# 매매 사이클 (_step) — 신규
# ============================================================


def _make_ohlcv_rows(start_ts_ms: int, count: int, tf_minutes: int, base: float = 100.0):
    """결정론적 OHLCV row list (가격 base 고정)."""
    return [
        [start_ts_ms + i * tf_minutes * 60_000, base, base + 1, base - 1, base + 0.5, 10.0]
        for i in range(count)
    ]


@pytest.mark.asyncio
async def test_start_with_configure_warmups_cache() -> None:
    """configure 후 start — 어댑터 생성 + warmup fetch 호출."""
    bot = bot_instance.get_instance()
    rows = _make_ohlcv_rows(start_ts_ms=1_700_000_000_000, count=10, tf_minutes=60)
    client = _make_mock_client(ohlcv_rows=rows)
    bot.configure(client=client, symbol="BTC/USDT:USDT", timeframes=["1H"])
    await bot.start()
    # warmup 1회 fetch (1H)
    assert client.fetch_ohlcv.call_count >= 1
    await bot.stop()


@pytest.mark.asyncio
async def test_stop_closes_client() -> None:
    """stop → client.close() 호출 + 어댑터 None 으로 정리."""
    bot = bot_instance.get_instance()
    rows = _make_ohlcv_rows(start_ts_ms=1_700_000_000_000, count=5, tf_minutes=60)
    client = _make_mock_client(ohlcv_rows=rows)
    bot.configure(client=client, timeframes=["1H"])
    await bot.start()
    await bot.stop()
    client.close.assert_called_once()
    assert bot._client is None
    assert bot._cache is None
    assert bot._executor is None


@pytest.mark.asyncio
async def test_step_noop_when_not_configured() -> None:
    """configure 없이 _step() 호출 — noop (예외 없음, fetch 0회)."""
    bot = bot_instance.get_instance()
    # _step 직접 호출 (start 없이)
    await bot._step()  # noop
    # 예외 없이 완료


@pytest.mark.asyncio
async def test_has_position_reflects_executor_state() -> None:
    """has_position property — executor.has_position 그대로 반영."""
    bot = bot_instance.get_instance()
    rows = _make_ohlcv_rows(start_ts_ms=1_700_000_000_000, count=5, tf_minutes=60)
    client = _make_mock_client(ohlcv_rows=rows)
    bot.configure(client=client, timeframes=["1H"])
    assert bot.has_position is False  # configure 만, executor 아직
    await bot.start()
    assert bot.has_position is False  # executor 생성됐지만 진입 X
    await bot.stop()


@pytest.mark.asyncio
async def test_step_skips_strategy_when_position_open() -> None:
    """포지션 보유 시 _step 은 트레일링/청산만, strategy 평가 X.

    Why: 동시에 진입+청산 평가하면 같은 봉에서 close+open 가능.
    Aurora 정책 (페어당 1개) 위반 방지.
    """
    bot = bot_instance.get_instance()
    rows = _make_ohlcv_rows(start_ts_ms=1_700_000_000_000, count=10, tf_minutes=60)
    client = _make_mock_client(ohlcv_rows=rows)
    bot.configure(client=client, timeframes=["1H"])
    await bot.start()

    # executor 가짜 포지션 상태로
    bot._executor._plan = MagicMock()                   # type: ignore[union-attr]
    bot._executor._plan.direction = "long"              # type: ignore[union-attr]
    bot._executor._plan.tp_prices = [110, 120, 130, 140]  # type: ignore[union-attr]
    bot._executor._plan.sl_price = 90                   # type: ignore[union-attr]
    bot._executor._remaining_qty = 0.001                # type: ignore[union-attr]
    bot._executor._highest_since_entry = 100            # type: ignore[union-attr]
    bot._executor._lowest_since_entry = 100             # type: ignore[union-attr]

    # get_equity 호출 카운트 baseline (warmup 후)
    baseline_equity_calls = client.get_equity.call_count

    # _step 직접 호출 — 포지션 있으므로 트레일링만, get_equity (진입용) X
    await bot._step()

    # get_equity 호출 X (진입 평가 안 됨)
    assert client.get_equity.call_count == baseline_equity_calls

    await bot.stop()
