"""updater.py — 자동 업데이트 헬퍼 단위 테스트.

Frozen 환경 의존이 강해 본 테스트는 헬퍼 함수 (parse / compare / fetch mock) 위주.
실제 swap + background check 는 dev 환경 (frozen=False) 에서 no-op 검증만.
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import patch

from aurora import updater

# ============================================================
# _parse_version
# ============================================================


def test_parse_version_strips_v_prefix():
    assert updater._parse_version("v0.1.0") == (0, 1, 0)
    assert updater._parse_version("0.1.0") == (0, 1, 0)


def test_parse_version_drops_pre_release():
    assert updater._parse_version("v0.1.0-rc1") == (0, 1, 0)
    assert updater._parse_version("0.2.0-beta") == (0, 2, 0)


def test_parse_version_partial_ok():
    assert updater._parse_version("v0.2") == (0, 2)


def test_parse_version_garbage_returns_partial():
    # "1.0a" 같은 비표준은 "1" 까지만 매핑
    assert updater._parse_version("v1.0a") == (1,)


# ============================================================
# is_newer
# ============================================================


def test_is_newer_returns_true_for_higher_remote():
    assert updater.is_newer("v0.2.0", "0.1.0") is True
    assert updater.is_newer("v0.1.1", "0.1.0") is True


def test_is_newer_returns_false_for_same_or_lower():
    assert updater.is_newer("v0.1.0", "0.1.0") is False
    assert updater.is_newer("v0.0.9", "0.1.0") is False


def test_is_newer_handles_garbage_safely():
    # tuple 비교 가능하면 결과 반환, 불가능하면 False
    assert updater.is_newer("garbage", "0.1.0") is False


# ============================================================
# Frozen 환경 가드 (dev/pytest 에서 no-op)
# ============================================================


def test_apply_pending_update_noop_when_not_frozen():
    """dev/pytest 환경 (frozen=False) → 항상 False 반환, side effect 없음."""
    assert updater.apply_pending_update() is False


def test_start_background_check_noop_when_not_frozen():
    """dev 환경에서 thread 안 띄움 — 시동 비용 0."""
    with patch("aurora.updater.threading.Thread") as mock_thread:
        updater.start_background_check()
        mock_thread.assert_not_called()


# ============================================================
# fetch_latest_release (mock urllib)
# ============================================================


def test_fetch_latest_release_returns_dict_on_success():
    """GitHub API 정상 응답 → dict 반환."""
    fake_payload = {"tag_name": "v0.2.0", "assets": []}
    fake_resp = BytesIO(json.dumps(fake_payload).encode())
    fake_resp.__enter__ = lambda self: self  # type: ignore[method-assign]
    fake_resp.__exit__ = lambda self, *a: None  # type: ignore[method-assign]
    with patch("aurora.updater.urllib.request.urlopen", return_value=fake_resp):
        result = updater.fetch_latest_release()
    assert result == fake_payload


def test_fetch_latest_release_returns_none_on_network_error():
    """네트워크 끊김 / timeout → None (조용히 skip)."""
    import urllib.error
    with patch(
        "aurora.updater.urllib.request.urlopen",
        side_effect=urllib.error.URLError("no network"),
    ):
        assert updater.fetch_latest_release() is None


# ============================================================
# download_update (mock)
# ============================================================


def test_download_update_success(tmp_path):
    target = tmp_path / "Aurora.exe.new"
    with patch("aurora.updater.urllib.request.urlretrieve") as mock_retrieve:
        # urlretrieve 는 파일 생성만 흉내
        def fake(url, dst):
            target.write_bytes(b"fake binary")
        mock_retrieve.side_effect = fake
        assert updater.download_update("https://example.com/x.exe", target) is True
    assert target.exists()


def test_download_update_cleans_partial_on_failure(tmp_path):
    target = tmp_path / "Aurora.exe.new"
    target.write_bytes(b"partial")  # 부분 파일 있는 상태
    with patch(
        "aurora.updater.urllib.request.urlretrieve",
        side_effect=OSError("boom"),
    ):
        assert updater.download_update("https://example.com/x.exe", target) is False
    assert not target.exists()  # 부분 파일 정리됨


# ============================================================
# UI 핫 업데이트 (PR b)
# ============================================================


def test_find_ui_asset_url_returns_browser_download_url():
    release = {
        "tag_name": "v0.1.2",
        "assets": [
            {"name": "Aurora-windows.exe", "browser_download_url": "https://example.com/exe"},
            {"name": "Aurora-ui.zip", "browser_download_url": "https://example.com/ui.zip"},
        ],
    }
    assert updater.find_ui_asset_url(release) == "https://example.com/ui.zip"


# ============================================================
# _is_frozen
# ============================================================


def test_is_frozen_returns_false_in_dev() -> None:
    """pytest / dev 환경에서는 sys.frozen 없음 → False."""
    assert updater._is_frozen() is False


def test_is_frozen_returns_true_when_frozen() -> None:
    """sys.frozen = True 로 patch → True."""
    import sys
    with patch.object(sys, "frozen", True, create=True):
        assert updater._is_frozen() is True


def test_is_frozen_returns_false_when_frozen_false() -> None:
    """sys.frozen = False 명시 시에도 False."""
    import sys
    with patch.object(sys, "frozen", False, create=True):
        assert updater._is_frozen() is False


# ============================================================
# _launched_from_launcher
# ============================================================


def test_launched_from_launcher_returns_true_when_env_set() -> None:
    """AURORA_FROM_LAUNCHER=1 → True."""
    import os
    with patch.dict(os.environ, {"AURORA_FROM_LAUNCHER": "1"}):
        assert updater._launched_from_launcher() is True


def test_launched_from_launcher_returns_false_when_env_absent() -> None:
    """AURORA_FROM_LAUNCHER 미설정 → False."""
    import os
    env = {k: v for k, v in os.environ.items() if k != "AURORA_FROM_LAUNCHER"}
    with patch("os.environ", env):
        assert updater._launched_from_launcher() is False


def test_launched_from_launcher_returns_false_for_other_values() -> None:
    """AURORA_FROM_LAUNCHER=0 / yes 등 비-'1' 값 → False."""
    import os
    for val in ("0", "yes", "true", ""):
        with patch.dict(os.environ, {"AURORA_FROM_LAUNCHER": val}):
            assert updater._launched_from_launcher() is False


def test_find_ui_asset_url_returns_none_when_missing():
    release = {"tag_name": "v0.1.2", "assets": [
        {"name": "Aurora-windows.exe", "browser_download_url": "https://example.com/exe"},
    ]}
    assert updater.find_ui_asset_url(release) is None


def test_apply_ui_update_extracts_zip(tmp_path):
    """Aurora-ui.zip → <exe_dir>/ui_override/ 풀기 + 기존 정리."""
    import zipfile

    zip_path = tmp_path / "Aurora-ui.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("index.html", "<html>new</html>")
        zf.writestr("js/app.js", "console.log('new');")

    exe_dir = tmp_path / "exe"
    exe_dir.mkdir()
    # 기존 ui_override/ 잔재 시뮬
    (exe_dir / "ui_override").mkdir()
    (exe_dir / "ui_override" / "old.html").write_text("<html>old</html>")

    assert updater.apply_ui_update(zip_path, exe_dir) is True

    # 새 파일 확인
    assert (exe_dir / "ui_override" / "index.html").read_text() == "<html>new</html>"
    assert (exe_dir / "ui_override" / "js" / "app.js").read_text() == "console.log('new');"
    # 기존 잔재 정리됨
    assert not (exe_dir / "ui_override" / "old.html").exists()
    # zip 자체도 정리
    assert not zip_path.exists()


def test_apply_ui_update_rejects_zip_slip(tmp_path):
    """경로 traversal 방지 — ../ entry 거부."""
    import zipfile

    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../escape.txt", "pwned")

    exe_dir = tmp_path / "exe"
    exe_dir.mkdir()

    assert updater.apply_ui_update(zip_path, exe_dir) is False
    assert not (tmp_path / "escape.txt").exists()


def test_apply_ui_update_rejects_corrupt_zip(tmp_path):
    """손상된 zip → False 반환."""
    bad_zip = tmp_path / "corrupt.zip"
    bad_zip.write_bytes(b"not a real zip file")
    exe_dir = tmp_path / "exe"
    exe_dir.mkdir()
    assert updater.apply_ui_update(bad_zip, exe_dir) is False


# ============================================================
# _is_frozen / _launched_from_launcher / _exe_path
# ============================================================


def test_is_frozen_false_in_dev():
    """dev/pytest 환경 → sys.frozen 없음 → False."""
    assert updater._is_frozen() is False


def test_is_frozen_true_when_frozen(monkeypatch):
    """sys.frozen=True 설정 시 → True."""
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert updater._is_frozen() is True


def test_launched_from_launcher_false_by_default(monkeypatch):
    """AURORA_FROM_LAUNCHER 미설정 → False."""
    monkeypatch.delenv("AURORA_FROM_LAUNCHER", raising=False)
    assert updater._launched_from_launcher() is False


def test_launched_from_launcher_true_when_env_set(monkeypatch):
    """AURORA_FROM_LAUNCHER=1 → True."""
    monkeypatch.setenv("AURORA_FROM_LAUNCHER", "1")
    assert updater._launched_from_launcher() is True


def test_launched_from_launcher_false_when_env_other_value(monkeypatch):
    """AURORA_FROM_LAUNCHER=0 → False (정확히 '1' 만 유효)."""
    monkeypatch.setenv("AURORA_FROM_LAUNCHER", "0")
    assert updater._launched_from_launcher() is False


def test_exe_path_returns_resolved_path():
    """_exe_path() → Path.resolve() 결과 (절대 경로)."""
    import sys
    from pathlib import Path
    expected = Path(sys.executable).resolve()
    assert updater._exe_path() == expected


# ============================================================
# _check_and_download_sync — 분기별 early exit
# ============================================================


def test_check_sync_noop_when_not_frozen():
    """_is_frozen() False (dev/pytest) → 즉시 return, fetch_latest_release 호출 X."""
    with patch("aurora.updater._is_frozen", return_value=False), \
         patch("aurora.updater.fetch_latest_release") as mock_fetch:
        updater._check_and_download_sync()
    mock_fetch.assert_not_called()


def test_check_sync_noop_when_not_windows():
    """_is_frozen() True 이지만 platform != Windows → return."""
    with patch("aurora.updater._is_frozen", return_value=True), \
         patch("aurora.updater.platform.system", return_value="Darwin"), \
         patch("aurora.updater.fetch_latest_release") as mock_fetch:
        updater._check_and_download_sync()
    mock_fetch.assert_not_called()


def test_check_sync_returns_on_fetch_none():
    """fetch_latest_release() None → is_newer 호출 X."""
    with patch("aurora.updater._is_frozen", return_value=True), \
         patch("aurora.updater.platform.system", return_value="Windows"), \
         patch("aurora.updater.fetch_latest_release", return_value=None), \
         patch("aurora.updater.is_newer") as mock_newer:
        updater._check_and_download_sync()
    mock_newer.assert_not_called()


def test_check_sync_returns_when_already_latest():
    """is_newer() False (현재 최신) → download_update 호출 X."""
    release = {"tag_name": "v0.1.0", "assets": []}
    with patch("aurora.updater._is_frozen", return_value=True), \
         patch("aurora.updater.platform.system", return_value="Windows"), \
         patch("aurora.updater.fetch_latest_release", return_value=release), \
         patch("aurora.updater.is_newer", return_value=False), \
         patch("aurora.updater.download_update") as mock_dl:
        updater._check_and_download_sync()
    mock_dl.assert_not_called()


def test_check_sync_returns_when_asset_not_found():
    """asset 목록에 해당 파일 없음 → download_update 호출 X."""
    release = {"tag_name": "v9.9.9", "assets": [{"name": "other.exe", "browser_download_url": "http://x"}]}
    with patch("aurora.updater._is_frozen", return_value=True), \
         patch("aurora.updater.platform.system", return_value="Windows"), \
         patch("aurora.updater.fetch_latest_release", return_value=release), \
         patch("aurora.updater.is_newer", return_value=True), \
         patch("aurora.updater.download_update") as mock_dl:
        updater._check_and_download_sync()
    mock_dl.assert_not_called()


def test_check_sync_returns_when_target_already_exists(tmp_path):
    """target .new 파일이 이미 있음 → download_update 호출 X (이전 다운로드 완료)."""
    asset_name = next(iter(updater.ASSET_NAME.values()))  # Windows 에셋 이름
    release = {
        "tag_name": "v9.9.9",
        "assets": [{"name": asset_name, "browser_download_url": "http://x/a.exe"}],
    }
    exe = tmp_path / "aurora.exe"
    exe.touch()
    target = exe.with_suffix(".exe.new")
    target.touch()  # 이미 존재

    with patch("aurora.updater._is_frozen", return_value=True), \
         patch("aurora.updater.platform.system", return_value="Windows"), \
         patch("aurora.updater.fetch_latest_release", return_value=release), \
         patch("aurora.updater.is_newer", return_value=True), \
         patch("aurora.updater._exe_path", return_value=exe), \
         patch("aurora.updater.download_update") as mock_dl:
        updater._check_and_download_sync()
    mock_dl.assert_not_called()


def test_check_sync_calls_download_when_all_conditions_met(tmp_path):
    """조건 모두 충족 → download_update 호출됨."""
    asset_name = next(iter(updater.ASSET_NAME.values()))
    release = {
        "tag_name": "v9.9.9",
        "assets": [{"name": asset_name, "browser_download_url": "http://x/a.exe"}],
    }
    exe = tmp_path / "aurora.exe"
    exe.touch()

    with patch("aurora.updater._is_frozen", return_value=True), \
         patch("aurora.updater.platform.system", return_value="Windows"), \
         patch("aurora.updater.fetch_latest_release", return_value=release), \
         patch("aurora.updater.is_newer", return_value=True), \
         patch("aurora.updater._exe_path", return_value=exe), \
         patch("aurora.updater.download_update", return_value=True) as mock_dl:
        updater._check_and_download_sync()
    mock_dl.assert_called_once()
