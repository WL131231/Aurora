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

⚠️ 두 종류의 피벗 검출 함수가 공존 — 알고리즘이 다르므로 혼동 주의:

    | 함수 | 알고리즘 | 확정 시점 | 용도 |
    |---|---|---|---|
    | ``pivot_high`` / ``pivot_low`` | TradingView ``ta.pivothigh/low`` (좌우 lookback) | ``lb_right`` 봉 지연 | RSI Divergence (확정 피벗 비교) |
    | ``detect_pivots`` | TradingView ``ta.highestbars/lowestbars == 0`` (좌측 lookback만) | 실시간 (단 마지막은 갱신 가능) | Harmonic XABCD (ZigZag 누적) |

    - **lookback 양면**: ``pivot_*`` — 좌우 ``lb_left``/``lb_right`` 양쪽 봉이 후보보다 작아야 피벗 인정. 미래 봉 ``lb_right`` 만큼 기다려야 확정.
    - **lookback 좌면만**: ``detect_pivots`` — 봉 ``t`` 가 좌측 ``length`` 윈도우 max/min 이면 피벗. 즉시 검출, 단 같은 방향 후속 피벗으로 갱신 가능.

담당: 장수
"""

from __future__ import annotations

from dataclasses import dataclass

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


def ma_cross(close: pd.Series, fast: int = 50, slow: int = 200) -> pd.Series:
    """이평선(SMA) 골든/데드 크로스 감지.

    각 봉 기준:
        - **golden**: 직전 봉 fast ≤ slow + 현재 봉 fast > slow (방금 위로 돌파)
        - **dead**:   직전 봉 fast ≥ slow + 현재 봉 fast < slow (방금 아래로 돌파)
        - 그 외: None

    초기 ``slow`` 봉은 NaN (slow MA 계산 불가) → None.

    Args:
        close: 종가 시리즈.
        fast: 빠른 MA 기간 (기본 50).
        slow: 느린 MA 기간 (기본 200).

    Returns:
        문자열 시리즈 (입력 인덱스 유지) — 'golden' / 'dead' / None.

    Raises:
        ValueError: fast < 1, slow < 1, 또는 fast ≥ slow 일 때.
    """
    if fast < 1 or slow < 1:
        raise ValueError(f"fast/slow 는 1 이상 (받은 값: fast={fast}, slow={slow})")
    if fast >= slow:
        raise ValueError(f"fast 는 slow 보다 작아야 함 (fast={fast}, slow={slow})")

    fast_ma = close.rolling(window=fast, min_periods=fast).mean()
    slow_ma = close.rolling(window=slow, min_periods=slow).mean()

    diff = fast_ma - slow_ma
    diff_prev = diff.shift(1)

    n = len(close)
    result: pd.Series = pd.Series([None] * n, index=close.index, dtype=object)

    # NaN safe: 둘 다 valid 일 때만 비교
    valid = diff.notna() & diff_prev.notna()
    golden = valid & (diff_prev <= 0) & (diff > 0)
    dead = valid & (diff_prev >= 0) & (diff < 0)

    result[golden] = "golden"
    result[dead] = "dead"
    return result


def volume_confirmation(
    volume: pd.Series,
    period: int = 20,
    multiplier: float = 1.5,
) -> pd.Series:
    """거래량 컨펌 검출 — 평균 대비 ``multiplier`` 배 이상이면 True.

    EMA 보스 정책 (자료 인용):
        - "거래량 없는 상승 = 수급 에너지 약한 상승" → 신뢰도 낮음
        - "거래량 동반 = 진짜 의미 있는 상승" → 신뢰도 높음
        - **상대적 증감** 중요 (절대값보다)

    공식:
        avg = SMA(volume, period)
        confirmed[t] = volume[t] >= avg[t] × multiplier

    초기 ``period - 1`` 봉은 SMA NaN → False (안전).

    Args:
        volume: 거래량 시리즈.
        period: 평균 계산 기간 (기본 20).
        multiplier: 평균 대비 배율 임계 (기본 1.5).

    Returns:
        bool 시리즈 (입력 인덱스 유지).

    Raises:
        ValueError: period < 1 또는 multiplier <= 0.
    """
    if period < 1:
        raise ValueError(f"period 는 1 이상 (받은: {period})")
    if multiplier <= 0:
        raise ValueError(f"multiplier 는 양수여야 함 (받은: {multiplier})")

    avg = volume.rolling(window=period, min_periods=period).mean()
    threshold = avg * multiplier
    # NaN 안전: avg 가 NaN 이면 False
    return (volume >= threshold).fillna(False)


def detect_pivots(
    ohlc: pd.DataFrame,
    length: int = 10,
) -> pd.DataFrame:
    """ZigZag 류 피벗 검출 — 가장 최근 피벗부터 역순으로 반환.

    트뷰 ``Tailored-Custom Harmonic Patterns`` 의 ``pivots_f`` 알고리즘 포팅:
        - 봉 ``t`` 에서 ``high[t]`` 가 좌측 ``length`` 봉 윈도우의 최고가면 swing high
        - 봉 ``t`` 에서 ``low[t]``  가 좌측 ``length`` 봉 윈도우의 최저가면 swing low
        - 같은 방향 연속 피벗은 더 극단값으로 갱신 (high 면 더 높은 값,
          low 면 더 낮은 값) → ZigZag 효과
        - 마지막(가장 최근) 피벗은 미확정 가능성 → 호출자 측에서 ``[1:]`` 사용

    Note:
        피벗 검출은 ``length`` 봉만큼 lookback 만 사용 (우측 미래 봉 미사용) →
        실시간 검출 가능, 단 마지막 피벗은 추후 갱신될 수 있음.

    Args:
        ohlc: 'high', 'low' 컬럼이 있는 DataFrame.
        length: 좌측 lookback 봉수 (기본 10).

    Returns:
        DataFrame with columns: ['bar_idx', 'value', 'dir'] — 가장 최근 피벗이
        index 0 (역순). dir 은 +1 (high) 또는 -1 (low).

    Raises:
        ValueError: 컬럼 누락 또는 length < 2.
    """
    if not {"high", "low"}.issubset(ohlc.columns):
        raise ValueError(
            f"ohlc 에 'high','low' 컬럼 필요 (받은: {list(ohlc.columns)})"
        )
    if length < 2:
        raise ValueError(f"length 는 2 이상이어야 함 (받은: {length})")

    high = ohlc["high"].to_numpy()
    low = ohlc["low"].to_numpy()
    n = len(ohlc)

    rolling_high = ohlc["high"].rolling(window=length, min_periods=length).max().to_numpy()
    rolling_low = ohlc["low"].rolling(window=length, min_periods=length).min().to_numpy()

    pivots: list[tuple[int, float, int]] = []  # (bar_idx, value, dir)
    for t in range(length - 1, n):
        is_high = high[t] == rolling_high[t]
        is_low = low[t] == rolling_low[t]
        # 동시 만족 시 한쪽만 채택 (직전 추세 반대 방향 우선)
        if is_high and not is_low:
            new_pivot = (t, float(high[t]), 1)
        elif is_low and not is_high:
            new_pivot = (t, float(low[t]), -1)
        else:
            continue

        # 같은 방향 연속이면 더 극단값으로 갱신
        if pivots and pivots[-1][2] == new_pivot[2]:
            prev = pivots[-1]
            if (new_pivot[2] == 1 and new_pivot[1] > prev[1]) or (
                new_pivot[2] == -1 and new_pivot[1] < prev[1]
            ):
                pivots[-1] = new_pivot
        else:
            pivots.append(new_pivot)

    # 가장 최근 피벗이 index 0 (역순)
    pivots.reverse()
    if not pivots:
        return pd.DataFrame(columns=["bar_idx", "value", "dir"])
    return pd.DataFrame(pivots, columns=["bar_idx", "value", "dir"])


@dataclass(slots=True, frozen=True)
class HarmonicPatternSpec:
    """하모닉 패턴 검증 스펙 (PDF '할머니의하모닉' 기반).

    Note:
        모든 비율은 절댓값 기반: |B-A|/|X-A|, |D-C|/|B-C|, |D-A|/|X-A| 등.
        BC projection / AB=CD 는 여러 옵션 중 적어도 하나에 ±tolerance 매치
        시 통과 (PDF 의 multi-target 정의를 OR 조건으로 해석).
    """

    name: str
    b_min: float            # B/XA 최소 (예: Bat 0.382)
    b_max: float            # B/XA 최대 (예: Bat 0.55)
    d_target: float         # D/XA 정확값 (예: Bat 0.886, Crab 1.618)
    bc_proj_options: tuple[float, ...]  # BC projection 옵션 (CD/BC)
    abcd_options: tuple[float, ...]     # AB=CD 옵션 (CD/AB)
    sl_xa: float            # SL = sl_xa × XA (예: Bat 1.13)


HARMONIC_PATTERN_SPECS: tuple[HarmonicPatternSpec, ...] = (
    # PDF p.4 Bat Pattern
    HarmonicPatternSpec(
        name="bat",
        b_min=0.382, b_max=0.55,
        d_target=0.886,
        bc_proj_options=(1.618, 2.0, 2.24, 2.618),
        abcd_options=(1.0, 1.27, 1.618),
        sl_xa=1.13,
    ),
    # PDF p.8 Butterfly Pattern
    HarmonicPatternSpec(
        name="butterfly",
        b_min=0.756, b_max=0.816,
        d_target=1.272,
        bc_proj_options=(1.618, 2.0, 2.24),
        abcd_options=(1.0, 1.27),
        sl_xa=1.414,
    ),
    # PDF p.2 Gartley Pattern
    HarmonicPatternSpec(
        name="gartley",
        b_min=0.588, b_max=0.648,
        d_target=0.786,
        bc_proj_options=(1.13, 1.272, 1.414, 1.618),
        abcd_options=(1.0, 1.27),
        sl_xa=1.0,
    ),
    # PDF p.6 Crab Pattern
    HarmonicPatternSpec(
        name="crab",
        b_min=0.382, b_max=0.886,
        d_target=1.618,
        bc_proj_options=(2.618, 3.14, 3.618),
        abcd_options=(1.27, 1.618),
        sl_xa=2.0,
    ),
    # PDF p.7 Deep Crab Pattern
    HarmonicPatternSpec(
        name="deep_crab",
        b_min=0.886, b_max=0.936,
        d_target=1.618,
        bc_proj_options=(2.0, 2.24, 2.618, 3.14, 3.618),
        abcd_options=(1.0, 1.27, 1.618),
        sl_xa=2.0,
    ),
)


# C point 범위 (모든 패턴 공통, PDF: 0.382~0.99 AB)
_HARMONIC_C_MIN = 0.382
_HARMONIC_C_MAX = 0.99


def _within(value: float, target: float, tolerance: float) -> bool:
    """value 가 target ± tolerance 비율 이내면 True."""
    if target == 0:
        return abs(value) <= tolerance
    return abs(value - target) / abs(target) <= tolerance


def _within_any(value: float, targets: tuple[float, ...], tolerance: float) -> bool:
    """value 가 targets 중 적어도 하나와 ± tolerance 매치하면 True."""
    return any(_within(value, t, tolerance) for t in targets)


@dataclass(slots=True, frozen=True)
class HarmonicMatch:
    """검출된 하모닉 패턴 결과."""

    name: str           # 'bat' / 'butterfly' / 'gartley' / 'crab' / 'deep_crab'
    direction: str      # 'long' (bullish, X<A) / 'short' (bearish, X>A)
    x: float
    a: float
    b: float
    c: float
    d: float
    x_bar: int
    a_bar: int
    b_bar: int
    c_bar: int
    d_bar: int
    xab: float
    abc: float
    bcd: float
    xad: float
    sl_price: float     # 패턴별 SL 가격 (X 방향 연장)
    tp1_price: float    # 0.382 AD 되돌림
    tp2_price: float    # 0.618 AD 되돌림


def harmonic_pattern(
    ohlc: pd.DataFrame,
    pivot_length: int = 10,
    tolerance: float = 0.10,
) -> HarmonicMatch | None:
    """하모닉 패턴 검출 (PDF 5개 패턴 풀 검증, 마지막 봉 시점).

    검증 항목 (PDF '할머니의하모닉' 풀 스펙):
        1. **B point**: |B-A|/|X-A| ∈ [b_min, b_max] ± tolerance
        2. **C point**: |C-B|/|A-B| ∈ [0.382, 0.99] ± tolerance (모든 패턴 공통)
        3. **D point**: |D-A|/|X-A| ≈ d_target ± tolerance (정확값)
        4. **BC projection**: |D-C|/|B-C| ∈ bc_proj_options 중 하나 ± tolerance
        5. **AB=CD**: |D-C|/|B-A| ∈ abcd_options 중 하나 ± tolerance

    위 5개 모두 통과해야 패턴 인정.

    진입 방향 (PDF):
        - X < A (=가격이 X에서 위로) → bullish XABCD → **long** 진입 at D
        - X > A (=가격이 X에서 아래로) → bearish XABCD → **short** 진입 at D

    SL/TP (PDF, 패턴별 자체 룰):
        - SL = X 방향으로 패턴별 sl_xa × |X-A| 연장 가격
        - TP1 = 0.382 × |A-D| 되돌림 (A 방향)
        - TP2 = 0.618 × |A-D| 되돌림 (A 방향)

    Args:
        ohlc: 'high', 'low' 컬럼이 있는 DataFrame.
        pivot_length: 피벗 검출 lookback (기본 10).
        tolerance: 비율 검증 허용 오차 (기본 0.10 = ±10%).

    Returns:
        ``HarmonicMatch`` 객체 또는 None (패턴 미검출).

    Raises:
        ValueError: 컬럼 누락, pivot_length < 2, tolerance ≤ 0 일 때.
    """
    if not {"high", "low"}.issubset(ohlc.columns):
        raise ValueError(
            f"ohlc 에 'high','low' 컬럼 필요 (받은: {list(ohlc.columns)})"
        )
    if tolerance <= 0:
        raise ValueError(f"tolerance 는 양수여야 함 (받은: {tolerance})")

    pivots = detect_pivots(ohlc, length=pivot_length)
    # 가장 최근 피벗(index 0)은 미확정. 1번부터 5개(D,C,B,A,X) 사용.
    if len(pivots) < 6:
        return None

    d_row = pivots.iloc[1]
    c_row = pivots.iloc[2]
    b_row = pivots.iloc[3]
    a_row = pivots.iloc[4]
    x_row = pivots.iloc[5]

    # XABCD 방향 일관성: X-A-B-C-D 가 high-low-high-low-high (bearish) 또는
    # low-high-low-high-low (bullish) 지그재그여야 함
    expected_dirs_bull = (-1, 1, -1, 1, -1)  # X=low, A=high, B=low, C=high, D=low
    expected_dirs_bear = (1, -1, 1, -1, 1)
    actual_dirs = (
        int(x_row["dir"]),
        int(a_row["dir"]),
        int(b_row["dir"]),
        int(c_row["dir"]),
        int(d_row["dir"]),
    )
    if actual_dirs == expected_dirs_bull:
        direction = "long"  # bullish XABCD: D 가 swing low → 롱 진입
    elif actual_dirs == expected_dirs_bear:
        direction = "short"
    else:
        return None  # 지그재그 패턴 아님

    x = float(x_row["value"])
    a = float(a_row["value"])
    b = float(b_row["value"])
    c = float(c_row["value"])
    d = float(d_row["value"])

    # 비율 계산 (절댓값 기반, 트뷰 표준)
    xa_dist = abs(x - a)
    ab_dist = abs(a - b)
    bc_dist = abs(b - c)
    if xa_dist == 0 or ab_dist == 0 or bc_dist == 0:
        return None

    xab = abs(b - a) / xa_dist
    abc = abs(c - b) / ab_dist
    bcd = abs(d - c) / bc_dist
    xad = abs(d - a) / xa_dist
    abcd = abs(d - c) / ab_dist  # AB=CD 비율 (CD 길이 / AB 길이)

    # C point 공통 범위 검증 (0.382~0.99 ± tolerance)
    if not (
        _HARMONIC_C_MIN * (1 - tolerance) <= abc <= _HARMONIC_C_MAX * (1 + tolerance)
    ):
        return None

    # 5개 패턴 매칭 시도
    for spec in HARMONIC_PATTERN_SPECS:
        # 1. B point 범위
        if not (
            spec.b_min * (1 - tolerance) <= xab <= spec.b_max * (1 + tolerance)
        ):
            continue
        # 2. D point 정확값
        if not _within(xad, spec.d_target, tolerance):
            continue
        # 3. BC projection (옵션 중 하나)
        if not _within_any(bcd, spec.bc_proj_options, tolerance):
            continue
        # 4. AB=CD (옵션 중 하나)
        if not _within_any(abcd, spec.abcd_options, tolerance):
            continue

        # SL/TP 가격 계산 (PDF 룰)
        # SL = X 방향으로 sl_xa × |XA| 연장
        if direction == "long":
            # bullish: D=low, X=low, X<A. SL 은 D 보다 더 아래 (X 방향 연장)
            sl_price = a - spec.sl_xa * xa_dist
            tp1_price = d + 0.382 * abs(a - d)
            tp2_price = d + 0.618 * abs(a - d)
        else:
            # bearish: D=high, X=high, X>A. SL 은 D 보다 더 위
            sl_price = a + spec.sl_xa * xa_dist
            tp1_price = d - 0.382 * abs(a - d)
            tp2_price = d - 0.618 * abs(a - d)

        return HarmonicMatch(
            name=spec.name,
            direction=direction,
            x=x, a=a, b=b, c=c, d=d,
            x_bar=int(x_row["bar_idx"]),
            a_bar=int(a_row["bar_idx"]),
            b_bar=int(b_row["bar_idx"]),
            c_bar=int(c_row["bar_idx"]),
            d_bar=int(d_row["bar_idx"]),
            xab=xab, abc=abc, bcd=bcd, xad=xad,
            sl_price=sl_price,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
        )

    return None


def ichimoku_cloud(
    ohlc: pd.DataFrame,
    conversion_period: int = 9,
    base_period: int = 26,
    span_b_period: int = 52,
    displacement: int = 26,
) -> pd.DataFrame:
    """일목균형표 — Span A / Span B 구름대 (TradingView 표준).

    트뷰 Pine Script 표준 수식:
        donchian(len) = (rolling_high(len) + rolling_low(len)) / 2
        Tenkan = donchian(conversion_period)        # 내부 계산용
        Kijun  = donchian(base_period)              # 내부 계산용
        Span A = (Tenkan + Kijun) / 2               # Leading Span A
        Span B = donchian(span_b_period)            # Leading Span B
        plot offset = displacement - 1              # forward shift = 25봉

    Tenkan/Kijun 은 Span A 계산용으로만 내부에서 사용 (반환하지 않음).
    Span A / B 는 (displacement - 1) 만큼 forward shift 되어, 차트 상의
    "현재 봉 구름값" = "(displacement - 1)봉 전에 계산된 lead 값" 이 됨.

    Args:
        ohlc: 'high', 'low' 컬럼이 있는 DataFrame.
        conversion_period: Conversion Line 기간 (기본 9).
        base_period: Base Line 기간 (기본 26).
        span_b_period: Leading Span B 기간 (기본 52).
        displacement: forward shift 양 (기본 26 → 실제 shift = 25봉).

    Returns:
        DataFrame with columns: ['span_a', 'span_b', 'cloud_upper', 'cloud_lower'].
        ``cloud_upper`` = max(span_a, span_b),
        ``cloud_lower`` = min(span_a, span_b).
        초기 ``span_b_period + displacement - 2`` 봉은 NaN.

    Raises:
        ValueError: 컬럼 누락 또는 파라미터 잘못된 값일 때.
    """
    required = {"high", "low"}
    if not required.issubset(ohlc.columns):
        raise ValueError(
            f"ohlc 에 'high','low' 컬럼 필요 (받은: {list(ohlc.columns)})"
        )
    if conversion_period < 1 or base_period < 1 or span_b_period < 1:
        raise ValueError(
            f"기간들은 1 이상 (받은: conversion={conversion_period}, "
            f"base={base_period}, span_b={span_b_period})"
        )
    if displacement < 1:
        raise ValueError(f"displacement 는 1 이상 (받은: {displacement})")

    high = ohlc["high"]
    low = ohlc["low"]

    def _donchian(length: int) -> pd.Series:
        return (
            high.rolling(window=length, min_periods=length).max()
            + low.rolling(window=length, min_periods=length).min()
        ) / 2.0

    tenkan = _donchian(conversion_period)
    kijun = _donchian(base_period)

    lead_a = (tenkan + kijun) / 2.0
    lead_b = _donchian(span_b_period)

    shift = displacement - 1  # 트뷰 표준: 25봉 forward
    span_a = lead_a.shift(shift)
    span_b = lead_b.shift(shift)

    pair = pd.concat([span_a, span_b], axis=1)
    cloud_upper = pair.max(axis=1)
    cloud_lower = pair.min(axis=1)

    return pd.DataFrame(
        {
            "span_a": span_a,
            "span_b": span_b,
            "cloud_upper": cloud_upper,
            "cloud_lower": cloud_lower,
        },
        index=ohlc.index,
    )
