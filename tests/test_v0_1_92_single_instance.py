"""v0.1.92 fix 단위 테스트 — Aurora 중복 실행 차단 (Windows named mutex).

본 모듈 측 검증:
- _acquire_single_instance_mutex() — Windows = mutex API 호출, non-Windows = True
- 중복 호출 시 두 번째 = False 반환 (named mutex 측 ALREADY_EXISTS 본질)

launcher polling auto-spawn (재시작 한 번 클릭) 측 별도 통합 테스트 박힘 X — process
spawn 본질 시뮬레이션 어려움 (subprocess.Popen mock 후 polling thread 측 다중 분기
시퀀스 검증). manual verify 본질.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from aurora.interfaces import webview as webview_mod


@pytest.fixture(autouse=True)
def _reset_mutex() -> None:
    """매 테스트 시작 전 mutex handle 초기화 — 테스트 간 격리."""
    webview_mod._MUTEX_HANDLE = None


def test_mutex_non_windows_always_returns_true(monkeypatch) -> None:
    """non-Windows 환경 (Linux/macOS) → mutex skip + True 반환 (single instance check 비활성)."""
    monkeypatch.setattr(sys, "platform", "darwin")
    assert webview_mod._acquire_single_instance_mutex() is True
    assert webview_mod._MUTEX_HANDLE is None


def test_mutex_windows_first_call_returns_true(monkeypatch) -> None:
    """Windows + 첫 호출 → CreateMutexW 측 신규 박음 (last_error != ALREADY_EXISTS) → True."""
    monkeypatch.setattr(sys, "platform", "win32")

    fake_kernel32 = MagicMock()
    fake_kernel32.CreateMutexW.return_value = 12345  # handle
    fake_kernel32.GetLastError.return_value = 0  # 신규 박음
    fake_ctypes = MagicMock()
    fake_ctypes.windll.kernel32 = fake_kernel32

    with patch.dict("sys.modules", {"ctypes": fake_ctypes}):
        result = webview_mod._acquire_single_instance_mutex()

    assert result is True
    assert webview_mod._MUTEX_HANDLE == 12345
    fake_kernel32.CreateMutexW.assert_called_once_with(
        None, True, webview_mod._MUTEX_NAME,
    )


def test_mutex_windows_already_exists_returns_false(monkeypatch) -> None:
    """Windows + mutex 이미 박힘 (last_error == ALREADY_EXISTS=183) → False.

    이게 핵심 fix — duplicate Aurora.exe 실행 시 두 번째 process 측 즉시 exit.
    """
    monkeypatch.setattr(sys, "platform", "win32")

    fake_kernel32 = MagicMock()
    fake_kernel32.CreateMutexW.return_value = 12345
    fake_kernel32.GetLastError.return_value = 183  # ERROR_ALREADY_EXISTS
    fake_ctypes = MagicMock()
    fake_ctypes.windll.kernel32 = fake_kernel32

    with patch.dict("sys.modules", {"ctypes": fake_ctypes}):
        result = webview_mod._acquire_single_instance_mutex()

    assert result is False


def test_mutex_ctypes_unavailable_fallback_true(monkeypatch) -> None:
    """ctypes / windll 미지원 환경 → fallback True (mutex 자체 skip).

    Why: dev 환경 / 일부 Windows 변종 측 ctypes.windll 미지원 가능성 — 차단보다
    실행 우선 (사용자 마찰 0).
    """
    monkeypatch.setattr(sys, "platform", "win32")

    # ctypes import 자체 fail 시뮬레이션 — sys.modules 측 None 박음
    with patch.dict("sys.modules", {"ctypes": None}):
        result = webview_mod._acquire_single_instance_mutex()

    assert result is True
