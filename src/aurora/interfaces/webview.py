"""Pywebview 윈도우 진입점 — .exe 로 패키징될 GUI 셸.

내부에서 FastAPI 를 별도 daemon 스레드로 띄우고, Pywebview 는 ``ui/index.html``
을 표시하면서 ``http://127.0.0.1:<api_port>`` 로 백엔드 호출.

담당: 정용우
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

import uvicorn

from aurora.config import settings
from aurora.interfaces.api import create_app

logger = logging.getLogger(__name__)


# ============================================================
# v0.1.92: Single-instance mutex — Aurora.exe 중복 실행 차단
# ============================================================
# Why: 사용자 보고 (2026-05-08) — .exe 실행하면 Aurora 가 두 개 실행되는 버그.
# launcher 가 살아있는 상태에서 Aurora.exe 직접 클릭 / Aurora.exe 두 번 클릭 시
# 두 process 동시 실행 가능. 두 번째 process 측 port 8765 bind fail → API 죽고
# GUI 만 표시 → 사용자 혼란. Windows named mutex 박아 즉시 exit.
#
# Mutex 측 process 종료 시 자동 release (named mutex 본질) → stale lock 위험 X.
# launcher 가 body 측 terminate 한 후 새 body spawn 시 mutex 자연 해제 + 새 body
# 측 정상 acquire 가능. 0.5초 race window 도 mutex 자체 atomic 이라 안전.

_MUTEX_HANDLE = None  # noqa: N816 — 모듈 글로벌, GC 방지 (mutex handle 보유)
_MUTEX_NAME = "Aurora-SingleInstance-v0.1.92"
_ERROR_ALREADY_EXISTS = 183


def _acquire_single_instance_mutex() -> bool:
    """Windows named mutex 측 single instance 보장.

    Returns:
        ``True`` — primary process (mutex 획득 성공, GUI 시작 가능).
        ``False`` — duplicate (이미 다른 Aurora 실행 중, exit 권장).

    Note:
        non-Windows / ctypes 미지원 환경 → 항상 True (mutex skip).
    """
    global _MUTEX_HANDLE  # noqa: PLW0603 — 모듈 lifetime handle 보유
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
    except (AttributeError, OSError, ImportError):
        return True  # ctypes 미지원 — fallback OK (skip mutex)

    # CreateMutexW(security_attrs=None, initial_owner=True, name)
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    last_error = kernel32.GetLastError()
    if last_error == _ERROR_ALREADY_EXISTS:
        # 이미 mutex 박힘 — 다른 Aurora 살아있음
        return False
    return True


def _exe_dir() -> Path | None:
    """PyInstaller 환경에서 .exe 가 있는 디렉토리 — UI 핫 업데이트의 override 위치 기준.

    ``sys.executable`` 은 .exe 파일 자체. 그 부모 디렉토리에 ``ui_override/`` 만들면
    소스 트리 / _MEIPASS 보다 우선해서 로드됨.
    dev/pytest 환경에서는 None (override 비활성화).
    """
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def _ui_index_path() -> Path:
    """``ui/index.html`` 경로 해결 — override / 번들 / 소스 트리 우선순위.

    Lookup 순서:
        1. ``<exe_dir>/ui_override/index.html`` — 사용자 핫 업데이트 (PR b 추가).
           ``/update/apply_ui`` 가 zip 풀어두는 위치. 있으면 즉시 반영.
        2. ``sys._MEIPASS/ui/index.html`` — PyInstaller 번들 fallback.
        3. ``<src 트리>/ui/index.html`` — dev 환경 (`python -m aurora.main`).

    PyInstaller 환경에서는 ``--add-data "ui;ui"`` 로 번들된 데이터가
    ``sys._MEIPASS`` (런타임 임시 디렉토리) 아래에 풀린다.

    Returns:
        실제 ``index.html`` 파일 경로 (override 우선).
    """
    # 1순위: 사용자 핫 업데이트 override (.exe 옆 ui_override/)
    exe_dir = _exe_dir()
    if exe_dir is not None:
        override = exe_dir / "ui_override" / "index.html"
        if override.exists():
            return override
    # 2순위: PyInstaller 번들 (--add-data 결과)
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "ui" / "index.html"  # type: ignore[attr-defined]
    # 3순위: 소스 트리 (dev)
    return Path(__file__).resolve().parents[3] / "ui" / "index.html"


def _start_api_server() -> None:
    """백그라운드 daemon 스레드에서 FastAPI 실행.

    daemon=True 이므로 메인(Pywebview) 가 종료되면 자동 정리.
    프로덕션 빌드 시 ``log_level`` 은 ``warning`` 이상 권장 (성능).
    """
    app = create_app()
    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


def launch() -> None:
    """Pywebview 윈도우 띄우기.

    1. ``log_buffer.install()`` — root logger 에 BufferHandler 부착.
       GUI 단독 기동(.exe 더블클릭 / ``python -m aurora.interfaces.webview``)
       시 ``main.py`` 를 안 거치므로 여기서 직접 호출. 누락 시 /logs · /ws/live
       에 아무 로그도 안 쌓임.
    2. ``_start_api_server`` 를 daemon 스레드로 시작 (FastAPI 가 준비될 때까지
       Pywebview 가 잠시 빈 화면을 보여줄 수 있음 — ``ui/`` 의 ``apiClient.js``
       가 retry 처리).
    3. ``_ui_index_path()`` 로 ui/index.html 경로 해결 (소스 트리 / PyInstaller
       번들 모두 대응).
    4. ``webview.create_window(...)`` + ``webview.start()`` 로 GUI 시작.

    Note:
        ``import webview`` 는 함수 내부에서 호출 — pywebview 가 설치 안 된 환경
        (예: CI, headless 서버) 에서 모듈 import 자체는 통과하도록.
    """
    import webview  # type: ignore[import-not-found]

    from aurora.interfaces import log_buffer
    log_buffer.install()

    # v0.1.92: 중복 실행 차단 — 이미 Aurora 살아있으면 즉시 exit
    if not _acquire_single_instance_mutex():
        logger.warning(
            "Aurora 이미 실행 중 (Windows named mutex %s) — 중복 실행 차단",
            _MUTEX_NAME,
        )
        sys.exit(0)

    api_thread = threading.Thread(target=_start_api_server, daemon=True)
    api_thread.start()

    ui_path = _ui_index_path()

    webview.create_window(
        "Aurora",
        str(ui_path),
        width=1280,                    # 일반 노트북 가로 기준 (FHD 1920 대비 보수적)
        height=800,                    # 16:10 비율 — Status/Logs/Chart 동시 표시 충분
        min_size=(960, 600),           # 차트 가독성 + 텍스트 잘림 방지 최소치
        resizable=True,
        background_color="#06060a",  # 웹사이트 배경과 동일 (로딩 깜빡임 방지)
    )
    webview.start()


def launch_headless(host: str | None = None, port: int | None = None) -> None:
    """헤드리스 모드 — pywebview 없이 uvicorn 만 실행.

    Termux / Linux / Chaquopy APK Phase B baseline.
    GUI 없이 FastAPI 서버만 기동. Telegram 으로 제어, 브라우저로 UI 접근 가능.

    Args:
        host: 바인딩 호스트 (기본 settings.api_host).
               Termux 에서 브라우저 접근 시 "0.0.0.0" 권장.
        port: 포트 (기본 settings.api_port).
    """
    from aurora.interfaces import log_buffer
    log_buffer.install()

    # v0.1.92: 중복 실행 차단 — headless 도 같은 mutex 적용
    if not _acquire_single_instance_mutex():
        logger.warning(
            "Aurora 이미 실행 중 (Windows named mutex %s) — 중복 실행 차단",
            _MUTEX_NAME,
        )
        sys.exit(0)

    app = create_app()
    uvicorn.run(
        app,
        host=host or settings.api_host,
        port=port or settings.api_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    launch()
