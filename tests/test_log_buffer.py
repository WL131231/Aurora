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


def test_emit_without_event_loop_silent_skip() -> None:
    """v0.1.106: event loop 박지 X 면 broadcast skip — emit 자체 측 raise X.

    이전 (v0.1.34~v0.1.105) 측 stderr 측 첫 1회 print 박았는데 PyInstaller
    --windowed + frozen 측 stderr None / asyncio.get_running_loop 측 hang 가능성.
    v0.1.106 측 emit 안 측 set_event_loop() 박혀있을 때만 broadcast — 안 박혀있으면
    silent skip. 더 안전 박힘.
    """
    # broadcaster 가짜 등록 — 본 테스트 측 실 push X (event loop 측 set 안 박음)
    async def _fake_broadcaster(_item):
        pass
    log_buffer.set_broadcaster(_fake_broadcaster)
    # event_loop 측 None 박음 (lifespan 측 X — 즉 broadcast 비활성)
    log_buffer.set_event_loop(None)

    log_buffer.install()
    try:
        # 여러 번 호출해도 raise X — 매 호출 측 buffer 만 append, broadcast skip
        logging.getLogger("test.noloop").info("first emit")
        logging.getLogger("test.noloop").info("second emit")
        logging.getLogger("test.noloop").info("third emit")
    finally:
        log_buffer.set_broadcaster(None)

    # buffer 측 새 record 박힘 (3 entries)
    assert len(log_buffer.get_recent(10)) >= 3


def test_get_recent_limit_zero_returns_empty() -> None:
    """limit=0 → 빈 리스트."""
    log_buffer._buffer.append({"ts": "x", "level": "INFO", "logger": "t", "message": "hi"})
    assert log_buffer.get_recent(0) == []


def test_get_recent_limit_larger_than_buffer_returns_all() -> None:
    """limit > buffer 크기 → 전부 반환."""
    for i in range(5):
        log_buffer._buffer.append({"ts": "x", "level": "INFO", "logger": "t", "message": str(i)})
    result = log_buffer.get_recent(999)
    assert len(result) == 5


def test_install_idempotent_no_duplicate_handler() -> None:
    """install() 두 번 호출해도 BufferHandler 는 한 번만 부착."""
    from aurora.interfaces.log_buffer import BufferHandler
    log_buffer.install()
    log_buffer.install()
    root = logging.getLogger()
    count = sum(1 for h in root.handlers if isinstance(h, BufferHandler))
    assert count == 1


def test_emit_with_exc_info_includes_traceback() -> None:
    """exc_info 있는 레코드 → message 에 traceback 포함."""
    log_buffer.install()
    try:
        raise ValueError("test error for aurora")
    except ValueError:
        logging.getLogger("test.exc").exception("caught!")
    recent = log_buffer.get_recent(1)
    assert len(recent) == 1
    assert "ValueError" in recent[0]["message"]
    assert "test error for aurora" in recent[0]["message"]


# ============================================================
# v0.1.106 broadcaster + event_loop 활성 경로 (lines 75-87)
# ============================================================


@pytest.fixture(autouse=False)
def _reset_broadcast_state():
    """broadcaster / event_loop 전역 상태 초기화 — broadcast 경로 테스트 격리."""
    yield
    log_buffer.set_broadcaster(None)
    log_buffer.set_event_loop(None)


def test_emit_broadcasts_when_loop_and_broadcaster_set(_reset_broadcast_state) -> None:
    """broadcaster + event_loop 모두 박혀있을 때 call_soon_threadsafe 경로 실행.

    line 75 (inner try), 76 (is_closed), 79-83 (call_soon_threadsafe + ensure_future) 커버.
    """
    import asyncio

    received: list[dict] = []

    async def _broadcaster(item: dict) -> None:
        received.append(item)

    loop = asyncio.new_event_loop()
    try:
        log_buffer.set_event_loop(loop)
        log_buffer.set_broadcaster(_broadcaster)
        log_buffer.install()
        logging.getLogger("test.broadcast").info("broadcast test")

        # call_soon_threadsafe 콜백 처리 → ensure_future task 생성 → task 실행
        async def _drain() -> None:
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        loop.run_until_complete(_drain())
    finally:
        loop.close()

    assert len(received) >= 1
    assert received[0]["message"] == "broadcast test"


def test_emit_broadcast_inner_exception_swallowed(_reset_broadcast_state) -> None:
    """call_soon_threadsafe 가 raise 해도 emit 측 raise X — line 84-85 커버.

    실제 loop 대신 is_closed() 는 False 반환하지만 call_soon_threadsafe 가
    RuntimeError 를 raise 하는 가짜 loop 로 inner except 경로 유도.
    """

    class _BrokenLoop:
        def is_closed(self) -> bool:
            return False

        def call_soon_threadsafe(self, _fn) -> None:  # noqa: ANN001
            raise RuntimeError("loop boom")

    async def _broadcaster(item: dict) -> None:
        pass

    log_buffer.set_event_loop(_BrokenLoop())
    log_buffer.set_broadcaster(_broadcaster)
    log_buffer.install()

    # RuntimeError 가 emit 밖으로 새어나오지 않아야 함
    logging.getLogger("test.broadcast_err").info("should buffer silently")

    recent = log_buffer.get_recent(5)
    assert any(r["message"] == "should buffer silently" for r in recent)


def test_emit_outer_exception_swallowed(_reset_broadcast_state) -> None:
    """emit 내부에서 broadcaster block 밖 예외도 raise X — line 86-87 커버.

    _buffer 를 None 으로 교체해 append 시 AttributeError 유발 →
    outer except 가 삼키고 정상 종료.
    """
    log_buffer.install()
    handler = next(
        h for h in logging.getLogger().handlers
        if isinstance(h, log_buffer.BufferHandler)
    )
    record = logging.LogRecord("t", logging.INFO, "", 0, "msg", (), None)

    original = log_buffer._buffer
    log_buffer._buffer = None  # type: ignore[assignment]
    try:
        handler.emit(record)  # AttributeError on None.append → outer except
    finally:
        log_buffer._buffer = original
