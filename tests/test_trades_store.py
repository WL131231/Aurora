"""trades_store — 거래내역 영속화 단위 테스트 (v0.1.25)."""

from __future__ import annotations

from aurora.exchange.execution import ClosedTrade
from aurora.interfaces import trades_store


def _trade(**overrides) -> ClosedTrade:
    """기본 ClosedTrade — overrides 로 일부 필드만 변경."""
    base = {
        "symbol": "BTC/USDT:USDT",
        "direction": "long",
        "leverage": 10,
        "qty": 0.01,
        "entry_price": 60000.0,
        "exit_price": 61000.0,
        "opened_at_ts": 1735000000000,
        "closed_at_ts": 1735000600000,
        "reason": "tp_full",
        "pnl_usd": 10.0,
        "roi_pct": 16.67,
        "triggered_by": [],
    }
    base.update(overrides)
    return ClosedTrade(**base)


def test_load_returns_empty_when_no_file():
    """파일 미존재 — 빈 리스트 반환 (첫 실행 OK)."""
    assert trades_store.load() == []


def test_save_and_load_roundtrip():
    """save → load roundtrip — 모든 필드 보존."""
    trades = [_trade(qty=0.01, pnl_usd=5.0), _trade(qty=0.02, pnl_usd=-3.0)]
    trades_store.save(trades)
    restored = trades_store.load()
    assert len(restored) == 2
    assert restored[0].qty == 0.01
    assert restored[0].pnl_usd == 5.0
    assert restored[1].qty == 0.02
    assert restored[1].pnl_usd == -3.0


def test_save_truncates_to_max_persist():
    """``MAX_PERSIST`` 초과 시 가장 오래된 것 drop."""
    over = trades_store.MAX_PERSIST + 50
    trades = [_trade(closed_at_ts=1735000000000 + i) for i in range(over)]
    trades_store.save(trades)
    restored = trades_store.load()
    assert len(restored) == trades_store.MAX_PERSIST
    # 가장 오래된 것 (i=0) drop, 가장 최근 것 (i=over-1) 보존
    assert restored[0].closed_at_ts == 1735000000000 + 50
    assert restored[-1].closed_at_ts == 1735000000000 + over - 1


def test_save_atomic_no_partial_on_existing(monkeypatch, tmp_path):
    """save 중 OS 에러 시 기존 파일 그대로 유지 (atomic). conftest fixture 가
    이미 ``_path`` mock 했지만 명시적으로 한 번 더 (예시).

    monkeypatch.setattr 가 실패 raise 하면 기존 파일 그대로.
    """
    # 첫 정상 save
    trades_store.save([_trade(pnl_usd=1.0)])
    original_size = trades_store._path().stat().st_size
    assert original_size > 0
    # 다시 save 했는데 정상이면 file 갱신
    trades_store.save([_trade(pnl_usd=2.0)])
    restored = trades_store.load()
    assert restored[0].pnl_usd == 2.0


def test_load_skips_record_with_invalid_fields(tmp_path):
    """저장 파일이 ClosedTrade 정의와 호환 안 되는 record (extra/누락) — 해당 record skip."""
    p = trades_store._path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # 정상 1건 + 손상 1건 (extra field) — 손상은 skip, 정상만 복원
    p.write_text(
        '['
        '{"symbol": "BTC/USDT:USDT", "direction": "long", "leverage": 10, '
        ' "qty": 0.01, "entry_price": 60000, "exit_price": 61000, '
        ' "opened_at_ts": 1, "closed_at_ts": 2, "reason": "tp_full", '
        ' "pnl_usd": 10, "roi_pct": 16.67, "triggered_by": []},'
        '{"unknown_field": "boom"}'
        ']',
        encoding="utf-8",
    )
    restored = trades_store.load()
    assert len(restored) == 1
    assert restored[0].symbol == "BTC/USDT:USDT"


def test_load_handles_corrupt_json():
    """JSON parse 실패 — 빈 리스트 + warn (파일 삭제 X)."""
    p = trades_store._path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not-json {{{", encoding="utf-8")
    assert trades_store.load() == []


def test_load_json_not_list_returns_empty(caplog) -> None:
    """JSON 이 list 가 아닌 경우 (dict 등) — 빈 리스트 반환 + WARNING."""
    import logging
    p = trades_store._path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"key": "value"}', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = trades_store.load()
    assert result == []
    assert any("list 아님" in r.message for r in caplog.records)


def test_load_non_dict_items_skipped() -> None:
    """list 내 비-dict 항목 (정수 등) — skip 하고 유효 record 만 복원."""
    p = trades_store._path()
    p.parent.mkdir(parents=True, exist_ok=True)
    valid_json = (
        '['
        '42,'
        '{"symbol":"BTC/USDT:USDT","direction":"long","leverage":10,'
        '"qty":0.01,"entry_price":60000,"exit_price":61000,'
        '"opened_at_ts":1,"closed_at_ts":2,"reason":"tp_full",'
        '"pnl_usd":10,"roi_pct":16.67,"triggered_by":[]}'
        ']'
    )
    p.write_text(valid_json, encoding="utf-8")
    result = trades_store.load()
    assert len(result) == 1
    assert result[0].symbol == "BTC/USDT:USDT"
