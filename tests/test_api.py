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


def test_market_trend_returns_disabled_without_api_key_v0_1_54() -> None:
    """v0.1.54: COINALYZE_API_KEY 미설정 시 /market-trend 가 enabled=False 반환.

    Why: bot._coinalyze 가 None (api_key 없음) → 비활성 응답. UI 가 카드 숨김.
    """
    client = _client()
    # 사용자 .env 에 키 박혀있을 수 있음 — 명시 비활성으로 격리
    bot_instance.get_instance()._coinalyze = None
    r = client.get("/market-trend")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["trends"] == []


def test_relaunch_marker_pattern_v0_1_83(monkeypatch, tmp_path) -> None:
    """v0.1.83: ``/relaunch`` 측 marker file 박음 패러다임.

    이전 (v0.1.43~v0.1.82): launcher_path env 검사 + launcher Popen + 본체 자체
    종료. 본체 자체 종료 9 회 fail (사용자 보고).

    새 패러다임 (v0.1.83): 본체 측 marker file 박음 + response 반환. launcher
    polling thread 가 marker 발견 시 process.terminate() 호출 (본체 kill).
    본체 자체 종료 의무 자체 X.
    """
    # tmp_path 측 LOCALAPPDATA fake — 본체 marker 박는 위치
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("AURORA_LAUNCHER_PATH", raising=False)
    client = _client()
    r = client.post("/relaunch")
    assert r.status_code == 200
    body = r.json()
    # launcher_path env 검사 X — marker 박음 자체 success 본질
    assert body["success"] is True
    # marker file 박힘 verify
    marker_path = tmp_path / "Aurora" / ".relaunch_request"
    assert marker_path.exists(), f"marker file 박힘 X: {marker_path}"


# ============================================================
# Chart (v0.1.86) — 봇 시점 차트
# ============================================================


def test_chart_returns_disabled_without_cache() -> None:
    """봇 미가동 (cache None) → enabled=False 반환. UI 가 placeholder 표시."""
    client = _client()
    # default 상태 — bot._cache is None
    r = client.get("/chart")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["timeframe"] == "1H"
    assert body["candles"] == []
    assert body["markers"] == []


def test_chart_returns_disabled_unknown_tf() -> None:
    """cache 에 없는 TF 요청 → enabled=False (KeyError 안전 처리)."""
    client = _client()
    r = client.get("/chart?timeframe=99h")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False


def test_chart_with_cache_returns_candles_and_indicators() -> None:
    """cache 박힘 + DataFrame 채워짐 → candles + 지표 라인 + markers 반환."""
    import pandas as pd

    client = _client()
    bot = bot_instance.get_instance()

    # Fake cache — 60 봉 OHLCV (1H) 박음. 지표 계산용 충분 길이.
    idx = pd.date_range("2026-01-01", periods=60, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "open":   [100.0 + i * 0.1 for i in range(60)],
        "high":   [101.0 + i * 0.1 for i in range(60)],
        "low":    [ 99.0 + i * 0.1 for i in range(60)],
        "close":  [100.5 + i * 0.1 for i in range(60)],
        "volume": [1000.0] * 60,
    }, index=idx)

    class _FakeCache:
        def get(self, tf: str) -> pd.DataFrame:
            if tf != "1H":
                raise KeyError(tf)
            return df

    bot._cache = _FakeCache()  # type: ignore[assignment]
    bot._symbol = "BTC/USDT:USDT"

    r = client.get("/chart?timeframe=1H&limit=50")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["symbol"] == "BTC/USDT:USDT"
    assert body["timeframe"] == "1H"
    assert len(body["candles"]) == 50
    # 첫 캔들 검증 — Unix sec + OHLC float
    c0 = body["candles"][0]
    assert isinstance(c0["time"], int)
    assert isinstance(c0["open"], float)
    # 지표 라인 박힘 (워밍업 NaN 빠진 후 일부 봉)
    assert len(body["ema_fast"]) > 0
    assert len(body["ema_slow"]) > 0
    assert len(body["bb_upper"]) > 0
    assert len(body["st_fast"]) > 0
    # 마커 — closed_trades 없음 + executor 없음 → 빈 list
    assert body["markers"] == []


# ============================================================
# Dashboard Flow (v0.1.87) — Phase 3 거래소 합본
# ============================================================


def test_dashboard_flow_returns_exchange_list() -> None:
    """``GET /dashboard-flow`` — exchanges 리스트 박힘. fetch 실패해도 빈 응답 정상."""
    from aurora.market import dashboard_flow as df_mod

    df_mod.reset_for_test()
    client = _client()

    # provider.fetch 측 raise → exception 격리 후 빈 snapshot 반환
    r = client.get("/dashboard-flow?coin=BTC")
    assert r.status_code == 200
    body = r.json()
    assert body["coin"] == "BTC"
    assert "binance" in body["exchanges"]
    # snapshot 박힘 (실제 네트워크 호출 — 측 errors 가 박힐 수도 / 정상일 수도)
    assert isinstance(body["snapshots"], list)


def test_dashboard_flow_uses_aggregator_singleton() -> None:
    """v0.1.87: 싱글톤 aggregator — 두 번째 호출 cache hit (TTL 60초)."""
    from aurora.market import dashboard_flow as df_mod

    df_mod.reset_for_test()
    # 싱글톤 박을 때 fake provider 측 박음 (실제 네트워크 호출 X)
    from aurora.market.dashboard_flow import (
        DashboardFlowAggregator,
    )
    from aurora.market.exchanges.base import ExchangeMarketData, ExchangeSnapshot

    class _Stub(ExchangeMarketData):
        EXCHANGE_NAME = "stub"
        call_count = 0

        async def fetch_snapshot(self, session, coin: str) -> ExchangeSnapshot:
            type(self).call_count += 1
            return ExchangeSnapshot(
                exchange="stub", symbol=f"{coin}USDT",
                fetched_at_ms=0, oi_usd=1_000_000.0,
            )

    df_mod._singleton = DashboardFlowAggregator([_Stub()], cache_ttl_sec=60)

    client = _client()
    r1 = client.get("/dashboard-flow?coin=BTC")
    r2 = client.get("/dashboard-flow?coin=BTC")
    assert r1.status_code == r2.status_code == 200
    body = r1.json()
    assert body["exchanges"] == ["stub"]
    assert body["total_oi_usd"] == 1_000_000.0
    # 두 번째 호출은 cache hit
    assert _Stub.call_count == 1


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


# ============================================================
# Android APK 업데이트 상태 (Phase C-2, v0.1.58)
# ============================================================


def test_apk_status_returns_200() -> None:
    """/update/apk-status — 항상 200 반환 (데스크탑/CI 포함)."""
    r = _client().get("/update/apk-status")
    assert r.status_code == 200


def test_apk_status_shape_idle() -> None:
    """기본 상태(idle) — 필수 키 전부 존재, has_update=False."""
    from aurora.interfaces import apk_updater
    apk_updater._state.update(
        has_update=False, apk_path=None, latest_tag=None,
        status="idle", download_pct=0, error_msg=None,
    )
    r = _client().get("/update/apk-status")
    body = r.json()
    assert body["has_update"] is False
    assert body["apk_path"] is None
    assert body["latest_tag"] is None
    assert body["status"] == "idle"
    assert body["download_pct"] == 0
    assert body["error_msg"] is None


def test_apk_status_reflects_downloading_state() -> None:
    """다운로드 중 상태 — status / download_pct 정확히 반영."""
    from aurora.interfaces import apk_updater
    apk_updater._state.update(
        has_update=False, apk_path=None, latest_tag="v999.0.0",
        status="downloading", download_pct=57, error_msg=None,
    )
    body = _client().get("/update/apk-status").json()
    assert body["status"] == "downloading"
    assert body["download_pct"] == 57
    assert body["latest_tag"] == "v999.0.0"


def test_apk_status_reflects_done_state() -> None:
    """다운로드 완료 — has_update=True, apk_path / latest_tag 노출."""
    from aurora.interfaces import apk_updater
    apk_updater._state.update(
        has_update=True, apk_path="/data/update/Aurora-android.apk",
        latest_tag="v999.0.0", status="done", download_pct=100, error_msg=None,
    )
    body = _client().get("/update/apk-status").json()
    assert body["has_update"] is True
    assert body["apk_path"] == "/data/update/Aurora-android.apk"
    assert body["status"] == "done"
    assert body["download_pct"] == 100


def test_apk_status_reflects_error_state() -> None:
    """오류 상태 — status=error, error_msg 노출."""
    from aurora.interfaces import apk_updater
    apk_updater._state.update(
        has_update=False, apk_path=None, latest_tag=None,
        status="error", download_pct=0, error_msg="connection refused",
    )
    body = _client().get("/update/apk-status").json()
    assert body["status"] == "error"
    assert body["error_msg"] == "connection refused"
