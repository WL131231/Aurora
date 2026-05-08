"""로그 ring buffer — 봇 활동 로그를 메모리에 적재해 /logs · /ws/live 에 공급.

- collections.deque(maxlen=1000) 로 최근 1000 줄 유지 (메모리 안전).
- logging.Handler 를 상속한 BufferHandler 로 root logger 에 등록되면
  봇 전체에서 발생하는 log record 가 자동으로 적재됨.
- thread-safe: deque.append 는 GIL 보호. Lock 불필요.
- broadcaster: api.py 가 set_broadcaster() 로 콜백 등록 → 새 record 마다 ws push.

담당: 정용우
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

_BUFFER_SIZE = 1000
_buffer: deque[dict] = deque(maxlen=_BUFFER_SIZE)
_broadcaster: Callable[[dict], Awaitable[None]] | None = None
# v0.1.106: 명시적 event loop reference — 이전 측 emit() 안 측
# `asyncio.get_running_loop()` 호출 측 PyInstaller frozen + main thread 외 측
# (api thread / logging thread) 측 호출 시 hang 박힘 (사용자 보고 2026-05-09
# step 3.13d 박힌 후 hang). lifespan 측 `set_event_loop(get_running_loop())`
# 박아 명시적 reference 박음. emit 측 stored loop 측 `call_soon_threadsafe`
# 박아 broadcast — get_running_loop 호출 자체 제거.
_event_loop = None


def set_broadcaster(fn: Callable[[dict], Awaitable[None]] | None) -> None:
    """api.py 가 broadcast_log 함수를 등록 — emit 시 자동 호출.

    None 박으면 broadcaster 비활성 (lifespan shutdown 측).
    """
    global _broadcaster
    _broadcaster = fn


def set_event_loop(loop) -> None:
    """v0.1.106: lifespan startup 측 event loop reference 박음.

    None 박으면 broadcast 비활성 — emit 측 buffer append 만 박음.
    """
    global _event_loop
    _event_loop = loop


class BufferHandler(logging.Handler):
    """logging.Handler — 모든 log record 를 _buffer 에 dict 형태로 push."""

    def emit(self, record: logging.LogRecord) -> None:
        # v0.1.106: emit 측 어떤 예외도 raise X — 매 log 측 안전 박음.
        try:
            # exc_info 가 있으면 (logger.exception / logger.error(exc_info=True))
            # message 끝에 traceback 붙임 — GUI 로그에 root cause 가시화.
            msg = record.getMessage()
            if record.exc_info:
                tb = "".join(traceback.format_exception(*record.exc_info)).rstrip()
                msg = f"{msg}\n{tb}"
            item = {
                "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": msg,
            }
            _buffer.append(item)
            # v0.1.106: broadcaster + event_loop 모두 박혀있을 때만 broadcast.
            # `asyncio.get_running_loop()` 호출 측 X — 이전 PyInstaller frozen
            # 측 hang 본질. stored loop reference 측 thread-safe call_soon 박음.
            loop = _event_loop
            if _broadcaster is not None and loop is not None:
                try:
                    if not loop.is_closed():
                        # call_soon_threadsafe 측 어느 thread 측 호출해도 OK.
                        # event loop 측 다음 iteration 측 _broadcaster 측 schedule.
                        loop.call_soon_threadsafe(
                            lambda: asyncio.ensure_future(
                                _broadcaster(item), loop=loop,  # type: ignore[arg-type]
                            ),
                        )
                except Exception:  # noqa: BLE001 — broadcast 실패 측 silent
                    pass
        except Exception:  # noqa: BLE001 — emit 측 절대 raise X (logging 보호)
            pass


def install() -> None:
    """root logger 에 BufferHandler 부착 — main.py 부팅 시 1회 호출."""
    root = logging.getLogger()
    # 중복 부착 방지
    if not any(isinstance(h, BufferHandler) for h in root.handlers):
        handler = BufferHandler()
        handler.setLevel(logging.INFO)
        root.addHandler(handler)
    # root logger 레벨이 INFO 보다 높으면 내림 (기본값 WARNING 이 INFO 를 차단하므로)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)


def get_recent(limit: int = 100) -> list[dict]:
    """최근 limit 줄 반환 (오래된 → 최신 순)."""
    if limit <= 0:
        return []
    return list(_buffer)[-limit:]


def clear() -> None:
    """버퍼 비우기 — 테스트용."""
    _buffer.clear()
