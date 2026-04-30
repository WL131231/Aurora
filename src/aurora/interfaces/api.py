"""FastAPI 백엔드 — GUI(HTML/JS)와 Telegram 봇이 공통으로 호출.

담당: 팀원 D
"""

from __future__ import annotations

from fastapi import FastAPI

from aurora.config import settings


def create_app() -> FastAPI:
    """FastAPI 앱 인스턴스 생성."""
    app = FastAPI(
        title="Aurora API",
        version="0.1.0",
        description="고빈도 룰 기반 자동매매 봇 백엔드",
    )

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"name": "Aurora", "version": "0.1.0", "mode": settings.run_mode}

    @app.get("/status")
    async def status() -> dict[str, str]:
        # TODO(D): 봇 상태 (running/stopped, current position, equity 등)
        return {"status": "stopped"}

    # TODO(D):
    #   GET /config, POST /config, GET /positions, POST /start, POST /stop
    #   WebSocket /ws/live (실시간 차트/로그 push)

    return app
