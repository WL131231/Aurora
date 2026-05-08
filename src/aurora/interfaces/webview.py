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


# ============================================================
# v0.1.94: Body file logging — 진단 자료 (disconnect / crash 추적)
# ============================================================
# Why: 사용자 보고 (2026-05-08) "갑자기 연결끊김" — body 자체 측 file log X 라
# in-memory log_buffer 만 있음. body process 죽으면 in-memory 측 사라짐 →
# 진단 자료 자체 X. launcher 측 file log 패턴 (v0.1.63) 차용 — 본체도 disk
# 박아 다음 cycle root cause 진단 가능.


def _setup_body_file_logging() -> Path | None:
    """본체 진단용 file logging — `%LOCALAPPDATA%\\Aurora\\aurora.log` (Windows).

    v0.1.99: fallback 경로 박음 — primary 디렉토리 권한 fail 시 ``%TEMP%\\Aurora`` /
    ``/tmp/aurora-{user}`` 측 시도. \"로그 자체 안 박힘\" 증상 차단.

    Returns:
        log 파일 절대 경로 (성공 시) / None (모든 fallback 실패 시).
    """
    import logging.handlers
    import os
    import platform as _plat
    import tempfile

    candidates: list[Path] = []
    if _plat.system() == "Windows":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            candidates.append(Path(local_app) / "Aurora")
        candidates.append(Path(tempfile.gettempdir()) / "Aurora")
        candidates.append(Path.home() / ".aurora")
    elif _plat.system() == "Darwin":
        candidates.append(Path.home() / "Library" / "Application Support" / "Aurora")
        candidates.append(Path(tempfile.gettempdir()) / "Aurora")
        candidates.append(Path.home() / ".aurora")
    else:
        candidates.append(Path.home() / ".aurora")
        candidates.append(Path(tempfile.gettempdir()) / "Aurora")

    last_error: Exception | None = None
    for log_dir in candidates:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "aurora.log"
            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=2_000_000,  # 2 MB (launcher 1MB 보다 큼 — 매매 사이클 로그 많음)
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            ))
            root_logger = logging.getLogger()
            root_logger.setLevel(logging.INFO)
            # self-update swap 등 main 재진입 시 중복 handler 박힘 방지
            for existing in list(root_logger.handlers):
                if isinstance(existing, logging.handlers.RotatingFileHandler):
                    root_logger.removeHandler(existing)
            root_logger.addHandler(file_handler)
            return log_file
        except OSError as e:
            last_error = e
            continue

    # 모든 candidate fail — stderr 로 fallback 정보 출력 (frozen 환경에선 X 표시)
    if last_error is not None:
        try:
            sys.stderr.write(
                f"[Aurora] body file logging 모든 위치 fail: {last_error}\n",
            )
        except OSError:
            pass
    return None


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

    v0.1.99: thread 측 예외 catch 박음 — uvicorn fail 시 silent 종료 차단 (로그
    남게). 정상 흐름엔 영향 X.
    """
    try:
        app = create_app()
        logger.info(
            "uvicorn 시작 시도: host=%s port=%s",
            settings.api_host, settings.api_port,
        )
        uvicorn.run(
            app,
            host=settings.api_host,
            port=settings.api_port,
            log_level=settings.log_level.lower(),
        )
    except Exception as e:
        logger.exception("uvicorn fail (api 서버 죽음): %s", e)
        # daemon thread 라 main 종료 안 시도. 하지만 frontend 측 영구 disconnect.
        raise


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

    # v0.1.94: file logging — disconnect / crash 진단 자료. mutex 박기 전에 박음
    # (mutex 측 sys.exit(0) 도 로그에 남음).
    # v0.1.99: 환경 정보 로깅 박음 (Python / platform / frozen / mode) — 진단 자료.
    import platform as _plat

    from aurora import __version__ as _av
    body_log_file = _setup_body_file_logging()
    if body_log_file is not None:
        logger.info("=" * 60)
        logger.info("Aurora body v%s 시작 — file log: %s", _av, body_log_file)
        logger.info("Python: %s", sys.version.replace("\n", " "))
        logger.info("Platform: %s", _plat.platform())
        logger.info(
            "frozen=%s _MEIPASS=%s",
            getattr(sys, "frozen", False),
            getattr(sys, "_MEIPASS", "-"),
        )
        logger.info("run_mode=%s", settings.run_mode)
    else:
        logger.warning("body file logging 활성 실패 (모든 fallback 위치 권한 X)")

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

    # v0.1.94: file logging (headless 도 동일 박음)
    body_log_file = _setup_body_file_logging()
    if body_log_file is not None:
        logger.info("=" * 60)
        logger.info("Aurora body (headless) 시작 — file log: %s", body_log_file)

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
