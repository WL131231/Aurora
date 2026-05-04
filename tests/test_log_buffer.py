"""log_buffer 단위 테스트 — Handler 부착·deque 적재·get_recent 동작."""

from __future__ import annotations

import logging

import pytest

from aurora.interfaces import log_buffer


@pytest.fixture(autouse=True)
def _reset() -> None:
    log_buffer.clear()


def test_install_appends_records() -> None:
    log_buffer.install()
    logging.getLogger("test").info("hello aurora")
    recent = log_buffer.get_recent()
    assert len(recent) == 1
    assert recent[0]["message"] == "hello aurora"
    assert recent[0]["level"] == "INFO"


def test_get_recent_respects_limit() -> None:
    for i in range(50):
        log_buffer._buffer.append({"ts": "x", "level": "INFO", "logger": "t", "message": str(i)})
    assert len(log_buffer.get_recent(10)) == 10
    assert log_buffer.get_recent(10)[-1]["message"] == "49"


def test_buffer_caps_at_1000() -> None:
    for i in range(1500):
        log_buffer._buffer.append({"ts": "x", "level": "INFO", "logger": "t", "message": str(i)})
    assert len(log_buffer.get_recent(2000)) == 1000


def test_emit_no_running_loop_warns_once_to_stderr(capsys) -> None:
    """v0.1.34 — running event loop 없을 때 첫 emit 만 stderr 명시, 이후 silent.

    sync 컨텍스트 (테스트) + broadcaster 등록된 상태 → asyncio.get_running_loop
    RuntimeError → 첫 1회만 print(stderr), 이후 silent (폭주 방지).
    """
    # broadcaster 가짜 등록 — 본 테스트는 실 push X (event loop 없음)
    async def _fake_broadcaster(_item):
        pass
    log_buffer.set_broadcaster(_fake_broadcaster)
    # 이전 테스트로 이미 warned 됐을 수 있음 → 모듈 flag reset
    log_buffer._no_loop_warned = False

    log_buffer.install()
    try:
        # 첫 호출 — stderr 메시지 1줄
        logging.getLogger("test.noloop").info("first emit")
        # 두 번째 호출 — silent (warned 후 폭주 X)
        logging.getLogger("test.noloop").info("second emit")
    finally:
        log_buffer.set_broadcaster(None)  # type: ignore[arg-type]

    captured = capsys.readouterr()
    # stderr 에 "running event loop 없음" 정확히 1번만 출현
    assert captured.err.count("running event loop 없음") == 1
    # _no_loop_warned 플래그 set 됨
    assert log_buffer._no_loop_warned is True
