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


# ============================================================
# 에러 경로 (lines 81-87, 105, 137-138)
# ============================================================


def test_load_non_dict_json_returns_none() -> None:
    """JSON 루트가 dict 아님 (list 등) — None 반환 (line 105)."""
    p = active_position_store._path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[1, 2, 3]', encoding="utf-8")
    assert active_position_store.load() is None


def test_save_oserror_logs_and_cleans_tmp(caplog) -> None:
    """tmp.replace 실패 시 warning 로그 + tmp 파일 정리 (lines 81-85)."""
    import logging
    from pathlib import Path
    from unittest.mock import patch

    with patch.object(Path, "replace", side_effect=OSError("disk full")):
        with caplog.at_level(logging.WARNING):
            active_position_store.save(
                plan=_plan(), symbol="X", triggered_by=[],
                opened_at_ts=0, remaining_qty=0.01, tp_hits=0,
            )

    assert any("save 실패" in r.message for r in caplog.records)
    # tmp 파일이 남아있지 않아야 함
    p = active_position_store._path()
    assert not p.with_suffix(p.suffix + ".tmp").exists()


def test_save_oserror_tmp_unlink_fails_silently(caplog) -> None:
    """tmp.replace 실패 + tmp.unlink 도 실패 → silent pass (lines 86-87)."""
    import logging
    from pathlib import Path
    from unittest.mock import patch

    with patch.object(Path, "replace", side_effect=OSError("disk full")):
        with patch.object(Path, "unlink", side_effect=OSError("cannot delete")):
            with caplog.at_level(logging.WARNING):
                active_position_store.save(
                    plan=_plan(), symbol="X", triggered_by=[],
                    opened_at_ts=0, remaining_qty=0.01, tp_hits=0,
                )

    # 예외가 밖으로 새지 않고, warning 은 남아야 함
    assert any("save 실패" in r.message for r in caplog.records)


def test_clear_unlink_oserror_logs_warning(caplog) -> None:
    """p.unlink() 가 OSError → warning 로그 (lines 137-138)."""
    import logging
    from pathlib import Path
    from unittest.mock import patch

    active_position_store.save(
        plan=_plan(), symbol="X", triggered_by=[],
        opened_at_ts=0, remaining_qty=0.01, tp_hits=0,
    )

    with patch.object(Path, "unlink", side_effect=OSError("cannot delete")):
        with caplog.at_level(logging.WARNING):
            active_position_store.clear()

    assert any("clear 실패" in r.message for r in caplog.records)
