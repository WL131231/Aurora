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

    Args:
        close: 종가 시리즈.
        period: 기간 (예: 200, 480).

    Returns:
        EMA 시리즈.
    """
    # TODO(장수): pandas + numpy 로 직접 구현 (pandas-ta 사용 안 함)
    raise NotImplementedError


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI 지표."""
    # TODO(A)
    raise NotImplementedError


def rsi_divergence(close: pd.Series, rsi_series: pd.Series, lookback: int = 30) -> pd.Series:
    """RSI 다이버전스 감지.

    Returns:
        시리즈 — 'bullish', 'bearish', 또는 None per bar.
    """
    # TODO(A): 가격 저점 → RSI 저점 비교 (bullish), 고점 → 고점 (bearish)
    raise NotImplementedError


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
