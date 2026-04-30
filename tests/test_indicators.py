"""core.indicators 단위 테스트."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from untrack.core.indicators import ema, rsi, rsi_divergence

# ============================================================
# EMA
# ============================================================


def test_ema_length_matches_input() -> None:
    """출력 길이는 입력과 동일."""
    close = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = ema(close, period=3)
    assert len(result) == len(close)


def test_ema_first_value_equals_input_first() -> None:
    """adjust=False 의 정의상 첫 EMA = 첫 입력 값."""
    close = pd.Series([10.0, 20.0, 30.0])
    result = ema(close, period=5)
    assert result.iloc[0] == pytest.approx(10.0)


def test_ema_constant_series_stays_constant() -> None:
    """일정한 가격이면 EMA 도 그 값 유지."""
    close = pd.Series([5.0] * 50)
    result = ema(close, period=20)
    assert result.iloc[-1] == pytest.approx(5.0)


def test_ema_recent_weighted_more_than_old() -> None:
    """최근 가격이 더 큰 가중치 → 단조 상승 시리즈에서 EMA 는 항상 입력보다 작거나 같음."""
    close = pd.Series([float(x) for x in range(100)])
    result = ema(close, period=20)
    # 모든 위치에서 EMA <= close (단조 상승이므로 EMA 는 따라가는 형태)
    assert (result <= close).all()


def test_ema_invalid_period_raises() -> None:
    with pytest.raises(ValueError):
        ema(pd.Series([1.0, 2.0]), period=0)


# ============================================================
# RSI
# ============================================================


def test_rsi_length_matches_input() -> None:
    close = pd.Series([float(x) for x in range(50)])
    result = rsi(close, period=14)
    assert len(result) == len(close)


def test_rsi_monotonic_rising_overbought() -> None:
    """단조 상승 가격이면 RSI > 70 (과매수 영역)."""
    close = pd.Series(np.linspace(100, 200, 100))
    result = rsi(close, period=14)
    assert result.iloc[-1] > 70


def test_rsi_monotonic_falling_oversold() -> None:
    """단조 하락 가격이면 RSI < 30 (과매도 영역)."""
    close = pd.Series(np.linspace(200, 100, 100))
    result = rsi(close, period=14)
    assert result.iloc[-1] < 30


def test_rsi_constant_series_returns_nan() -> None:
    """가격 변화가 없으면 RSI 정의 불가 → NaN."""
    close = pd.Series([100.0] * 50)
    result = rsi(close, period=14)
    assert pd.isna(result.iloc[-1])


def test_rsi_in_valid_range() -> None:
    """RSI 결과는 항상 0 ~ 100 범위."""
    rng = np.random.RandomState(42)
    close = pd.Series(rng.randn(200).cumsum() + 100)
    result = rsi(close, period=14)
    valid = result.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_rsi_invalid_period_raises() -> None:
    with pytest.raises(ValueError):
        rsi(pd.Series([1.0, 2.0]), period=0)


# ============================================================
# RSI Divergence
# ============================================================


def test_rsi_divergence_length_matches_input() -> None:
    rng = np.random.RandomState(0)
    close = pd.Series(rng.randn(100).cumsum() + 100)
    r = rsi(close, period=14)
    result = rsi_divergence(close, r, lookback=20)
    assert len(result) == len(close)


def test_rsi_divergence_initial_window_is_none() -> None:
    """lookback 봉 이전에는 비교 불가 → 모두 None."""
    rng = np.random.RandomState(0)
    close = pd.Series(rng.randn(100).cumsum() + 100)
    r = rsi(close, period=14)
    result = rsi_divergence(close, r, lookback=20)
    assert result.iloc[:20].isna().all()


def test_rsi_divergence_only_valid_labels() -> None:
    """결과는 'bullish' / 'bearish' / None 만 포함."""
    rng = np.random.RandomState(7)
    close = pd.Series(rng.randn(100).cumsum() + 100)
    r = rsi(close, period=14)
    result = rsi_divergence(close, r, lookback=20)
    valid_labels = result.dropna().unique()
    assert all(label in ("bullish", "bearish") for label in valid_labels)


def test_rsi_divergence_detects_bullish_pattern() -> None:
    """급락 → 반등 → 더 낮지만 완만한 하락 → bullish 검출 기대."""
    close = pd.Series(
        list(np.linspace(100.0, 60.0, 10))   # 급락 (RSI 깊이 떨어짐)
        + list(np.linspace(60.0, 85.0, 8))    # 반등
        + list(np.linspace(85.0, 55.0, 12)),  # 완만한 하락 (새 저점, RSI 덜 떨어짐)
    )
    r = rsi(close, period=14)
    result = rsi_divergence(close, r, lookback=30)
    assert "bullish" in result.values, (
        f"bullish 미검출. 결과: {result.dropna().tolist()}"
    )


def test_rsi_divergence_invalid_lookback_raises() -> None:
    close = pd.Series([1.0] * 50)
    r = pd.Series([50.0] * 50)
    with pytest.raises(ValueError):
        rsi_divergence(close, r, lookback=2)


def test_rsi_divergence_length_mismatch_raises() -> None:
    close = pd.Series([1.0] * 30)
    r = pd.Series([50.0] * 25)
    with pytest.raises(ValueError):
        rsi_divergence(close, r)
