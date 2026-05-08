"""aurora_launcher.launcher — 단위 테스트.

Frozen 환경 의존이 강해 헬퍼 함수 + LauncherApi 위주.
실제 GUI / subprocess.Popen 은 mock.
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import patch

from aurora_launcher import launcher

# ============================================================
# _parse_version
# ============================================================


def test_parse_version_v_prefix():
    assert launcher._parse_version("v0.1.0") == (0, 1, 0)
    assert launcher._parse_version("0.1.0") == (0, 1, 0)
    assert launcher._parse_version("v1.2.3") == (1, 2, 3)


def test_parse_version_drops_pre_release():
    assert launcher._parse_version("v0.1.0-rc1") == (0, 1, 0)


# ============================================================
# find_aurora_exe_url
# ============================================================


def test_find_aurora_exe_url_returns_url():
    release = {
        "tag_name": "v0.2.0",
        "assets": [
            {"name": "Aurora-windows.exe", "browser_download_url": "https://example.com/exe"},
            {"name": "Aurora-launcher.exe", "browser_download_url": "https://example.com/launcher"},
        ],
    }
    assert launcher.find_aurora_exe_url(release) == "https://example.com/exe"


def test_find_aurora_exe_url_returns_none_when_missing():
    release = {"tag_name": "v0.2.0", "assets": [
        {"name": "Aurora-launcher.exe", "browser_download_url": "https://example.com/launcher"},
    ]}
    assert launcher.find_aurora_exe_url(release) is None


# ============================================================
# fetch_latest_release (mock urllib)
# ============================================================


def test_fetch_latest_release_returns_dict_on_success():
    fake_payload = {"tag_name": "v0.2.0", "assets": []}
    fake_resp = BytesIO(json.dumps(fake_payload).encode())
    fake_resp.__enter__ = lambda self: self  # type: ignore[method-assign]
    fake_resp.__exit__ = lambda self, *a: None  # type: ignore[method-assign]
    with patch("aurora_launcher.launcher.urllib.request.urlopen", return_value=fake_resp):
        result = launcher.fetch_latest_release()
    assert result == fake_payload


def test_fetch_latest_release_returns_none_on_network_error():
    import urllib.error
    with patch(
        "aurora_launcher.launcher.urllib.request.urlopen",
        side_effect=urllib.error.URLError("no network"),
    ):
        assert launcher.fetch_latest_release() is None


# ============================================================
# apply_swap (filesystem)
# ============================================================


def test_apply_swap_replaces_exe(tmp_path, monkeypatch):
    """다운로드된 .new → 격리 폴더의 Aurora.exe 와 swap.

    v0.1.22: 격리 폴더가 ``%LOCALAPPDATA%\\Aurora`` 로 이동. 테스트는 ``_aurora_data_dir``
    을 ``tmp_path/_aurora`` 로 직접 mock — 사용자 LocalAppData 오염 방지.
    """
    aurora_dir = tmp_path / "_aurora"
    aurora_dir.mkdir()
    monkeypatch.setattr(launcher, "_launcher_dir", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_aurora_data_dir", lambda: aurora_dir)

    exe = aurora_dir / "Aurora.exe"
    exe.write_bytes(b"old-exe-content")
    new = tmp_path / "Aurora.exe.new"
    new.write_bytes(b"new-exe-content")

    assert launcher.apply_swap(new) is True
    assert exe.read_bytes() == b"new-exe-content"
    assert not new.exists()  # .new 는 swap 후 사라짐 (rename 으로)
    assert not (aurora_dir / "Aurora.exe.old").exists()  # 백업도 정리됨


def test_apply_swap_when_no_existing_exe(tmp_path, monkeypatch):
    """격리 폴더 미존재 (첫 다운로드) — 폴더 자동 생성 + .new 가 본체 자리로."""
    aurora_dir = tmp_path / "_aurora"  # 일부러 미생성 — apply_swap 이 만들어야 함
    monkeypatch.setattr(launcher, "_launcher_dir", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_aurora_data_dir", lambda: aurora_dir)

    new = tmp_path / "Aurora.exe.new"
    new.write_bytes(b"first-time")

    assert launcher.apply_swap(new) is True
    assert (aurora_dir / "Aurora.exe").read_bytes() == b"first-time"


# ============================================================
# LauncherApi
# ============================================================


def test_launcher_api_get_versions():
    api = launcher.LauncherApi()
    assert api.get_launcher_version() == launcher.__version__
    # 본체 버전 미상 — 'unknown' 반환
    with patch("aurora_launcher.launcher.get_local_aurora_version", return_value=None):
        assert api.get_local_version() == "unknown"


def test_launcher_api_check_update_no_release():
    api = launcher.LauncherApi()
    with patch("aurora_launcher.launcher.fetch_latest_release", return_value=None):
        result = api.check_update()
    assert result["error"] is not None
    assert result["has_update"] is False


def test_launcher_api_check_update_has_update(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_launcher_dir", lambda: tmp_path)
    # 본체 .exe 미존재 → has_update=True (첫 다운로드 권유)
    fake_release = {
        "tag_name": "v0.2.0",
        "assets": [
            {"name": "Aurora-windows.exe", "browser_download_url": "https://example.com/x"},
        ],
    }
    api = launcher.LauncherApi()
    with patch("aurora_launcher.launcher.fetch_latest_release", return_value=fake_release):
        result = api.check_update()
    assert result["latest"] == "v0.2.0"
    assert result["has_update"] is True
    assert result["url"] == "https://example.com/x"


# ============================================================
# v0.1.116: readiness polling — ChoYoon #133
# ============================================================


def _wait_for(predicate, timeout=3.0, interval=0.05):
    """thread 측 결과 박힐 때까지 짧게 polling — flaky test 회피."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_readiness_polling_hides_when_health_ok():
    """본체 /health 200 OK 박힐 때 _hide_launcher_window 측 호출 박힘."""
    import time
    from unittest.mock import MagicMock

    api = launcher.LauncherApi()
    api._aurora_spawn_at = time.time()
    api._hide_launcher_window = MagicMock()
    api._update_status_js = MagicMock()

    # urlopen 측 200 OK 응답 mock
    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.__enter__ = MagicMock(return_value=fake_resp)
    fake_resp.__exit__ = MagicMock(return_value=False)

    with patch("aurora_launcher.launcher.urllib.request.urlopen", return_value=fake_resp):
        api._start_readiness_polling(ready_timeout=2.0, ready_interval=0.05)
        # thread 측 hide 호출 박힐 때까지 대기
        assert _wait_for(lambda: api._hide_launcher_window.called)

    # status 측 ✓ 박힌 갱신 박힘
    calls = [c.args[0] for c in api._update_status_js.call_args_list]
    assert any("✓ Aurora 시작됨" in c for c in calls)


def test_readiness_polling_timeout_hides_anyway():
    """/health 측 끝까지 응답 X → timeout 박힘 + hide 강행 + ⚠ status.

    real time 측 ready_timeout=0.3 박아 빨리 만료. 시간 mock X (thread 측
    logger 등 time.time 호출 측 안 깨지게).
    """
    import time
    from unittest.mock import MagicMock
    from urllib.error import URLError

    api = launcher.LauncherApi()
    api._aurora_spawn_at = time.time()
    api._hide_launcher_window = MagicMock()
    api._update_status_js = MagicMock()

    with patch("aurora_launcher.launcher.urllib.request.urlopen",
               side_effect=URLError("connection refused")):
        api._start_readiness_polling(ready_timeout=0.3, ready_interval=0.05)
        # timeout 박힌 후 hide 강행 박힐 때까지 대기 (1초 sleep + buffer)
        assert _wait_for(lambda: api._hide_launcher_window.called, timeout=4.0)

    calls = [c.args[0] for c in api._update_status_js.call_args_list]
    # ⚠ 또는 hide 강행 status 박힘
    assert any("응답 X" in c or "hide 강행" in c or "⚠" in c for c in calls)


def test_check_update_logs_entry_and_result(caplog):
    """check_update 측 진입/결과 logger.info 박힘 (ChoYoon #133 진단 강화)."""
    import logging
    fake_release = {
        "tag_name": "v0.2.0",
        "assets": [
            {"name": "Aurora-windows.exe", "browser_download_url": "https://example.com/x"},
        ],
    }
    api = launcher.LauncherApi()
    with caplog.at_level(logging.INFO, logger="aurora_launcher.launcher"), \
         patch("aurora_launcher.launcher.fetch_latest_release", return_value=fake_release):
        api.check_update()

    msgs = [r.message for r in caplog.records]
    assert any("check_update 진입" in m for m in msgs)
    assert any("check_update 결과" in m for m in msgs)


def test_download_and_swap_logs_entry_and_result(caplog, tmp_path, monkeypatch):
    """download_and_swap 측 진입/완료 logger 박힘."""
    import logging
    monkeypatch.setattr(launcher, "_launcher_dir", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_aurora_data_dir", lambda: tmp_path / "_aurora")
    api = launcher.LauncherApi()
    with caplog.at_level(logging.INFO, logger="aurora_launcher.launcher"), \
         patch("aurora_launcher.launcher.download_to", return_value=True), \
         patch("aurora_launcher.launcher.apply_swap", return_value=True), \
         patch("aurora_launcher.launcher.fetch_latest_release", return_value=None):
        api.download_and_swap("https://example.com/x")

    msgs = [r.message for r in caplog.records]
    assert any("download_and_swap 진입" in m for m in msgs)
    assert any("download_and_swap 완료" in m for m in msgs)
