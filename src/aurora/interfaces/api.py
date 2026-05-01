"""FastAPI 백엔드 — GUI(HTML/JS)와 Telegram 봇이 공통으로 호출.

이 파일은 **엔드포인트 골격(stub)** 만 정의. 각 함수의 ``TODO(정용우)`` 를
보고 실제 로직을 채워나갈 것. 모든 stub 은 일관된 더미 응답을 돌려주므로
프론트엔드(`ui/`) 가 먼저 화면을 만들 수 있음.

엔드포인트 카테고리:
    - **Health**: ``GET /``, ``GET /health``, ``GET /status``
    - **Config**: ``GET /config``, ``POST /config``
    - **Positions**: ``GET /positions``
    - **제어**: ``POST /start``, ``POST /stop``
    - **로그**: ``GET /logs`` (TODO)
    - **WebSocket**: ``/ws/live`` (TODO — 실시간 차트/로그 push)

CORS 정책:
    Pywebview 윈도우는 ``file://`` 또는 ``http://127.0.0.1:<port>`` origin 으로
    호출하므로 로컬호스트 기반은 모두 허용. 프로덕션 배포 시(Phase 3) 화이트리스트
    정교화 필요.

담당: 정용우
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from aurora.config import settings
from aurora.interfaces import config_store

# ============================================================
# Pydantic 모델 (요청/응답 스키마)
# ============================================================


class HealthResponse(BaseModel):
    """``GET /health`` 응답."""

    status: str  # "ok" / "degraded" / "down"
    version: str
    mode: str  # paper / demo / live


class StatusResponse(BaseModel):
    """``GET /status`` — 봇 런타임 상태 요약."""

    running: bool
    mode: str
    open_positions: int
    equity_usd: float | None  # 거래소 미연결 시 None


class PositionDTO(BaseModel):
    """``GET /positions`` 의 한 항목."""

    symbol: str  # "BTC/USDT"
    direction: str  # "long" / "short"
    entry_price: float
    quantity: float
    leverage: int
    unrealized_pnl_usd: float
    sl_price: float | None
    tp_prices: list[float]


class ConfigDTO(BaseModel):
    """``GET/POST /config`` — 사용자 전략 설정.

    Selectable 지표 on/off + 파라미터 일부. 전체 ``StrategyConfig`` 에서
    프론트가 노출할 만한 것만 골라서 표시.
    """

    use_bollinger: bool = False
    use_ma_cross: bool = False
    use_harmonic: bool = False
    use_ichimoku: bool = False
    leverage: int = 10
    risk_pct: float = 0.01
    full_seed: bool = False


class ControlResponse(BaseModel):
    """``POST /start``, ``POST /stop`` 응답."""

    success: bool
    message: str


# ============================================================
# 런타임 상태 (Phase 1: 단일 사용자 가정 → 모듈 레벨 플래그)
# ============================================================

# 봇 실행 여부. ``/start`` / ``/stop`` 으로 토글, ``/status`` 가 읽음.
# Phase 2 이후 다중 인스턴스/멀티 유저 지원 시 ``app.state`` 또는 별도 매니저로 이전.
_bot_running: bool = False


# ============================================================
# 앱 팩토리
# ============================================================


def create_app() -> FastAPI:
    """FastAPI 앱 인스턴스 생성 + CORS + 엔드포인트 등록."""
    app = FastAPI(
        title="Aurora API",
        version="0.1.0",
        description="고빈도 룰 기반 자동매매 봇 백엔드",
    )

    # ───── CORS ─────────────────────────────────────
    # Pywebview 의 file:// origin 은 ``null`` 로 들어오므로 ``allow_origins=["*"]``
    # + ``allow_credentials=False`` 조합. 단, 프로덕션 배포 시(Phase 3) 정교화.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ───── Health / Status ──────────────────────────

    @app.get("/")
    async def root() -> dict[str, str]:
        """기본 핑 엔드포인트."""
        return {"name": "Aurora", "version": "0.1.0", "mode": settings.run_mode}

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """헬스체크 — 봇 프로세스가 살아있고 응답 가능한지."""
        # TODO(정용우): 거래소 ping / DB 연결 등 실제 헬스 점검 추가.
        return HealthResponse(status="ok", version="0.1.0", mode=settings.run_mode)

    @app.get("/status", response_model=StatusResponse)
    async def status() -> StatusResponse:
        """봇 런타임 상태 요약 — 대시보드 첫 화면용."""
        # TODO(정용우): open_positions / equity 도 봇 인스턴스에서 실제 값 조회.
        return StatusResponse(
            running=_bot_running,
            mode=settings.run_mode,
            open_positions=0,
            equity_usd=None,
        )

    # ───── Positions ────────────────────────────────

    @app.get("/positions", response_model=list[PositionDTO])
    async def positions() -> list[PositionDTO]:
        """현재 열린 포지션 목록."""
        # TODO(정용우): exchange 어댑터(추후 ChoYoon 영역) 의 ``get_positions()`` 호출.
        return []

    # ───── Config ───────────────────────────────────

    @app.get("/config", response_model=ConfigDTO)
    async def get_config() -> ConfigDTO:
        """현재 사용자 전략 설정 조회 — 파일 없으면 ConfigDTO 기본값."""
        raw = config_store.load()
        if not raw:
            return ConfigDTO()
        return ConfigDTO(**raw)

    @app.post("/config", response_model=ConfigDTO)
    async def update_config(config: ConfigDTO) -> ConfigDTO:
        """사용자 전략 설정 갱신 — 영구 저장."""
        config_store.save(config.model_dump())
        return config

    # ───── 제어 (Start/Stop) ────────────────────────

    @app.post("/start", response_model=ControlResponse)
    async def start_bot() -> ControlResponse:
        """봇 시작 — 모듈 레벨 ``_bot_running`` 플래그 토글."""
        global _bot_running
        if _bot_running:
            return ControlResponse(success=False, message="이미 실행 중")
        _bot_running = True
        # TODO(정용우): 실제 봇 인스턴스 ``start()`` 호출 + 시작 시각 기록.
        return ControlResponse(success=True, message="봇 시작됨")

    @app.post("/stop", response_model=ControlResponse)
    async def stop_bot() -> ControlResponse:
        """봇 중지 — 모듈 레벨 ``_bot_running`` 플래그 토글."""
        global _bot_running
        if not _bot_running:
            return ControlResponse(success=False, message="이미 중지됨")
        _bot_running = False
        # TODO(정용우): 열린 포지션 안전 청산 옵션 + 봇 인스턴스 ``stop()`` 호출.
        return ControlResponse(success=True, message="봇 중지됨")

    # ───── 로그 (단순 폴링) ─────────────────────────

    @app.get("/logs")
    async def get_logs(limit: int = 100) -> dict[str, Any]:
        """최근 로그 라인 조회 (단순 폴링용 — 실시간은 ``/ws/live``)."""
        # TODO(정용우): 로그 핸들러(또는 ring buffer) 에서 최근 limit 줄 반환.
        return {"lines": [], "limit": limit}

    # TODO(정용우): WebSocket /ws/live — 실시간 차트/로그 push (python-telegram-bot
    # / FastAPI websocket route 패턴). 로그 라인 + 새 신호 발생 시 broadcast.

    return app
