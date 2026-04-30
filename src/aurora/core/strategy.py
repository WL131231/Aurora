"""전략 룰 — EMA 터치 진입, RSI Div 단독 진입, Selectable OR 조합.

담당: 장수
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import pandas as pd

from aurora.core.indicators import bollinger_bands, ema, ma_cross, rsi, rsi_divergence

# ============================================================
# 공통 dataclass / enum
# ============================================================


class Direction(StrEnum):
    LONG = "long"
    SHORT = "short"


@dataclass(slots=True)
class EntrySignal:
    """진입 신호 (지표 1개 × TF 1개 산출 단위)."""

    direction: Direction
    timeframe: str  # "15m", "1H", "4H", etc.
    source: str  # "ema_touch_200", "rsi_div", "bb", etc.
    strength: float = 1.0  # 0.0 ~ 1.0
    note: str = ""


@dataclass(slots=True)
class StrategyConfig:
    """사용자 설정 — Selectable 지표 on/off 및 파라미터."""

    # Selectable on/off
    use_bollinger: bool = False
    use_ma_cross: bool = False
    use_harmonic: bool = False
    use_ichimoku: bool = False

    # 진입 파라미터
    ema_touch_tolerance: float = 0.003  # ±0.3%
    ema_periods: tuple[int, ...] = (200, 480)
    rsi_period: int = 14
    rsi_div_lb_left: int = 5
    rsi_div_lb_right: int = 5

    # Bollinger Bands (Selectable — 1H 고정)
    bollinger_period: int = 20
    bollinger_std: float = 2.0
    bollinger_proximity_pct: float = 0.005
    """가장자리 라인에서 안쪽 ``proximity_pct`` 이내 = 진입 zone (예: 0.005 = 0.5%)."""
    bollinger_squeeze_threshold: float = 0.015
    """폭(upper-lower) / middle 이 이 값 이하면 squeeze 상태로 진입 보류 (기본 0.015 = 1.5%)."""

    # MA Cross (Selectable — 1H/2H/4H 멀티 TF, HTF 가중치 자동 적용)
    ma_cross_fast: int = 50
    ma_cross_slow: int = 200
    """SMA 기간. 골크/데크 표준은 50/200."""


# ============================================================
# Fixed: EMA 터치 진입
# ============================================================

# EMA 터치 진입에 사용할 TF 목록 (project_indicator_spec.md 기준)
EMA_TIMEFRAMES: tuple[str, ...] = ("15m", "1H", "2H", "4H", "6H", "12H", "1D", "1W")


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

        for period in config.ema_periods:
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

            signals.append(
                EntrySignal(
                    direction=direction,
                    timeframe=tf,
                    source=f"ema_touch_{period}",
                    strength=1.0,
                    note=note,
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
        )
    ]


# ============================================================
# Selectable: Bollinger Bands (1H 고정, 횡보장 특화)
# ============================================================


def detect_bollinger_touch(
    df_1h: pd.DataFrame,
    config: StrategyConfig,
) -> list[EntrySignal]:
    """볼린저 밴드 4-tier 진입 룰 (1H 고정, 양방향 진입+청산 정책).

    우선순위 순:
        1. **Squeeze 보류**: 폭 / middle ≤ ``squeeze_threshold`` (밴드 좁음, 추세 대기) → []
        2. **Reversal 진입** (강도 1.5): 직전 봉 종가가 BB 밖 + 현재 봉 종가 안쪽
           → 위로 찢어졌다 회귀 = SHORT / 아래로 찢어졌다 회귀 = LONG
        3. **찢어짐 보류**: 현재 봉 종가가 BB 밖 (위 또는 아래) → []
        4. **Proximity 터치 진입** (강도 1.0): 봉의 high/low 가 가장자리 zone 진입
           → high ≥ ``upper × (1 - proximity)`` + close 안쪽 = SHORT
           → low  ≤ ``lower × (1 + proximity)`` + close 안쪽 = LONG

    "양방향 진입+청산": BB 신호 = 진입 신호 + 반대 포지션 청산 신호.
    구체 동작은 ``signal.compose_entry`` / ``compose_exit`` 가 처리.

    Args:
        df_1h: 1H OHLC DataFrame (close/high/low 컬럼).
        config: 전략 설정 (bollinger_period/std/proximity_pct/squeeze_threshold).

    Returns:
        ``EntrySignal`` 리스트 (마지막 봉 기준).
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

    # ─── 1. Squeeze 보류 ──────────────────────────────────
    if last_middle_f > 0:
        narrowness = (last_upper_f - last_lower_f) / last_middle_f
        if narrowness <= config.bollinger_squeeze_threshold:
            return []

    # ─── 2. Reversal 진입 (직전 봉 outside → 현재 봉 inside) ───
    prev_close = float(df_1h["close"].iloc[-2])
    prev_upper = bb["upper"].iloc[-2]
    prev_lower = bb["lower"].iloc[-2]

    if not pd.isna(prev_upper) and not pd.isna(prev_lower):
        prev_upper_f = float(prev_upper)
        prev_lower_f = float(prev_lower)
        # 위로 찢어졌다 회귀
        if prev_close > prev_upper_f and last_close <= last_upper_f:
            return [EntrySignal(
                direction=Direction.SHORT,
                timeframe="1H",
                source="bollinger_reversal_upper",
                strength=1.5,
                note=f"BB 상단 찢어짐 회귀 (prev={prev_close:.4f}>{prev_upper_f:.4f}, "
                     f"last={last_close:.4f}≤{last_upper_f:.4f})",
            )]
        # 아래로 찢어졌다 회귀
        if prev_close < prev_lower_f and last_close >= last_lower_f:
            return [EntrySignal(
                direction=Direction.LONG,
                timeframe="1H",
                source="bollinger_reversal_lower",
                strength=1.5,
                note=f"BB 하단 찢어짐 회귀 (prev={prev_close:.4f}<{prev_lower_f:.4f}, "
                     f"last={last_close:.4f}≥{last_lower_f:.4f})",
            )]

    # ─── 3. 찢어짐 보류 (현재 봉 종가가 BB 밖) ────────────
    if last_close > last_upper_f or last_close < last_lower_f:
        return []

    # ─── 4. Proximity 터치 진입 (가장자리 안쪽 zone) ──────
    upper_zone_start = last_upper_f * (1.0 - config.bollinger_proximity_pct)
    lower_zone_end = last_lower_f * (1.0 + config.bollinger_proximity_pct)

    signals: list[EntrySignal] = []
    if last_high >= upper_zone_start:
        signals.append(EntrySignal(
            direction=Direction.SHORT,
            timeframe="1H",
            source="bollinger_upper",
            strength=1.0,
            note=f"BB 상단 zone 진입 (high={last_high:.4f}, zone_start={upper_zone_start:.4f})",
        ))
    if last_low <= lower_zone_end:
        signals.append(EntrySignal(
            direction=Direction.LONG,
            timeframe="1H",
            source="bollinger_lower",
            strength=1.0,
            note=f"BB 하단 zone 진입 (low={last_low:.4f}, zone_end={lower_zone_end:.4f})",
        ))
    return signals


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
        if last == "golden":
            signals.append(EntrySignal(
                direction=Direction.LONG,
                timeframe=tf,
                source="ma_cross_golden",
                strength=1.0,
                note=f"SMA{config.ma_cross_fast}/{config.ma_cross_slow} 골든크로스 ({tf})",
            ))
        elif last == "dead":
            signals.append(EntrySignal(
                direction=Direction.SHORT,
                timeframe=tf,
                source="ma_cross_dead",
                strength=1.0,
                note=f"SMA{config.ma_cross_fast}/{config.ma_cross_slow} 데드크로스 ({tf})",
            ))

    return signals


# ============================================================
# Selectable 지표 라우터 (사용자 on/off)
# ============================================================


def evaluate_selectable(
    df_by_tf: dict[str, pd.DataFrame],
    config: StrategyConfig,
) -> list[EntrySignal]:
    """사용자가 켠 Selectable 지표만 평가해서 신호 리스트 반환.

    각 지표는 자기 고정 TF 의 데이터만 참조.

    Args:
        df_by_tf: TF 별 DataFrame 딕셔너리.
        config: 어떤 Selectable 지표를 켤지 결정.

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

    # TODO(장수): Harmonic, Ichimoku 추가 (별도 PR)

    return signals
