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
