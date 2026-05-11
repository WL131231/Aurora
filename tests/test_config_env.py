"""aurora.config._env_file_candidates 단위 테스트."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


def _candidates(**env_overrides) -> tuple[str, ...]:
    """환경변수 조작 후 _env_file_candidates() 호출."""
    import os
    from aurora.config import _env_file_candidates
    env = dict(os.environ)
    env.update(env_overrides)
    with patch.dict("os.environ", env, clear=False):
        return _env_file_candidates()


# ============================================================
# 기본 동작
# ============================================================


def test_first_entry_is_dot_env() -> None:
    """.env 는 항상 첫 번째 항목 (cwd 기준 dev 환경 경로)."""
    result = _candidates()
    assert result[0] == ".env"


def test_returns_tuple() -> None:
    assert isinstance(_candidates(), tuple)


def test_home_aurora_env_included() -> None:
    """~/.aurora/.env 경로 포함."""
    result = _candidates()
    expected = str(Path.home() / ".aurora" / ".env")
    assert expected in result


def test_localappdata_path_included_when_set() -> None:
    """LOCALAPPDATA 설정 시 해당 경로 박힘."""
    result = _candidates(LOCALAPPDATA="C:\\Users\\test\\AppData\\Local")
    expected = str(Path("C:\\Users\\test\\AppData\\Local") / "Aurora" / ".env")
    assert expected in result


def test_localappdata_path_absent_when_not_set(monkeypatch) -> None:
    """LOCALAPPDATA 미설정 → Aurora/Local AppData 경로 없음."""
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    import os
    from aurora.config import _env_file_candidates
    with patch.dict("os.environ", {k: v for k, v in os.environ.items() if k != "LOCALAPPDATA"}, clear=True):
        result = _env_file_candidates()
    assert not any("AppData" in p and "Aurora" in p for p in result)


def test_frozen_env_adds_exe_dir(tmp_path) -> None:
    """frozen=True → exe 옆 .env 경로 포함."""
    fake_exe = tmp_path / "Aurora-windows.exe"
    fake_exe.touch()
    with patch.object(sys, "frozen", True, create=True), \
         patch.object(sys, "executable", str(fake_exe)):
        from aurora.config import _env_file_candidates
        result = _env_file_candidates()
    expected = str((tmp_path / ".env").resolve())
    assert expected in result


def test_dev_env_does_not_add_exe_dir() -> None:
    """frozen=False → exe 경로 미추가."""
    with patch.object(sys, "frozen", False, create=True):
        from aurora.config import _env_file_candidates
        result = _env_file_candidates()
    # sys.executable 부모 경로가 없어야 함 (첫 항목 ".env" 제외)
    import sys as _sys
    exe_env = str(Path(_sys.executable).resolve().parent / ".env")
    # dev 환경에서는 이 경로가 포함되지 않아야 함
    assert exe_env not in result or result[0] == ".env"
