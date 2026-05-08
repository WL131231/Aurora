"""v0.1.93 fix 단위 테스트 — Launcher 중복 실행 차단 (Windows named mutex).

본 모듈 측 검증:
- launcher mutex 측 body mutex (v0.1.92) 와 분리된 name 사용 — launcher + body
  동시 실행은 정상 흐름이라 같은 mutex 박으면 안 됨
- Windows = mutex API 호출, non-Windows = True (skip)
- 중복 호출 시 두 번째 = False (ALREADY_EXISTS)
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from aurora_launcher import launcher as launcher_mod


@pytest.fixture(autouse=True)
def _reset_mutex() -> None:
    """매 테스트 시작 전 mutex handle 초기화 — 테스트 간 격리."""
    launcher_mod._LAUNCHER_MUTEX_HANDLE = None


def test_launcher_mutex_name_distinct_from_body_mutex() -> None:
    """launcher mutex name 측 body (v0.1.92) 와 분리.

    Why: launcher + body 동시 실행은 정상 흐름. 같은 mutex 박으면 launcher
    살아있는 동안 body spawn 차단됨.
    """
    from aurora.interfaces import webview as body_webview_mod

    assert launcher_mod._LAUNCHER_MUTEX_NAME != body_webview_mod._MUTEX_NAME
    assert "Launcher" in launcher_mod._LAUNCHER_MUTEX_NAME


def test_launcher_mutex_non_windows_returns_true(monkeypatch) -> None:
    """non-Windows 환경 → mutex skip + True 반환."""
    monkeypatch.setattr(sys, "platform", "darwin")
    assert launcher_mod._acquire_launcher_single_instance_mutex() is True
    assert launcher_mod._LAUNCHER_MUTEX_HANDLE is None


def test_launcher_mutex_windows_first_call_returns_true(monkeypatch) -> None:
    """Windows + 첫 호출 → 신규 박음 (last_error != ALREADY_EXISTS) → True."""
    monkeypatch.setattr(sys, "platform", "win32")

    fake_kernel32 = MagicMock()
    fake_kernel32.CreateMutexW.return_value = 7777
    fake_kernel32.GetLastError.return_value = 0
    fake_ctypes = MagicMock()
    fake_ctypes.windll.kernel32 = fake_kernel32

    with patch.dict("sys.modules", {"ctypes": fake_ctypes}):
        result = launcher_mod._acquire_launcher_single_instance_mutex()

    assert result is True
    assert launcher_mod._LAUNCHER_MUTEX_HANDLE == 7777
    fake_kernel32.CreateMutexW.assert_called_once_with(
        None, True, launcher_mod._LAUNCHER_MUTEX_NAME,
    )


def test_launcher_mutex_already_exists_returns_false(monkeypatch) -> None:
    """Windows + mutex 이미 박힘 → False (duplicate launcher → exit).

    핵심 fix — 사용자 보고 (2026-05-08) Launcher 두 개 실행 차단.
    """
    monkeypatch.setattr(sys, "platform", "win32")

    fake_kernel32 = MagicMock()
    fake_kernel32.CreateMutexW.return_value = 7777
    fake_kernel32.GetLastError.return_value = 183  # ERROR_ALREADY_EXISTS
    fake_ctypes = MagicMock()
    fake_ctypes.windll.kernel32 = fake_kernel32

    with patch.dict("sys.modules", {"ctypes": fake_ctypes}):
        result = launcher_mod._acquire_launcher_single_instance_mutex()

    assert result is False


def test_launcher_mutex_ctypes_unavailable_fallback_true(monkeypatch) -> None:
    """ctypes 미지원 → fallback True (skip mutex, 사용자 마찰 0)."""
    monkeypatch.setattr(sys, "platform", "win32")
    with patch.dict("sys.modules", {"ctypes": None}):
        result = launcher_mod._acquire_launcher_single_instance_mutex()
    assert result is True
