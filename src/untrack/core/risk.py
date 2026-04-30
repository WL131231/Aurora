"""리스크 관리 — 포지션 사이즈, SL/TP 거리, 트레일링, 레버리지별 SL 캡.

Tako 차용:
    - TP/SL 3 모드: ATR Dynamic / Fixed % / Manual %
    - 4단계 분할 익절 + allocation
    - 5가지 트레일링: Moving Target, Moving 2-Target, Breakeven,
      Percent Below Triggers, Percent Below Highest

담당: 장수
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# Note: pandas / Literal 등은 추후 ATR 계산·구체 구현 시 다시 import.


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


def sl_pct_for_leverage(leverage: int) -> float:
    """레버리지별 SL 거리 (가격 변동 %).

    공식:
        SL(L) = max(3.0, 0.08 × L)

    근거:
        - 0.08 × L : 풀시드 진입 시 수수료가 시드를 갉아먹는 정도 기반.
                     레버리지 높을수록 수수료 부담 큼 → 더 큰 가격 변동 필요.
        - 3.0% 하한: 작은 레버리지에선 0.8%(=10x×0.08) 같은 SL이 시장 노이즈에
                     자주 발동(whipsaw) → 최소 3% 보장.

    예시:
        10x → 3.00% (하한 활성)
        25x → 3.00% (하한 활성)
        38x → 3.04% (공식 활성)
        50x → 4.00% (공식 활성, 장수 기준점)

    Note: 출발값이고 테스트 결과 따라 조정 가능.
    """
    return max(3.0, 0.08 * leverage)


def tp_pct_range_for_leverage(leverage: int) -> tuple[float, float]:
    """레버리지별 TP 범위 (가격 변동 %).

    공식:
        TP(L) = SL(L) + 2.0 ~ 3.0

    SL 위 +2~3% 영역이 수수료 메꾸고 진짜 순수익이 나는 구간.
    사용자가 이 범위 내에서 선택 (디폴트 mid = SL + 2.5%).

    Returns:
        (tp_min, tp_max) 튜플.

    예시:
        10x → (5.00%, 6.00%)
        50x → (6.00%, 7.00%)
    """
    sl = sl_pct_for_leverage(leverage)
    return (sl + 2.0, sl + 3.0)


# === Deprecated alias (이전 이름) ===
def min_sl_pct_by_leverage(leverage: int) -> float:
    """Deprecated: sl_pct_for_leverage 를 사용할 것."""
    return sl_pct_for_leverage(leverage)


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
