"""중앙 timeout 상수 — 곳곳 분산된 timeout 값 한 곳에서 관리 (v0.1.98 audit P2).

Why: v0.1.95~97 박힌 ccxt timeout 측 5/8/10/15초 측 곳곳 hardcode. 거래소 측
응답 시간 변화 / 사용자 환경별 튜닝 시 한 파일만 수정하면 박힘.

용도별 분류:
- API 엔드포인트 측 ccxt 호출 (사용자 UI 폴링) — 짧게 (5~8초)
- 봇 메인 루프 측 ccxt 호출 — 적당히 (5~15초, _step 1초 주기보다 충분히 길게)
- 외부 시장 자료 (Coinalyze / Dashboard Flow) — 8~10초
- HTTP session 측 client-side total timeout — 8~10초

각 상수 측 inline 도큐먼트 박힘 — 변경 시 영향 범위 명확.
"""

from __future__ import annotations

import aiohttp

# ============================================================
# API 엔드포인트 측 ccxt 호출 timeout (UI 폴링 친화)
# ============================================================
# /status 측 매 15초 폴링 → 5초 timeout 충분. 거래소 hang 시 빈 값 반환.
API_CCXT_TIMEOUT_SEC: float = 5.0
"""/status, /positions 측 ccxt 호출 timeout. UI 폴링 hang 차단용."""

# /trades, /stats 측 fetch_closed_positions 측 history 응답 큼 (200 records).
API_HISTORY_TIMEOUT_SEC: float = 8.0
"""/trades, /stats 측 history fetch timeout. 큰 응답 대비 여유."""


# ============================================================
# 봇 메인 루프 (_step) 측 ccxt 호출 timeout
# ============================================================
# _step 1초 주기. 너무 짧으면 정상 응답도 timeout, 너무 길면 hang 시 폴링 누적.
BOT_CACHE_FETCH_TIMEOUT_SEC: float = 15.0
"""_cache.step() (fetch_ohlcv) timeout. 봉 경계 시점만 호출, 다중 TF 병렬 fetch."""

BOT_TICKER_TIMEOUT_SEC: float = 5.0
"""fetch_ticker timeout. SL/TP 폴링용 — 빠른 응답 필수."""

BOT_POSITION_TIMEOUT_SEC: float = 5.0
"""fetch_position timeout. 외부 청산 / qty sync 감지용."""

BOT_TREND_TIMEOUT_SEC: float = 5.0
"""Coinalyze fetch_trend timeout. 진입 평가 시점 호출."""


# ============================================================
# 외부 시장 자료 fetch (Dashboard Flow / Coinalyze)
# ============================================================
# Dashboard Flow 측 5 거래소 병렬 fetch — per-provider 타임아웃 (한 거래소 hang
# 가 다른 거래소 영향 X).
DASHBOARD_FLOW_PROVIDER_TIMEOUT_SEC: float = 8.0
"""dashboard_flow 측 per-provider timeout (Binance/Bybit/OKX/Bitget/Hyperliquid)."""

DASHBOARD_FLOW_SESSION_TIMEOUT_SEC: float = 10.0
"""dashboard_flow 측 aiohttp.ClientSession total timeout. provider × N 합산 envelope."""

# v0.1.115: 14D 시계열 fetch — 거래소별 5 endpoint 병렬 → snapshot 보다 envelope ↑
DASHBOARD_SERIES_PROVIDER_TIMEOUT_SEC: float = 12.0
"""dashboard_series 측 per-provider timeout. 5 endpoint 병렬 fetch envelope."""

DASHBOARD_SERIES_SESSION_TIMEOUT_SEC: float = 15.0
"""dashboard_series 측 ClientSession total timeout. 5 거래소 × 5 endpoint envelope."""

# 거래소별 endpoint 측 client-side timeout (ClientSession 단위).
EXCHANGE_HTTP_TIMEOUT_SEC: float = 8.0
"""거래소 HTTP 측 ClientSession total timeout (Binance/Bybit/OKX/Bitget/HL 공통)."""

# Coinalyze API timeout (별도 — 5분 cache 박힘).
COINALYZE_HTTP_TIMEOUT_SEC: float = 8.0
"""Coinalyze API 측 ClientSession total timeout."""


# ============================================================
# aiohttp.ClientTimeout helper — 각 거래소 모듈 측 import 박음
# ============================================================


def make_exchange_timeout() -> aiohttp.ClientTimeout:
    """거래소별 marketdata fetcher 측 ClientTimeout 생성."""
    return aiohttp.ClientTimeout(total=EXCHANGE_HTTP_TIMEOUT_SEC)


def make_dashboard_session_timeout() -> aiohttp.ClientTimeout:
    """dashboard_flow Aggregator 측 ClientSession timeout."""
    return aiohttp.ClientTimeout(total=DASHBOARD_FLOW_SESSION_TIMEOUT_SEC)


def make_dashboard_series_session_timeout() -> aiohttp.ClientTimeout:
    """dashboard_series Aggregator 측 ClientSession timeout (v0.1.115)."""
    return aiohttp.ClientTimeout(total=DASHBOARD_SERIES_SESSION_TIMEOUT_SEC)


def make_coinalyze_timeout() -> aiohttp.ClientTimeout:
    """Coinalyze 측 ClientSession timeout."""
    return aiohttp.ClientTimeout(total=COINALYZE_HTTP_TIMEOUT_SEC)
