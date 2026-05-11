"""Coinalyze 추세 통합 단위 테스트 (v0.1.53).

interpret_score / trend_filter / trend_score_multiplier 회귀.
CoinalyzeClient._cache_valid + __init__ + score edge-case 추가.
"""

from __future__ import annotations

import time

from aurora.market.coinalyze import (
    CoinalyzeClient,
    MarketTrend,
    _interpret_score,
    trend_filter,
    trend_score_multiplier,
)

# ============================================================
# _interpret_score — 매매일지 정합 score 산출
# ============================================================


def test_interpret_score_strong_long() -> None:
    """OI·가격 동반 ↑ + CVD 둘 다 매수 + funding 중립 → 강한 롱 (+4)."""
    score, direction, strong, reasons = _interpret_score(
        oi=110, oi_24h=100, price=80100, price_24h=80000,
        cvd_spot=1000, cvd_futures=2000, funding_rate=0.0005,
    )
    assert score == 4
    assert direction == "long"
    assert strong is True
    assert "OI·가격 동반 상승(신규 롱 유입)" in reasons
    assert "현물·선물 CVD 모두 매수 우세" in reasons


def test_interpret_score_strong_short() -> None:
    """OI ↑ + 가격 ↓ + CVD 둘 다 매도 + funding 롱 과열 → 강한 숏 (-5)."""
    score, direction, strong, reasons = _interpret_score(
        oi=110, oi_24h=100, price=79000, price_24h=80000,
        cvd_spot=-1000, cvd_futures=-2000, funding_rate=0.002,
    )
    assert score == -5
    assert direction == "short"
    assert strong is True


def test_interpret_score_oi_down_price_up_cvd_buy() -> None:
    """OI ↓ + 가격 ↑ + CVD 매수 우세 → 약한~강한 롱 (스크린샷 패턴 정합).

    사용자 스크린샷 BTC: 가격 -1.9% + OI -4.4% + CVD 매수 + Funding -0.18% → 롱.
    여기는 OI↓·가격↑ 패턴 검증.
    """
    score, direction, strong, reasons = _interpret_score(
        oi=95, oi_24h=100, price=80100, price_24h=80000,
        cvd_spot=500, cvd_futures=1000, funding_rate=0.0005,
    )
    # OI↓·가격↑ +1, CVD 둘 매수 +2, funding 중립 0 → 합 +3
    assert score == 3
    assert direction == "long"
    assert strong is True


def test_interpret_score_screenshot_btc_pattern() -> None:
    """매매일지 스크린샷 BTC 패턴 정합 — 가격↓ OI↓ CVD↑ Funding 숏극단 → 롱(+2).

    사용자 보고 (2026-05-05) BTC:
    - 가격 -1.9%, OI -4.4% → -1 (둘 다 ↓ = 롱 청산)
    - CVD Spot +5.12M, Futures +29.53M → +2 (둘 다 매수)
    - Funding -0.1837% (< -0.1%) → +1 (숏 극단)
    - 합 = +2 → 롱 ⭐️
    """
    score, direction, strong, reasons = _interpret_score(
        oi=95, oi_24h=100, price=79000, price_24h=80000,
        cvd_spot=5_120_000, cvd_futures=29_530_000, funding_rate=-0.001837,
    )
    assert score == 2
    assert direction == "long"
    assert strong is True


def test_interpret_score_neutral() -> None:
    """OI 미상 + CVD 0 + Funding 미상 → 중립 (0)."""
    score, direction, strong, _reasons = _interpret_score(
        oi=None, oi_24h=None, price=None, price_24h=None,
        cvd_spot=0, cvd_futures=0, funding_rate=None,
    )
    assert score == 0
    assert direction == "neutral"
    assert strong is False


# ============================================================
# trend_filter — 강한 추세 반대 진입 차단
# ============================================================


def _direction_for(score: int) -> str:
    if score >= 1:
        return "long"
    if score <= -1:
        return "short"
    return "neutral"


def _trend(
    score: int,
    *,
    s_short: int | None = None,
    s_mid_short: int | None = None,
    s_mid: int | None = None,
) -> MarketTrend:
    """Helper — score 기반 MarketTrend 생성. v0.1.84: multi-tf 박음.

    기본: 셋 다 같은 score (legacy 단일 tf 본질). multi-tf 케이스 측 명시 박음.
    """
    sh = s_short if s_short is not None else score
    msh = s_mid_short if s_mid_short is not None else score
    md = s_mid if s_mid is not None else score
    return MarketTrend(
        coin="BTC", score=md, direction=_direction_for(md),
        strong=abs(md) >= 2, reasons=["test"], fetched_at_ms=0,
        score_short=sh, score_mid_short=msh, score_mid=md,
        direction_short=_direction_for(sh),
        direction_mid_short=_direction_for(msh),
        direction_mid=_direction_for(md),
    )


def test_trend_filter_strong_long_blocks_short() -> None:
    """강한 롱 (+2) + SHORT 진입 → 차단."""
    assert trend_filter(_trend(2), "short") is True
    assert trend_filter(_trend(3), "short") is True


def test_trend_filter_strong_short_blocks_long() -> None:
    """강한 숏 (-2) + LONG 진입 → 차단."""
    assert trend_filter(_trend(-2), "long") is True
    assert trend_filter(_trend(-3), "long") is True


def test_trend_filter_strong_same_direction_no_block() -> None:
    """강한 롱 (+2) + LONG 진입 → 차단 X (같은 방향)."""
    assert trend_filter(_trend(2), "long") is False
    assert trend_filter(_trend(-2), "short") is False


def test_trend_filter_weak_blocks_opposite() -> None:
    """v0.1.58: 약한 추세 (±1) + 반대 진입 → 차단 (사용자 요청).

    추세 = 롱/강한 롱 이면 무조건 롱만, 추세 = 숏/강한 숏 이면 무조건 숏만.
    중립 (0) 만 양방향 허용.
    """
    assert trend_filter(_trend(1), "short") is True
    assert trend_filter(_trend(-1), "long") is True


def test_trend_filter_weak_same_direction_no_block() -> None:
    """약한 추세 + 같은 방향 → 차단 X (통과)."""
    assert trend_filter(_trend(1), "long") is False
    assert trend_filter(_trend(-1), "short") is False


def test_trend_filter_neutral_no_block() -> None:
    """중립 (0) → 차단 X."""
    assert trend_filter(_trend(0), "long") is False
    assert trend_filter(_trend(0), "short") is False


def test_trend_filter_none_no_block() -> None:
    """trend=None (Coinalyze 비활성 또는 fetch 실패) → 차단 X (기존 동작)."""
    assert trend_filter(None, "long") is False
    assert trend_filter(None, "short") is False


# ============================================================
# trend_score_multiplier — 진입 score 가중치
# ============================================================


def test_trend_multiplier_all_three_match() -> None:
    """v0.1.84: 셋 다 일치 (단기/중단기/중기 모두 같은 방향) → ×2.0 (강 정렬)."""
    assert trend_score_multiplier(_trend(2), "long") == 2.0
    assert trend_score_multiplier(_trend(-2), "short") == 2.0
    # 약한 추세도 셋 다 일치 시 ×2.0
    assert trend_score_multiplier(_trend(1), "long") == 2.0


def test_trend_multiplier_two_match() -> None:
    """v0.1.84: 둘 일치 / 한 개 X → ×1.5."""
    # 단기 long + 중단기 long + 중기 short → long 신호 측 matches=2
    t = _trend(0, s_short=1, s_mid_short=1, s_mid=-1)
    assert trend_score_multiplier(t, "long") == 1.5


def test_trend_multiplier_one_match() -> None:
    """v0.1.84: 한 개만 일치 → ×1.0."""
    # 단기 long + 중단기 short + 중기 short → long 신호 측 matches=1
    t = _trend(0, s_short=1, s_mid_short=-1, s_mid=-1)
    assert trend_score_multiplier(t, "long") == 1.0


def test_trend_multiplier_neutral() -> None:
    """셋 다 중립 → ×1.0."""
    assert trend_score_multiplier(_trend(0), "long") == 1.0
    assert trend_score_multiplier(_trend(0), "short") == 1.0


def test_trend_multiplier_strong_opposite() -> None:
    """v0.1.84: 둘 이상 반대 방향 → ×0.5 (강 반대)."""
    # 셋 다 반대
    assert trend_score_multiplier(_trend(1), "short") == 0.5
    assert trend_score_multiplier(_trend(-1), "long") == 0.5
    # 둘 반대 + 한 개 중립
    t = _trend(0, s_short=-1, s_mid_short=-1, s_mid=0)
    assert trend_score_multiplier(t, "long") == 0.5


def test_trend_multiplier_none() -> None:
    """trend=None → ×1.0 (기존 동작 유지)."""
    assert trend_score_multiplier(None, "long") == 1.0
    assert trend_score_multiplier(None, "short") == 1.0


# ============================================================
# CoinalyzeClient.__init__ — 생성자 검증
# ============================================================


def test_client_empty_api_key_raises_value_error() -> None:
    """빈 문자열 api_key → ValueError."""
    with __import__("pytest").raises(ValueError, match="Coinalyze API key required"):
        CoinalyzeClient(api_key="")


# ============================================================
# CoinalyzeClient._cache_valid — TTL 캐시 유효성 검사
# ============================================================


def test_cache_valid_returns_false_when_coin_absent() -> None:
    """coin 키가 _cache_ts 에 없으면 False 반환."""
    client = CoinalyzeClient(api_key="dummy")
    assert client._cache_valid("BTC") is False


def test_cache_valid_returns_true_for_fresh_entry() -> None:
    """방금 저장한 ts → TTL 이내 → True."""
    client = CoinalyzeClient(api_key="dummy", cache_ttl_sec=300)
    client._cache_ts["BTC"] = time.time()
    assert client._cache_valid("BTC") is True


def test_cache_valid_returns_false_for_expired_entry() -> None:
    """TTL + 1초 이전 ts → 만료 → False."""
    client = CoinalyzeClient(api_key="dummy", cache_ttl_sec=300)
    client._cache_ts["BTC"] = time.time() - 301
    assert client._cache_valid("BTC") is False


# ============================================================
# _interpret_score — 미커버 edge-case 분기
# ============================================================


def test_interpret_score_slightly_negative_funding_decrements() -> None:
    """펀딩 -0.05% (-0.1 ≤ pct < 0) → score -1, "펀딩 숏 우세".

    pct > 0.1 / pct < -0.1 / pct >= 0 분기를 모두 통과해 else 에 진입하는
    경계 케이스. CVD/OI 모두 0 으로 격리해 funding 효과만 검증.
    """
    score, direction, strong, reasons = _interpret_score(
        oi=None, oi_24h=None, price=None, price_24h=None,
        cvd_spot=0, cvd_futures=0,
        funding_rate=-0.0005,  # pct = -0.05% — 경계 안쪽
    )
    assert score == -1
    assert direction == "short"
    assert "펀딩 숏 우세" in reasons


def test_interpret_score_zero_funding_neutral_reason() -> None:
    """펀딩 0.0% → "펀딩 중립" reason 추가, score 변화 없음."""
    score, _dir, _strong, reasons = _interpret_score(
        oi=None, oi_24h=None, price=None, price_24h=None,
        cvd_spot=0, cvd_futures=0,
        funding_rate=0.0,
    )
    assert score == 0
    assert "펀딩 중립" in reasons


def test_interpret_score_cvd_futures_only_positive() -> None:
    """cvd_spot=0, cvd_futures>0 → score +1, "선물 CVD 매수 우세".

    현물 CVD 없이 선물 CVD 만 양수인 경우 (elif cvd_f > 0 분기).
    """
    score, direction, _strong, reasons = _interpret_score(
        oi=None, oi_24h=None, price=None, price_24h=None,
        cvd_spot=0, cvd_futures=500,
        funding_rate=None,
    )
    assert score == 1
    assert direction == "long"
    assert "선물 CVD 매수 우세" in reasons
