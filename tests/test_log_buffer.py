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
