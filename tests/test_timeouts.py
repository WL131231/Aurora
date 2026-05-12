"""aurora.timeouts — ClientTimeout 팩토리 함수 단위 테스트.

make_exchange_timeout / make_dashboard_session_timeout /
make_dashboard_series_session_timeout / make_coinalyze_timeout
반환값 타입 + total 값 상수 정합 검증.

네트워크 호출 없음 — 팩토리 함수 직접 호출.

담당: 정용우
"""

from __future__ import annotations

import aiohttp
import pytest

from aurora.timeouts import (
    COINALYZE_HTTP_TIMEOUT_SEC,
    DASHBOARD_FLOW_SESSION_TIMEOUT_SEC,
    DASHBOARD_SERIES_SESSION_TIMEOUT_SEC,
    EXCHANGE_HTTP_TIMEOUT_SEC,
    make_coinalyze_timeout,
    make_dashboard_series_session_timeout,
    make_dashboard_session_timeout,
    make_exchange_timeout,
)

# ── make_exchange_timeout ─────────────────────────────────────────────


def test_make_exchange_timeout_returns_client_timeout() -> None:
    """반환값이 aiohttp.ClientTimeout 인스턴스."""
    assert isinstance(make_exchange_timeout(), aiohttp.ClientTimeout)


def test_make_exchange_timeout_total_matches_constant() -> None:
    """total 값이 EXCHANGE_HTTP_TIMEOUT_SEC 상수와 일치."""
    t = make_exchange_timeout()
    assert t.total == pytest.approx(EXCHANGE_HTTP_TIMEOUT_SEC)


# ── make_dashboard_session_timeout ───────────────────────────────────


def test_make_dashboard_session_timeout_returns_client_timeout() -> None:
    """반환값이 aiohttp.ClientTimeout 인스턴스."""
    assert isinstance(make_dashboard_session_timeout(), aiohttp.ClientTimeout)


def test_make_dashboard_session_timeout_total_matches_constant() -> None:
    """total 값이 DASHBOARD_FLOW_SESSION_TIMEOUT_SEC 상수와 일치."""
    t = make_dashboard_session_timeout()
    assert t.total == pytest.approx(DASHBOARD_FLOW_SESSION_TIMEOUT_SEC)


# ── make_dashboard_series_session_timeout ────────────────────────────


def test_make_dashboard_series_session_timeout_returns_client_timeout() -> None:
    """반환값이 aiohttp.ClientTimeout 인스턴스."""
    assert isinstance(make_dashboard_series_session_timeout(), aiohttp.ClientTimeout)


def test_make_dashboard_series_session_timeout_total_matches_constant() -> None:
    """total 값이 DASHBOARD_SERIES_SESSION_TIMEOUT_SEC 상수와 일치."""
    t = make_dashboard_series_session_timeout()
    assert t.total == pytest.approx(DASHBOARD_SERIES_SESSION_TIMEOUT_SEC)


# ── make_coinalyze_timeout ────────────────────────────────────────────


def test_make_coinalyze_timeout_returns_client_timeout() -> None:
    """반환값이 aiohttp.ClientTimeout 인스턴스."""
    assert isinstance(make_coinalyze_timeout(), aiohttp.ClientTimeout)


def test_make_coinalyze_timeout_total_matches_constant() -> None:
    """total 값이 COINALYZE_HTTP_TIMEOUT_SEC 상수와 일치."""
    t = make_coinalyze_timeout()
    assert t.total == pytest.approx(COINALYZE_HTTP_TIMEOUT_SEC)


# ── 상수 sanity — 합리적인 범위 (0 < timeout ≤ 30 sec) ────────────────


@pytest.mark.parametrize("value", [
    EXCHANGE_HTTP_TIMEOUT_SEC,
    DASHBOARD_FLOW_SESSION_TIMEOUT_SEC,
    DASHBOARD_SERIES_SESSION_TIMEOUT_SEC,
    COINALYZE_HTTP_TIMEOUT_SEC,
])
def test_timeout_constant_in_sane_range(value: float) -> None:
    """모든 timeout 상수는 0 초과 30 이하 (설정 실수 회귀 가드)."""
    assert 0 < value <= 30.0


def test_factory_functions_return_independent_objects() -> None:
    """호출마다 새 인스턴스 반환 — 동일 객체 공유 X."""
    t1 = make_exchange_timeout()
    t2 = make_exchange_timeout()
    assert t1 is not t2
