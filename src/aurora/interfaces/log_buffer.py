"""로그 ring buffer — 봇 활동 로그를 메모리에 적재해 /logs · /ws/live 에 공급.

- collections.deque(maxlen=1000) 로 최근 1000 줄 유지 (메모리 안전).
- logging.Handler 를 상속한 BufferHandler 로 root logger 에 등록되면
  봇 전체에서 발생하는 log record 가 자동으로 적재됨.
- thread-safe: deque.append 는 GIL 보호. Lock 불필요.

담당: 정용우
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime

_BUFFER_SIZE = 1000
_buffer: deque[dict] = deque(maxlen=_BUFFER_SIZE)


class BufferHandler(logging.Handler):
    """logging.Handler — 모든 log record 를 _buffer 에 dict 형태로 push."""

    def emit(self, record: logging.LogRecord) -> None:
        _buffer.append({
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        })


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
