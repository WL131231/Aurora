"""Executor 단위 테스트 — RiskPlan 기반 진입·트레일링·청산.

DESIGN.md §6 + risk.update_trailing_sl 정합 검증.

영역: ChoYoon (어댑터 PR 위임 받음 2026-05-03)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aurora.core.risk import (
    PositionSize,
    RiskPlan,
    TpSlConfig,
    TrailingMode,
)
from aurora.exchange.base import Order
from aurora.exchange.execution import Executor

# ============================================================
# 헬퍼 — RiskPlan / mock client 생성
# ============================================================


def _make_plan(
    *,
    direction: str = "long",
    entry: float = 100.0,
    sl: float = 95.0,
    tps: tuple[float, ...] = (102.0, 104.0, 106.0, 108.0),
    leverage: int = 10,
    coin_amount: float = 1.0,
    trailing_mode: TrailingMode = TrailingMode.MOVING_TARGET,
) -> RiskPlan:
    """테스트용 RiskPlan — 명시적 가격 / 방향만 받음."""
    return RiskPlan(
        entry_price=entry,
        direction=direction,
        leverage=leverage,
        position=PositionSize(
            notional_usd=entry * coin_amount * leverage,
            margin_usd=entry * coin_amount,
            coin_amount=coin_amount,
        ),
        tp_prices=list(tps),
        sl_price=sl,
        trailing_mode=trailing_mode,
    )


def _make_client() -> MagicMock:
    """Executor 가 사용할 ExchangeClient mock — set_leverage / place_order 만."""
    client = MagicMock()
    client.set_leverage = AsyncMock(return_value=None)
    client.place_order = AsyncMock(
        return_value=Order(
            order_id="test-1", symbol="BTC/USDT:USDT", side="buy", qty=1.0,
            price=None, status="filled", timestamp_ms=0,
        ),
    )
    return client


def _make_executor(
    *,
    config: TpSlConfig | None = None,
) -> tuple[Executor, MagicMock]:
    """Executor + mock client 묶음."""
    client = _make_client()
    cfg = config or TpSlConfig(trailing_mode=TrailingMode.MOVING_TARGET)
    executor = Executor(client, "BTC/USDT:USDT", cfg)
    return executor, client


# ============================================================
# open_position
# ============================================================


@pytest.mark.asyncio
async def test_open_position_sets_leverage_first():
    """open → set_leverage 먼저 호출 (margin 모드 명시)."""
    executor, client = _make_executor()
    plan = _make_plan(leverage=20)
    await executor.open_position(plan)
    client.set_leverage.assert_called_once_with("BTC/USDT:USDT", 20)


@pytest.mark.asyncio
async def test_open_position_long_calls_buy_market():
    """long 진입 = buy 시장가 (price=None, reduce_only=False)."""
    executor, client = _make_executor()
    plan = _make_plan(direction="long", coin_amount=0.5)
    await executor.open_position(plan)
    call = client.place_order.call_args
    assert call.kwargs["side"] == "buy"
    assert call.kwargs["qty"] == 0.5
    assert call.kwargs["price"] is None
    assert call.kwargs["reduce_only"] is False


@pytest.mark.asyncio
async def test_open_position_short_calls_sell_market():
    """short 진입 = sell 시장가."""
    executor, client = _make_executor()
    plan = _make_plan(direction="short")
    await executor.open_position(plan)
    assert client.place_order.call_args.kwargs["side"] == "sell"


@pytest.mark.asyncio
async def test_open_position_initializes_state():
    """진입 후 state — has_position True, remaining_qty=qty, tp_hits=0."""
    executor, _ = _make_executor()
    plan = _make_plan(coin_amount=2.0)
    await executor.open_position(plan)
    assert executor.has_position
    assert executor.remaining_qty == 2.0
    assert executor.tp_hits == 0


@pytest.mark.asyncio
async def test_open_position_blocks_duplicate():
    """이미 활성 포지션 보유 중 open → RuntimeError."""
    executor, _ = _make_executor()
    plan = _make_plan()
    await executor.open_position(plan)
    with pytest.raises(RuntimeError, match="활성 포지션 존재"):
        await executor.open_position(plan)


# ============================================================
# update_trailing_sl
# ============================================================


@pytest.mark.asyncio
async def test_update_trailing_sl_no_position_returns_false():
    """활성 포지션 없으면 False (no-op)."""
    executor, _ = _make_executor()
    result = await executor.update_trailing_sl(100.0)
    assert result is False


@pytest.mark.asyncio
async def test_update_trailing_sl_counts_tp_hits_long():
    """long 진입 후 가격 상승 시 tp_hits 단계별 증가."""
    executor, _ = _make_executor()
    plan = _make_plan(direction="long", entry=100.0, tps=(102.0, 104.0, 106.0, 108.0))
    await executor.open_position(plan)
    # 가격 102 도달 → tp_hits=1
    await executor.update_trailing_sl(102.5)
    assert executor.tp_hits == 1
    # 105 → 104 도달 → tp_hits=2
    await executor.update_trailing_sl(105.0)
    assert executor.tp_hits == 2


@pytest.mark.asyncio
async def test_update_trailing_sl_counts_tp_hits_short():
    """short 진입 후 가격 하락 시 tp_hits 단계별 증가."""
    executor, _ = _make_executor()
    plan = _make_plan(direction="short", entry=100.0, tps=(98.0, 96.0, 94.0, 92.0))
    await executor.open_position(plan)
    await executor.update_trailing_sl(97.5)
    assert executor.tp_hits == 1
    await executor.update_trailing_sl(95.0)
    assert executor.tp_hits == 2


@pytest.mark.asyncio
async def test_update_trailing_sl_moves_to_breakeven_after_tp1():
    """MOVING_TARGET 모드 + TP1 도달 → SL = entry_price (브레이크이븐)."""
    executor, _ = _make_executor(
        config=TpSlConfig(trailing_mode=TrailingMode.MOVING_TARGET),
    )
    plan = _make_plan(
        direction="long", entry=100.0, sl=95.0,
        tps=(102.0, 104.0, 106.0, 108.0),
    )
    await executor.open_position(plan)
    # TP1 (102) 도달 → MOVING_TARGET tp_hits=1 일 때 SL=entry (브레이크이븐)
    changed = await executor.update_trailing_sl(102.5)
    assert changed is True
    assert plan.sl_price == 100.0  # 95 → 100 (entry)


@pytest.mark.asyncio
async def test_update_trailing_sl_tracks_highest_lowest():
    """high/low 추적 — PERCENT_BELOW_HIGHEST 모드 입력."""
    executor, _ = _make_executor(
        config=TpSlConfig(
            trailing_mode=TrailingMode.PERCENT_BELOW_HIGHEST, trailing_pct=2.0,
        ),
    )
    plan = _make_plan(direction="long", entry=100.0, sl=95.0)
    await executor.open_position(plan)
    # 가격 110 → highest=110 → SL = 110 × 0.98 = 107.8
    await executor.update_trailing_sl(110.0)
    assert plan.sl_price == pytest.approx(107.8)
    # 105 → highest 그대로 110 (단방향)
    await executor.update_trailing_sl(105.0)
    assert plan.sl_price == pytest.approx(107.8)


# ============================================================
# should_close
# ============================================================


@pytest.mark.asyncio
async def test_should_close_sl_long():
    """long SL 도달 — current ≤ sl_price."""
    executor, _ = _make_executor()
    plan = _make_plan(direction="long", entry=100.0, sl=95.0)
    await executor.open_position(plan)
    assert executor.should_close(94.5) == "sl"
    assert executor.should_close(95.0) == "sl"  # 경계 포함
    assert executor.should_close(96.0) is None


@pytest.mark.asyncio
async def test_should_close_sl_short():
    """short SL 도달 — current ≥ sl_price."""
    executor, _ = _make_executor()
    plan = _make_plan(direction="short", entry=100.0, sl=105.0)
    await executor.open_position(plan)
    assert executor.should_close(105.5) == "sl"
    assert executor.should_close(105.0) == "sl"
    assert executor.should_close(104.0) is None


@pytest.mark.asyncio
async def test_should_close_tp_full_after_all_tp_hit():
    """모든 TP 단계 도달 — 'tp_full' 반환 (잔여 전량 청산 트리거)."""
    executor, _ = _make_executor()
    plan = _make_plan(direction="long", entry=100.0, tps=(102.0, 104.0, 106.0, 108.0))
    await executor.open_position(plan)
    # TP4 (108) 까지 도달
    await executor.update_trailing_sl(108.5)
    assert executor.tp_hits == 4
    assert executor.should_close(108.0) == "tp_full"


def test_should_close_returns_none_when_no_position():
    """활성 포지션 없으면 None."""
    executor, _ = _make_executor()
    assert executor.should_close(100.0) is None


# ============================================================
# close_position
# ============================================================


@pytest.mark.asyncio
async def test_close_position_full_default():
    """qty 미명시 → 잔여 전량 청산 + state reset."""
    executor, client = _make_executor()
    plan = _make_plan(direction="long", coin_amount=2.0)
    await executor.open_position(plan)
    await executor.close_position(reason="manual")
    # 청산 주문 = 잔여 전량, reduce_only=True
    last_call = client.place_order.call_args
    assert last_call.kwargs["side"] == "sell"  # long 청산 = sell
    assert last_call.kwargs["qty"] == 2.0
    assert last_call.kwargs["reduce_only"] is True
    # state reset
    assert not executor.has_position
    assert executor.remaining_qty == 0.0


@pytest.mark.asyncio
async def test_close_position_partial_keeps_state():
    """부분 청산 — qty 명시 시 잔여 유지, state 그대로."""
    executor, client = _make_executor()
    plan = _make_plan(direction="long", coin_amount=2.0)
    await executor.open_position(plan)
    await executor.close_position(qty=0.5, reason="tp_partial")
    assert executor.has_position
    assert executor.remaining_qty == pytest.approx(1.5)
    last_call = client.place_order.call_args
    assert last_call.kwargs["qty"] == 0.5


@pytest.mark.asyncio
async def test_close_position_short_calls_buy():
    """short 청산 = buy reduce_only (반대 방향)."""
    executor, client = _make_executor()
    plan = _make_plan(direction="short")
    await executor.open_position(plan)
    await executor.close_position()
    last_call = client.place_order.call_args
    assert last_call.kwargs["side"] == "buy"
    assert last_call.kwargs["reduce_only"] is True


@pytest.mark.asyncio
async def test_close_position_resets_when_partial_sums_to_full():
    """부분 청산 누적이 잔여 0 도달 시 state reset (epsilon 처리 확인)."""
    executor, _ = _make_executor()
    plan = _make_plan(direction="long", coin_amount=1.0)
    await executor.open_position(plan)
    await executor.close_position(qty=0.5, reason="tp_partial")
    assert executor.has_position
    await executor.close_position(qty=0.5, reason="tp_partial")
    assert not executor.has_position
    assert executor.remaining_qty == 0.0


@pytest.mark.asyncio
async def test_close_position_no_active_raises():
    """활성 포지션 없는데 close → RuntimeError."""
    executor, _ = _make_executor()
    with pytest.raises(RuntimeError, match="활성 포지션 없음"):
        await executor.close_position()


@pytest.mark.asyncio
async def test_close_position_oversize_qty_raises():
    """잔여보다 큰 qty 청산 시도 → RuntimeError (회귀 보호)."""
    executor, _ = _make_executor()
    plan = _make_plan(coin_amount=1.0)
    await executor.open_position(plan)
    with pytest.raises(RuntimeError, match="remaining"):
        await executor.close_position(qty=1.5)


# ============================================================
# tp_hits 단방향 — 가격 후퇴 시 카운트 보존 (회귀 보호)
# ============================================================


@pytest.mark.asyncio
async def test_tp_hits_does_not_decrease_on_pullback():
    """TP1 도달 후 가격 후퇴해도 tp_hits 유지 (단방향 카운트)."""
    executor, _ = _make_executor()
    plan = _make_plan(direction="long", entry=100.0, tps=(102.0, 104.0, 106.0, 108.0))
    await executor.open_position(plan)
    await executor.update_trailing_sl(103.0)  # TP1 도달
    assert executor.tp_hits == 1
    await executor.update_trailing_sl(101.0)  # 후퇴
    assert executor.tp_hits == 1  # 유지


# ============================================================
# restore_plan (v0.1.26) — 재시작 후 포지션 복원
# ============================================================


def test_restore_plan_no_existing_position():
    """빈 Executor 에 restore_plan — has_position=True + 모든 state 정확."""
    executor, client = _make_executor()
    plan = _make_plan(
        direction="long", entry=100.0, sl=95.0,
        tps=(102.0, 104.0, 106.0, 108.0),
    )

    executor.restore_plan(
        plan=plan,
        triggered_by=["EMA", "RSI"],
        opened_at_ts=1735000000000,
        remaining_qty=0.7,    # partial 청산 후 잔여
        tp_hits=2,
    )
    # 거래소 호출 X — restore 는 메모리 state 만 채움
    client.set_leverage.assert_not_called()
    client.place_order.assert_not_called()

    assert executor.has_position is True
    assert executor.triggered_by == ["EMA", "RSI"]
    assert executor.tp_hits == 2
    assert executor.remaining_qty == 0.7
    # high/low 는 entry_price 부터 출발 (current_market 모름)
    assert executor._highest_since_entry == 100.0
    assert executor._lowest_since_entry == 100.0


def test_restore_plan_blocks_when_position_active():
    """이미 활성 포지션 있는데 restore — RuntimeError (사용 패턴 위반)."""
    import asyncio
    executor, _ = _make_executor()
    plan = _make_plan(direction="long")
    asyncio.run(executor.open_position(plan))

    with pytest.raises(RuntimeError, match="활성 포지션 존재"):
        executor.restore_plan(
            plan=_make_plan(direction="short"),
            triggered_by=[],
            opened_at_ts=0, remaining_qty=1.0, tp_hits=0,
        )


@pytest.mark.asyncio
async def test_restore_plan_then_trailing_works():
    """restore 후 update_trailing_sl 정상 동작 — TP/SL 추적 이어짐."""
    executor, _ = _make_executor()
    plan = _make_plan(
        direction="long", entry=100.0, sl=95.0,
        tps=(102.0, 104.0, 106.0, 108.0),
    )
    executor.restore_plan(
        plan=plan, triggered_by=[], opened_at_ts=0,
        remaining_qty=1.0, tp_hits=1,   # TP1 이미 도달 상태 복원
    )
    # 가격이 TP2 도달 → tp_hits 2 로 증가
    await executor.update_trailing_sl(105.0)
    assert executor.tp_hits == 2
