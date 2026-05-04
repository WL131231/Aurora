"""active_position_store — 활성 포지션 영속화 단위 테스트 (v0.1.26)."""

from __future__ import annotations

from aurora.core.risk import PositionSize, RiskPlan, TrailingMode
from aurora.interfaces import active_position_store


def _plan(
    direction: str = "long",
    entry: float = 60000.0,
    leverage: int = 10,
    qty: float = 0.01,
) -> RiskPlan:
    return RiskPlan(
        entry_price=entry,
        direction=direction,
        leverage=leverage,
        position=PositionSize(
            notional_usd=entry * qty,
            margin_usd=(entry * qty) / leverage,
            coin_amount=qty,
        ),
        tp_prices=[entry * 1.01, entry * 1.02, entry * 1.03, entry * 1.04],
        sl_price=entry * 0.98,
        trailing_mode=TrailingMode.MOVING_TARGET,
    )


def test_load_returns_none_when_no_file():
    """파일 미존재 — None (첫 실행 / 정상 종료 후 OK)."""
    assert active_position_store.load() is None


def test_save_then_load_roundtrip():
    """save → load → reconstruct_plan — 모든 필드 정확."""
    plan = _plan(direction="long", entry=60000.0, leverage=10, qty=0.01)
    active_position_store.save(
        plan=plan,
        symbol="BTC/USDT:USDT",
        triggered_by=["EMA", "RSI"],
        opened_at_ts=1735000000000,
        remaining_qty=0.005,   # partial 청산 후 잔여
        tp_hits=2,
    )
    saved = active_position_store.load()
    assert saved is not None
    assert saved["symbol"] == "BTC/USDT:USDT"
    assert saved["triggered_by"] == ["EMA", "RSI"]
    assert saved["opened_at_ts"] == 1735000000000
    assert saved["remaining_qty"] == 0.005
    assert saved["tp_hits"] == 2

    restored = active_position_store.reconstruct_plan(saved["plan"])
    assert restored is not None
    assert restored.entry_price == 60000.0
    assert restored.direction == "long"
    assert restored.leverage == 10
    assert restored.position.coin_amount == 0.01
    assert restored.sl_price == 60000.0 * 0.98
    assert restored.trailing_mode == TrailingMode.MOVING_TARGET
    assert len(restored.tp_prices) == 4


def test_clear_removes_file():
    """clear 후 load None."""
    active_position_store.save(
        plan=_plan(),
        symbol="BTC/USDT:USDT",
        triggered_by=[],
        opened_at_ts=0,
        remaining_qty=0.01,
        tp_hits=0,
    )
    assert active_position_store.load() is not None
    active_position_store.clear()
    assert active_position_store.load() is None


def test_clear_when_no_file_does_not_raise():
    """파일 없을 때 clear 호출 — silent OK (첫 실행 보호)."""
    active_position_store.clear()  # 안 raise


def test_load_corrupt_json_returns_none():
    """JSON parse 실패 — None (시작 차단 X)."""
    p = active_position_store._path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("garbage {{{", encoding="utf-8")
    assert active_position_store.load() is None


def test_reconstruct_plan_missing_field_returns_none():
    """plan dict 에 필수 필드 누락 — None."""
    bad = {"entry_price": 100.0}  # direction / leverage 등 missing
    assert active_position_store.reconstruct_plan(bad) is None


def test_reconstruct_plan_invalid_trailing_mode_returns_none():
    """trailing_mode 가 enum 값 아님 — None."""
    plan = _plan()
    active_position_store.save(
        plan=plan, symbol="X", triggered_by=[],
        opened_at_ts=0, remaining_qty=0.01, tp_hits=0,
    )
    saved = active_position_store.load()
    saved["plan"]["trailing_mode"] = "not_a_real_mode"
    assert active_position_store.reconstruct_plan(saved["plan"]) is None


def test_save_overwrites_previous():
    """두 번째 save 가 첫 번째 덮어씀."""
    active_position_store.save(
        plan=_plan(direction="long"),
        symbol="A", triggered_by=["EMA"],
        opened_at_ts=1, remaining_qty=0.01, tp_hits=0,
    )
    active_position_store.save(
        plan=_plan(direction="short"),
        symbol="B", triggered_by=["RSI"],
        opened_at_ts=2, remaining_qty=0.02, tp_hits=3,
    )
    saved = active_position_store.load()
    assert saved["symbol"] == "B"
    assert saved["triggered_by"] == ["RSI"]
    assert saved["tp_hits"] == 3
