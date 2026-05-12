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
    """다운로드된 .new → 격리 폴더의 본체 .exe 와 swap.

    v0.1.22: 격리 폴더가 ``%LOCALAPPDATA%\\Aurora`` 로 이동. 테스트는 ``_aurora_data_dir``
    을 ``tmp_path/_aurora`` 로 직접 mock — 사용자 LocalAppData 오염 방지.
    v0.2.18: AURORA_EXE_NAME 측 platform 분기 (Windows: Aurora-windows.exe).
    """
    aurora_dir = tmp_path / "_aurora"
    aurora_dir.mkdir()
    monkeypatch.setattr(launcher, "_launcher_dir", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_aurora_data_dir", lambda: aurora_dir)

    exe_name = launcher.AURORA_EXE_NAME  # v0.2.18: platform 분기
    exe = aurora_dir / exe_name
    exe.write_bytes(b"old-exe-content")
    new = tmp_path / f"{exe_name}.new"
    new.write_bytes(b"new-exe-content")

    assert launcher.apply_swap(new) is True
    assert exe.read_bytes() == b"new-exe-content"
    assert not new.exists()  # .new 는 swap 후 사라짐 (rename 으로)
    assert not (aurora_dir / f"{exe_name}.old").exists()  # 백업도 정리됨


def test_apply_swap_when_no_existing_exe(tmp_path, monkeypatch):
    """격리 폴더 미존재 (첫 다운로드) — 폴더 자동 생성 + .new 가 본체 자리로."""
    aurora_dir = tmp_path / "_aurora"  # 일부러 미생성 — apply_swap 이 만들어야 함
    monkeypatch.setattr(launcher, "_launcher_dir", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_aurora_data_dir", lambda: aurora_dir)

    exe_name = launcher.AURORA_EXE_NAME  # v0.2.18: platform 분기
    new = tmp_path / f"{exe_name}.new"
    new.write_bytes(b"first-time")

    assert launcher.apply_swap(new) is True
    assert (aurora_dir / exe_name).read_bytes() == b"first-time"


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
    # v0.2.19 fix: _aurora_data_dir 측 mock 박음 — 사용자 머신 측 LocalAppData 측 본체
    # 박혀있어 has_update=False 박는 fragile test 본질 (CI runner 측 pass 박는 거 측
    # LocalAppData 측 비어있어 정합 박힌 것).
    monkeypatch.setattr(launcher, "_aurora_data_dir", lambda: tmp_path / "_aurora")
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


# ============================================================
# v0.2.18: ChoYoon #133 P0 ⑧ — AURORA_EXE_NAME platform 분기
# ============================================================


def test_aurora_exe_name_matches_platform():
    """AURORA_EXE_NAME 측 platform 정합 (Windows: Aurora-windows.exe)."""
    import platform as _p
    if _p.system() == "Windows":
        assert launcher.AURORA_EXE_NAME == "Aurora-windows.exe"
    else:
        # macOS / Linux dev fallback
        assert launcher.AURORA_EXE_NAME == "Aurora.exe"


def test_migrate_legacy_v0116_to_v0218_renames_aurora_exe(tmp_path, monkeypatch):
    """v0.2.18 측 Windows 자동 migration — `Aurora.exe` 측 박혔으면 `Aurora-windows.exe` 측 rename.

    v0.1.116 까지 박힌 사용자 측 본체 측 보존 박음 (재 download X).
    """
    import platform as _p
    if _p.system() != "Windows":
        return  # macOS / Linux 측 본 step 측 noop — skip
    aurora_dir = tmp_path / "_aurora"
    aurora_dir.mkdir()
    monkeypatch.setattr(launcher, "_launcher_dir", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_aurora_data_dir", lambda: aurora_dir)

    legacy = aurora_dir / "Aurora.exe"
    legacy.write_bytes(b"v0.1.116-body")

    launcher._migrate_legacy_layout()

    assert not legacy.exists()
    assert (aurora_dir / "Aurora-windows.exe").read_bytes() == b"v0.1.116-body"


def test_migrate_skips_when_v0218_already_present(tmp_path, monkeypatch):
    """v0.2.18 측 본체 측 이미 박혀있으면 legacy 측 그대로 (덮어쓰기 X)."""
    import platform as _p
    if _p.system() != "Windows":
        return
    aurora_dir = tmp_path / "_aurora"
    aurora_dir.mkdir()
    monkeypatch.setattr(launcher, "_launcher_dir", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_aurora_data_dir", lambda: aurora_dir)

    legacy = aurora_dir / "Aurora.exe"
    legacy.write_bytes(b"old-v0116")
    new = aurora_dir / "Aurora-windows.exe"
    new.write_bytes(b"already-v0218")

    launcher._migrate_legacy_layout()

    # v0.2.18 측 그대로 박힘
    assert new.read_bytes() == b"already-v0218"


# ============================================================
# v0.2.18: ChoYoon #133 P0 ① — macOS launcher show-back (osascript set visible=true)
# ============================================================


def test_show_launcher_window_macos_calls_osascript_visible_and_activate():
    """macOS 측 _show_launcher_window 측 osascript 측 visible=true + activate 두 번 호출."""
    from unittest.mock import MagicMock

    # webview module 측 mock — windows[0].show() 만 호출 박힘
    fake_win = MagicMock()
    fake_webview = MagicMock(windows=[fake_win])

    api = launcher.LauncherApi()
    with patch.dict("sys.modules", {"webview": fake_webview}), \
         patch("aurora_launcher.launcher.platform.system", return_value="Darwin"), \
         patch("aurora_launcher.launcher.subprocess.run") as mock_run:
        api._show_launcher_window()

    # show() 호출 박힘
    fake_win.show.assert_called_once()
    # osascript 측 visible=true + activate 두 번 호출 박힘
    cmds = [c.args[0] for c in mock_run.call_args_list]
    visible_cmds = [c for c in cmds if "set visible" in c[-1]]
    activate_cmds = [c for c in cmds if "to activate" in c[-1]]
    assert visible_cmds, f"set visible=true osascript 측 호출 X: {cmds}"
    assert activate_cmds, f"activate osascript 측 호출 X: {cmds}"
    # visible 측 활성화 (true)
    assert "to true" in visible_cmds[0][-1]


def test_show_launcher_window_windows_skips_osascript():
    """Windows 측 osascript 호출 X (정상 동작 보존)."""
    from unittest.mock import MagicMock

    fake_win = MagicMock()
    fake_webview = MagicMock(windows=[fake_win])

    api = launcher.LauncherApi()
    with patch.dict("sys.modules", {"webview": fake_webview}), \
         patch("aurora_launcher.launcher.platform.system", return_value="Windows"), \
         patch("aurora_launcher.launcher.subprocess.run") as mock_run:
        api._show_launcher_window()

    fake_win.show.assert_called_once()
    mock_run.assert_not_called()


# ============================================================
# v0.2.22: ChoYoon #133 18 cycle P1 ② — self-update PyObjC race
# ============================================================


def test_apply_pending_macos_skips_when_self_version_higher_or_equal(tmp_path, monkeypatch):
    """metadata pending 측 자기 ≤ 케이스 — 단순 .zip.new + metadata 삭제 (swap X)."""
    monkeypatch.setattr(launcher, "_aurora_data_dir", lambda: tmp_path)
    src_zip = tmp_path / "Aurora-launcher-macOS.zip.new"
    src_zip.write_bytes(b"fake-zip-bytes")
    metadata = src_zip.with_suffix(".new.version")
    metadata.write_text("v0.0.1", encoding="utf-8")  # 자기 (__version__) 보다 낮음
    monkeypatch.setattr(launcher, "_launcher_new_path", lambda: src_zip)

    result = launcher._apply_pending_launcher_update_macos()

    assert result is False
    assert not src_zip.exists(), "pending .zip.new 측 단순 삭제 박혀야"
    assert not metadata.exists(), "metadata 측 단순 삭제 박혀야"


def test_apply_pending_macos_no_metadata_skips_version_branch(tmp_path, monkeypatch):
    """metadata X 박힌 경우 — version 비교 분기 측 진입 X (legacy 호환)."""
    monkeypatch.setattr(launcher, "_aurora_data_dir", lambda: tmp_path)
    src_zip = tmp_path / "Aurora-launcher-macOS.zip.new"
    src_zip.write_bytes(b"fake-zip-bytes")
    monkeypatch.setattr(launcher, "_launcher_new_path", lambda: src_zip)
    # sys.executable 측 .app X 측 mock — swap 측 fail 박지만 핵심 측 "단순 삭제 X"
    monkeypatch.setattr(launcher.sys, "executable", "/tmp/not-an-app/launcher")

    result = launcher._apply_pending_launcher_update_macos()

    # metadata X → 단순 삭제 분기 X → swap 흐름 진입 (단 .app X 라 fail)
    assert src_zip.exists(), "metadata X 측 단순 삭제 박지 X"
    assert result is False


# ============================================================
# v0.2.22: P1 ④ — macOS polling skip (legacy false positive 차단)
# ============================================================


def test_start_aurora_polling_skips_on_macos(caplog):
    """macOS 측 polling skip — open wrapper Popen 측 PID 추적 의미 X."""
    import logging as _logging

    api = launcher.LauncherApi()

    with caplog.at_level(_logging.INFO, logger="aurora_launcher.launcher"), \
         patch("aurora_launcher.launcher.platform.system", return_value="Darwin"), \
         patch("aurora_launcher.launcher.threading.Thread") as mock_thread:
        api._start_aurora_polling()

    mock_thread.assert_not_called()
    msgs = [r.message for r in caplog.records]
    assert any("macOS skip" in m for m in msgs)


def test_start_aurora_polling_runs_on_windows():
    """Windows 측 정상 polling thread 시작 박힘 (기존 흐름 보존)."""
    from unittest.mock import MagicMock

    api = launcher.LauncherApi()
    api._aurora_proc = MagicMock()

    with patch("aurora_launcher.launcher.platform.system", return_value="Windows"), \
         patch("aurora_launcher.launcher.threading.Thread") as mock_thread:
        api._start_aurora_polling()

    mock_thread.assert_called_once()


# ============================================================
# v0.2.24: ChoYoon #133 P1 ③ — launcher progress bar
# ============================================================


def test_update_progress_js_calls_evaluate_js():
    """_update_progress_js 측 webview.evaluate_js 측 setProgress 호출 박음."""
    from unittest.mock import MagicMock

    fake_win = MagicMock()
    fake_webview = MagicMock(windows=[fake_win])

    api = launcher.LauncherApi()
    with patch.dict("sys.modules", {"webview": fake_webview}):
        api._update_progress_js(45.7, "본체 준비 중... (27초)")

    fake_win.evaluate_js.assert_called_once()
    call_arg = fake_win.evaluate_js.call_args[0][0]
    assert "setProgress" in call_arg
    assert "45.7" in call_arg
    assert "본체 준비 중" in call_arg


def test_update_progress_js_escapes_single_quote():
    """text 측 single quote 박힘 — JS 측 escape 박음."""
    from unittest.mock import MagicMock

    fake_win = MagicMock()
    fake_webview = MagicMock(windows=[fake_win])

    api = launcher.LauncherApi()
    with patch.dict("sys.modules", {"webview": fake_webview}):
        api._update_progress_js(50.0, "test 'quote'")

    call_arg = fake_win.evaluate_js.call_args[0][0]
    assert "\'" in call_arg


def test_update_progress_js_no_webview_window_safe():
    """webview.windows 측 빈 — silent return (exception X)."""
    from unittest.mock import MagicMock

    fake_webview = MagicMock(windows=[])

    api = launcher.LauncherApi()
    with patch.dict("sys.modules", {"webview": fake_webview}):
        api._update_progress_js(50.0, "test")  # raise X


# ============================================================
# _is_frozen — sys.frozen 분기
# ============================================================

import sys  # noqa: E402


def test_is_frozen_dev_env_returns_false() -> None:
    """pytest 환경 (sys.frozen 미설정) → False."""
    assert launcher._is_frozen() is False


def test_is_frozen_true_when_sys_frozen_set() -> None:
    """sys.frozen=True 박힘 → True 반환."""
    with patch.object(sys, "frozen", True, create=True):
        assert launcher._is_frozen() is True


def test_is_frozen_false_when_sys_frozen_false() -> None:
    """sys.frozen=False 명시 → False."""
    with patch.object(sys, "frozen", False, create=True):
        assert launcher._is_frozen() is False


# ============================================================
# _body_artifact_name — 플랫폼별 asset 이름
# ============================================================


def test_body_artifact_name_windows() -> None:
    """Windows → Aurora-windows.exe 반환."""
    with patch("aurora_launcher.launcher.platform.system", return_value="Windows"):
        assert launcher._body_artifact_name() == "Aurora-windows.exe"


def test_body_artifact_name_macos() -> None:
    """macOS (Darwin) → Aurora-macOS.zip 반환."""
    with patch("aurora_launcher.launcher.platform.system", return_value="Darwin"):
        assert launcher._body_artifact_name() == "Aurora-macOS.zip"


def test_body_artifact_name_linux_falls_back_to_windows_exe() -> None:
    """Linux → Windows exe fallback (release.yml CI 빌드 X)."""
    with patch("aurora_launcher.launcher.platform.system", return_value="Linux"):
        assert launcher._body_artifact_name() == "Aurora-windows.exe"


# ============================================================
# _launcher_new_path — 다운로드 임시 경로 플랫폼별
# ============================================================


def test_launcher_new_path_windows_ends_with_exe_new(tmp_path) -> None:
    """Windows → Aurora-launcher.exe.new."""
    with (
        patch("aurora_launcher.launcher.platform.system", return_value="Windows"),
        patch("aurora_launcher.launcher._aurora_data_dir", return_value=tmp_path),
    ):
        result = launcher._launcher_new_path()
    assert result == tmp_path / "Aurora-launcher.exe.new"


def test_launcher_new_path_macos_ends_with_zip_new(tmp_path) -> None:
    """macOS → Aurora-launcher-macOS.zip.new."""
    with (
        patch("aurora_launcher.launcher.platform.system", return_value="Darwin"),
        patch("aurora_launcher.launcher._aurora_data_dir", return_value=tmp_path),
    ):
        result = launcher._launcher_new_path()
    assert result == tmp_path / "Aurora-launcher-macOS.zip.new"
# find_launcher_url — 플랫폼별 launcher asset URL 반환
# ============================================================


def _release_with(*names: str) -> dict:
    """헬퍼 — 지정 이름들의 asset 목록을 가진 release dict 생성."""
    return {
        "tag_name": "v0.3.0",
        "assets": [
            {"name": n, "browser_download_url": f"https://example.com/{n}"}
            for n in names
        ],
    }


def test_find_launcher_url_windows_returns_exe_url(monkeypatch):
    """Windows 환경 — Aurora-launcher.exe asset URL 반환."""
    monkeypatch.setattr("aurora_launcher.launcher.platform.system", lambda: "Windows")
    release = _release_with("Aurora-windows.exe", "Aurora-launcher.exe", "Aurora-launcher-macOS.zip")
    result = launcher.find_launcher_url(release)
    assert result == "https://example.com/Aurora-launcher.exe"


def test_find_launcher_url_macos_returns_zip_url(monkeypatch):
    """macOS 환경 — Aurora-launcher-macOS.zip asset URL 반환."""
    monkeypatch.setattr("aurora_launcher.launcher.platform.system", lambda: "Darwin")
    release = _release_with("Aurora-windows.exe", "Aurora-launcher.exe", "Aurora-launcher-macOS.zip")
    result = launcher.find_launcher_url(release)
    assert result == "https://example.com/Aurora-launcher-macOS.zip"


def test_find_launcher_url_returns_none_when_asset_missing(monkeypatch):
    """해당 asset 없음 → None."""
    monkeypatch.setattr("aurora_launcher.launcher.platform.system", lambda: "Windows")
    release = _release_with("Aurora-windows.exe")  # launcher.exe 없음
    assert launcher.find_launcher_url(release) is None


def test_find_launcher_url_empty_assets(monkeypatch):
    """assets 빈 리스트 → None."""
    monkeypatch.setattr("aurora_launcher.launcher.platform.system", lambda: "Windows")
    assert launcher.find_launcher_url({"tag_name": "v0.3.0", "assets": []}) is None


# ============================================================
# get_local_aurora_version — .aurora_version 파일 읽기
# ============================================================


def test_get_local_aurora_version_returns_stripped_text(tmp_path, monkeypatch):
    """버전 파일 존재 → stripped text 반환."""
    monkeypatch.setattr(
        "aurora_launcher.launcher._aurora_data_dir", lambda: tmp_path,
    )
    (tmp_path / ".aurora_version").write_text("v0.3.1\n", encoding="utf-8")
    assert launcher.get_local_aurora_version() == "v0.3.1"


def test_get_local_aurora_version_returns_none_when_file_missing(tmp_path, monkeypatch):
    """버전 파일 없음 → None."""
    monkeypatch.setattr(
        "aurora_launcher.launcher._aurora_data_dir", lambda: tmp_path,
    )
    assert launcher.get_local_aurora_version() is None


def test_get_local_aurora_version_returns_none_on_oserror(tmp_path, monkeypatch):
    """파일 읽기 OSError → None (예외 전파 X)."""
    monkeypatch.setattr(
        "aurora_launcher.launcher._aurora_data_dir", lambda: tmp_path,
    )
    version_file = tmp_path / ".aurora_version"
    version_file.write_text("v0.3.0", encoding="utf-8")
    # read_text 측 OSError 강제
    monkeypatch.setattr(
        "pathlib.Path.read_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("permission denied")),
    )
    assert launcher.get_local_aurora_version() is None


# ============================================================
# _body_local_target — 플랫폼별 본체 경로
# ============================================================


def test_body_local_target_windows_returns_exe(tmp_path, monkeypatch):
    """Windows — AURORA_EXE_NAME 파일명 반환."""
    monkeypatch.setattr("aurora_launcher.launcher.platform.system", lambda: "Windows")
    monkeypatch.setattr("aurora_launcher.launcher._aurora_data_dir", lambda: tmp_path)
    result = launcher._body_local_target()
    assert result == tmp_path / launcher.AURORA_EXE_NAME


def test_body_local_target_macos_returns_app_bundle(tmp_path, monkeypatch):
    """macOS — .app 번들 경로 반환."""
    monkeypatch.setattr("aurora_launcher.launcher.platform.system", lambda: "Darwin")
    monkeypatch.setattr("aurora_launcher.launcher._aurora_data_dir", lambda: tmp_path)
    result = launcher._body_local_target()
    assert result == tmp_path / "Aurora.app"
