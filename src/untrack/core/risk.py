"""리스크 관리 — 포지션 사이즈, SL/TP 거리, 트레일링, 레버리지별 SL 캡.

Tako 차용:
    - TP/SL 3 모드: ATR Dynamic / Fixed % / Manual %
    - 4단계 분할 익절 + allocation
    - 5가지 트레일링: Moving Target, Moving 2-Target, Breakeven,
      Percent Below Triggers, Percent Below Highest

담당: 팀원 A
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

import pandas as pd


class TpSlMode(str, Enum):
    ATR = "atr"
    FIXED_PCT = "fixed_pct"
    MANUAL = "manual"


class TrailingMode(str, Enum):
    OFF = "off"
    MOVING_TARGET = "moving_target"
    MOVING_2_TARGET = "moving_2_target"
    BREAKEVEN = "breakeven"
    PERCENT_BELOW_TRIGGERS = "percent_below_triggers"
    PERCENT_BELOW_HIGHEST = "percent_below_highest"


@dataclass(slots=True)
class TpSlConfig:
    """TP/SL 사용자 설정."""

    mode: TpSlMode = TpSlMode.FIXED_PCT
    # 모드별 파라미터
    atr_period: int = 14
    atr_tp_multipliers: list[float] = field(default_factory=lambda: [1.0, 2.0, 3.0, 4.0])
    atr_sl_multiplier: float = 1.5
    fixed_tp_pcts: list[float] = field(default_factory=lambda: [1.0, 2.0, 3.0, 4.0])
    fixed_sl_pct: float = 2.0
    manual_tp_pcts: list[float] = field(default_factory=lambda: [0.5, 1.0, 1.5, 2.0])
    manual_sl_pct: float = 1.0

    # 분할 익절 비율 (합 100)
    tp_allocations: list[float] = field(default_factory=lambda: [25.0, 25.0, 25.0, 25.0])

    # 트레일링
    trailing_mode: TrailingMode = TrailingMode.MOVING_TARGET
    trailing_trigger_target: int = 2  # TP 몇 단계 도달 시 발동
    trailing_trigger_pct: float = 2.0  # 또는 % 기반 발동
    trailing_pct: float = 1.0  # 트레일링 거리


@dataclass(slots=True)
class RiskPlan:
    """진입 시 산출되는 리스크 계획."""

    entry_price: float
    direction: str
    leverage: int
    position_usd: float
    tp_prices: list[float]  # 4개
    sl_price: float
    trailing_mode: TrailingMode


def min_sl_pct_by_leverage(leverage: int) -> float:
    """레버리지별 최소 SL 거리 — 청산 위험 방지.

    예시:
        10x → 7%
        20x → 5%
        50x → 3%

    이 값보다 좁은 SL은 청산선에 근접해 위험.
    """
    # TODO(A): 사용자 룰 확정 후 구현
    raise NotImplementedError


def calc_position_size(
    equity_usd: float,
    leverage: int,
    risk_pct: float,
    sl_distance_pct: float,
) -> float:
    """리스크 기반 포지션 사이즈 계산.

    risk_pct: 1회 거래 최대 손실 비율 (전체 자본 대비)
    """
    # TODO(A)
    raise NotImplementedError


def build_risk_plan(
    entry_price: float,
    direction: str,
    leverage: int,
    equity_usd: float,
    config: TpSlConfig,
    atr: float | None = None,
) -> RiskPlan:
    """진입 시점에 SL/TP/사이즈를 한 번에 계산."""
    # TODO(A)
    raise NotImplementedError


def update_trailing_sl(
    current_sl: float,
    plan: RiskPlan,
    config: TpSlConfig,
    tp_hits: int,
    highest_since_entry: float,
    lowest_since_entry: float,
) -> float:
    """트레일링 모드에 따라 SL 갱신.

    tp_hits: 0~4 (몇 단계 익절 도달)
    """
    # TODO(A): 5가지 트레일링 모드 구현
    raise NotImplementedError
