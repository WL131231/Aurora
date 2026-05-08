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
# v0.1.101: window auto-front — 사용자 측 작업표시줄 클릭 마찰 X
# ============================================================
# Why: Windows 측 detached process 측 spawn 시 OS auto-focus 안 함 (Vista+
# 측 focus stealing prevention 정책). 사용자 측 launcher Trading Start 클릭
# → body window 뒤에 박힘 → 작업표시줄 클릭 필요. SetForegroundWindow 박아
# 자동 front + 포커스 박음.


def _bring_window_to_front_async() -> None:
    """body window 측 자동 front + focus. 별도 thread 측 박음.

    pywebview create_window 측 동기 호출이지만 실제 OS window 생성 측
    webview.start() 측 시작. start() 측 main thread 박힘 (block) → window
    생성 시점 측 비동기 검출 본질. 1.5초 대기 후 FindWindow 박음.

    non-Windows / FindWindow fail / SetForegroundWindow fail 측 silent skip.
    """
    if sys.platform != "win32":
        return
    import time

    # window 생성 측 시간 — webview.start() 측 backend 측 (WebView2) 초기화
    # 측 보통 0.5~1.5초. 1.5초 대기 박음.
    time.sleep(1.5)
    try:
        import ctypes
        user32 = ctypes.windll.user32
        # title "Aurora" 찾기. body 측 webview.create_window("Aurora", ...) 본질.
        hwnd = user32.FindWindowW(None, "Aurora")
        if not hwnd:
            logger.debug("Aurora window 측 hwnd 측 발견 X (1.5초 후) — skip")
            return
        # SW_RESTORE = 9 (minimized 상태 복원), SW_SHOWNORMAL = 1
        user32.ShowWindow(hwnd, 9)
        # SetForegroundWindow 측 다른 process 측 focus 뺏는 케이스 측 OS 거부 가능.
        # 우회: AttachThreadInput 박아 본 process 측 thread 측 attach.
        # 단순화 — 일단 SetForegroundWindow 박고 fail 시 BringWindowToTop fallback.
        if not user32.SetForegroundWindow(hwnd):
            user32.BringWindowToTop(hwnd)
        logger.info("body window 측 front + focus 박음 (hwnd=%s)", hwnd)
    except Exception as e:  # noqa: BLE001 — front 측 실패해도 main 흐름 영향 X
        logger.debug("window front 박기 실패 (계속 진행): %s", e)


# ============================================================
# v0.1.100: 다른 Aurora.exe process 강제 정리 — mutex race 보강
# ============================================================
# Why: 사용자 보고 (2026-05-08) Aurora.exe (3) + Aurora-launcher.exe (2) 동시
# 실행 cascade. mutex acquire 측 timing race 측 일부 환경 못 잡힘. 시작 시
# taskkill /F 박아 강제 정리. mutex 측 자동 release.


def _kill_other_body_processes() -> None:
    """body 시작 시 다른 Aurora.exe process 강제 정리. 자기 PID + 부모 PID 제외.

    v0.1.102 fix:
    - 부모 PID 제외 — apply_pending_update self-update spawn 시 옛 body = 부모,
      새 body = 자식. ``taskkill /F /T`` 측 tree-kill 측 자식 (= 자기 자신) 도
      박힘 → 사용자 보고 \"본체 / 런처 안 나옴\" 본질.
    - CREATE_NO_WINDOW 박음 — PyInstaller --windowed 측 subprocess 측 cmd
      창 깜빡임 차단.

    non-Windows 환경 / tasklist fail 시 silent skip — fallback 흐름 유지.
    """
    if sys.platform != "win32":
        return
    import os
    import subprocess
    import time
    my_pid = os.getpid()
    try:
        my_parent_pid = os.getppid()
    except OSError:
        my_parent_pid = -1
    try:
        r = subprocess.run(  # noqa: S603, S607
            ["tasklist", "/FI", "IMAGENAME eq Aurora.exe", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=5, check=False,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        if r.returncode != 0:
            return
        text = r.stdout.decode("cp949", errors="replace")
        killed = 0
        for line in text.splitlines():
            if not line.strip():
                continue
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[1])
            except ValueError:
                continue
            if pid == my_pid:
                continue
            if pid == my_parent_pid:
                logger.info(
                    "v0.1.102 body startup nuke: Aurora.exe PID=%d 측 부모 — skip",
                    pid,
                )
                continue
            logger.info("v0.1.100 body startup nuke: Aurora.exe PID=%d kill", pid)
            try:
                subprocess.run(  # noqa: S603, S607
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=5, check=False,
                    creationflags=0x08000000,  # CREATE_NO_WINDOW
                )
                killed += 1
            except (OSError, subprocess.TimeoutExpired) as e:
                logger.warning("body kill 실패 PID=%d: %s", pid, e)
        if killed > 0:
            # OS 측 process slot release + mutex / port release 시간
            time.sleep(0.5)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("body startup nuke 실패 (계속 진행): %s", e)


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
    v0.1.100: 매 critical 단계 측 force flush 박음 — buffering 측 진단 자료 lost
    차단. 사용자 보고 \"uvicorn 시작 시도\" 로그 측 안 남는 본질 fix.
    """

    def _flush() -> None:
        for h in logging.getLogger().handlers:
            try:
                h.flush()
            except (OSError, ValueError):
                pass

    try:
        logger.info("api thread 시작 — create_app 호출 중")
        _flush()
        # v0.1.103: create_app() 호출 측 전후 박음 — 사용자 보고 (2026-05-08)
        # \"api thread 시작\" 박힌 후 다음 로그 안 박힘 → create_app() 측 hang 추정
        # 정밀 진단 박음.
        import time as _t
        t0 = _t.monotonic()
        app = create_app()
        logger.info(
            "create_app() 반환 OK (%.3f초) — uvicorn.run 호출 직전: host=%s port=%s",
            _t.monotonic() - t0, settings.api_host, settings.api_port,
        )
        _flush()
        uvicorn.run(
            app,
            host=settings.api_host,
            port=settings.api_port,
            log_level=settings.log_level.lower(),
        )
        logger.warning("uvicorn.run 측 정상 return — api 종료 (예상 X)")
        _flush()
    except OSError as e:
        # port bind fail 측 가장 흔한 케이스 (다른 process 측 8765 점유)
        logger.exception(
            "uvicorn OSError (port %d 점유 / 권한 X 등): %s",
            settings.api_port, e,
        )
        _flush()
        raise
    except Exception as e:
        logger.exception("uvicorn fail (api 서버 죽음): %s", e)
        _flush()
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

    # v0.1.100: body 시작 시 다른 Aurora.exe 측 강제 정리. mutex 측 timing race
    # 못 잡는 케이스 보강 (사용자 보고 body 3개 동시 실행 cascade fix).
    # 자기 PID 측 제외 + taskkill /F 박음. mutex 측 자동 release.
    _kill_other_body_processes()

    # v0.1.92: 중복 실행 차단 — 이미 Aurora 살아있으면 즉시 exit
    if not _acquire_single_instance_mutex():
        logger.warning(
            "Aurora 이미 실행 중 (Windows named mutex %s) — 중복 실행 차단",
            _MUTEX_NAME,
        )
        sys.exit(0)

    # v0.1.100: 모든 startup 로그 즉시 flush 박음 — file handler buffering 측
    # 본체 crash 시 진단 자료 lost 차단.
    for h in logging.getLogger().handlers:
        try:
            h.flush()
        except (OSError, ValueError):
            pass

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

    # v0.1.101: 본체 window 자동 front + focus — 사용자 보고 (2026-05-08)
    # "처음에 시작 누르면 작업표시줄에서 클릭해야 창 뜸" 본질. Windows 측 detached
    # process 측 spawn 시 OS 가 auto-focus 안 함 (뺏기 방지 정책). pywebview
    # window 생성 후 짧게 대기 → ctypes 측 SetForegroundWindow 박음.
    threading.Thread(target=_bring_window_to_front_async, daemon=True).start()

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
