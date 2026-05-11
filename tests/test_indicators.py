"""core.indicators 단위 테스트."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aurora.core.indicators import (
    HarmonicMatch,
    atr_wilder,
    bollinger_bands,
    detect_pivots,
    dual_supertrend_alignment,
    ema,
    harmonic_pattern,
    ichimoku_cloud,
    ma_cross,
    pivot_high,
    pivot_low,
    rsi,
    rsi_divergence,
    volume_confirmation,
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


# ============================================================
# Harmonic Pattern (5종 PDF 풀 검증)
# ============================================================


def _zigzag_ohlc(points: list[float], between: int = 10, spread: float = 0.1) -> pd.DataFrame:
    """X-A-B-C-D... 꼭짓점들을 between 봉씩 선형 보간해 OHLC 생성.

    각 leg 의 마지막 봉에서 high/low 가 다음 꼭짓점에 정확히 닿도록 함.
    """
    closes: list[float] = []
    for i in range(len(points) - 1):
        leg = list(np.linspace(points[i], points[i + 1], between, endpoint=False))
        closes.extend(leg)
    closes.append(points[-1])
    s = pd.Series(closes, dtype=float)
    return pd.DataFrame({
        "open": s,
        "high": s + spread,
        "low": s - spread,
        "close": s,
    })


def test_detect_pivots_columns_and_dir() -> None:
    """반환 DataFrame 컬럼/dir 부호 검증."""
    df = _zigzag_ohlc([100, 110, 100, 110, 100], between=5)
    pivots = detect_pivots(df, length=4)
    assert list(pivots.columns) == ["bar_idx", "value", "dir"]
    assert pivots["dir"].isin((-1, 1)).all()


def test_detect_pivots_alternating_direction() -> None:
    """ZigZag 데이터에서 피벗 방향이 교대로 나옴 (high → low → high → ...)."""
    df = _zigzag_ohlc([100, 110, 100, 110, 100, 110], between=5)
    pivots = detect_pivots(df, length=4)
    # 같은 방향 연속이 없어야 함
    dirs = pivots["dir"].tolist()
    for i in range(len(dirs) - 1):
        assert dirs[i] != dirs[i + 1]


def test_detect_pivots_invalid_length_raises() -> None:
    df = _zigzag_ohlc([100, 110], between=5)
    with pytest.raises(ValueError):
        detect_pivots(df, length=1)


def test_detect_pivots_missing_columns_raises() -> None:
    df = pd.DataFrame({"close": [100.0] * 30})
    with pytest.raises(ValueError):
        detect_pivots(df)


def test_harmonic_returns_none_when_no_pattern() -> None:
    """랜덤성 데이터에서 패턴 없으면 None."""
    df = _zigzag_ohlc([100, 110], between=15)
    assert harmonic_pattern(df) is None


def test_harmonic_detects_bullish_bat() -> None:
    """Bullish Bat 정확 검출 (XAB≈0.5, XAD≈0.866, BCD≈2.4, AB=CD≈1.23)."""
    # X=100(low), A=120(high), B=110(low), C=115(high), D=102.7(low), E=104(high) 더미
    pts = [105, 100, 120, 110, 115, 102.7, 104]
    df = _zigzag_ohlc(pts, between=10)
    match = harmonic_pattern(df, pivot_length=5, tolerance=0.10)
    assert match is not None
    assert match.name == "bat"
    assert match.direction == "long"
    assert match.x == pytest.approx(99.9, abs=0.5)
    assert match.a == pytest.approx(120.1, abs=0.5)
    # SL = A - 1.13 × |XA| = 120.1 - 1.13 × 20.2 = 97.274
    assert match.sl_price == pytest.approx(97.27, abs=0.5)


def test_harmonic_detects_bearish_bat() -> None:
    """Bearish Bat: 방향 반전 (X=high, A=low, B=high, C=low, D=high)."""
    # 가격 거꾸로 (X=120 high → A=100 low → B=110 high → C=105 low → D=117.3 high)
    pts = [115, 120, 100, 110, 105, 117.3, 116]
    df = _zigzag_ohlc(pts, between=10)
    match = harmonic_pattern(df, pivot_length=5, tolerance=0.10)
    assert match is not None
    assert match.name == "bat"
    assert match.direction == "short"


def test_harmonic_detects_bullish_butterfly() -> None:
    """Bullish Butterfly (XAB≈0.786, XAD≈1.179~1.272, BCD≈2.0, AB=CD≈1.0)."""
    # X=100, A=120 (XA=20), B=120-0.786×20=104.28, C=B+0.5×AB=112.14
    # AB=CD 옵션 1.0 충족 위해 CD=AB=15.72 → D=C-15.72=96.42
    # BCD = 15.72 / |C-B| = 15.72 / 7.86 = 2.0 (옵션 2.0 정확)
    # XAD = (120-96.42)/20 = 1.179 (target 1.272 ± 10% = 1.145~1.399 OK)
    pts = [110, 100, 120, 104.28, 112.14, 96.42, 98]
    df = _zigzag_ohlc(pts, between=10)
    match = harmonic_pattern(df, pivot_length=5, tolerance=0.10)
    assert match is not None
    assert match.name == "butterfly"
    assert match.direction == "long"


def test_harmonic_detects_bullish_gartley() -> None:
    """Bullish Gartley (XAB≈0.618, XAD≈0.786)."""
    # X=100, A=120 (XA=20), B=120-0.618×20=107.64, D=120-0.786×20=104.28
    # C: 0.5 of AB → C=107.64+0.5×12.36=113.82
    # CD=|D-C|=9.54, BC=|C-B|=6.18 → BCD=1.544 (옵션 1.618 ± 10% OK)
    # AB=CD = 9.54/12.36 = 0.772 → 옵션 1.0 ± 10% (0.9~1.1) 매치 X, 1.27 매치 X
    # → AB=CD 가 1.0 매치하도록 D 조정. CD=AB=12.36 → D=A-0.618×XA-12.36? 너무 깊음.
    # Gartley AB=CD 1.0: D=A-XA×0.786=104.28, CD=12.36 필요 → C=D+12.36=116.64
    # 그러면 ABC = (116.64-107.64)/12.36 = 0.728 (0.382~0.99 OK)
    # BCD = (116.64-104.28)/(116.64-107.64) = 12.36/9.0 = 1.373 (옵션 1.272/1.414 ± 10% OK)
    pts = [110, 100, 120, 107.64, 116.64, 104.28, 106]
    df = _zigzag_ohlc(pts, between=10)
    match = harmonic_pattern(df, pivot_length=5, tolerance=0.10)
    assert match is not None
    assert match.name == "gartley"
    assert match.direction == "long"


def test_harmonic_invalid_columns_raises() -> None:
    df = pd.DataFrame({"close": [100.0] * 60})
    with pytest.raises(ValueError):
        harmonic_pattern(df)


def test_harmonic_invalid_tolerance_raises() -> None:
    df = _zigzag_ohlc([100, 110], between=10)
    with pytest.raises(ValueError):
        harmonic_pattern(df, tolerance=0)


def test_harmonic_returns_none_with_few_pivots() -> None:
    """피벗 6개 미만이면 None."""
    df = _zigzag_ohlc([100, 110, 100], between=5)
    assert harmonic_pattern(df, pivot_length=4) is None


def test_harmonic_match_dataclass_immutable() -> None:
    """HarmonicMatch 는 frozen dataclass."""
    from dataclasses import FrozenInstanceError
    match = HarmonicMatch(
        name="bat", direction="long",
        x=100, a=120, b=110, c=115, d=102.7,
        x_bar=10, a_bar=20, b_bar=30, c_bar=40, d_bar=50,
        xab=0.5, abc=0.5, bcd=2.4, xad=0.865,
        sl_price=97.27, tp1_price=109.29, tp2_price=113.42,
    )
    with pytest.raises(FrozenInstanceError):
        match.name = "crab"  # type: ignore[misc]


# ============================================================
# Volume Confirmation
# ============================================================


def test_volume_confirmation_returns_bool_series() -> None:
    """반환 dtype 은 bool, 길이는 입력과 동일."""
    vol = pd.Series([100.0] * 30)
    result = volume_confirmation(vol, period=10, multiplier=1.5)
    assert len(result) == len(vol)
    assert result.dtype == bool


def test_volume_confirmation_initial_period_false() -> None:
    """초기 (period - 1) 봉은 SMA NaN → False."""
    vol = pd.Series([100.0] * 30)
    result = volume_confirmation(vol, period=10, multiplier=1.5)
    assert not result.iloc[:9].any()


def test_volume_confirmation_constant_volume_never_confirms() -> None:
    """일정 거래량 → 평균 = 자기 자신 → 1.5배 미달 → 모두 False."""
    vol = pd.Series([100.0] * 30)
    result = volume_confirmation(vol, period=10, multiplier=1.5)
    assert not result.any()


def test_volume_confirmation_spike_detected() -> None:
    """평균 대비 명확히 큰 스파이크 → True."""
    # 첫 20봉 거래량 100, 마지막 봉만 200 (= 평균 100 × 2)
    vol = pd.Series([100.0] * 20 + [200.0])
    result = volume_confirmation(vol, period=10, multiplier=1.5)
    assert bool(result.iloc[-1])
    # 이전 봉들은 평균이 100 이라 1.5×100=150 미달 → False
    assert not result.iloc[19]


def test_volume_confirmation_strong_spike_passes() -> None:
    """평균 대비 명확히 큰 스파이크 (3배) → True.

    Note:
        rolling SMA 는 마지막 봉도 평균에 포함하므로, 임계 계산엔 이미
        spike 가 포함된 평균이 쓰임. 따라서 ``multiplier × adjusted_avg``
        보다 큰 값이 필요.
    """
    vol = pd.Series([100.0] * 20 + [300.0])  # 3배 spike → 충분히 통과
    result = volume_confirmation(vol, period=10, multiplier=1.5)
    assert bool(result.iloc[-1])


def test_volume_confirmation_low_volume_fails() -> None:
    """평균 미만이면 False (거래량 위축)."""
    vol = pd.Series([100.0] * 20 + [50.0])  # 평균보다 작음
    result = volume_confirmation(vol, period=10, multiplier=1.5)
    assert not bool(result.iloc[-1])


def test_volume_confirmation_invalid_period_raises() -> None:
    with pytest.raises(ValueError):
        volume_confirmation(pd.Series([1.0, 2.0]), period=0)


def test_volume_confirmation_invalid_multiplier_raises() -> None:
    with pytest.raises(ValueError):
        volume_confirmation(pd.Series([1.0, 2.0]), multiplier=0)
    with pytest.raises(ValueError):
        volume_confirmation(pd.Series([1.0, 2.0]), multiplier=-0.5)


# ============================================================
# ATR Wilder — Stage 1C engine.py 직접 의존성 (DESIGN.md §11 D-4)
# ============================================================


def _ohlc_for_atr(highs: list[float], lows: list[float], closes: list[float]) -> pd.DataFrame:
    """ATR 테스트용 OHLC DataFrame 헬퍼 (open/volume 미사용)."""
    return pd.DataFrame({"high": highs, "low": lows, "close": closes})


def test_atr_wilder_length_matches_input() -> None:
    """ATR 길이 = 입력 길이 (인덱스 유지)."""
    df = _ohlc_for_atr(
        highs=[10, 11, 12, 11, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
        lows=[ 9, 10, 11, 10,  9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
        closes=[10, 11, 12, 11, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
    )
    atr = atr_wilder(df, period=14)
    assert len(atr) == len(df)


def test_atr_wilder_first_bar_equals_first_range() -> None:
    """첫 봉 ATR = high[0] - low[0] (prev_close NaN 이라 tr1 만 적용)."""
    df = _ohlc_for_atr(highs=[105.0], lows=[95.0], closes=[100.0])
    atr = atr_wilder(df, period=14)
    # ewm 첫 값 = TR[0] (alpha 보정 X)
    assert atr.iloc[0] == pytest.approx(10.0)


def test_atr_wilder_constant_range_converges() -> None:
    """모든 봉의 TR 동일하면 ATR 도 그 값으로 수렴."""
    n = 50
    # 매 봉 동일한 5 단위 변동 (high=close+2, low=close-3, 다음 close 그대로)
    closes = [100.0] * n
    highs = [102.0] * n
    lows = [97.0] * n
    df = _ohlc_for_atr(highs=highs, lows=lows, closes=closes)
    atr = atr_wilder(df, period=14)
    # TR[0] = 5 (high-low). TR[t≥1] = max(5, |102-100|, |97-100|) = 5.
    # Wilder MA 가 일정 입력 → 같은 값 수렴.
    assert atr.iloc[-1] == pytest.approx(5.0)


def test_atr_wilder_responds_to_volatility_spike() -> None:
    """변동성 급등 봉 발생 시 ATR 상승 (Wilder smoothing 단조 반응)."""
    n = 30
    # 안정 구간 (range=2) → spike (range=20) → 안정 (range=2)
    highs = [101.0] * 15 + [120.0] + [101.0] * (n - 16)
    lows = [99.0] * 15 + [100.0] + [99.0] * (n - 16)
    closes = [100.0] * n
    df = _ohlc_for_atr(highs=highs, lows=lows, closes=closes)
    atr = atr_wilder(df, period=14)
    # spike 직전 vs 직후 ATR 비교
    assert atr.iloc[15] > atr.iloc[14], "spike 봉에서 ATR 즉시 상승해야"
    # 안정 회귀하면 ATR 점진 하락 (다만 즉시는 아님 — Wilder 부드러움)
    assert atr.iloc[-1] < atr.iloc[15], "안정 구간 회귀 시 ATR 하락 추세"


def test_atr_wilder_invalid_period_raises() -> None:
    """period < 1 → ValueError."""
    df = _ohlc_for_atr(highs=[10, 11], lows=[9, 10], closes=[10, 11])
    with pytest.raises(ValueError):
        atr_wilder(df, period=0)
    with pytest.raises(ValueError):
        atr_wilder(df, period=-5)


def test_atr_wilder_missing_columns_raises() -> None:
    """필수 컬럼 누락 → ValueError."""
    # close 컬럼 없음
    df = pd.DataFrame({"high": [10, 11], "low": [9, 10]})
    with pytest.raises(ValueError):
        atr_wilder(df, period=14)
    # high 컬럼 없음
    df2 = pd.DataFrame({"low": [9, 10], "close": [10, 11]})
    with pytest.raises(ValueError):
        atr_wilder(df2, period=14)


def test_atr_wilder_gap_uses_prev_close_in_tr() -> None:
    """갭 발생 시 TR = max(H-L, |H-prevC|) — prev_close 반영 검증.

    봉 1: high=100, low=99, close=99.5
    봉 2: high=110, low=108, close=109 (갭 상승, prev_close=99.5)
      → tr1 = 110-108 = 2
      → tr2 = |110-99.5| = 10.5  ← 가장 큼
      → tr3 = |108-99.5| = 8.5
      → TR[1] = 10.5 (단순 H-L 이 아닌 prev_close 반영 입증)
    """
    df = _ohlc_for_atr(
        highs=[100.0, 110.0],
        lows=[99.0, 108.0],
        closes=[99.5, 109.0],
    )
    atr = atr_wilder(df, period=14)
    # ewm alpha=1/14, TR=[1.0, 10.5]
    # ATR[0] = 1.0
    # ATR[1] = 1.0 + (1/14)*(10.5 - 1.0) = 1.0 + 9.5/14 ≈ 1.6786
    assert atr.iloc[1] == pytest.approx(1.0 + 9.5 / 14.0, rel=1e-6)


# ============================================================
# dual_supertrend_alignment — 두 SuperTrend 정렬 신호
# ============================================================


def _ohlc_trend(closes: list[float], spread: float = 0.5) -> pd.DataFrame:
    """합성 OHLC DataFrame (high = close + spread, low = close - spread)."""
    return pd.DataFrame({
        "high":  [c + spread for c in closes],
        "low":   [c - spread for c in closes],
        "close": closes,
    })


def test_dual_st_alignment_both_bull_returns_plus_one() -> None:
    """강한 상승 추세 → 두 ST 모두 bull → +1."""
    closes = list(np.linspace(80.0, 120.0, 60))
    assert dual_supertrend_alignment(_ohlc_trend(closes)) == 1


def test_dual_st_alignment_both_bear_returns_minus_one() -> None:
    """강한 하락 추세 → 두 ST 모두 bear → -1."""
    closes = list(np.linspace(120.0, 80.0, 60))
    assert dual_supertrend_alignment(_ohlc_trend(closes)) == -1


def test_dual_st_alignment_insufficient_data_returns_zero() -> None:
    """데이터 부족 (< period + 2) → 0."""
    closes = [100.0] * 5
    assert dual_supertrend_alignment(_ohlc_trend(closes), period_fast=14, period_slow=14) == 0
