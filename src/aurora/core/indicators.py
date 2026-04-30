"""기술 지표 계산 — 모든 함수는 OHLCV DataFrame을 받아 Series/값을 반환.

설계 원칙:
    - 순수 함수 (외부 상태·IO 없음)
    - pandas DataFrame 입력 표준: ['open', 'high', 'low', 'close', 'volume']
    - 결과는 입력 인덱스를 그대로 유지
    - pandas + numpy 만 사용 (외부 지표 라이브러리 의존 X)

진입 신호 사용 정책:
    - **EMA**: 터치 거리로 진입 (Fixed)
    - **RSI 수치 자체**: 진입 신호로 사용하지 않음 (다이버전스 계산의 입력)
    - **RSI Divergence**: 단독 진입 가능 (Fixed) — 4 종류 검출
    - **Selectable**: BB / MA Cross / Harmonic / Ichimoku — 사용자 on/off

담당: 장수
"""

from __future__ import annotations

import pandas as pd

# ============================================================
# 헬퍼 — 피벗 검출
# ============================================================


def pivot_low(series: pd.Series, lb_left: int, lb_right: int) -> pd.Series:
    """피벗 저점 검출 — TradingView ``ta.pivotlow`` 와 동등.

    봉 ``t`` 에서 True 면, 봉 ``(t - lb_right)`` 가 좌우 ``lb_left``/``lb_right``
    봉 범위에서 로컬 최저점이라는 뜻. 즉 우측 ``lb_right`` 봉의 미래 데이터를
    써서 피벗을 확정하므로 검출은 ``lb_right`` 봉 지연됨.

    Args:
        series: 검사할 시리즈 (예: RSI 또는 close).
        lb_left: 좌측 lookback 봉수 (1 이상).
        lb_right: 우측 lookback 봉수 (1 이상).

    Returns:
        bool 시리즈 (입력 인덱스 유지) — 봉 t 에서 ``(t - lb_right)`` 가 피벗이면 True.

    Raises:
        ValueError: lb_left/lb_right 가 1 미만일 때.
    """
    if lb_left < 1 or lb_right < 1:
        raise ValueError(
            f"lb_left/lb_right 는 1 이상 (받은 값: {lb_left}, {lb_right})"
        )
    window = lb_left + lb_right + 1
    rolling_min = series.rolling(window=window, min_periods=window).min()
    candidate = series.shift(lb_right)
    return (candidate == rolling_min) & candidate.notna()


def pivot_high(series: pd.Series, lb_left: int, lb_right: int) -> pd.Series:
    """피벗 고점 검출 — TradingView ``ta.pivothigh`` 와 동등."""
    if lb_left < 1 or lb_right < 1:
        raise ValueError(
            f"lb_left/lb_right 는 1 이상 (받은 값: {lb_left}, {lb_right})"
        )
    window = lb_left + lb_right + 1
    rolling_max = series.rolling(window=window, min_periods=window).max()
    candidate = series.shift(lb_right)
    return (candidate == rolling_max) & candidate.notna()


# ============================================================
# Fixed 지표
# ============================================================


def ema(close: pd.Series, period: int) -> pd.Series:
    """지수이동평균 (EMA).

    공식:
        EMA[t] = α × close[t] + (1 - α) × EMA[t-1]
        α = 2 / (period + 1)

    pandas.Series.ewm 의 ``adjust=False`` 가 위 점화식과 동일한 결과를 줌.
    첫 값은 입력 첫 값을 그대로 사용.

    Args:
        close: 종가 시리즈.
        period: 기간 (예: 200, 480).

    Returns:
        EMA 시리즈 (입력 인덱스 유지, 길이 동일).

    Raises:
        ValueError: period 가 1 미만일 때.
    """
    if period < 1:
        raise ValueError(f"period 는 1 이상이어야 함 (받은 값: {period})")
    return close.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI 지표 — Wilder's smoothing 사용.

    Note:
        RSI 수치 자체는 진입 신호로 사용하지 않음.
        ``rsi_divergence`` 의 입력으로 활용.

    공식:
        delta    = close.diff()
        gain     = max(delta, 0)
        loss     = max(-delta, 0)
        avg_gain = Wilder MA(gain, period)
        avg_loss = Wilder MA(loss, period)
        RS       = avg_gain / avg_loss
        RSI      = 100 - 100 / (1 + RS)

    Wilder's MA = ``ewm(alpha=1/period, adjust=False).mean()`` 와 동일.

    Args:
        close: 종가 시리즈.
        period: 기간 (기본 14).

    Returns:
        RSI 시리즈 (0~100, 입력 인덱스 유지).
        가격이 일정해 변화 없을 때는 NaN (0/0 정의 안 됨).

    Raises:
        ValueError: period 가 1 미만일 때.
    """
    if period < 1:
        raise ValueError(f"period 는 1 이상이어야 함 (받은 값: {period})")

    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsi_divergence(
    low: pd.Series,
    high: pd.Series,
    rsi_series: pd.Series,
    lb_left: int = 5,
    lb_right: int = 5,
    range_lower: int = 5,
    range_upper: int = 60,
) -> pd.Series:
    """RSI 다이버전스 검출 — TradingView 표준 (피벗 기반).

    4 가지 종류 검출:
        - ``"regular_bull"``: 가격 더 낮은 저점(LL) + RSI 더 높은 저점(HL)
                              → 강세 반전 (롱 진입 신호)
        - ``"hidden_bull"``: 가격 더 높은 저점(HL) + RSI 더 낮은 저점(LL)
                             → 상승 추세 지속 (롱 추가 진입 신호)
        - ``"regular_bear"``: 가격 더 높은 고점(HH) + RSI 더 낮은 고점(LH)
                              → 약세 반전 (숏 진입 신호)
        - ``"hidden_bear"``: 가격 더 낮은 고점(LH) + RSI 더 높은 고점(HH)
                             → 하락 추세 지속 (숏 추가 진입 신호)

    감지 로직:
        1. RSI 시리즈에서 피벗 저점/고점 검출 (좌우 ``lb_left``/``lb_right`` 봉).
        2. 각 피벗에서 직전 같은 방향 피벗과 가격·RSI 값 비교.
        3. 두 피벗의 거리가 [``range_lower``, ``range_upper``] 범위면 검출.
        4. 검출은 피벗 확정 시점(피벗 인덱스 + ``lb_right``)에 표시.

    Args:
        low: 저가 시리즈 (강세 다이버전스에 사용).
        high: 고가 시리즈 (약세 다이버전스에 사용).
        rsi_series: 미리 계산된 RSI 시리즈 (low/high 와 같은 인덱스·길이).
        lb_left: 피벗 좌측 lookback 봉수 (기본 5, 최소 1).
        lb_right: 피벗 우측 lookback 봉수 (기본 5, 최소 1).
        range_lower: 이전 피벗과 최소 거리 봉수 (기본 5, 최소 1).
        range_upper: 이전 피벗과 최대 거리 봉수 (기본 60).

    Returns:
        문자열 시리즈 (입력 인덱스 유지) — 검출된 종류 또는 None.

    Raises:
        ValueError: 길이 불일치, 또는 파라미터 잘못된 값일 때.

    Example:
        >>> r = rsi(df["close"], period=14)
        >>> div = rsi_divergence(df["low"], df["high"], r)
        >>> df.loc[div == "regular_bull"]   # 강세 반전 시점만
    """
    if not (len(low) == len(high) == len(rsi_series)):
        raise ValueError(
            f"low({len(low)}), high({len(high)}), rsi_series({len(rsi_series)}) "
            "길이 불일치"
        )
    if range_lower < 1 or range_upper < range_lower:
        raise ValueError(
            f"range 잘못됨 (range_lower={range_lower}, range_upper={range_upper})"
        )

    n = len(rsi_series)
    result: pd.Series = pd.Series([None] * n, index=rsi_series.index, dtype=object)

    pl_found = pivot_low(rsi_series, lb_left, lb_right)
    ph_found = pivot_high(rsi_series, lb_left, lb_right)

    # ===== 강세 다이버전스 (RSI 피벗 저점 비교) =====
    last_pl_idx: int | None = None
    last_pl_rsi: float | None = None
    last_pl_low: float | None = None

    for t in range(n):
        if not bool(pl_found.iloc[t]):
            continue
        curr_idx = t - lb_right
        curr_rsi = float(rsi_series.iloc[curr_idx])
        curr_low = float(low.iloc[curr_idx])

        if last_pl_idx is not None:
            bars_apart = curr_idx - last_pl_idx
            if range_lower <= bars_apart <= range_upper:
                # Regular Bullish: 가격 LL + RSI HL
                if curr_low < last_pl_low and curr_rsi > last_pl_rsi:
                    result.iloc[t] = "regular_bull"
                # Hidden Bullish: 가격 HL + RSI LL
                elif curr_low > last_pl_low and curr_rsi < last_pl_rsi:
                    result.iloc[t] = "hidden_bull"

        last_pl_idx = curr_idx
        last_pl_rsi = curr_rsi
        last_pl_low = curr_low

    # ===== 약세 다이버전스 (RSI 피벗 고점 비교) =====
    last_ph_idx: int | None = None
    last_ph_rsi: float | None = None
    last_ph_high: float | None = None

    for t in range(n):
        if not bool(ph_found.iloc[t]):
            continue
        curr_idx = t - lb_right
        curr_rsi = float(rsi_series.iloc[curr_idx])
        curr_high = float(high.iloc[curr_idx])

        if last_ph_idx is not None:
            bars_apart = curr_idx - last_ph_idx
            if range_lower <= bars_apart <= range_upper:
                # Regular Bearish: 가격 HH + RSI LH
                if curr_high > last_ph_high and curr_rsi < last_ph_rsi:
                    result.iloc[t] = "regular_bear"
                # Hidden Bearish: 가격 LH + RSI HH
                elif curr_high < last_ph_high and curr_rsi > last_ph_rsi:
                    result.iloc[t] = "hidden_bear"

        last_ph_idx = curr_idx
        last_ph_rsi = curr_rsi
        last_ph_high = curr_high

    return result


# ============================================================
# Selectable 지표
# ============================================================


def bollinger_bands(close: pd.Series, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    """볼린저 밴드 (Bollinger Bands).

    공식:
        middle = SMA(close, period)
        sigma  = 표준편차(close, period, ddof=0 = 모집단 분산)
        upper  = middle + std × sigma
        lower  = middle - std × sigma

    초기 ``period - 1`` 봉은 NaN.

    Args:
        close: 종가 시리즈.
        period: 이동평균 기간 (기본 20).
        std: 표준편차 배수 (기본 2.0).

    Returns:
        DataFrame with columns: ['upper', 'middle', 'lower'].

    Raises:
        ValueError: period < 1, std ≤ 0 일 때.
    """
    if period < 1:
        raise ValueError(f"period 는 1 이상이어야 함 (받은 값: {period})")
    if std <= 0:
        raise ValueError(f"std 는 양수여야 함 (받은 값: {std})")

    middle = close.rolling(window=period, min_periods=period).mean()
    sigma = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = middle + std * sigma
    lower = middle - std * sigma

    return pd.DataFrame({"upper": upper, "middle": middle, "lower": lower})


def ma_cross(close: pd.Series, fast: int, slow: int) -> pd.Series:
    """MA 골든/데드 크로스 감지.

    Returns:
        시리즈 — 'golden', 'dead', 또는 None per bar.
    """
    # TODO(장수)
    raise NotImplementedError


def harmonic_pattern(ohlc: pd.DataFrame) -> pd.Series:
    """하모닉 패턴 감지 (Bat / Butterfly / Gartley).

    Returns:
        시리즈 — pattern name 또는 None per bar.
    """
    # TODO(장수): ZigZag 피벗 + 피보 비율 검증
    raise NotImplementedError


def ichimoku_cloud(ohlc: pd.DataFrame) -> pd.DataFrame:
    """일목균형표 — Span A, Span B 등.

    Returns:
        DataFrame with columns: ['tenkan', 'kijun', 'span_a', 'span_b', 'chikou']
    """
    # TODO(장수)
    raise NotImplementedError
