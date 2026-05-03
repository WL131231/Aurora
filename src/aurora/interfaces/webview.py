"""Pywebview 윈도우 진입점 — .exe 로 패키징될 GUI 셸.

내부에서 FastAPI 를 별도 daemon 스레드로 띄우고, Pywebview 는 ``ui/index.html``
을 표시하면서 ``http://127.0.0.1:<api_port>`` 로 백엔드 호출.

담당: 정용우
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import uvicorn

from aurora.config import settings
from aurora.interfaces.api import create_app


def _ui_index_path() -> Path:
    """``ui/index.html`` 경로 해결 — 소스 트리 / PyInstaller 번들 모두 대응.

    PyInstaller 빌드 환경에서는 ``--add-data "ui;ui"`` 로 번들된 데이터가
    ``sys._MEIPASS`` (런타임 임시 디렉토리) 아래에 풀린다. ``--onefile`` 모드는
    실행 시마다, 폴더 모드는 ``_internal/`` 안에 유지.

    Returns:
        실제 ``index.html`` 파일 경로 (소스 트리든 번들이든).
    """
    if hasattr(sys, "_MEIPASS"):
        # PyInstaller 환경 (onefile / folder 모두 _MEIPASS 가짐)
        return Path(sys._MEIPASS) / "ui" / "index.html"  # type: ignore[attr-defined]
    # 소스 트리: src/aurora/interfaces/webview.py 기준 ../../../ui/index.html
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


if __name__ == "__main__":
    launch()
