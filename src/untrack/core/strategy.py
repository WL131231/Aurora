"""전략 룰 — EMA 터치 진입, RSI Div 단독 진입, Selectable OR 조합.

담당: 팀원 A
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

import pandas as pd


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(slots=True)
class EntrySignal:
    """진입 신호."""

    direction: Direction
    timeframe: str  # "1H", "4H", etc.
    source: str  # "ema_touch", "rsi_div", "bb", "ma_cross", "harmonic", "ichimoku"
    strength: float  # 0.0 ~ 1.0
    note: str = ""


@dataclass(slots=True)
class StrategyConfig:
    """사용자 설정 — Selectable 지표 on/off."""

    use_bollinger: bool = False
    use_ma_cross: bool = False
    use_harmonic: bool = False
    use_ichimoku: bool = False

    ema_touch_tolerance: float = 0.003  # ±0.3%
    rsi_div_lookback: int = 30


def detect_ema_touch(
    df_by_tf: dict[str, pd.DataFrame],
    config: StrategyConfig,
) -> EntrySignal | None:
    """EMA 200/480 터치 감지.

    1H~1W 다중 타임프레임에서 가격이 EMA에 일정 거리 이내로 접근했는지 확인.
    지지(아래에서 위) → 롱, 저항(위에서 아래) → 숏.

    Args:
        df_by_tf: {"1H": DataFrame, "4H": ..., "1D": ..., "1W": ...}
        config: 전략 설정.
    """
    # TODO(A)
    raise NotImplementedError


def detect_rsi_divergence(
    df_1h: pd.DataFrame,
    config: StrategyConfig,
) -> EntrySignal | None:
    """RSI 다이버전스 단독 진입.

    1H 차트에서 강세/약세 다이버전스 감지 시 진입 신호 발생.
    """
    # TODO(A)
    raise NotImplementedError


def evaluate_selectable(
    df_by_tf: dict[str, pd.DataFrame],
    config: StrategyConfig,
) -> list[EntrySignal]:
    """사용자가 켠 Selectable 지표만 평가해서 신호 리스트 반환."""
    # TODO(A): config 플래그 보고 각 지표 평가
    raise NotImplementedError
