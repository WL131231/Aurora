"""Aurora 헤드리스 모드 진입점 — Chaquopy Android 브리지.

MainActivity.kt 에서 ``aurora_bridge.start()`` 호출.
``--headless --host 0.0.0.0`` 모드로 uvicorn + Telegram 봇 기동.
UI 는 WebView 가 http://127.0.0.1:8765 로 접근.

.env 위치: /data/data/com.aurora.trading/files/.env
  adb push 로 복사: adb push .env /data/data/com.aurora.trading/files/.env

담당: 정용우
"""

from __future__ import annotations

import os
import sys


def start() -> None:
    """Aurora headless 모드 시작 — Chaquopy 백그라운드 스레드에서 호출."""
    _load_env()

    # main.py 의 _parse_args() 가 읽을 CLI 인자
    sys.argv = ["aurora", "--headless", "--host", "0.0.0.0", "--port", "8765"]

    from aurora.main import main
    main()


def _load_env() -> None:
    """앱 filesDir 기준 .env 로드 — API 키 / Telegram 토큰 등."""
    try:
        from com.chaquo.python import PyApplication  # type: ignore[import-not-found]
        files_dir = str(PyApplication.getInstance().getFilesDir())
        env_path = os.path.join(files_dir, ".env")
        if os.path.exists(env_path):
            from dotenv import load_dotenv
            load_dotenv(env_path, override=True)
    except Exception:
        pass  # .env 없으면 기본값 (demo 모드) 로 진행
