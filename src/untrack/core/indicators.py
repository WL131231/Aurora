"""기술 지표 계산 — 모든 함수는 OHLCV DataFrame을 받아 Series/값을 반환.

설계 원칙:
    - 순수 함수 (외부 상태·IO 없음)
    - pandas DataFrame 입력 표준: ['open', 'high', 'low', 'close', 'volume']
    - 결과는 입력 인덱스를 그대로 유지
    - pandas + numpy 만 사용 (외부 지표 라이브러리 의존 X)

담당: 장수
"""

from __future__ import annotations

import pandas as pd

# ===== Fixed 지표 =====


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


def rsi_divergence(close: pd.Series, rsi_series: pd.Series, lookback: int = 30) -> pd.Series:
    """RSI 다이버전스 감지 (윈도우 분할 비교 방식).

    각 봉 t 에서 직전 ``lookback`` 봉 윈도우를 절반으로 나눠 비교:
        - **bullish**: 두 번째 절반의 최저가 < 첫 번째 절반의 최저가
                       AND 두 번째 절반의 최저 RSI > 첫 번째 절반의 최저 RSI
                       (가격은 새 저점인데 모멘텀은 약화 → 강세 반전 신호)
        - **bearish**: 두 번째 절반의 최고가 > 첫 번째 절반의 최고가
                       AND 두 번째 절반의 최고 RSI < 첫 번째 절반의 최고 RSI
                       (가격은 새 고점인데 모멘텀은 약화 → 약세 반전 신호)

    Note:
        간단한 1차 구현. 정밀한 ZigZag 기반 피벗 감지는 추후 개선 가능.

    Args:
        close: 종가 시리즈.
        rsi_series: 미리 계산된 RSI 시리즈 (close 와 같은 인덱스·길이).
        lookback: 비교할 lookback 기간 (봉 단위, 기본 30, 최소 4).

    Returns:
        문자열 시리즈 — 각 봉에서 ``"bullish"``, ``"bearish"``, 또는 None.
        초기 ``lookback`` 봉은 모두 None.

    Raises:
        ValueError: lookback 이 4 미만이거나, close 와 rsi_series 의
                    길이가 다를 때.
    """
    if lookback < 4:
        raise ValueError(f"lookback 은 4 이상이어야 함 (받은 값: {lookback})")
    if len(close) != len(rsi_series):
        raise ValueError(
            f"close({len(close)}) 와 rsi_series({len(rsi_series)}) 길이 불일치"
        )

    result: pd.Series = pd.Series([None] * len(close), index=close.index, dtype=object)
    mid = lookback // 2

    for i in range(lookback, len(close)):
        win_close = close.iloc[i - lookback : i + 1]
        win_rsi = rsi_series.iloc[i - lookback : i + 1]

        first_close = win_close.iloc[:mid]
        first_rsi = win_rsi.iloc[:mid]
        second_close = win_close.iloc[mid:]
        second_rsi = win_rsi.iloc[mid:]

        # 강세 다이버전스: 가격 새 저점 + RSI 더 높은 저점
        if (
            second_close.min() < first_close.min()
            and second_rsi.min() > first_rsi.min()
        ):
            result.iloc[i] = "bullish"
        # 약세 다이버전스: 가격 새 고점 + RSI 더 낮은 고점
        elif (
            second_close.max() > first_close.max()
            and second_rsi.max() < first_rsi.max()
        ):
            result.iloc[i] = "bearish"

    return result


# ===== Selectable 지표 =====


def bollinger_bands(close: pd.Series, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    """볼린저 밴드.

    Returns:
        DataFrame with columns: ['upper', 'middle', 'lower']
    """
    # TODO(A)
    raise NotImplementedError


def ma_cross(close: pd.Series, fast: int, slow: int) -> pd.Series:
    """MA 골든/데드 크로스 감지.

    Returns:
        시리즈 — 'golden', 'dead', 또는 None per bar.
    """
    # TODO(A)
    raise NotImplementedError


def harmonic_pattern(ohlc: pd.DataFrame) -> pd.Series:
    """하모닉 패턴 감지 (Bat / Butterfly / Gartley).

    Returns:
        시리즈 — pattern name 또는 None per bar.
    """
    # TODO(A): ZigZag 피벗 + 피보 비율 검증
    raise NotImplementedError


def ichimoku_cloud(ohlc: pd.DataFrame) -> pd.DataFrame:
    """일목균형표 — Span A, Span B 등.

    Returns:
        DataFrame with columns: ['tenkan', 'kijun', 'span_a', 'span_b', 'chikou']
    """
    # TODO(A)
    raise NotImplementedError
