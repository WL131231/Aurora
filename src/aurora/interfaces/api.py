"""FastAPI 백엔드 — GUI(HTML/JS)와 Telegram 봇이 공통으로 호출.

이 파일은 **엔드포인트 골격(stub)** 만 정의. 각 함수의 ``TODO(정용우)`` 를
보고 실제 로직을 채워나갈 것. 모든 stub 은 일관된 더미 응답을 돌려주므로
프론트엔드(`ui/`) 가 먼저 화면을 만들 수 있음.

엔드포인트 카테고리:
    - **Health**: ``GET /``, ``GET /health``, ``GET /status``
    - **Config**: ``GET /config``, ``POST /config``
    - **Positions**: ``GET /positions``
    - **제어**: ``POST /start``, ``POST /stop``
    - **로그**: ``GET /logs``
    - **WebSocket**: ``/ws/live`` (TODO — 실시간 차트/로그 push)

CORS 정책:
    Pywebview 윈도우는 ``file://`` 또는 ``http://127.0.0.1:<port>`` origin 으로
    호출하므로 로컬호스트 기반은 모두 허용. 프로덕션 배포 시(Phase 3) 화이트리스트
    정교화 필요.

담당: 정용우
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from aurora import __version__
from aurora.config import settings
from aurora.interfaces import bot_instance, config_store, log_buffer

logger = logging.getLogger(__name__)

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
    external_position: bool = False  # 사용자가 직접 연 포지션 감지 (Aurora 진입 skip 중)
    # 매 step 지표 트리거 상태 (v0.1.14) — UI 대시보드 패널 표시용.
    # 형식: {"EMA": "long"|"short"|None, "RSI": ..., "BB": ..., "MA": ..., "Ichimoku": ..., "Harmonic": ...}
    indicator_status: dict[str, str | None] = {}


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
    triggered_by: list[str] = []  # 진입 발동 지표 (예: ["EMA", "RSI"]) — 봇 자기 진입만


class ConfigDTO(BaseModel):
    """``GET/POST /config`` — 사용자 전략 + 매매 설정.

    Selectable 지표 on/off + 매매 파라미터. 전체 ``StrategyConfig`` /
    ``TpSlConfig`` 에서 프론트가 노출할 만한 것만 추림. ``BotInstance.configure_from_settings``
    가 본 dict 를 읽어 매매 사이클에 적용.
    """

    # ===== Selectable 지표 on/off =====
    use_bollinger: bool = False
    use_ma_cross: bool = False
    use_harmonic: bool = False
    use_ichimoku: bool = False

    # ===== 시드 / 리스크 =====
    leverage: int = 10
    risk_pct: float = 0.01
    full_seed: bool = False

    # ===== 거래소 / 페어 / TF (Stage 2E C 통합) =====
    # default_exchange 는 .env 에서도 읽을 수 있지만 GUI 에서 전환 가능
    default_exchange: str = "bybit"
    primary_symbol: str = "BTC/USDT:USDT"     # ccxt 표준 (linear perpetual)
    # 멀티 TF 셋 — 전략 평가용. EMA 480 안정 warmup + RSI Div 1H 고정 정합
    timeframes: list[str] = ["15m", "1H", "4H"]

    # ===== 팀 alias (testing 단계 단순화, ~1~2주 한정) =====
    # 사용자 nickname 입력 (예: "장수") → data/team_aliases.json lookup → 실 키.
    # 빈 문자열이면 .env 의 BYBIT_API_KEY/SECRET fallback. cleanup 시 본 필드 제거.
    bybit_alias: str = ""

    # ===== 외부 사용자 alias (testing 단계, PC 한정) =====
    # 외부 사용자가 GUI 거래소 view 에서 (API Key + Secret + Nickname) 입력 → 본 dict 에 등록.
    # config_store.json (.gitignore'd) 평문 저장 — localhost 통신만, repo commit X.
    # Phase 3 보안 강화 = OS keyring 또는 별도 register endpoint 마스킹.
    # 형식: {"nickname": {"api_key": "...", "api_secret": "..."}}
    user_aliases: dict[str, dict[str, str]] = {}


class ControlResponse(BaseModel):
    """``POST /start``, ``POST /stop`` 응답."""

    success: bool
    message: str


class TradeDTO(BaseModel):
    """``GET /trades`` — 청산된 trade 한 개 (v0.1.20).

    Bybit P&L 표 매핑:
        - market = symbol (BTCUSDT Perp)
        - instrument = "USDT Perpetuals" (고정)
        - entry_price → Entry Price
        - exit_price → Traded Price
        - qty → Order Quantity (sell 빨강)
        - direction → Long / Short (UI 색)
        - pnl_usd → Realized P&L (양수 초록 / 음수 빨강)
        - roi_pct → ROI%
        - closed_at_ts → Trade Time
        - reason → 청산 사유 (sl / tp_full / tp_partial / reverse / manual)
        - triggered_by → 진입 트리거
    """

    symbol: str
    instrument: str = "USDT Perpetuals"  # Bybit 표기 일치
    direction: str
    leverage: int
    qty: float
    entry_price: float
    exit_price: float
    pnl_usd: float
    roi_pct: float
    opened_at_ts: int
    closed_at_ts: int
    reason: str
    triggered_by: list[str] = []


class UiUpdateResponse(BaseModel):
    """``POST /update/apply_ui`` — UI 핫 업데이트 결과.

    success=True 시 클라이언트가 ``location.reload()`` 호출하면 새 GUI 적용됨
    (앱 종료 X). version 은 다운로드한 release tag (예: ``"v0.1.2"``).
    """

    success: bool
    message: str
    version: str | None = None  # tag_name (성공 시)


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
        return {"name": "Aurora", "version": __version__, "mode": settings.run_mode}

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """헬스체크 — 봇 프로세스가 살아있고 응답 가능한지."""
        # TODO(정용우): 거래소 ping / DB 연결 등 실제 헬스 점검 추가.
        return HealthResponse(status="ok", version=__version__, mode=settings.run_mode)

    @app.get("/status", response_model=StatusResponse)
    async def status() -> StatusResponse:
        """봇 런타임 상태 요약 — 대시보드 첫 화면용.

        equity_usd: 거래소 어댑터의 ``get_equity()`` 호출 (USDT total).
        open_positions: 어댑터의 ``get_positions()`` 길이.
        client 미설정·거래소 호출 실패 시 각각 ``None`` / ``0`` (UI 안전 폴백).
        """
        bot = bot_instance.get_instance()
        equity: float | None = None
        open_count = 0
        if bot.client is not None:
            try:
                balance = await bot.client.get_equity()
                equity = balance.total_usd
            except Exception as e:  # noqa: BLE001 — 거래소 호출 실패는 UI 끄지 않고 None 반환
                logger.warning("/status get_equity 실패 (None 반환): %s", e)
            try:
                positions = await bot.client.get_positions()
                open_count = len(positions)
            except Exception as e:  # noqa: BLE001 — 포지션 조회 실패해도 status 자체는 응답
                logger.warning("/status get_positions 실패 (0 반환): %s", e)
        return StatusResponse(
            running=bot.running,
            mode=settings.run_mode,
            open_positions=open_count,
            equity_usd=equity,
            external_position=bot.external_position_detected,
            indicator_status=bot.last_indicator_status,
        )

    # ───── Positions ────────────────────────────────

    @app.get("/positions", response_model=list[PositionDTO])
    async def positions() -> list[PositionDTO]:
        """현재 열린 포지션 목록 — 거래소 어댑터의 ``get_positions()`` 결과 매핑.

        client 미설정·거래소 호출 실패 시 빈 리스트.
        SL/TP 정보는 거래소가 안 줌 — Executor 가 별도 관리 (TODO).
        """
        bot = bot_instance.get_instance()
        if bot.client is None:
            return []
        try:
            raw = await bot.client.get_positions()
        except Exception as e:  # noqa: BLE001 — UI 안전 (빈 리스트)
            logger.warning("/positions get_positions 실패 (빈 리스트 반환): %s", e)
            return []
        # triggered_by 는 봇 자기 진입한 포지션에만 의미 있음.
        # Executor._plan 이 살아있고 거래소 측 포지션 = 봇 자기 → triggered_by 노출.
        # 외부 포지션이거나 stop/start 사이클 중 _plan 잃은 케이스 → 빈 list.
        bot_triggered = (
            bot._executor.triggered_by
            if bot._executor is not None and bot._executor.has_position
            else []
        )
        return [
            PositionDTO(
                symbol=p.symbol,
                direction=p.side,
                entry_price=p.entry_price,
                quantity=p.qty,
                leverage=p.leverage,
                unrealized_pnl_usd=p.unrealized_pnl,
                sl_price=None,    # 거래소 미반환 — Executor 별도 관리 (TODO)
                tp_prices=[],     # 동일
                # 봇 자기 포지션이면 triggered_by, 외부면 빈 list
                triggered_by=(
                    bot_triggered
                    if bot._executor is not None
                    and bot._executor.has_position
                    and p.symbol == bot._symbol
                    and p.side == bot._executor._plan.direction
                    else []
                ),
            )
            for p in raw
        ]

    # ───── UI 핫 업데이트 (PR b) ────────────────────

    @app.post("/update/apply_ui", response_model=UiUpdateResponse)
    async def apply_ui_update_endpoint() -> UiUpdateResponse:
        """UI zip 최신 버전 다운로드 + ``ui_override/`` 에 풀기.

        흐름:
            1. GitHub Releases ``/latest`` 호출 → ``Aurora-ui.zip`` URL 찾기
            2. 임시 파일에 다운로드
            3. ``<exe_dir>/ui_override/`` 에 풀기 (기존 정리 후 swap)
            4. 응답 반환 — 클라이언트가 ``location.reload()`` 호출하면 즉시 적용

        dev/pytest 환경 (frozen=False) 에서는 exe_dir 없음 → 명시적 에러 응답.
        """
        import tempfile

        from aurora import updater
        from aurora.interfaces.webview import _exe_dir

        exe_dir = _exe_dir()
        if exe_dir is None:
            return UiUpdateResponse(
                success=False,
                message="UI 핫 업데이트는 빌드된 .exe 환경에서만 동작 (dev 는 코드 직접 수정)",
            )

        release = updater.fetch_latest_release()
        if release is None:
            return UiUpdateResponse(
                success=False,
                message="GitHub Releases 조회 실패 (네트워크 또는 rate limit)",
            )
        tag = release.get("tag_name", "")
        url = updater.find_ui_asset_url(release)
        if url is None:
            return UiUpdateResponse(
                success=False,
                message=f"release {tag} 에 {updater.UI_ASSET_NAME} asset 없음",
                version=tag,
            )

        # 임시 zip 다운로드 (NamedTemporaryFile 은 Windows 에서 즉시 reuse 가 까다로워 manual)
        with tempfile.TemporaryDirectory(prefix="aurora-ui-") as tmpdir:
            tmp_zip = Path(tmpdir) / updater.UI_ASSET_NAME
            if not updater.download_update(url, tmp_zip):
                return UiUpdateResponse(
                    success=False,
                    message="UI zip 다운로드 실패",
                    version=tag,
                )
            if not updater.apply_ui_update(tmp_zip, exe_dir):
                return UiUpdateResponse(
                    success=False,
                    message="UI zip 적용 실패 (zip 손상 또는 권한 에러)",
                    version=tag,
                )

        logger.info("UI 핫 업데이트 적용 완료: %s → %s/", tag, exe_dir / updater.UI_OVERRIDE_DIR)
        return UiUpdateResponse(
            success=True,
            message="UI 갱신 완료 — 새로고침으로 즉시 적용됨",
            version=tag,
        )

    # ───── Trades (거래내역, v0.1.20) ────────────────

    @app.get("/trades", response_model=list[TradeDTO])
    async def trades(limit: int = 50) -> list[TradeDTO]:
        """청산된 거래내역 — 최근 N개 (rolling buffer).

        Bybit P&L 표 형식 매핑. 봇 자기 청산만 (사용자 직접 청산은 미포함).
        """
        bot = bot_instance.get_instance()
        records = bot.closed_trades[-limit:] if limit > 0 else bot.closed_trades
        # 신→구 (가장 최근 trade 가 위로 오게 reverse)
        return [
            TradeDTO(
                symbol=t.symbol,
                direction=t.direction,
                leverage=t.leverage,
                qty=t.qty,
                entry_price=t.entry_price,
                exit_price=t.exit_price,
                pnl_usd=t.pnl_usd,
                roi_pct=t.roi_pct,
                opened_at_ts=t.opened_at_ts,
                closed_at_ts=t.closed_at_ts,
                reason=t.reason,
                triggered_by=list(t.triggered_by),
            )
            for t in reversed(records)
        ]

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
        """봇 시작 — BotInstance lifecycle 시작."""
        bot = bot_instance.get_instance()
        if bot.running:
            return ControlResponse(success=False, message="이미 실행 중")
        await bot.start()
        return ControlResponse(success=True, message="봇 시작됨")

    @app.post("/stop", response_model=ControlResponse)
    async def stop_bot() -> ControlResponse:
        """봇 중지 — BotInstance lifecycle 중지."""
        bot = bot_instance.get_instance()
        if not bot.running:
            return ControlResponse(success=False, message="이미 중지됨")
        await bot.stop()
        return ControlResponse(success=True, message="봇 중지됨")

    @app.post("/restart", response_model=ControlResponse)
    async def restart_bot() -> ControlResponse:
        """봇 재시작 — stop + start 통합 (한 번 클릭으로 lifecycle 갱신).

        PR #73 의 auto-reconfigure 와 결합 → 재시작 시 client/cache/executor 새로 만듦.
        설정 변경 후 즉시 반영하고 싶을 때 (▶ ■ ▶ 두 번 클릭 대신 ↻ 한 번).
        """
        bot = bot_instance.get_instance()
        if bot.running:
            await bot.stop()
        await bot.start()
        return ControlResponse(success=True, message="봇 재시작됨")

    # ───── 로그 (단순 폴링) ─────────────────────────

    @app.get("/logs")
    async def get_logs(limit: int = 100) -> dict[str, Any]:
        """최근 로그 라인 조회 (단순 폴링용 — 실시간은 ``/ws/live``)."""
        return {"lines": log_buffer.get_recent(limit), "limit": limit}

    # ───── WebSocket 실시간 push ────────────────────

    _ws_clients: set[WebSocket] = set()

    async def broadcast_log(record: dict) -> None:
        """새 log record 발생 시 모든 연결된 클라이언트에 push."""
        dead = []
        for ws in _ws_clients:
            try:
                await ws.send_json({"type": "log", "data": record})
            except Exception:
                dead.append(ws)
        for ws in dead:
            _ws_clients.discard(ws)

    log_buffer.set_broadcaster(broadcast_log)

    @app.websocket("/ws/live")
    async def ws_live(websocket: WebSocket) -> None:
        """실시간 로그 broadcast — 연결 직후 최근 50줄 catch-up 후 신규 record push."""
        await websocket.accept()
        _ws_clients.add(websocket)
        try:
            for line in log_buffer.get_recent(50):
                await websocket.send_json({"type": "log", "data": line})
            while True:
                await websocket.receive_text()  # 클라이언트 ping 수신 (keep-alive)
        except WebSocketDisconnect:
            _ws_clients.discard(websocket)

    return app
