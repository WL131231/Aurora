"""Pywebview 윈도우 진입점 — .exe로 패키징될 GUI 셸.

내부에서 FastAPI를 별도 스레드로 띄우고, Pywebview는 ui/index.html을
표시하면서 JS Bridge로 백엔드 호출.

담당: 팀원 D
"""

from __future__ import annotations

import threading
from pathlib import Path

import uvicorn

from aurora.config import settings
from aurora.interfaces.api import create_app


def _start_api_server() -> None:
    """백그라운드 스레드에서 FastAPI 실행."""
    app = create_app()
    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


def launch() -> None:
    """Pywebview 윈도우 띄우기."""
    # TODO(D):
    #   1. _start_api_server를 daemon 스레드로 시작
    #   2. import webview; webview.create_window(...) 로 ui/index.html 열기
    #   3. webview.start()
    import webview  # type: ignore[import-not-found]

    api_thread = threading.Thread(target=_start_api_server, daemon=True)
    api_thread.start()

    ui_path = Path(__file__).resolve().parents[3] / "ui" / "index.html"
    webview.create_window(
        "Aurora",
        str(ui_path),
        width=1280,
        height=800,
        resizable=True,
    )
    webview.start()


if __name__ == "__main__":
    launch()
