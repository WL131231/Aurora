"""전략 룰 — EMA 터치 진입, RSI Div 단독 진입, Selectable OR 조합.

담당: 장수
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import pandas as pd

from aurora.core.indicators import ema, rsi, rsi_divergence

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
# Selectable 지표 (사용자 on/off)
# ============================================================


def evaluate_selectable(
    df_by_tf: dict[str, pd.DataFrame],
    config: StrategyConfig,
) -> list[EntrySignal]:
    """사용자가 켠 Selectable 지표만 평가해서 신호 리스트 반환."""
    # TODO(장수): config 플래그 보고 각 지표 평가 (BB, MA Cross, Harmonic, Ichimoku)
    return []
