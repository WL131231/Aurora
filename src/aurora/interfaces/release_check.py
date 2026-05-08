"""GitHub Releases 5분 주기 폴링 — 새 버전 발견 시 UI 우상단 알림 push (v0.1.25).

흐름:
    1. FastAPI 시작 시 ``start_polling()`` — 즉시 1회 체크 + 5분 주기 task
    2. 매 체크마다 ``/repos/.../releases/latest`` 호출 → tag 비교
    3. 현재 ``__version__`` 보다 새 버전이면 ``_state["pending_release"]`` 갱신
    4. UI 가 ``GET /release/latest`` 폴링 (대시보드 15초 주기 같이)
    5. 사용자가 알림 dismiss 하면 client localStorage 에 ``dismissed_<tag>`` 저장

설계 노트:
    - **launcher self-update 와 별개**: launcher 도 자기 update 백그라운드 다운하지만 (그건
      다음 launcher 시작 시 swap), 본체 사용자는 본 알림으로 수동 업데이트 가능 시점 확인.
    - **모듈 state**: FastAPI lifespan 안에서 1회 시작 — 싱글톤 OK.
    - **즉시 1회 체크**: 시작 직후 알림이 떠야 사용자가 체감.

담당: 정용우
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

from aurora import __version__

logger = logging.getLogger(__name__)


GITHUB_API_LATEST = "https://api.github.com/repos/WL131231/Aurora/releases/latest"
HTTP_TIMEOUT_SEC = 5
POLL_INTERVAL_SEC = 5 * 60  # 5분


# 모듈 state — FastAPI lifespan 안에서 1회 초기화. 멀티 worker 시 worker 별 상태.
_state: dict[str, Any] = {
    "pending_release": None,   # dict | None — 새 버전 정보
    "last_check_ts": None,     # int | None — 마지막 체크 ms
    "task": None,              # asyncio.Task | None — 폴링 루프
    "_notify_tag": None,       # str | None — 마지막 알림 전송 tag (중복 전송 방지)
    "_notify_cb": None,        # callable | None — 새 버전 발견 시 호출 (telegram 등)
}


def _parse_version(raw: str) -> tuple[int, ...]:
    """``"v0.1.10"`` → ``(0, 1, 10)``."""
    s = raw.lstrip("v").split("-", 1)[0]
    parts: list[int] = []
    for chunk in s.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    return tuple(parts)


def fetch_latest() -> dict | None:
    """GitHub Releases /latest — 네트워크 실패 시 None (silent)."""
    try:
        req = urllib.request.Request(
            GITHUB_API_LATEST,
            headers={"Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:  # noqa: S310
            return json.load(resp)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        logger.debug("release_check fetch 실패 (조용히 skip): %s", e)
        return None


def check_once() -> None:
    """1회 체크 — 새 버전 발견 시 ``_state["pending_release"]`` 갱신.

    버전이 같거나 작으면 pending 을 None 으로 clear (UI 알림 사라짐).
    """
    _state["last_check_ts"] = int(time.time() * 1000)
    release = fetch_latest()
    if release is None:
        return
    tag = release.get("tag_name", "")
    if not tag:
        return
    try:
        latest_v = _parse_version(tag)
        current_v = _parse_version(__version__)
    except (ValueError, TypeError):
        return
    if latest_v <= current_v:
        _state["pending_release"] = None  # 최신 = clear
        return
    _state["pending_release"] = {
        "tag": tag,
        "name": release.get("name") or tag,
        "body": release.get("body") or "",
        "html_url": release.get("html_url") or "",
        "published_at": release.get("published_at") or "",
    }
    logger.info("새 버전 발견: %s (현재 v%s)", tag, __version__)

    # 새 tag 첫 발견 시만 콜백 — 5분 주기 재체크마다 중복 알림 방지
    if tag != _state["_notify_tag"] and callable(_state["_notify_cb"]):
        _state["_notify_tag"] = tag
        try:
            _state["_notify_cb"](_state["pending_release"])
        except Exception as e:
            logger.warning("release 알림 콜백 실패: %s", e)


def get_pending_release() -> dict | None:
    """현재 pending release dict 또는 None — UI 가 노출용."""
    return _state["pending_release"]


def get_last_check_ts() -> int | None:
    """마지막 체크 시각 (ms) — UI 디버깅 / 표시용."""
    return _state["last_check_ts"]


async def _poll_loop() -> None:
    """5분 주기 백그라운드 폴링 — cancel 시 정상 종료."""
    while True:
        try:
            check_once()
        except Exception as e:  # noqa: BLE001 — 폴링 lifecycle 보호 (어떤 에러도 task 안 죽이게)
            logger.warning("release_check 폴링 실패 (계속 진행): %s", e)
        try:
            await asyncio.sleep(POLL_INTERVAL_SEC)
        except asyncio.CancelledError:
            return


def start_polling() -> None:
    """FastAPI startup 에서 호출 — 즉시 1회 체크 + 5분 주기 task 띄우기.

    이미 실행 중이면 noop (싱글톤). 즉시 체크는 시작 직후 알림 표시 보장.
    """
    if _state["task"] is not None and not _state["task"].done():
        return
    try:
        check_once()
    except Exception as e:  # noqa: BLE001 — 시작 차단 방지
        logger.warning("release_check 첫 체크 실패: %s", e)
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # 이벤트 루프 없음 (test 등) — task 띄우지 않고 즉시 1회 체크만 한 상태로 종료.
        return
    _state["task"] = loop.create_task(_poll_loop(), name="release-check-poller")


def stop_polling() -> None:
    """폴링 task 취소 — FastAPI shutdown 에서 호출."""
    task = _state["task"]
    if task is not None and not task.done():
        task.cancel()
    _state["task"] = None


def reset_state() -> None:
    """test 격리용 — 모든 state 초기화."""
    _state["pending_release"] = None
    _state["last_check_ts"] = None
    _state["_notify_tag"] = None
    _state["_notify_cb"] = None
    if _state["task"] is not None and not _state["task"].done():
        _state["task"].cancel()
    _state["task"] = None
