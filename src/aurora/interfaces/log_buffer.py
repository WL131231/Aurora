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
import sys
import traceback
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

_BUFFER_SIZE = 1000
_buffer: deque[dict] = deque(maxlen=_BUFFER_SIZE)
_broadcaster: Callable[[dict], Awaitable[None]] | None = None
# v0.1.34: event loop 없을 때 silent pass 방지 — 첫 실패만 stderr 1줄 명시
# (이후 silent 유지, 로그 폭주 회피). logging 모듈 재호출 시 BufferHandler.emit
# 재귀 위험 → print 직접 사용.
_no_loop_warned: bool = False


def set_broadcaster(fn: Callable[[dict], Awaitable[None]]) -> None:
    """api.py 가 broadcast_log 함수를 등록 — emit 시 자동 호출."""
    global _broadcaster
    _broadcaster = fn


class BufferHandler(logging.Handler):
    """logging.Handler — 모든 log record 를 _buffer 에 dict 형태로 push."""

    def emit(self, record: logging.LogRecord) -> None:
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
        # broadcaster 등록 + running event loop 가 있으면 비동기 push.
        # v0.1.34: get_event_loop (deprecated 3.10+) → get_running_loop. 실패 시 첫
        # 1회 stderr 명시 후 silent — 디버그 가능 + 폭주 회피.
        if _broadcaster is not None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                global _no_loop_warned
                if not _no_loop_warned:
                    print(
                        "log_buffer: running event loop 없음 — broadcaster skip "
                        "(이후 silent, 테스트/동기 컨텍스트면 정상)",
                        file=sys.stderr,
                    )
                    _no_loop_warned = True
            else:
                asyncio.create_task(_broadcaster(item))


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
