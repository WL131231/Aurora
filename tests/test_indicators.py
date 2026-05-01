"""core.indicators 단위 테스트."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aurora.core.indicators import (
    bollinger_bands,
    ema,
    ichimoku_cloud,
    ma_cross,
    pivot_high,
    pivot_low,
    rsi,
    rsi_divergence,
)

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
# Pivot 헬퍼
# ============================================================


def test_pivot_low_detects_local_min() -> None:
    """V 자 시리즈의 바닥이 피벗 저점으로 검출되어야 함."""
    series = pd.Series([5.0, 4.0, 3.0, 2.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    # 인덱스 4 가 최저점, lb_left=4, lb_right=4 → 봉 8 에서 검출
    result = pivot_low(series, lb_left=4, lb_right=4)
    assert bool(result.iloc[8]) is True


def test_pivot_high_detects_local_max() -> None:
    """역 V 자 시리즈의 꼭대기가 피벗 고점으로 검출되어야 함."""
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0, 2.0, 1.0])
    result = pivot_high(series, lb_left=4, lb_right=4)
    assert bool(result.iloc[8]) is True


def test_pivot_low_invalid_lb_raises() -> None:
    series = pd.Series([1.0] * 20)
    with pytest.raises(ValueError):
        pivot_low(series, lb_left=0, lb_right=5)


def test_pivot_high_invalid_lb_raises() -> None:
    series = pd.Series([1.0] * 20)
    with pytest.raises(ValueError):
        pivot_high(series, lb_left=5, lb_right=0)


# ============================================================
# RSI Divergence
# ============================================================


def test_rsi_divergence_length_matches_input() -> None:
    rng = np.random.RandomState(0)
    close = pd.Series(rng.randn(200).cumsum() + 100)
    r = rsi(close, period=14)
    result = rsi_divergence(close, close, r)
    assert len(result) == len(close)


def test_rsi_divergence_only_valid_labels() -> None:
    """결과 라벨은 4 가지 종류 또는 None 만."""
    rng = np.random.RandomState(7)
    close = pd.Series(rng.randn(300).cumsum() + 100)
    r = rsi(close, period=14)
    result = rsi_divergence(close, close, r)
    valid = result.dropna().unique()
    expected = {"regular_bull", "hidden_bull", "regular_bear", "hidden_bear"}
    assert all(label in expected for label in valid)


def test_rsi_divergence_detects_regular_bull() -> None:
    """규칙적 강세 다이버전스: 가격 새 저점 + RSI 더 높은 저점.

    피벗 확정에 lb_right(5) 봉의 미래가 필요하므로 회복 구간을 데이터에 포함.
    1차 급락 → 반등(피벗1 확정) → 2차 완만한 하락 → 회복(피벗2 확정).
    """
    close = pd.Series(
        list(np.linspace(100.0, 50.0, 30))     # 1차 급락: 매우 가파름 → RSI 깊이 떨어짐
        + list(np.linspace(50.0, 75.0, 20))     # 반등 (피벗1 확정)
        + list(np.linspace(75.0, 40.0, 35))     # 2차 완만 하락 → 새 저점 + RSI 덜 떨어짐
        + list(np.linspace(40.0, 55.0, 15)),    # 회복 (피벗2 확정)
    )
    r = rsi(close, period=14)
    result = rsi_divergence(close, close, r)
    assert "regular_bull" in result.values, (
        f"regular_bull 미검출. 결과: {result.dropna().tolist()}"
    )


def test_rsi_divergence_detects_regular_bear() -> None:
    """규칙적 약세 다이버전스: 가격 새 고점 + RSI 더 낮은 고점."""
    close = pd.Series(
        list(np.linspace(50.0, 100.0, 30))     # 1차 급등: 가파름 → RSI 높이 오름
        + list(np.linspace(100.0, 75.0, 20))    # 조정 (피벗1 확정)
        + list(np.linspace(75.0, 110.0, 35))    # 2차 완만 상승 → 새 고점 + RSI 덜 오름
        + list(np.linspace(110.0, 95.0, 15)),   # 조정 (피벗2 확정)
    )
    r = rsi(close, period=14)
    result = rsi_divergence(close, close, r)
    assert "regular_bear" in result.values, (
        f"regular_bear 미검출. 결과: {result.dropna().tolist()}"
    )


def test_rsi_divergence_length_mismatch_raises() -> None:
    low = pd.Series([1.0] * 30)
    high = pd.Series([2.0] * 30)
    r = pd.Series([50.0] * 25)
    with pytest.raises(ValueError):
        rsi_divergence(low, high, r)


def test_rsi_divergence_invalid_lb_raises() -> None:
    s = pd.Series([1.0] * 50)
    with pytest.raises(ValueError):
        rsi_divergence(s, s, s, lb_left=0)


def test_rsi_divergence_invalid_range_raises() -> None:
    s = pd.Series([1.0] * 50)
    with pytest.raises(ValueError):
        rsi_divergence(s, s, s, range_lower=10, range_upper=5)


# ============================================================
# Bollinger Bands
# ============================================================


def test_bb_columns_and_length() -> None:
    close = pd.Series([float(x) for x in range(50)])
    bb = bollinger_bands(close, period=20)
    assert list(bb.columns) == ["upper", "middle", "lower"]
    assert len(bb) == len(close)


def test_bb_initial_nan_until_period() -> None:
    """period=20 이면 첫 19 봉은 NaN, 20번째부터 값."""
    close = pd.Series([100.0] * 50)
    bb = bollinger_bands(close, period=20)
    assert bb.iloc[:19].isna().all().all()
    assert not bb.iloc[19:].isna().any().any()


def test_bb_constant_series_zero_width() -> None:
    """일정 가격 → sigma=0 → upper=middle=lower."""
    close = pd.Series([100.0] * 50)
    bb = bollinger_bands(close, period=20)
    last = bb.iloc[-1]
    assert last["upper"] == pytest.approx(100.0)
    assert last["middle"] == pytest.approx(100.0)
    assert last["lower"] == pytest.approx(100.0)


def test_bb_upper_above_middle_above_lower() -> None:
    """변동성 시 upper > middle > lower."""
    rng = np.random.RandomState(0)
    close = pd.Series(rng.randn(100).cumsum() + 100)
    bb = bollinger_bands(close, period=20)
    valid = bb.dropna()
    assert (valid["upper"] > valid["middle"]).all()
    assert (valid["middle"] > valid["lower"]).all()


def test_bb_std_multiplier_widens_band() -> None:
    """std 배수 클수록 밴드 폭 비례 증가."""
    rng = np.random.RandomState(0)
    close = pd.Series(rng.randn(100).cumsum() + 100)
    bb1 = bollinger_bands(close, period=20, std=1.0)
    bb3 = bollinger_bands(close, period=20, std=3.0)
    width1 = (bb1["upper"] - bb1["lower"]).iloc[-1]
    width3 = (bb3["upper"] - bb3["lower"]).iloc[-1]
    assert width3 == pytest.approx(width1 * 3.0)


def test_bb_invalid_period_raises() -> None:
    with pytest.raises(ValueError):
        bollinger_bands(pd.Series([1.0, 2.0]), period=0)


def test_bb_invalid_std_raises() -> None:
    with pytest.raises(ValueError):
        bollinger_bands(pd.Series([1.0, 2.0]), std=0)


# ============================================================
# MA Cross (Golden / Dead)
# ============================================================


def test_ma_cross_length_and_dtype() -> None:
    """결과 길이는 입력과 동일, dtype object."""
    rng = np.random.RandomState(0)
    close = pd.Series(rng.randn(50).cumsum() + 100)
    result = ma_cross(close, fast=5, slow=10)
    assert len(result) == len(close)
    assert result.dtype == object


def test_ma_cross_constant_no_cross() -> None:
    """일정 가격이면 fast == slow → 크로스 없음."""
    close = pd.Series([100.0] * 100)
    result = ma_cross(close, fast=5, slow=20)
    assert result.dropna().empty


def test_ma_cross_detects_golden() -> None:
    """V자 (하락 → 상승) 데이터에서 golden cross 발생."""
    closes = list(np.linspace(100, 50, 30)) + list(np.linspace(50, 100, 30))
    result = ma_cross(pd.Series(closes), fast=5, slow=10)
    assert "golden" in result.values


def test_ma_cross_detects_dead() -> None:
    """역 V자 (상승 → 하락) 데이터에서 dead cross 발생."""
    closes = list(np.linspace(50, 100, 30)) + list(np.linspace(100, 50, 30))
    result = ma_cross(pd.Series(closes), fast=5, slow=10)
    assert "dead" in result.values


def test_ma_cross_initial_bars_none() -> None:
    """초기 slow 봉은 NaN → None."""
    rng = np.random.RandomState(0)
    close = pd.Series(rng.randn(50).cumsum() + 100)
    result = ma_cross(close, fast=5, slow=20)
    # 처음 20봉 (slow까지) 은 None
    assert result.iloc[:19].isna().all()


def test_ma_cross_only_valid_labels() -> None:
    """결과 라벨은 'golden' / 'dead' / None 만."""
    rng = np.random.RandomState(7)
    close = pd.Series(rng.randn(200).cumsum() + 100)
    result = ma_cross(close, fast=10, slow=30)
    valid = result.dropna().unique()
    assert all(label in ("golden", "dead") for label in valid)


def test_ma_cross_invalid_period_raises() -> None:
    with pytest.raises(ValueError):
        ma_cross(pd.Series([1.0] * 50), fast=0, slow=10)


def test_ma_cross_fast_ge_slow_raises() -> None:
    """fast ≥ slow 면 무효."""
    with pytest.raises(ValueError):
        ma_cross(pd.Series([1.0] * 50), fast=10, slow=10)
    with pytest.raises(ValueError):
        ma_cross(pd.Series([1.0] * 50), fast=20, slow=10)


# ============================================================
# Ichimoku Cloud
# ============================================================


def _make_ohlc(closes: list[float], spread: float = 0.5) -> pd.DataFrame:
    """high = close + spread / low = close - spread 의 단순 OHLC 생성."""
    close = pd.Series(closes)
    return pd.DataFrame({
        "open": close,
        "high": close + spread,
        "low": close - spread,
        "close": close,
    })


def test_ichimoku_columns_and_length() -> None:
    """결과 컬럼은 span_a/span_b/cloud_upper/cloud_lower, 길이는 입력과 동일."""
    df = _make_ohlc([100.0 + i for i in range(120)])
    result = ichimoku_cloud(df)
    assert list(result.columns) == ["span_a", "span_b", "cloud_upper", "cloud_lower"]
    assert len(result) == len(df)


def test_ichimoku_initial_nan_until_warmup() -> None:
    """초기 (span_b_period + displacement - 2) 봉은 NaN — 기본값 52 + 26 - 2 = 76."""
    df = _make_ohlc([100.0 + i * 0.1 for i in range(120)])
    result = ichimoku_cloud(df)
    # 76번째 인덱스 이전은 모두 NaN
    assert result["span_b"].iloc[:76].isna().all()
    # 76번째 봉부터는 NaN 아님
    assert not pd.isna(result["span_b"].iloc[76])


def test_ichimoku_span_a_is_avg_of_tenkan_kijun_shifted() -> None:
    """Span A = (Tenkan + Kijun) / 2 를 25봉 forward shift."""
    closes = [100.0 + i * 0.5 for i in range(120)]
    df = _make_ohlc(closes, spread=0.5)
    result = ichimoku_cloud(df)

    # Tenkan/Kijun 직접 계산 (donchian)
    high = df["high"]
    low = df["low"]
    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2.0
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2.0
    expected_lead_a = (tenkan + kijun) / 2.0
    expected_span_a = expected_lead_a.shift(25)

    pd.testing.assert_series_equal(
        result["span_a"], expected_span_a, check_names=False
    )


def test_ichimoku_span_b_is_donchian52_shifted() -> None:
    """Span B = donchian(52) 를 25봉 forward shift."""
    closes = [100.0 + (i % 10) for i in range(120)]
    df = _make_ohlc(closes, spread=1.0)
    result = ichimoku_cloud(df)

    high = df["high"]
    low = df["low"]
    expected_lead_b = (high.rolling(52).max() + low.rolling(52).min()) / 2.0
    expected_span_b = expected_lead_b.shift(25)

    pd.testing.assert_series_equal(
        result["span_b"], expected_span_b, check_names=False
    )


def test_ichimoku_cloud_upper_lower_relation() -> None:
    """cloud_upper >= cloud_lower (NaN 제외)."""
    rng = np.random.RandomState(42)
    closes = list(rng.randn(120).cumsum() + 100)
    df = _make_ohlc(closes, spread=1.0)
    result = ichimoku_cloud(df)
    valid = result.dropna()
    assert (valid["cloud_upper"] >= valid["cloud_lower"]).all()


def test_ichimoku_cloud_upper_is_max_of_spans() -> None:
    """cloud_upper = max(span_a, span_b), cloud_lower = min(span_a, span_b)."""
    rng = np.random.RandomState(7)
    closes = list(rng.randn(120).cumsum() + 100)
    df = _make_ohlc(closes, spread=0.5)
    result = ichimoku_cloud(df).dropna()

    pair_max = result[["span_a", "span_b"]].max(axis=1)
    pair_min = result[["span_a", "span_b"]].min(axis=1)
    pd.testing.assert_series_equal(result["cloud_upper"], pair_max, check_names=False)
    pd.testing.assert_series_equal(result["cloud_lower"], pair_min, check_names=False)


def test_ichimoku_constant_price_flat_cloud() -> None:
    """일정 가격이면 span_a == span_b → 두께 0."""
    df = _make_ohlc([100.0] * 120, spread=0.0)
    result = ichimoku_cloud(df).dropna()
    pd.testing.assert_series_equal(result["span_a"], result["span_b"], check_names=False)
    assert (result["cloud_upper"] - result["cloud_lower"]).abs().max() == pytest.approx(0.0)


def test_ichimoku_custom_periods() -> None:
    """커스텀 기간 파라미터 동작 (warmup 길이 변화)."""
    df = _make_ohlc([100.0 + i * 0.1 for i in range(60)])
    result = ichimoku_cloud(
        df,
        conversion_period=5,
        base_period=10,
        span_b_period=20,
        displacement=10,
    )
    # warmup = span_b_period + displacement - 2 = 20 + 10 - 2 = 28
    assert result["span_b"].iloc[:28].isna().all()
    assert not pd.isna(result["span_b"].iloc[28])


def test_ichimoku_missing_columns_raises() -> None:
    df = pd.DataFrame({"close": [100.0] * 60})
    with pytest.raises(ValueError):
        ichimoku_cloud(df)


def test_ichimoku_invalid_periods_raises() -> None:
    df = _make_ohlc([100.0] * 60)
    with pytest.raises(ValueError):
        ichimoku_cloud(df, conversion_period=0)
    with pytest.raises(ValueError):
        ichimoku_cloud(df, base_period=0)
    with pytest.raises(ValueError):
        ichimoku_cloud(df, span_b_period=0)


def test_ichimoku_invalid_displacement_raises() -> None:
    df = _make_ohlc([100.0] * 60)
    with pytest.raises(ValueError):
        ichimoku_cloud(df, displacement=0)
