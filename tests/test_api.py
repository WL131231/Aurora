"""interfaces.api 단위 테스트 — FastAPI TestClient 로 엔드포인트 골격 검증.

stub 단계라 응답 구조와 status code 위주로 확인. ``/start`` / ``/stop`` 은
BotInstance 싱글톤을 제어하므로 각 테스트 시작 전 reset_for_test() 로 격리.

담당: 정용우
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aurora.interfaces import bot_instance, config_store, log_buffer
from aurora.interfaces.api import create_app


@pytest.fixture(autouse=True)
def _reset_bot_instance() -> None:
    """BotInstance 싱글톤을 매 테스트 시작 전 초기화."""
    bot_instance.reset_for_test()


@pytest.fixture(autouse=True)
def _reset_log_buffer() -> None:
    """테스트 간 log buffer 격리 — 다른 테스트 파일의 잔여 로그 차단."""
    log_buffer.clear()


@pytest.fixture(autouse=True)
def _isolated_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``config_store._config_path`` 를 tmp_path 하위로 교체해 진짜 홈 디렉토리 격리."""
    monkeypatch.setattr(config_store, "_config_path", lambda: tmp_path / ".aurora" / "config.json")


def _client() -> TestClient:
    return TestClient(create_app())


# ============================================================
# Health / Status
# ============================================================


def test_root_returns_meta() -> None:
    r = _client().get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Aurora"
    assert "version" in body
    assert "mode" in body


def test_health_response_shape() -> None:
    r = _client().get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # 빌드 시 release.yml 이 __version__ 갱신 — 정확한 값 X, 형식만 검증
    assert isinstance(body["version"], str)
    assert "." in body["version"]
    assert "mode" in body


def test_status_response_shape() -> None:
    r = _client().get("/status")
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is False
    assert body["open_positions"] == 0
    assert "equity_usd" in body
    assert "mode" in body


def test_status_returns_equity_when_configured() -> None:
    """configure 후 /status 가 client.get_equity() 결과를 반환."""
    from unittest.mock import AsyncMock, MagicMock

    from aurora.exchange.base import Balance

    mock_client = MagicMock()
    mock_client.get_equity = AsyncMock(
        return_value=Balance(total_usd=12345.67, free_usd=10000.0, used_usd=2345.67),
    )
    mock_client.get_positions = AsyncMock(return_value=[])
    bot_instance.get_instance().configure(client=mock_client)

    body = _client().get("/status").json()
    assert body["equity_usd"] == 12345.67


def test_status_equity_none_on_exchange_error() -> None:
    """거래소 호출 실패 시 equity_usd=None — UI 끄지 않고 stub 메시지 유지."""
    from unittest.mock import AsyncMock, MagicMock

    mock_client = MagicMock()
    mock_client.get_equity = AsyncMock(side_effect=RuntimeError("network down"))
    mock_client.get_positions = AsyncMock(return_value=[])
    bot_instance.get_instance().configure(client=mock_client)

    body = _client().get("/status").json()
    assert body["equity_usd"] is None


def test_status_open_positions_reflects_count_when_configured() -> None:
    """/status open_positions 가 get_positions() 길이를 반영."""
    from unittest.mock import AsyncMock, MagicMock

    from aurora.exchange.base import Balance, Position

    mock_client = MagicMock()
    mock_client.get_equity = AsyncMock(
        return_value=Balance(total_usd=8500.0, free_usd=666.0, used_usd=7834.0),
    )
    mock_client.get_positions = AsyncMock(
        return_value=[
            Position(
                symbol="BTC/USDT:USDT", side="long", qty=1.0, entry_price=78600.0,
                leverage=10, unrealized_pnl=-52.0, margin_mode="cross",
            ),
        ],
    )
    bot_instance.get_instance().configure(client=mock_client)

    body = _client().get("/status").json()
    assert body["open_positions"] == 1
    assert body["equity_usd"] == 8500.0


# ============================================================
# Positions / Config
# ============================================================


def test_positions_returns_list() -> None:
    r = _client().get("/positions")
    assert r.status_code == 200
    assert r.json() == []


def test_positions_returns_mapped_dtos_when_configured() -> None:
    """configure 후 /positions 가 client.get_positions() 결과 → PositionDTO 매핑."""
    from unittest.mock import AsyncMock, MagicMock

    from aurora.exchange.base import Position

    mock_client = MagicMock()
    mock_client.get_positions = AsyncMock(
        return_value=[
            Position(
                symbol="BTC/USDT:USDT", side="long", qty=0.5, entry_price=78000.0,
                leverage=10, unrealized_pnl=12.34, margin_mode="cross",
            ),
        ],
    )
    bot_instance.get_instance().configure(client=mock_client)

    body = _client().get("/positions").json()
    assert len(body) == 1
    p = body[0]
    assert p["symbol"] == "BTC/USDT:USDT"
    assert p["direction"] == "long"
    assert p["entry_price"] == 78000.0
    assert p["quantity"] == 0.5
    assert p["leverage"] == 10
    assert p["unrealized_pnl_usd"] == 12.34
    assert p["sl_price"] is None
    assert p["tp_prices"] == []


def test_positions_empty_on_exchange_error() -> None:
    """거래소 호출 실패 시 빈 리스트 (UI 안전)."""
    from unittest.mock import AsyncMock, MagicMock

    mock_client = MagicMock()
    mock_client.get_positions = AsyncMock(side_effect=RuntimeError("network down"))
    bot_instance.get_instance().configure(client=mock_client)

    body = _client().get("/positions").json()
    assert body == []


def test_get_config_returns_defaults() -> None:
    r = _client().get("/config")
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["use_bollinger"] is False
    assert cfg["use_ma_cross"] is False
    assert cfg["use_harmonic"] is False
    assert cfg["use_ichimoku"] is False
    assert cfg["leverage"] == 10
    assert cfg["full_seed"] is False


def test_post_config_echoes_input() -> None:
    payload = {
        "use_bollinger": True,
        "use_ma_cross": False,
        "use_harmonic": True,
        "use_ichimoku": False,
        "leverage": 30,
        "risk_pct": 0.02,
        "full_seed": True,
    }
    r = _client().post("/config", json=payload)
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["use_bollinger"] is True
    assert cfg["use_harmonic"] is True
    assert cfg["leverage"] == 30
    assert cfg["full_seed"] is True


# ============================================================
# 제어 (start/stop) — BotInstance lifecycle
# ============================================================


def test_start_bot_sets_running_true() -> None:
    """``/start`` 호출 시 success=True 반환 + ``/status`` 의 running=True 반영."""
    client = _client()

    r = client.post("/start")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["message"] == "봇 시작됨"

    # /status 가 새 상태를 반영해야 함.
    s = client.get("/status")
    assert s.status_code == 200
    assert s.json()["running"] is True


def test_start_bot_when_already_running_returns_failure() -> None:
    """이미 실행 중인 상태에서 ``/start`` 재호출 시 success=False."""
    client = _client()
    client.post("/start")  # 첫 호출로 True 전이

    r = client.post("/start")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["message"] == "이미 실행 중"


def test_stop_bot_sets_running_false() -> None:
    """실행 중일 때 ``/stop`` 호출 시 success=True + running=False 로 복귀."""
    client = _client()
    client.post("/start")  # 먼저 실행 상태로

    r = client.post("/stop")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["message"] == "봇 중지됨"

    s = client.get("/status")
    assert s.status_code == 200
    assert s.json()["running"] is False


def test_stop_bot_when_already_stopped_returns_failure() -> None:
    """초기 상태(중지)에서 ``/stop`` 호출 시 success=False."""
    r = _client().post("/stop")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["message"] == "이미 중지됨"


def test_restart_bot_from_running_state() -> None:
    """실행 중 상태에서 ``/restart`` 호출 시 stop + start 통합 → running=True 유지."""
    client = _client()
    client.post("/start")  # 실행 상태 진입

    r = client.post("/restart")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["message"] == "봇 재시작됨"

    # 재시작 후 running=True 유지 (stop 후 다시 start)
    s = client.get("/status")
    assert s.json()["running"] is True


def test_restart_bot_from_stopped_state() -> None:
    """중지 상태에서 ``/restart`` 호출 시 stop 단계 skip → start 만."""
    client = _client()  # 초기 상태 = 중지

    r = client.post("/restart")
    assert r.status_code == 200
    assert r.json()["success"] is True

    s = client.get("/status")
    assert s.json()["running"] is True


def test_relaunch_without_launcher_path_v0_1_43(monkeypatch) -> None:
    """v0.1.43: ``/relaunch`` 호출 시 launcher path env 없으면 실패 응답.

    Why: 사용자가 launcher 없이 직접 본체 .exe 실행한 경우 launcher path 모름.
    위험한 spawn 방지 위해 명시 실패 응답 (silent 종료 X).
    """
    monkeypatch.delenv("AURORA_LAUNCHER_PATH", raising=False)
    client = _client()
    r = client.post("/relaunch")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "launcher" in body["message"].lower()


# ============================================================
# Logs
# ============================================================


def test_logs_default_limit() -> None:
    r = _client().get("/logs")
    assert r.status_code == 200
    body = r.json()
    assert body["lines"] == []
    assert body["limit"] == 100


def test_logs_custom_limit() -> None:
    r = _client().get("/logs?limit=50")
    assert r.status_code == 200
    assert r.json()["limit"] == 50


def test_logs_returns_lines_with_limit() -> None:
    r = _client().get("/logs?limit=50")
    assert r.status_code == 200
    body = r.json()
    assert "lines" in body
    assert body["limit"] == 50
    assert isinstance(body["lines"], list)


# ============================================================
# WebSocket /ws/live
# ============================================================


def test_ws_live_connects_and_receives_catchup() -> None:
    """/ws/live 연결 직후 최근 로그 catch-up 메시지 수신."""
    log_buffer.install()
    import logging
    logging.getLogger("test.ws").info("ws catchup msg")

    with _client().websocket_connect("/ws/live") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "log"
        assert msg["data"]["message"] == "ws catchup msg"
        assert "ts" in msg["data"]
        assert "level" in msg["data"]
        assert "logger" in msg["data"]


def test_ws_live_empty_buffer_no_catchup() -> None:
    """버퍼가 비어있으면 catch-up 메시지 없이 연결만 유지."""
    client = _client()
    with client.websocket_connect("/ws/live") as ws:
        # 메시지 없으면 receive_json 은 blocking — close 로 확인
        ws.close()


# ============================================================
# CORS (Pywebview file:// origin 호환)
# ============================================================


def test_cors_headers_present() -> None:
    r = _client().options(
        "/status",
        headers={
            "Origin": "http://127.0.0.1:8765",
            "Access-Control-Request-Method": "GET",
        },
    )
    # CORS preflight 응답 — 200 또는 204
    assert r.status_code in (200, 204)
    # CORSMiddleware 가 origin echo
    assert "access-control-allow-origin" in {k.lower() for k in r.headers}
