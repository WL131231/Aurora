"""release_check — 5분 주기 GitHub Releases 폴링 단위 테스트 (v0.1.25)."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

import pytest

from aurora.interfaces import release_check


@pytest.fixture(autouse=True)
def _reset_release_state():
    release_check.reset_state()
    yield
    release_check.reset_state()


def _fake_response(payload: dict):
    body = BytesIO()
    import json
    body.write(json.dumps(payload).encode())
    body.seek(0)
    body.__enter__ = lambda self: self  # type: ignore[method-assign]
    body.__exit__ = lambda self, *a: None  # type: ignore[method-assign]
    return body


def test_parse_version_v_prefix():
    assert release_check._parse_version("v0.1.0") == (0, 1, 0)
    assert release_check._parse_version("0.1.5") == (0, 1, 5)


def test_check_once_no_release_keeps_pending_none():
    """fetch 실패 (None) — pending 변경 X."""
    with patch("aurora.interfaces.release_check.fetch_latest", return_value=None):
        release_check.check_once()
    assert release_check.get_pending_release() is None


def test_check_once_same_version_clears_pending():
    """현재 == latest — pending None (이미 최신)."""
    fake = {"tag_name": release_check.__version__, "assets": []}
    with patch("aurora.interfaces.release_check.fetch_latest", return_value=fake):
        release_check.check_once()
    assert release_check.get_pending_release() is None


def test_check_once_older_version_clears_pending():
    """현재 > latest (개발 환경 등) — pending None."""
    fake = {"tag_name": "v0.0.1", "assets": []}
    with patch("aurora.interfaces.release_check.fetch_latest", return_value=fake):
        release_check.check_once()
    assert release_check.get_pending_release() is None


def test_check_once_newer_version_sets_pending():
    """latest > 현재 — pending dict 채워짐."""
    fake = {
        "tag_name": "v999.0.0",
        "name": "Big Release",
        "body": "Notes here",
        "html_url": "https://github.com/WL131231/Aurora/releases/tag/v999.0.0",
        "published_at": "2026-05-04T00:00:00Z",
    }
    with patch("aurora.interfaces.release_check.fetch_latest", return_value=fake):
        release_check.check_once()
    pending = release_check.get_pending_release()
    assert pending is not None
    assert pending["tag"] == "v999.0.0"
    assert pending["name"] == "Big Release"
    assert pending["body"] == "Notes here"
    assert pending["html_url"].endswith("/v999.0.0")


def test_last_check_ts_updates_each_call():
    """check_once 마다 last_check_ts 갱신."""
    assert release_check.get_last_check_ts() is None
    with patch("aurora.interfaces.release_check.fetch_latest", return_value=None):
        release_check.check_once()
    ts1 = release_check.get_last_check_ts()
    assert ts1 is not None


def test_fetch_latest_returns_none_on_network_error():
    """네트워크 에러 — None."""
    import urllib.error
    with patch(
        "aurora.interfaces.release_check.urllib.request.urlopen",
        side_effect=urllib.error.URLError("nope"),
    ):
        assert release_check.fetch_latest() is None


def test_fetch_latest_returns_dict_on_success():
    fake = {"tag_name": "v0.2.0", "assets": []}
    with patch(
        "aurora.interfaces.release_check.urllib.request.urlopen",
        return_value=_fake_response(fake),
    ):
        assert release_check.fetch_latest() == fake


def test_notify_cb_called_on_new_version():
    """새 버전 첫 발견 시 _notify_cb 호출."""
    received = []
    release_check._state["_notify_cb"] = lambda p: received.append(p)
    fake = {
        "tag_name": "v999.0.0",
        "html_url": "https://x",
        "name": "Big", "body": "", "published_at": "",
    }
    with patch("aurora.interfaces.release_check.fetch_latest", return_value=fake):
        release_check.check_once()
    assert len(received) == 1
    assert received[0]["tag"] == "v999.0.0"


def test_notify_cb_not_called_twice_for_same_tag():
    """같은 tag 두 번 발견 — 콜백 1회만 호출 (중복 알림 방지)."""
    received = []
    release_check._state["_notify_cb"] = lambda p: received.append(p)
    fake = {
        "tag_name": "v999.0.0",
        "html_url": "https://x",
        "name": "Big", "body": "", "published_at": "",
    }
    with patch("aurora.interfaces.release_check.fetch_latest", return_value=fake):
        release_check.check_once()
        release_check.check_once()
    assert len(received) == 1


def test_notify_cb_exception_does_not_propagate():
    """콜백 내부 예외 — check_once 가 정상 종료 (봇 안정성)."""
    release_check._state["_notify_cb"] = lambda _: (_ for _ in ()).throw(RuntimeError("boom"))
    fake = {
        "tag_name": "v999.0.0",
        "html_url": "https://x",
        "name": "Big", "body": "", "published_at": "",
    }
    with patch("aurora.interfaces.release_check.fetch_latest", return_value=fake):
        release_check.check_once()  # 예외 없이 통과해야 함
    assert release_check.get_pending_release() is not None


def test_start_polling_without_event_loop_falls_back_to_check_only():
    """이벤트 루프 없는 환경 (sync test) — task 안 띄우고 즉시 1회 체크만."""
    with patch("aurora.interfaces.release_check.fetch_latest", return_value=None):
        release_check.start_polling()
    # task 띄우려다 RuntimeError 면 폴링 task 는 None 이지만 last_check_ts 는 갱신
    assert release_check.get_last_check_ts() is not None


def test_start_polling_with_loop_creates_task():
    """이벤트 루프 있는 환경 — task 생성 확인."""
    import asyncio

    async def _run():
        with patch("aurora.interfaces.release_check.fetch_latest", return_value=None):
            release_check.start_polling()
        task = release_check._state["task"]
        assert task is not None
        assert not task.done()
        release_check.stop_polling()

    asyncio.run(_run())


def test_start_polling_noop_when_already_running():
    """이미 task 실행 중 — 두 번째 start_polling 은 noop (task 교체 X)."""
    import asyncio

    async def _run():
        with patch("aurora.interfaces.release_check.fetch_latest", return_value=None):
            release_check.start_polling()
        task1 = release_check._state["task"]
        with patch("aurora.interfaces.release_check.fetch_latest", return_value=None):
            release_check.start_polling()  # 두 번째 호출
        task2 = release_check._state["task"]
        assert task1 is task2  # 같은 task (교체 X)
        release_check.stop_polling()
        # cancel 된 task 완료 대기 — coroutine never awaited 경고 제거
        await asyncio.gather(task1, return_exceptions=True)

    asyncio.run(_run())


def test_stop_polling_cancels_task():
    """stop_polling — task cancel + _state["task"] = None."""
    import asyncio

    async def _run():
        with patch("aurora.interfaces.release_check.fetch_latest", return_value=None):
            release_check.start_polling()
        assert release_check._state["task"] is not None
        release_check.stop_polling()
        assert release_check._state["task"] is None

    asyncio.run(_run())


def test_stop_polling_noop_when_no_task():
    """task 없는 상태에서 stop_polling — 예외 없이 통과."""
    assert release_check._state["task"] is None
    release_check.stop_polling()  # 예외 없이 통과
    assert release_check._state["task"] is None
