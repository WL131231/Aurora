"""interfaces.api 단위 테스트 — FastAPI TestClient 로 엔드포인트 골격 검증.

stub 단계라 응답 구조와 status code 만 확인 (실제 봇 동작은 후속 PR).

담당: 정용우
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from aurora.interfaces.api import create_app


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
    assert body["version"] == "0.1.0"
    assert "mode" in body


def test_status_response_shape() -> None:
    r = _client().get("/status")
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is False
    assert body["open_positions"] == 0
    assert "equity_usd" in body
    assert "mode" in body


# ============================================================
# Positions / Config
# ============================================================


def test_positions_returns_list() -> None:
    r = _client().get("/positions")
    assert r.status_code == 200
    assert r.json() == []


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
# 제어 (stub — 미구현 응답)
# ============================================================


def test_start_bot_stub_returns_unimplemented() -> None:
    r = _client().post("/start")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "미구현" in body["message"]


def test_stop_bot_stub_returns_unimplemented() -> None:
    r = _client().post("/stop")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "미구현" in body["message"]


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
