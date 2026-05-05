"""전략 룰 — EMA 터치 진입, RSI Div 단독 진입, Selectable OR 조합.

담당: 장수
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import pandas as pd

from aurora.core.indicators import (
    HarmonicMatch,
    atr_wilder,
    bollinger_bands,
    ema,
    harmonic_pattern,
    ichimoku_cloud,
    ma_cross,
    rsi,
    rsi_divergence,
    volume_confirmation,
)

# ============================================================
# 공통 dataclass / enum
# ============================================================


class Direction(StrEnum):
    LONG = "long"
    SHORT = "short"


@dataclass(slots=True)
class EntrySignal:
    """진입 신호 (지표 1개 × TF 1개 산출 단위).

    식별 필드 (``bar_timestamp`` / ``pattern_id``) 는 신호 dedup 및 재진입
    방지용. 호출자(position management 또는 봇 메인루프) 가 같은 식별자의
    신호를 중복 합산 / 재진입하지 않도록 사용한다.
    """

    direction: Direction
    timeframe: str  # "15m", "1H", "4H", etc.
    source: str  # "ema_touch_200", "rsi_div", "bb", etc.
    strength: float = 1.0  # 0.0 ~ 1.0
    note: str = ""

    # ─── 식별 필드 (M2/M3 dedup용) ──────────────────────────
    bar_timestamp: pd.Timestamp | None = None
    """신호 발생 봉의 timestamp. 같은 봉에서 같은 source 가 두 번
    emit 되면 호출자가 dedup 해야 한다.
    DataFrame 인덱스가 DatetimeIndex 일 때만 채워짐 (그 외 None)."""

    pattern_id: str | None = None
    """패턴 단위 식별자 (Harmonic 등). 예: ``"bat@d_bar=50"``.
    같은 패턴이 여러 봉에 걸쳐 검출될 때 재진입 방지용."""

    meta: dict[str, float] | None = None
    """신호별 부가 정보 (v0.1.42). BB 신호의 경우 ``{"bb_upper": x,
    "bb_lower": y}`` 형태로 진입 시점 BB 값 박음. ``build_risk_plan`` 이
    BB Structural SL 산출에 사용. 다른 신호는 None."""


def _last_bar_timestamp(df: pd.DataFrame) -> pd.Timestamp | None:
    """DataFrame 의 마지막 봉 timestamp (DatetimeIndex 일 때만, 그 외 None)."""
    if df is None or df.empty:
        return None
    if isinstance(df.index, pd.DatetimeIndex):
        return df.index[-1]
    return None


@dataclass(slots=True)
class StrategyConfig:
    """사용자 설정 — Selectable 지표 on/off 및 파라미터."""

    # Selectable on/off
    use_bollinger: bool = False
    use_ma_cross: bool = False
    use_harmonic: bool = False
    use_ichimoku: bool = False

    # 진입 파라미터
    ema_touch_tolerance: float = 0.003  # ±0.3% — 노이즈는 통과시키고 의미있는 터치만 잡는 출발값
    ema_periods: tuple[int, ...] = (200, 480)  # 200=중기 추세, 480=장기 추세 (EMA 보스 자료 기반)
    rsi_period: int = 14                 # Wilder 1978 표준
    rsi_div_lb_left: int = 5             # 트뷰 RSI Divergence 표준 lookback (좌)
    rsi_div_lb_right: int = 5            # 트뷰 RSI Divergence 표준 lookback (우)

    # 거래량 컨펌 (EMA 한 세트, 단독 신호 X — strength 부스트만)
    volume_period: int = 20
    """거래량 평균 계산 기간 (SMA)."""
    volume_multiplier: float = 1.5
    """평균 대비 배율 임계 (사용자 결정 = 1.5배)."""
    volume_boost: float = 1.5
    """거래량 동반 시 ``EntrySignal.strength`` 곱셈 (기본 1.5)."""

    # Bollinger Bands (Selectable — 1H 고정)
    bollinger_period: int = 20           # John Bollinger 1980 표준 (20 SMA)
    bollinger_std: float = 2.0           # ±2σ — 표준 정규분포 95% 구간
    bollinger_breakout_buffer_pct: float = 0.003
    """BB 이탈/회귀 buffer (v0.1.42). BB 라인 ± buffer 가 진입 + SL 라인 동시.
    기본 0.003 = 0.3%. 호가 noise (~0.08%) 위로 진입 안정성 확보."""
    bollinger_squeeze_threshold: float = 0.015
    """폭(upper-lower) / middle 이 이 값 이하면 squeeze 상태로 진입 보류 (기본 0.015 = 1.5%)."""

    # MA Cross (Selectable — 1H/2H/4H 멀티 TF, HTF 가중치 자동 적용)
    ma_cross_fast: int = 50
    ma_cross_slow: int = 200
    """SMA 기간. 골크/데크 표준은 50/200."""

    # Ichimoku Cloud (Selectable — 1H/2H/4H 멀티 TF, HTF 가중치 자동 적용)
    ichimoku_conversion_period: int = 9
    """Tenkan (Conversion Line) 기간. Span A 계산용 내부값."""
    ichimoku_base_period: int = 26
    """Kijun (Base Line) 기간. Span A 계산용 내부값."""
    ichimoku_span_b_period: int = 52
    """Leading Span B donchian 기간."""
    ichimoku_displacement: int = 26
    """forward shift 양 (트뷰 표준 26 → 실제 shift = 25봉)."""
    ichimoku_breakout_buffer_pct: float = 0.006
    """Ichimoku 구름 이탈 buffer (v0.1.44). 진입 시점 cloud 라인 ± buffer 가
    SL 라인. 기본 0.006 = 0.6% (BB 0.3% 보다 2배 여유 — Ichimoku 는 멀티 TF
    추세 지표라 wick 흡수 폭 더 필요. 사용자 결정)."""

    # Harmonic Pattern (Selectable — 15m/1H 멀티 TF, HTF 가중치 자동 적용)
    harmonic_pivot_length: int = 10
    """피벗 검출 lookback 봉수 (트뷰 ``pivots_f`` length)."""
    harmonic_tolerance: float = 0.10
    """비율 검증 허용 오차 (기본 ±10%)."""

    # 2,4,6,8 타점 매매 (BTC 전용, 가격 기반, 항상 ON — Selectable 아님)
    use_2468: bool = True
    """2468 룰 활성화 (디폴트 True). 테스트/실험용 flag — 일반 사용자 GUI 노출 X."""
    k_unit: float = 1000.0
    """가격 단위 (BTC = $1000 = 1K). 90K → 91K 같은 심리적 단위."""
    zone_lower_min: float = 200.0
    """상방 추세 저항 zone 시작 (= N.200)."""
    zone_lower_max: float = 400.0
    """상방 추세 저항 zone 끝 (= N.400)."""
    zone_upper_min: float = 600.0
    """하방 추세 지지 zone 시작 (= N.600)."""
    zone_upper_max: float = 800.0
    """하방 추세 지지 zone 끝 (= N.800)."""
    zone_sl_buffer: float = 1000.0
    """SL = zone 너머 ``zone_sl_buffer`` 이상 이탈 (기본 1K = 1000 USD)."""


# ============================================================
# Fixed: EMA 터치 진입
# ============================================================

# EMA 터치 진입에 사용할 TF 목록 (project_indicator_spec.md 기준)
EMA_TIMEFRAMES: tuple[str, ...] = ("15m", "1H", "2H", "4H", "6H", "12H", "1D", "1W")

# EMA 480 (장기 EMA) 안정 warmup 데이터 절대량 부족 TF — 메모리 spec 명시.
# 1W EMA 480 안정 warmup 위해 ~25년치 (5×period 주) 필요한데 BTC 자체가 ~10년치 한계
# → 1W 는 EMA 200 만 적용. 거래소 차트도 1W 에선 480 미표시 (사용자 확인 2026-05).
EMA_LONG_EXCLUDED_TFS: tuple[str, ...] = ("1W",)
EMA_LONG_THRESHOLD: int = 480
"""``EMA_LONG_EXCLUDED_TFS`` 의 TF에서 이 값 이상의 EMA period 제외."""


def detect_ema_touch(
    df_by_tf: dict[str, pd.DataFrame],
    config: StrategyConfig,
) -> list[EntrySignal]:
    """EMA 200/480 터치 감지 (멀티 TF).

    각 TF × 각 EMA 기간마다 가격이 EMA 라인 ± ``ema_touch_tolerance`` 이내면 신호:
        - 종가 ≥ EMA (위에서) → ``"long"`` (지지 가능)
        - 종가 < EMA (아래서) → ``"short"`` (저항 가능)

    HTF 가중치는 ``signal.compose_entry`` 가 처리하므로 여기선 단순 신호만 산출.

    Args:
        df_by_tf: {"15m": DataFrame, "1H": ..., ...}. 누락 TF 있어도 OK.
        config: 전략 설정 (ema_periods, ema_touch_tolerance).

    Returns:
        ``EntrySignal`` 리스트. 신호 없으면 빈 리스트.
    """
    signals: list[EntrySignal] = []

    for tf, df in df_by_tf.items():
        if df is None or df.empty or "close" not in df.columns:
            continue
        last_close = float(df["close"].iloc[-1])
        if pd.isna(last_close) or last_close <= 0:
            continue

        ts = _last_bar_timestamp(df)

        # 거래량 컨펌 (EMA 한 세트, 단독 신호 X — strength 부스트용)
        # 자료 인용: "거래량 동반 = 진짜 의미 있는 상승" → strength 부스트.
        # volume 컬럼 없거나 데이터 짧으면 컨펌 X (기본 신호만 emit).
        vol_confirmed = False
        if "volume" in df.columns and len(df) >= config.volume_period:
            try:
                vol_series = volume_confirmation(
                    df["volume"],
                    period=config.volume_period,
                    multiplier=config.volume_multiplier,
                )
                vol_confirmed = bool(vol_series.iloc[-1])
            except ValueError:
                vol_confirmed = False

        for period in config.ema_periods:
            # 1W 등 데이터 절대량 부족 TF 에선 EMA 480 같은 장기 EMA 제외
            # (메모리 spec: 1W 는 EMA 200 만 사용 — Binance 차트도 1W 480 미표시)
            if period >= EMA_LONG_THRESHOLD and tf in EMA_LONG_EXCLUDED_TFS:
                continue

            ema_series = ema(df["close"], period)
            ema_val = ema_series.iloc[-1]
            if pd.isna(ema_val) or ema_val <= 0:
                continue

            distance = abs(last_close - ema_val) / ema_val
            if distance > config.ema_touch_tolerance:
                continue  # 터치 거리 초과 → 신호 X

            # 종가 위치로 지지/저항 판단
            if last_close >= ema_val:
                direction = Direction.LONG
                note = f"EMA{period} 지지 (close ≥ EMA, 거리 {distance:.4f})"
            else:
                direction = Direction.SHORT
                note = f"EMA{period} 저항 (close < EMA, 거리 {distance:.4f})"

            # 거래량 동반 시 공격적 진입 = strength 부스트
            strength = config.volume_boost if vol_confirmed else 1.0
            if vol_confirmed:
                note += f" + 거래량 동반(×{config.volume_multiplier})"

            signals.append(
                EntrySignal(
                    direction=direction,
                    timeframe=tf,
                    source=f"ema_touch_{period}",
                    strength=strength,
                    note=note,
                    bar_timestamp=ts,
                )
            )

    return signals


# ============================================================
# Fixed: RSI Divergence 진입
# ============================================================


def detect_rsi_divergence(
    df_1h: pd.DataFrame,
    config: StrategyConfig,
) -> list[EntrySignal]:
    """1H 차트 RSI 다이버전스 진입.

    4 가지 종류 (regular_bull/bear, hidden_bull/bear) 검출 → 각각 신호로 변환:
        - regular_bull / hidden_bull → 롱
        - regular_bear / hidden_bear → 숏

    검출은 마지막 봉 기준만 반환 (지속 신호는 strategy 레벨에서 중복 진입 방지 필요).

    Args:
        df_1h: 1H OHLC DataFrame (open/high/low/close).
        config: 전략 설정 (rsi_period, rsi_div_lb_*).

    Returns:
        ``EntrySignal`` 리스트 (마지막 봉에 검출된 다이버전스).
    """
    if df_1h is None or df_1h.empty:
        return []
    required = {"close", "low", "high"}
    if not required.issubset(df_1h.columns):
        return []

    rsi_series = rsi(df_1h["close"], period=config.rsi_period)
    div = rsi_divergence(
        df_1h["low"],
        df_1h["high"],
        rsi_series,
        lb_left=config.rsi_div_lb_left,
        lb_right=config.rsi_div_lb_right,
    )

    last = div.iloc[-1]
    if last is None or (isinstance(last, float) and pd.isna(last)):
        return []

    if last in ("regular_bull", "hidden_bull"):
        direction = Direction.LONG
    elif last in ("regular_bear", "hidden_bear"):
        direction = Direction.SHORT
    else:
        return []

    return [
        EntrySignal(
            direction=direction,
            timeframe="1H",
            source=f"rsi_div_{last}",
            strength=1.0,
            note=f"RSI {last}",
            bar_timestamp=_last_bar_timestamp(df_1h),
        )
    ]


# ============================================================
# Selectable: Bollinger Bands (1H 고정, 횡보장 특화)
# ============================================================


def detect_bollinger_touch(
    df_1h: pd.DataFrame,
    config: StrategyConfig,
) -> list[EntrySignal]:
    """볼린저 밴드 buffered reversal 진입 룰 (1H 고정, v0.1.42 재설계).

    v0.1.42 변경: ``proximity`` 진입 폐기 + buffer 도입.
    Reversal/Wick reversal 만 신호. 진입 + SL 라인 = ``BB ± buffer`` 동일.
    Why: proximity 진입은 ``last_high`` 누적 max 기반이라 봉 안 wick 한 번
    닿으면 봉 닫힐 때까지 신호 stateful ON → 무한 재진입 사이클 발생
    (사용자 보고 v0.1.41 사고팔고 무한 루프). 또 진입 + SL 라인이 동일
    buffer (BB ± 0.3%) 라야 호가 noise (~0.08%) 위로 안정.

    우선순위 순:
        1. **Squeeze 보류**: 폭 / middle ≤ ``squeeze_threshold`` → []
        2. **Reversal 진입** (강도 1.5): 직전 봉 종가가 BB ± buffer 밖 +
           현재 봉 종가 BB ± buffer 안쪽 → SHORT (위 회귀) / LONG (아래 회귀)
        3. **Wick reversal** (강도 1.5, v0.1.28 신규): 현재 봉 안에서 wick 만
           BB ± buffer 밖 + close 안쪽 → SHORT/LONG.
        4. **나머지**: 진입 신호 X (proximity 폐기됨).

    "양방향 진입+청산": BB 신호 = 진입 신호 + 반대 포지션 청산 신호.

    Args:
        df_1h: 1H OHLC DataFrame (close/high/low 컬럼).
        config: 전략 설정 (bollinger_period/std/breakout_buffer_pct/squeeze).

    Returns:
        ``EntrySignal`` 리스트 (마지막 봉 기준). BB 신호엔 ``meta`` 박힘
        (bb_upper / bb_lower / buffer_pct).
    """
    if df_1h is None or df_1h.empty:
        return []
    required = {"close", "high", "low"}
    if not required.issubset(df_1h.columns):
        return []

    bb = bollinger_bands(df_1h["close"], period=config.bollinger_period, std=config.bollinger_std)
    if len(bb) < 2:
        return []

    last_close = float(df_1h["close"].iloc[-1])
    last_high = float(df_1h["high"].iloc[-1])
    last_low = float(df_1h["low"].iloc[-1])
    last_upper = bb["upper"].iloc[-1]
    last_lower = bb["lower"].iloc[-1]
    last_middle = bb["middle"].iloc[-1]

    if pd.isna(last_upper) or pd.isna(last_lower) or pd.isna(last_middle):
        return []

    last_upper_f = float(last_upper)
    last_lower_f = float(last_lower)
    last_middle_f = float(last_middle)
    ts = _last_bar_timestamp(df_1h)

    # ─── 1. Squeeze 보류 ──────────────────────────────────
    if last_middle_f > 0:
        narrowness = (last_upper_f - last_lower_f) / last_middle_f
        if narrowness <= config.bollinger_squeeze_threshold:
            return []

    # ─── buffer 적용 라인 (진입 + SL 동일 라인, v0.1.42) ───
    buffer = config.bollinger_breakout_buffer_pct
    upper_threshold = last_upper_f * (1.0 + buffer)
    lower_threshold = last_lower_f * (1.0 - buffer)

    # BB 메타 — 진입 시점 BB 값. build_risk_plan 이 SL 산출에 사용.
    bb_meta = {
        "bb_upper": last_upper_f,
        "bb_lower": last_lower_f,
        "bb_middle": last_middle_f,
        "buffer_pct": buffer,
    }

    # ─── 2. Reversal 진입 (직전 봉 buffer 밖 → 현재 봉 buffer 안 회귀) ───
    prev_close = float(df_1h["close"].iloc[-2])
    prev_upper = bb["upper"].iloc[-2]
    prev_lower = bb["lower"].iloc[-2]

    if not pd.isna(prev_upper) and not pd.isna(prev_lower):
        prev_upper_f = float(prev_upper)
        prev_lower_f = float(prev_lower)
        prev_upper_thr = prev_upper_f * (1.0 + buffer)
        prev_lower_thr = prev_lower_f * (1.0 - buffer)
        # 위로 buffer 이탈 → buffer 안 회귀
        if prev_close > prev_upper_thr and last_close <= upper_threshold:
            return [EntrySignal(
                direction=Direction.SHORT,
                timeframe="1H",
                source="bollinger_reversal_upper",
                strength=1.5,
                note=f"BB 상단 buffer 이탈 회귀 (prev={prev_close:.4f}>{prev_upper_thr:.4f}, "
                     f"last={last_close:.4f}≤{upper_threshold:.4f})",
                bar_timestamp=ts,
                meta=bb_meta,
            )]
        # 아래로 buffer 이탈 → buffer 안 회귀
        if prev_close < prev_lower_thr and last_close >= lower_threshold:
            return [EntrySignal(
                direction=Direction.LONG,
                timeframe="1H",
                source="bollinger_reversal_lower",
                strength=1.5,
                note=f"BB 하단 buffer 이탈 회귀 (prev={prev_close:.4f}<{prev_lower_thr:.4f}, "
                     f"last={last_close:.4f}≥{lower_threshold:.4f})",
                bar_timestamp=ts,
                meta=bb_meta,
            )]

    # ─── 3. 단일 봉 wick reversal (v0.1.28 + v0.1.42 buffer 적용) ────
    # 현재 봉 안에서 wick 만 buffer 밖 (high > upper_thr or low < lower_thr)
    # + close 가 buffer 안쪽 → 단일 봉 reversion candle 자체.
    if last_high > upper_threshold and last_close <= upper_threshold:
        return [EntrySignal(
            direction=Direction.SHORT,
            timeframe="1H",
            source="bollinger_wick_reversal_upper",
            strength=1.5,
            note=f"BB 상단 wick reversal (high={last_high:.4f}>{upper_threshold:.4f}, "
                 f"close={last_close:.4f}≤{upper_threshold:.4f})",
            bar_timestamp=ts,
            meta=bb_meta,
        )]
    if last_low < lower_threshold and last_close >= lower_threshold:
        return [EntrySignal(
            direction=Direction.LONG,
            timeframe="1H",
            source="bollinger_wick_reversal_lower",
            strength=1.5,
            note=f"BB 하단 wick reversal (low={last_low:.4f}<{lower_threshold:.4f}, "
                 f"close={last_close:.4f}≥{lower_threshold:.4f})",
            bar_timestamp=ts,
            meta=bb_meta,
        )]

    # v0.1.42: Proximity 터치 진입 폐기 (last_high 누적 max trap →
    # 사고팔고 무한 사이클). Reversal + Wick reversal 만 신호.
    return []


# ============================================================
# Selectable: MA Cross (1H/2H/4H, HTF 가중치 자동 적용)
# ============================================================

MA_CROSS_TIMEFRAMES: tuple[str, ...] = ("1H", "2H", "4H")


def detect_ma_cross(
    df_by_tf: dict[str, pd.DataFrame],
    config: StrategyConfig,
) -> list[EntrySignal]:
    """이평선 골든/데드 크로스 진입 (멀티 TF).

    각 TF 의 마지막 봉에서 막 발생한 cross 만 신호:
        - golden → LONG  (빠른 MA가 느린 MA 위로 돌파)
        - dead   → SHORT (빠른 MA가 느린 MA 아래로 돌파)

    HTF 가중치 (1H=2, 2H=3, 4H=5) 는 ``signal.compose_entry`` 가 자동 적용.

    Args:
        df_by_tf: TF 별 OHLC DataFrame.
        config: 전략 설정 (ma_cross_fast, ma_cross_slow).

    Returns:
        ``EntrySignal`` 리스트 (TF 별로 0~1 개).
    """
    signals: list[EntrySignal] = []

    for tf in MA_CROSS_TIMEFRAMES:
        df = df_by_tf.get(tf)
        if df is None or df.empty or "close" not in df.columns:
            continue

        cross = ma_cross(df["close"], fast=config.ma_cross_fast, slow=config.ma_cross_slow)
        last = cross.iloc[-1]
        ts = _last_bar_timestamp(df)
        if last == "golden":
            signals.append(EntrySignal(
                direction=Direction.LONG,
                timeframe=tf,
                source="ma_cross_golden",
                strength=1.0,
                note=f"SMA{config.ma_cross_fast}/{config.ma_cross_slow} 골든크로스 ({tf})",
                bar_timestamp=ts,
            ))
        elif last == "dead":
            signals.append(EntrySignal(
                direction=Direction.SHORT,
                timeframe=tf,
                source="ma_cross_dead",
                strength=1.0,
                note=f"SMA{config.ma_cross_fast}/{config.ma_cross_slow} 데드크로스 ({tf})",
                bar_timestamp=ts,
            ))

    return signals


# ============================================================
# Selectable: Ichimoku Cloud (1H/2H/4H, HTF 가중치 자동 적용)
# ============================================================

ICHIMOKU_TIMEFRAMES: tuple[str, ...] = ("1H", "2H", "4H")


def detect_ichimoku_signal(
    df_by_tf: dict[str, pd.DataFrame],
    config: StrategyConfig,
) -> list[EntrySignal]:
    """이치모쿠 구름대 진입 — 스팬 터치 (멀티 TF, HTF 가중치 자동 적용).

    진입 룰 (마지막 봉 기준):
        - 가격이 **구름 위**에서 상단 스팬(=cloud_upper) 터치 → LONG (지지)
        - 가격이 **구름 아래**에서 하단 스팬(=cloud_lower) 터치 → SHORT (저항)
        - 가격이 **구름 안** → 무신호

    터치 판정:
        long  = (last_close > cloud_upper) and (last_low  <= cloud_upper)
        short = (last_close < cloud_lower) and (last_high >= cloud_lower)

    HTF 가중치 (1H=2, 2H=3, 4H=5) 는 ``signal.compose_entry`` 가 자동 적용.

    Args:
        df_by_tf: TF 별 OHLC DataFrame (high/low/close 컬럼).
        config: 전략 설정 (ichimoku_* 파라미터).

    Returns:
        ``EntrySignal`` 리스트 (TF 별로 0~1 개).
    """
    signals: list[EntrySignal] = []

    for tf in ICHIMOKU_TIMEFRAMES:
        df = df_by_tf.get(tf)
        if df is None or df.empty:
            continue
        if not {"high", "low", "close"}.issubset(df.columns):
            continue

        try:
            cloud = ichimoku_cloud(
                df,
                conversion_period=config.ichimoku_conversion_period,
                base_period=config.ichimoku_base_period,
                span_b_period=config.ichimoku_span_b_period,
                displacement=config.ichimoku_displacement,
            )
        except ValueError:
            continue

        last_upper = cloud["cloud_upper"].iloc[-1]
        last_lower = cloud["cloud_lower"].iloc[-1]
        if pd.isna(last_upper) or pd.isna(last_lower):
            continue

        last_close = float(df["close"].iloc[-1])
        last_high = float(df["high"].iloc[-1])
        last_low = float(df["low"].iloc[-1])
        upper_f = float(last_upper)
        lower_f = float(last_lower)
        ts = _last_bar_timestamp(df)

        # v0.1.44: Ichimoku Structural SL meta — 진입 시점 cloud 값 + buffer 박음.
        # build_risk_plan 이 SL = cloud_upper × (1 - buffer) (LONG) /
        # cloud_lower × (1 + buffer) (SHORT) 로 override.
        ichimoku_buffer = config.ichimoku_breakout_buffer_pct

        # 가격이 구름 위 + 상단 스팬 터치 → LONG
        if last_close > upper_f and last_low <= upper_f:
            signals.append(EntrySignal(
                direction=Direction.LONG,
                timeframe=tf,
                source="ichimoku_cloud_upper",
                strength=1.0,
                note=f"이치모쿠 구름 상단 지지 ({tf}, "
                     f"low={last_low:.4f}≤upper={upper_f:.4f}<close={last_close:.4f})",
                bar_timestamp=ts,
                meta={
                    "cloud_upper": upper_f,
                    "cloud_lower": lower_f,
                    "buffer_pct": ichimoku_buffer,
                    "sl_price": upper_f * (1.0 - ichimoku_buffer),
                },
            ))
        # 가격이 구름 아래 + 하단 스팬 터치 → SHORT
        elif last_close < lower_f and last_high >= lower_f:
            signals.append(EntrySignal(
                direction=Direction.SHORT,
                timeframe=tf,
                source="ichimoku_cloud_lower",
                strength=1.0,
                note=f"이치모쿠 구름 하단 저항 ({tf}, "
                     f"close={last_close:.4f}<lower={lower_f:.4f}≤high={last_high:.4f})",
                bar_timestamp=ts,
                meta={
                    "cloud_upper": upper_f,
                    "cloud_lower": lower_f,
                    "buffer_pct": ichimoku_buffer,
                    "sl_price": lower_f * (1.0 + ichimoku_buffer),
                },
            ))

    return signals


def detect_ichimoku_exit(
    df: pd.DataFrame,
    position_direction: Direction,
    config: StrategyConfig,
) -> bool:
    """이치모쿠 청산 — 진입한 구름 면 종가 이탈 시 True.

    청산 룰 (마지막 봉 종가 기준):
        - 롱 보유 중: ``close < cloud_upper`` → True (구름 안/아래로 마감 = 손절)
        - 숏 보유 중: ``close > cloud_lower`` → True (구름 안/위로 마감 = 손절)

    Note:
        이 함수는 "구름대 이탈 마감" 트리거만 검출. 레버리지 SL 캡
        (``risk.sl_pct_for_leverage``) 은 상위 청산 레이어에서 별도 적용한다.
        우선순위: SL 캡 > 구름대 이탈 (캡 도달 시 즉시 손절, 캡 미도달이면 이 함수 평가).

    Args:
        df: 단일 TF OHLC DataFrame (high/low/close 컬럼).
        position_direction: 현재 포지션 방향.
        config: 전략 설정 (ichimoku_* 파라미터).

    Returns:
        구름대 이탈 마감이 발생했으면 True.
    """
    if df is None or df.empty:
        return False
    if not {"high", "low", "close"}.issubset(df.columns):
        return False

    try:
        cloud = ichimoku_cloud(
            df,
            conversion_period=config.ichimoku_conversion_period,
            base_period=config.ichimoku_base_period,
            span_b_period=config.ichimoku_span_b_period,
            displacement=config.ichimoku_displacement,
        )
    except ValueError:
        return False

    last_close = df["close"].iloc[-1]
    last_upper = cloud["cloud_upper"].iloc[-1]
    last_lower = cloud["cloud_lower"].iloc[-1]
    if pd.isna(last_close) or pd.isna(last_upper) or pd.isna(last_lower):
        return False

    last_close_f = float(last_close)
    if position_direction == Direction.LONG:
        return last_close_f < float(last_upper)
    if position_direction == Direction.SHORT:
        return last_close_f > float(last_lower)
    return False


# ============================================================
# Selectable: Harmonic Pattern (15m/1H, HTF 가중치 자동 적용)
# ============================================================

HARMONIC_TIMEFRAMES: tuple[str, ...] = ("15m", "1H")


def detect_harmonic_signal(
    df_by_tf: dict[str, pd.DataFrame],
    config: StrategyConfig,
) -> list[EntrySignal]:
    """하모닉 패턴 진입 — D 비율 도달 시 (15m/1H 멀티 TF, HTF 가중치 자동 적용).

    PDF '할머니의하모닉' 5개 패턴 풀 검증 (B/C/D point + BC projection + AB=CD):
        - **Bat**, **Butterfly**, **Gartley**, **Crab**, **Deep Crab**

    진입 룰:
        - bullish XABCD (X<A) 패턴 검출 → **LONG** 진입 at D
        - bearish XABCD (X>A) 패턴 검출 → **SHORT** 진입 at D

    HTF 가중치 (15m=1, 1H=2) 는 ``signal.compose_entry`` 가 자동 적용:
        예) 15m Bat + 1H Crab 동시 발현 → 1H 신호가 2배 우선.

    Args:
        df_by_tf: TF 별 OHLC DataFrame.
        config: 전략 설정 (harmonic_pivot_length, harmonic_tolerance).

    Returns:
        ``EntrySignal`` 리스트 (TF 별로 0~1 개).
    """
    signals: list[EntrySignal] = []

    for tf in HARMONIC_TIMEFRAMES:
        df = df_by_tf.get(tf)
        if df is None or df.empty:
            continue
        if not {"high", "low"}.issubset(df.columns):
            continue

        try:
            match = harmonic_pattern(
                df,
                pivot_length=config.harmonic_pivot_length,
                tolerance=config.harmonic_tolerance,
            )
        except ValueError:
            continue

        if match is None:
            continue

        direction = Direction.LONG if match.direction == "long" else Direction.SHORT
        signals.append(EntrySignal(
            direction=direction,
            timeframe=tf,
            source=f"harmonic_{match.name}",
            strength=1.0,
            note=(
                f"{match.name} ({tf}, "
                f"XAB={match.xab:.3f}, ABC={match.abc:.3f}, "
                f"BCD={match.bcd:.3f}, XAD={match.xad:.3f}, "
                f"SL={match.sl_price:.4f}, TP1={match.tp1_price:.4f}, "
                f"TP2={match.tp2_price:.4f})"
            ),
            bar_timestamp=_last_bar_timestamp(df),
            # 재진입 방지용 식별자: 같은 D 봉의 같은 패턴이면 같은 id
            pattern_id=f"{match.name}@{tf}@d_bar={match.d_bar}",
        ))

    return signals


def detect_harmonic_exit(
    position_direction: Direction,
    last_price: float,
    match: HarmonicMatch,
) -> str | None:
    """하모닉 청산 — 패턴별 자체 SL/TP 도달 검출.

    PDF 룰 (레버리지 SL 캡 미적용 — 사용자 명시):
        - **SL**: 가격이 패턴별 SL (X 방향 연장) 도달 → 'sl'
        - **TP1**: 가격이 0.382 AD 도달 → 'tp1' (반익절 권장)
        - **TP2**: 가격이 0.618 AD 도달 → 'tp2' (전체 청산)

    우선순위: SL > TP2 > TP1 (가까운 게 먼저 도달했으므로 보수적으로 SL 우선 검사).

    Args:
        position_direction: 현재 포지션 방향.
        last_price: 마지막 봉 종가 (또는 실시간 가격).
        match: 진입 시 사용된 ``HarmonicMatch``.

    Returns:
        'sl' / 'tp1' / 'tp2' / None (어느 레벨도 도달 X).
    """
    if position_direction == Direction.LONG:
        # 롱: SL 은 D 보다 아래 (X 방향)
        if last_price <= match.sl_price:
            return "sl"
        if last_price >= match.tp2_price:
            return "tp2"
        if last_price >= match.tp1_price:
            return "tp1"
    elif position_direction == Direction.SHORT:
        # 숏: SL 은 D 보다 위 (X 방향)
        if last_price >= match.sl_price:
            return "sl"
        if last_price <= match.tp2_price:
            return "tp2"
        if last_price <= match.tp1_price:
            return "tp1"
    return None


# ============================================================
# 2,4,6,8 타점 매매 (BTC 전용, 가격 기반, 항상 ON)
# ============================================================
#
# 자료: PDF "나뇨띠 — 2,4,6,8 타점 매매".
#   - "2,4,6,8" = 200/400/600/800 (1K 단위 내 위치)
#   - **상방 추세** → N.200~N.400 = 1차 저항 zone (SHORT 진입)
#   - **하방 추세** → N.600~N.800 = 1차 지지 zone (LONG 진입)
#   - 추세 판단: MA Cross 상태 (사용자 결정 = B)
#   - SL: zone 너머 ``zone_sl_buffer`` (= 1K) 이상 이탈
#   - **TF 무관 — 가격 기반 작동** (사용자 명시).
#     호출자가 어떤 TF df 줘도 마지막 봉 high/low 로 판정.
#     관례적으로 가장 빠른 TF (15m → 1H → ...) 우선 사용.


# 가장 빠른 TF 부터 우선순위 (가격 기반이라 빠른 TF 가 가격 변동 빠르게 반영)
_2468_TF_PREFERENCE: tuple[str, ...] = ("15m", "1H", "2H", "4H", "6H", "12H", "1D", "1W")


def _select_2468_df(df_by_tf: dict[str, pd.DataFrame]) -> tuple[str, pd.DataFrame] | None:
    """``df_by_tf`` 에서 2468 판정에 사용할 (TF, df) 한 쌍 선택.

    가장 빠른 TF 부터 시도해 ``high/low/close`` 컬럼이 있고 비어있지 않은
    첫 데이터를 반환. 모두 부적합하면 None.
    """
    for tf in _2468_TF_PREFERENCE:
        df = df_by_tf.get(tf)
        if df is None or df.empty:
            continue
        if not {"high", "low", "close"}.issubset(df.columns):
            continue
        return tf, df
    return None


def _detect_ma_trend(df: pd.DataFrame, fast: int, slow: int) -> str | None:
    """MA Cross 상태 기반 현재 추세 — 가장 최근 cross 의 방향.

    Returns:
        ``"up"`` (가장 최근 cross = golden) / ``"down"`` (dead) / ``None`` (아직 cross 없음).
    """
    if "close" not in df.columns or len(df) < slow:
        return None
    cross = ma_cross(df["close"], fast=fast, slow=slow).dropna()
    if cross.empty:
        return None
    last = cross.iloc[-1]
    if last == "golden":
        return "up"
    if last == "dead":
        return "down"
    return None


def detect_2468_signal(
    df_by_tf: dict[str, pd.DataFrame],
    config: StrategyConfig,
    symbol: str = "BTC/USDT",
) -> list[EntrySignal]:
    """2,4,6,8 타점 매매 — 가격 기반, BTC 전용, 항상 ON.

    PDF 룰 (사용자 결정 반영):
        - **상방 추세** (MA Cross golden) → 가격이 N.200~N.400 zone 터치 → SHORT
        - **하방 추세** (MA Cross dead) → 가격이 N.600~N.800 zone 터치 → LONG
        - 가격대 = ``close // k_unit × k_unit`` (예: 91234 → 91000 K 봉)
        - zone 진입 판정: 마지막 봉 ``high/low`` 가 zone 안에 닿음 (사용자 결정 a)

    BTC 전용:
        ``symbol`` 이 ``"BTC"`` 로 시작하지 않으면 빈 리스트 반환.

    TF 무관 (가격 기반):
        ``df_by_tf`` 중 가장 빠른 TF (15m → 1H → ...) 자동 선택.
        signal.timeframe 은 선택된 TF, strength 1.0 (HTF 가중치 자연 적용).

    SL:
        EntrySignal 자체엔 SL 가격 미포함 — 호출자(상위 청산 레이어)가
        ``config.zone_sl_buffer`` 와 ``note`` 정보로 판정.
        예: SHORT @ 91.2~91.4 → SL = 91.4 + 1000 = 92.4.

    Args:
        df_by_tf: TF 별 OHLC DataFrame.
        config: 전략 설정 (k_unit, zone_*, ma_cross_*).
        symbol: 거래 페어 (예: "BTC/USDT"). BTC 가 아니면 무시.

    Returns:
        ``EntrySignal`` 리스트 (0 또는 1 개).
    """
    if not config.use_2468:
        return []
    if not symbol.upper().startswith("BTC"):
        return []  # PDF 자체가 BTC 1K 단위 심리 기반

    selected = _select_2468_df(df_by_tf)
    if selected is None:
        return []
    tf, df = selected

    # 추세 판단 (MA Cross 상태)
    trend = _detect_ma_trend(df, fast=config.ma_cross_fast, slow=config.ma_cross_slow)
    if trend is None:
        return []  # 추세 미확정 → 진입 보류

    last_close = float(df["close"].iloc[-1])
    last_high = float(df["high"].iloc[-1])
    last_low = float(df["low"].iloc[-1])
    if any(pd.isna(v) for v in (last_close, last_high, last_low)):
        return []

    # 1K 단위 정규화 — 현재 봉이 속한 N.000 가격대
    k_floor = (last_close // config.k_unit) * config.k_unit
    # zone 절대 가격
    short_zone_lo = k_floor + config.zone_lower_min  # N.200
    short_zone_hi = k_floor + config.zone_lower_max  # N.400
    long_zone_lo = k_floor + config.zone_upper_min   # N.600
    long_zone_hi = k_floor + config.zone_upper_max   # N.800

    ts = _last_bar_timestamp(df)

    # 상방 추세 + N.200~N.400 zone 터치 → SHORT
    if trend == "up" and last_high >= short_zone_lo and last_low <= short_zone_hi:
        sl_price = short_zone_hi + config.zone_sl_buffer
        return [EntrySignal(
            direction=Direction.SHORT,
            timeframe=tf,
            source="zone_2468_short",
            strength=1.0,
            note=(
                f"2468 저항 ({tf}, 상방추세, "
                f"zone={short_zone_lo:.0f}~{short_zone_hi:.0f}, "
                f"high={last_high:.0f}, SL={sl_price:.0f})"
            ),
            bar_timestamp=ts,
            pattern_id=f"2468@{tf}@N{int(k_floor)}_short",
        )]

    # 하방 추세 + N.600~N.800 zone 터치 → LONG
    if trend == "down" and last_high >= long_zone_lo and last_low <= long_zone_hi:
        sl_price = long_zone_lo - config.zone_sl_buffer
        return [EntrySignal(
            direction=Direction.LONG,
            timeframe=tf,
            source="zone_2468_long",
            strength=1.0,
            note=(
                f"2468 지지 ({tf}, 하방추세, "
                f"zone={long_zone_lo:.0f}~{long_zone_hi:.0f}, "
                f"low={last_low:.0f}, SL={sl_price:.0f})"
            ),
            bar_timestamp=ts,
            pattern_id=f"2468@{tf}@N{int(k_floor)}_long",
        )]

    return []


# ============================================================
# Selectable 지표 라우터 (사용자 on/off)
# ============================================================


def evaluate_selectable(
    df_by_tf: dict[str, pd.DataFrame],
    config: StrategyConfig,
    symbol: str = "BTC/USDT",
) -> list[EntrySignal]:
    """사용자가 켠 Selectable 지표 + 2468(항상 ON, BTC 전용) 신호 합본.

    각 지표는 자기 고정 TF 의 데이터만 참조.

    2468 정책 (사용자 결정):
        - **별도 Selectable 지표 X, 기본값 ON** — GUI 토글에 노출 안 함
          (``config.use_2468`` 은 테스트/실험용 internal flag).
        - **BTC 전용** — 다른 symbol 에선 자동 무시.
        - **우선순위**: 일반 지표 > 2468 (strength 1.0 vs 다른 지표 1.0~1.5,
          HTF 가중치 합산으로 자연 정렬).

    Args:
        df_by_tf: TF 별 DataFrame 딕셔너리.
        config: 어떤 Selectable 지표를 켤지 결정.
        symbol: 거래 페어 (예: "BTC/USDT"). 2468 BTC 전용 검증에 사용.

    Returns:
        활성화된 지표들의 ``EntrySignal`` 합본 리스트.
    """
    signals: list[EntrySignal] = []

    if config.use_bollinger:
        df_1h = df_by_tf.get("1H")
        if df_1h is not None:
            signals.extend(detect_bollinger_touch(df_1h, config))

    if config.use_ma_cross:
        signals.extend(detect_ma_cross(df_by_tf, config))

    if config.use_ichimoku:
        signals.extend(detect_ichimoku_signal(df_by_tf, config))

    if config.use_harmonic:
        signals.extend(detect_harmonic_signal(df_by_tf, config))

    # 2,4,6,8 — 항상 ON, BTC 전용 (GUI 토글 X)
    signals.extend(detect_2468_signal(df_by_tf, config, symbol=symbol))

    return signals


# ============================================================
# D-5 Regime breakdown — 4H 시장 국면 분류 (2026-05-05)
# ============================================================


class Regime(StrEnum):
    """시장 국면 enum — TradeRecord.regime metadata 입력.

    분류 우선순위 (정책 spec 5/5 LGTM 합치, Issue #110): VOLATILE > TREND > RANGE.
    UNKNOWN 은 4H 미닫힘 진입 시 Position.regime 디폴트 (분류 미산출 자연 처리).
    """

    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    VOLATILE = "VOLATILE"
    UNKNOWN = "UNKNOWN"


# Regime 분류 임계 — 정책 spec drafts/D-5-regime-policy-spec.md 5/5 LGTM 합치 (잠정안)
# RegimeConfig 디폴트 source — 본 4 상수 보존 (배포 호환성, 외부 import path 유지).
# 모듈 상수 deprecation 은 후속 트래커 (외부 import 영향 점검 후 결정).
TREND_THRESHOLD = 0.005          # EMA50/200 격차 ±0.5% — TREND vs RANGE 경계
VOLATILITY_MULTIPLIER = 2.0      # atr_now / atr_avg ≥ 2.0 → VOLATILE
VOLATILITY_LOOKBACK = 20         # ATR 평균 산출 윈도우 (4H 봉 20 개 = 약 3.3 일)


@dataclass(slots=True)
class RegimeConfig:
    """Regime 분류 임계 + F1 옵트인 가드 매개변수 (D-5 보충 F1 + F3, Issue #110 후속).

    F3 — `classify_regime` 임계 3 개 매개변수화 (디폴트는 모듈 상수 그대로,
    배포 호환성 보존). F1 — `skip_on_volatile` 옵트인 (디폴트 False — D-5 #124
    현 동작 보존). `BacktestConfig.regime_config` 노출 (D-1 risk_config 패턴 정합)
    + `BacktestEngine.__init__` 시점 `self._regime_config` 박음 + `step()` 8 단계
    인자 전달 + 9b 진입 직전 가드 (옵션 C).

    Attributes:
        trend_threshold: TREND vs RANGE 경계 — `|gap| ≥ trend_threshold` 시 TREND.
            디폴트 ``TREND_THRESHOLD = 0.005`` (±0.5%).
        volatility_multiplier: VOLATILE 분류 — `atr_now / atr_avg ≥ multiplier`.
            디폴트 ``VOLATILITY_MULTIPLIER = 2.0``.
        volatility_lookback: ATR 평균 산출 윈도우 (4H 봉 N 개). 디폴트
            ``VOLATILITY_LOOKBACK = 20`` (≈ 3.3 일).
        skip_on_volatile: True 시 `BacktestEngine.step()` 9b 가드 — `_last_regime`
            이 VOLATILE 일 때 진입 skip (옵션 C, drafts/D-5-supplements-policy-spec.md
            Q1 정합). 디폴트 False — D-5 #124 현 동작 보존 (옵트인).
    """

    trend_threshold: float = TREND_THRESHOLD
    volatility_multiplier: float = VOLATILITY_MULTIPLIER
    volatility_lookback: int = VOLATILITY_LOOKBACK
    skip_on_volatile: bool = False


def classify_regime(
    df_4h: pd.DataFrame,
    regime_config: RegimeConfig | None = None,
) -> Regime:
    """4H DataFrame 기반 시장 국면 분류 — 4 regime + UNKNOWN fallback.

    분류 우선순위 (정책 spec, 5/5 LGTM 합치 — Issue #110):
        1. **VOLATILE** — ``atr_now / atr_avg ≥ volatility_multiplier`` (변동성 우선,
           TREND 동시 발동 시에도 VOLATILE 채택).
        2. **TREND_UP** — ``(ema50 - ema200) / ema200 ≥ trend_threshold``.
        3. **TREND_DOWN** — ``(ema50 - ema200) / ema200 ≤ -trend_threshold``.
        4. **RANGE** — fallback (격차 미만 또는 변동성 평균 안정).

    Sample 부족 (``len(df_4h) < volatility_lookback``) 또는 NaN 발생 시
    ``RANGE`` fallback (silent — UNKNOWN 은 진입 시점 박는 디폴트로만 사용).

    Args:
        df_4h: 4H OHLC DataFrame (``open/high/low/close`` 컬럼 필요).
        regime_config: 임계 매개변수화 (D-5 보충 F3). ``None`` 시 ``RegimeConfig()``
            디폴트 (모듈 상수 그대로 — 배포 호환성). mutable default 안티패턴
            회피 위해 ``None`` 분기 + 함수 내 인스턴스 생성 패턴.

    Returns:
        ``Regime`` enum (VOLATILE / TREND_UP / TREND_DOWN / RANGE).

    Note:
        본 함수는 분류만 책임. VOLATILE 시 신호 평가 skip 등의 액션은 호출자
        (`BacktestEngine.step()` 9b 가드, D-5 보충 F1) 책임 — `RegimeConfig.
        skip_on_volatile` 옵트인.
    """
    cfg = regime_config or RegimeConfig()

    # Sample 부족 가드 — lookback 미만이면 ATR 평균 산출 무의미 → RANGE fallback
    if len(df_4h) < cfg.volatility_lookback:
        return Regime.RANGE

    close = df_4h["close"]
    ema50 = ema(close, 50)
    ema200 = ema(close, 200)
    atr = atr_wilder(df_4h, period=14)

    ema50_now = ema50.iloc[-1]
    ema200_now = ema200.iloc[-1]
    atr_now = atr.iloc[-1]

    # NaN 가드 — close 끝부분 NaN / 산출 실패 → RANGE fallback
    if pd.isna(ema50_now) or pd.isna(ema200_now) or pd.isna(atr_now):
        return Regime.RANGE

    # VOLATILE 우선 (정책 spec — 변동성 평균 ≥ multiplier)
    atr_avg = atr.iloc[-cfg.volatility_lookback:].mean()
    # Why: atr_avg=0 가드 — 완전 정적 가격 (high=low=close 모든 봉) 시 ZeroDiv 회피
    if atr_avg > 0 and atr_now / atr_avg >= cfg.volatility_multiplier:
        return Regime.VOLATILE

    # TREND 분류 — EMA50/200 상대 격차 (정책 spec ±trend_threshold)
    # Why: ema200_now=0 가드 — 산출가 0 (이론상 비정상) 시 ZeroDiv 회피 → RANGE
    if ema200_now == 0:
        return Regime.RANGE
    gap = (ema50_now - ema200_now) / ema200_now
    if gap >= cfg.trend_threshold:
        return Regime.TREND_UP
    if gap <= -cfg.trend_threshold:
        return Regime.TREND_DOWN

    return Regime.RANGE
