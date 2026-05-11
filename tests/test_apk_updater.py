"""apk_updater — Android APK 자가 업데이트 단위 테스트 (v0.1.58).

커버 범위:
    - _progress_hook: 진행률 계산 / 상한 99% / unknown size 무시
    - get_status: 복사본 반환 (state 직접 노출 방지)
    - _check_and_download: fetch 실패 / 버전 skip / asset 없음 / 이미 완료 / 정상 다운 / 네트워크 오류
    - start(): 플랫폼 가드 (android 아니면 thread 안 띄움)

외부 네트워크 호출 없음 — urllib.request.urlretrieve + fetch_latest 합성 stub 교체.
파일 I/O 는 pytest tmp_path 로 격리.
"""

from __future__ import annotations

import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aurora import __version__
from aurora.interfaces import apk_updater

# ── 상태 격리 ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_apk_state():
    """각 테스트 전·후 _state 초기화 — 테스트 간 상태 누수 방지."""
    _blank = dict(
        has_update=False, apk_path=None, latest_tag=None,
        status="idle", download_pct=0, error_msg=None,
    )
    apk_updater._state.update(_blank)
    yield
    apk_updater._state.update(_blank)


# ── 헬퍼 ─────────────────────────────────────────────────────────

def _fake_release(tag: str = "v999.0.0", with_apk: bool = True) -> dict:
    """테스트용 GitHub Release 응답 합성."""
    assets = []
    if with_apk:
        assets.append({
            "name": "Aurora-android.apk",
            "browser_download_url": "https://example.com/Aurora-android.apk",
        })
    return {
        "tag_name": tag,
        "name": tag,
        "body": "",
        "html_url": f"https://github.com/WL131231/Aurora/releases/tag/{tag}",
        "published_at": "2026-05-06T00:00:00Z",
        "assets": assets,
    }


def _fake_urlretrieve(url: str, dst: str, reporthook=None) -> None:
    """urlretrieve stub — dst 에 더미 바이트 기록."""
    Path(dst).write_bytes(b"fake apk content")


# ── _progress_hook ───────────────────────────────────────────────


def test_progress_hook_updates_pct():
    """정상 계산: 5블록 × 1024B / 10240B = 50%."""
    apk_updater._progress_hook(5, 1024, 10240)
    assert apk_updater._state["download_pct"] == 50


def test_progress_hook_caps_at_99():
    """완료 전 훅은 최대 99% — rename 이후 _state 에서 100% 로 올림."""
    apk_updater._progress_hook(9999, 9999, 1)
    assert apk_updater._state["download_pct"] == 99


def test_progress_hook_ignores_unknown_size():
    """Content-Length 없음 (total_size ≤ 0) — download_pct 변경 없음."""
    apk_updater._state["download_pct"] = 42
    apk_updater._progress_hook(10, 1024, -1)
    assert apk_updater._state["download_pct"] == 42


# ── get_status ───────────────────────────────────────────────────


def test_get_status_returns_copy():
    """반환값 수정이 내부 _state 에 영향 없음 (shallow copy 보호)."""
    snap = apk_updater.get_status()
    snap["status"] = "hacked"
    assert apk_updater._state["status"] == "idle"


# ── _check_and_download: early exit 경로 ─────────────────────────


def test_check_fetch_none_stays_idle():
    """fetch_latest 실패(None) → status 변경 없이 idle 유지."""
    with patch("aurora.interfaces.apk_updater.release_check.fetch_latest", return_value=None):
        apk_updater._check_and_download()
    assert apk_updater._state["status"] == "idle"


def test_check_no_tag_stays_idle():
    """tag_name 없는 릴리스 응답 → idle."""
    with patch("aurora.interfaces.apk_updater.release_check.fetch_latest",
               return_value={"assets": []}):
        apk_updater._check_and_download()
    assert apk_updater._state["status"] == "idle"


def test_check_same_version_stays_idle():
    """latest == 현재 번들 버전 → 업데이트 필요 없음, idle."""
    fake = _fake_release(tag=f"v{__version__}")
    with patch("aurora.interfaces.apk_updater.release_check.fetch_latest", return_value=fake):
        apk_updater._check_and_download()
    assert apk_updater._state["status"] == "idle"
    assert apk_updater._state["has_update"] is False


def test_check_older_version_stays_idle():
    """latest < 현재 (개발 빌드 등) → skip, idle."""
    fake = _fake_release(tag="v0.0.1")
    with patch("aurora.interfaces.apk_updater.release_check.fetch_latest", return_value=fake):
        apk_updater._check_and_download()
    assert apk_updater._state["status"] == "idle"


def test_check_no_apk_asset_stays_idle():
    """새 버전이지만 Aurora-android.apk asset 없음 → idle."""
    fake = _fake_release(tag="v999.0.0", with_apk=False)
    with patch("aurora.interfaces.apk_updater.release_check.fetch_latest", return_value=fake):
        apk_updater._check_and_download()
    assert apk_updater._state["status"] == "idle"


# ── _check_and_download: 이미 완료된 경우 ────────────────────────


def test_check_already_downloaded_skips_download(tmp_path, monkeypatch):
    """APK + tag 파일 모두 존재하고 태그 일치 → urlretrieve 호출 없이 status=done."""
    monkeypatch.setenv("AURORA_DATA_DIR", str(tmp_path))
    apk_dir = tmp_path / "update"
    apk_dir.mkdir()
    (apk_dir / "Aurora-android.apk").write_bytes(b"existing apk")
    (apk_dir / "Aurora-android.apk.tag").write_text("v999.0.0", encoding="utf-8")

    fake = _fake_release(tag="v999.0.0")
    with patch("aurora.interfaces.apk_updater.release_check.fetch_latest", return_value=fake):
        with patch("aurora.interfaces.apk_updater.urllib.request.urlretrieve") as mock_dl:
            apk_updater._check_and_download()
            mock_dl.assert_not_called()

    assert apk_updater._state["status"] == "done"
    assert apk_updater._state["has_update"] is True
    assert apk_updater._state["download_pct"] == 100
    assert apk_updater._state["latest_tag"] == "v999.0.0"


# ── _check_and_download: 정상 다운로드 ───────────────────────────


def test_check_download_success(tmp_path, monkeypatch):
    """새 버전 + APK asset 있음 → 다운로드 완료, status=done, 파일 + tag 기록."""
    monkeypatch.setenv("AURORA_DATA_DIR", str(tmp_path))
    fake = _fake_release(tag="v999.0.0")

    with patch("aurora.interfaces.apk_updater.release_check.fetch_latest", return_value=fake):
        with patch("aurora.interfaces.apk_updater.urllib.request.urlretrieve",
                   side_effect=_fake_urlretrieve):
            apk_updater._check_and_download()

    assert apk_updater._state["status"] == "done"
    assert apk_updater._state["has_update"] is True
    assert apk_updater._state["latest_tag"] == "v999.0.0"
    assert apk_updater._state["download_pct"] == 100

    apk_path = tmp_path / "update" / "Aurora-android.apk"
    assert apk_path.exists()
    assert (tmp_path / "update" / "Aurora-android.apk.tag").read_text(encoding="utf-8") == "v999.0.0"


# ============================================================
# _apk_dir
# ============================================================


def test_apk_dir_uses_aurora_data_dir_env(tmp_path, monkeypatch) -> None:
    """AURORA_DATA_DIR 환경변수 설정 시 → 해당 경로 / 'update'."""
    monkeypatch.setenv("AURORA_DATA_DIR", str(tmp_path))
    result = apk_updater._apk_dir()
    assert result == tmp_path / "update"


def test_apk_dir_fallback_when_env_absent(monkeypatch) -> None:
    """AURORA_DATA_DIR 미설정 → /tmp/aurora_update 폴백."""
    from pathlib import Path
    monkeypatch.delenv("AURORA_DATA_DIR", raising=False)
    result = apk_updater._apk_dir()
    assert result == Path("/tmp/aurora_update")


def test_apk_dir_fallback_when_env_empty(monkeypatch) -> None:
    """AURORA_DATA_DIR='' (빈 문자열) → 폴백."""
    from pathlib import Path
    monkeypatch.setenv("AURORA_DATA_DIR", "")
    result = apk_updater._apk_dir()
    assert result == Path("/tmp/aurora_update")


def test_check_download_tmp_not_left_on_success(tmp_path, monkeypatch):
    """.tmp 파일이 rename 으로 정리되어 잔재 없음."""
    monkeypatch.setenv("AURORA_DATA_DIR", str(tmp_path))
    fake = _fake_release(tag="v999.0.0")

    with patch("aurora.interfaces.apk_updater.release_check.fetch_latest", return_value=fake):
        with patch("aurora.interfaces.apk_updater.urllib.request.urlretrieve",
                   side_effect=_fake_urlretrieve):
            apk_updater._check_and_download()

    assert not (tmp_path / "update" / "Aurora-android.apk.tmp").exists()


# ── _check_and_download: 네트워크 오류 ───────────────────────────


def test_check_download_network_error_sets_error(tmp_path, monkeypatch):
    """URLError → status=error, error_msg 기록, download_pct=0."""
    monkeypatch.setenv("AURORA_DATA_DIR", str(tmp_path))
    fake = _fake_release(tag="v999.0.0")

    with patch("aurora.interfaces.apk_updater.release_check.fetch_latest", return_value=fake):
        with patch(
            "aurora.interfaces.apk_updater.urllib.request.urlretrieve",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            apk_updater._check_and_download()

    assert apk_updater._state["status"] == "error"
    assert "connection refused" in (apk_updater._state["error_msg"] or "")
    assert apk_updater._state["download_pct"] == 0


def test_check_download_error_cleans_tmp(tmp_path, monkeypatch):
    """다운로드 실패 시 .tmp 파일 정리."""
    monkeypatch.setenv("AURORA_DATA_DIR", str(tmp_path))
    fake = _fake_release(tag="v999.0.0")

    def _fail_after_write(url, dst, reporthook=None):
        Path(dst).write_bytes(b"partial")
        raise OSError("disk full")

    with patch("aurora.interfaces.apk_updater.release_check.fetch_latest", return_value=fake):
        with patch("aurora.interfaces.apk_updater.urllib.request.urlretrieve",
                   side_effect=_fail_after_write):
            apk_updater._check_and_download()

    assert not (tmp_path / "update" / "Aurora-android.apk.tmp").exists()
    assert apk_updater._state["status"] == "error"


# ── start() — 플랫폼 가드 ─────────────────────────────────────────


def test_start_noop_on_non_android(monkeypatch):
    """AURORA_PLATFORM != android → Thread 생성 없음."""
    monkeypatch.delenv("AURORA_PLATFORM", raising=False)
    with patch("aurora.interfaces.apk_updater.threading.Thread") as mock_t:
        apk_updater.start()
        mock_t.assert_not_called()


def test_start_launches_daemon_thread_on_android(monkeypatch):
    """AURORA_PLATFORM=android → daemon=True thread 시작."""
    monkeypatch.setenv("AURORA_PLATFORM", "android")
    mock_thread = MagicMock()
    with patch("aurora.interfaces.apk_updater.threading.Thread", return_value=mock_thread):
        apk_updater.start()
    mock_thread.start.assert_called_once()
