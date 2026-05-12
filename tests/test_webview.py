"""webview 순수 헬퍼 함수 단위 테스트 — _exe_dir / _ui_index_path.

담당: WooJae
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from aurora.interfaces.webview import _exe_dir, _ui_index_path

# ──────────────────────────────────────────────
# _exe_dir
# ──────────────────────────────────────────────

def test_exe_dir_dev_env_returns_none() -> None:
    """pytest 환경 — sys.frozen 없음 → None 반환 확인."""
    # patch 없이 직접 호출. pytest 실행 시 sys.frozen 미설정이 기본.
    assert _exe_dir() is None


def test_exe_dir_frozen_true_returns_parent_of_executable() -> None:
    """sys.frozen=True 시 sys.executable 부모 디렉토리 반환."""
    fake_exe = "/fake/dist/Aurora.exe"
    with (
        patch.object(sys, "frozen", True, create=True),
        patch.object(sys, "executable", fake_exe),
    ):
        result = _exe_dir()
    assert result == Path(fake_exe).resolve().parent


def test_exe_dir_frozen_false_returns_none() -> None:
    """sys.frozen=False 명시 → None (동작 분기 확인)."""
    with patch.object(sys, "frozen", False, create=True):
        assert _exe_dir() is None


def test_exe_dir_frozen_truthy_string_returns_path() -> None:
    """PyInstaller는 sys.frozen='macosx_app' 같은 문자열도 사용 — truthy 처리 확인."""
    fake_exe = "/opt/app/Aurora"
    with (
        patch.object(sys, "frozen", "macosx_app", create=True),
        patch.object(sys, "executable", fake_exe),
    ):
        result = _exe_dir()
    assert result == Path(fake_exe).resolve().parent


# ──────────────────────────────────────────────
# _ui_index_path
# ──────────────────────────────────────────────

def _without_meipass():
    """컨텍스트 헬퍼 — sys._MEIPASS 없는 상태로 테스트 실행."""
    # sys._MEIPASS 가 원래 있을 경우를 대비해 저장/복원
    had = hasattr(sys, "_MEIPASS")
    original = getattr(sys, "_MEIPASS", None)
    if had:
        delattr(sys, "_MEIPASS")
    return had, original


def test_ui_index_path_dev_mode_source_tree() -> None:
    """dev 환경 (frozen X, _MEIPASS X) → 소스 트리 ui/index.html 경로."""
    had, original = _without_meipass()
    try:
        with patch("aurora.interfaces.webview._exe_dir", return_value=None):
            result = _ui_index_path()
    finally:
        if had:
            sys._MEIPASS = original  # type: ignore[attr-defined]

    assert result.name == "index.html"
    assert "ui" in result.parts


def test_ui_index_path_dev_mode_ends_with_ui_index() -> None:
    """dev 경로가 …/ui/index.html 로 끝나는지 확인."""
    had, original = _without_meipass()
    try:
        with patch("aurora.interfaces.webview._exe_dir", return_value=None):
            result = _ui_index_path()
    finally:
        if had:
            sys._MEIPASS = original  # type: ignore[attr-defined]

    assert result.parts[-2:] == ("ui", "index.html")


def test_ui_index_path_meipass_no_exe_dir() -> None:
    """sys._MEIPASS 있고 override X → _MEIPASS/ui/index.html 반환."""
    with (
        patch("aurora.interfaces.webview._exe_dir", return_value=None),
        patch.object(sys, "_MEIPASS", "/fake/meipass", create=True),
    ):
        result = _ui_index_path()
    assert result == Path("/fake/meipass") / "ui" / "index.html"


def test_ui_index_path_override_exists_takes_priority(tmp_path: Path) -> None:
    """override index.html 존재 시 _MEIPASS / source tree 무시하고 override 반환."""
    override_dir = tmp_path / "ui_override"
    override_dir.mkdir()
    override_file = override_dir / "index.html"
    override_file.touch()

    with (
        patch("aurora.interfaces.webview._exe_dir", return_value=tmp_path),
        patch.object(sys, "_MEIPASS", "/fake/meipass", create=True),
    ):
        result = _ui_index_path()
    assert result == override_file


def test_ui_index_path_override_dir_exists_but_no_file_falls_back_to_meipass(
    tmp_path: Path,
) -> None:
    """ui_override/ 폴더는 있지만 index.html 없음 → _MEIPASS fallback."""
    (tmp_path / "ui_override").mkdir()  # 파일 없이 폴더만 생성

    with (
        patch("aurora.interfaces.webview._exe_dir", return_value=tmp_path),
        patch.object(sys, "_MEIPASS", "/fake/meipass", create=True),
    ):
        result = _ui_index_path()
    assert result == Path("/fake/meipass") / "ui" / "index.html"


def test_ui_index_path_exe_dir_present_no_override_no_meipass_source_tree(
    tmp_path: Path,
) -> None:
    """frozen 환경이지만 override X + _MEIPASS X → 소스 트리 fallback."""
    had, original = _without_meipass()
    try:
        with patch("aurora.interfaces.webview._exe_dir", return_value=tmp_path):
            result = _ui_index_path()
    finally:
        if had:
            sys._MEIPASS = original  # type: ignore[attr-defined]

    # override 파일 없으니 source tree 3순위 선택
    assert result.name == "index.html"
    assert "ui" in result.parts
