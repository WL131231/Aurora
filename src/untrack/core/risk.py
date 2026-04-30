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
from enum import StrEnum

# Note: pandas / Literal 등은 추후 ATR 계산·구체 구현 시 다시 import.


class TpSlMode(StrEnum):
    ATR = "atr"
    FIXED_PCT = "fixed_pct"
    MANUAL = "manual"


class TrailingMode(StrEnum):
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

    구간 분기:
        10x ~ 37x (보수): SL = 2 + (L - 10) / 27   (선형: 10x→2%, 37x→3%)
        38x ~ 50x (공격): SL = 0.08 × L            (50x→4%)

    근거:
        - 저배율(10~37x): 작은 가격 변동에도 의미 있는 거래라 SL 2~3% 충분.
        - 고배율(38~50x): 풀시드 수수료가 시드를 많이 갉아먹음 (50x = 5.5%)
                          → 더 큰 가격 변동 필요.
        - 37x→38x 경계: 보수 → 공격 전환점 (의도된 점프).

    예시:
        10x → 2.00%
        37x → 3.00% (보수 영역 끝)
        38x → 3.04% (공격 영역 시작)
        50x → 4.00%

    Note: 출발값이고 테스트 결과 따라 조정 가능.
    """
    if leverage <= 37:
        return 2.0 + (leverage - 10) / 27.0
    return 0.08 * leverage


def tp_pct_range_for_leverage(leverage: int) -> tuple[float, float]:
    """레버리지별 TP 범위 (가격 변동 %).

    구간 분기:
        10x ~ 37x: TP min/max 모두 선형 그래디언트
            TP min(L) = SL(L) + 0.8        (10x→2.8%, 37x→3.8%)
            TP max(L) = SL(L) + 1.8        (10x→3.8%, 37x→4.8%)
        38x ~ 50x: TP = SL + 2 ~ 3
            TP min = SL + 2.0              (50x→6%)
            TP max = SL + 3.0              (50x→7%)

    저배율은 작은 익절을 빈도로 누적, 고배율은 큰 가격 변동 요구.
    사용자가 min~max 범위 내에서 선택 (디폴트 mid).

    Returns:
        (tp_min, tp_max) 튜플.

    예시:
        10x → (2.80%, 3.80%)
        37x → (3.80%, 4.80%)
        38x → (5.04%, 6.04%)
        50x → (6.00%, 7.00%)
    """
    sl = sl_pct_for_leverage(leverage)
    if leverage <= 37:
        return (sl + 0.8, sl + 1.8)
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
