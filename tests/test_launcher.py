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
    """다운로드된 .new → 본체 .exe 와 swap. 기존 본체는 .old 백업 후 정리."""
    monkeypatch.setattr(launcher, "_launcher_dir", lambda: tmp_path)

    exe = tmp_path / "Aurora.exe"
    exe.write_bytes(b"old-exe-content")
    new = tmp_path / "Aurora.exe.new"
    new.write_bytes(b"new-exe-content")

    assert launcher.apply_swap(new) is True
    assert exe.read_bytes() == b"new-exe-content"
    assert not new.exists()  # .new 는 swap 후 사라짐 (rename 으로)
    assert not (tmp_path / "Aurora.exe.old").exists()  # 백업도 정리됨


def test_apply_swap_when_no_existing_exe(tmp_path, monkeypatch):
    """본체 미존재 (첫 다운로드) — .new 가 본체 자리로."""
    monkeypatch.setattr(launcher, "_launcher_dir", lambda: tmp_path)

    new = tmp_path / "Aurora.exe.new"
    new.write_bytes(b"first-time")

    assert launcher.apply_swap(new) is True
    assert (tmp_path / "Aurora.exe").read_bytes() == b"first-time"


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
