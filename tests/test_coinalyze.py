"""Coinalyze 추세 통합 단위 테스트 (v0.1.53).

interpret_score / trend_filter / trend_score_multiplier 회귀.
"""

from __future__ import annotations

from aurora.market.coinalyze import (
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


def _trend(score: int) -> MarketTrend:
    """Helper — score 기반 MarketTrend 생성."""
    if score >= 1:
        direction = "long"
    elif score <= -1:
        direction = "short"
    else:
        direction = "neutral"
    return MarketTrend(
        coin="BTC", score=score, direction=direction,
        strong=abs(score) >= 2, reasons=["test"], fetched_at_ms=0,
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

    추세 = 롭/강한 롭 이면 무조건 롭만, 추세 = 숙/강한 숙 이면 무조건 숙만.
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


def test_trend_multiplier_strong_match() -> None:
    """강한 추세 일치 → ×1.5."""
    assert trend_score_multiplier(_trend(2), "long") == 1.5
    assert trend_score_multiplier(_trend(-2), "short") == 1.5


def test_trend_multiplier_weak_match() -> None:
    """약한 추세 일치 → ×1.3."""
    assert trend_score_multiplier(_trend(1), "long") == 1.3
    assert trend_score_multiplier(_trend(-1), "short") == 1.3


def test_trend_multiplier_neutral() -> None:
    """중립 → ×1.0."""
    assert trend_score_multiplier(_trend(0), "long") == 1.0
    assert trend_score_multiplier(_trend(0), "short") == 1.0


def test_trend_multiplier_weak_opposite() -> None:
    """약한 추세 반대 → ×0.7."""
    assert trend_score_multiplier(_trend(1), "short") == 0.7
    assert trend_score_multiplier(_trend(-1), "long") == 0.7


def test_trend_multiplier_strong_opposite_fallback() -> None:
    """강한 추세 반대 — trend_filter 가 차단 처리해야 (여기 도달 X), fallback ×1.0."""
    # 차단 호출자가 안 했을 경우 fallback. multiplier 자체는 1.0 반환 (안전).
    assert trend_score_multiplier(_trend(2), "short") == 1.0
    assert trend_score_multiplier(_trend(-2), "long") == 1.0


def test_trend_multiplier_none() -> None:
    """trend=None → ×1.0 (기존 동작 유지)."""
    assert trend_score_multiplier(None, "long") == 1.0
    assert trend_score_multiplier(None, "short") == 1.0
