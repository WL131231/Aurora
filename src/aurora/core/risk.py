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
from typing import NamedTuple


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
    """TP/SL 사용자 설정.

    Note:
        디폴트 값은 모두 "출발값" — 사용자가 GUI 에서 조정. 백테스트 결과로
        상수 자체를 튜닝하지 말 것 (사용자 입력 오버라이드되는 값).
    """

    mode: TpSlMode = TpSlMode.FIXED_PCT
    # 모드별 파라미터
    atr_period: int = 14                   # 표준 ATR 기간 (Wilder 1978 권고치)
    atr_tp_multipliers: list[float] = field(default_factory=lambda: [1.0, 2.0, 3.0, 4.0])
    atr_sl_multiplier: float = 1.5         # SL = 1.5×ATR — 노이즈 통과 보편 값
    # FIXED_PCT 모드 — 단위 = ROI % (마진 기준 손익률, 사용자 친화). build_risk_plan
    # 에서 / leverage 변환해 가격 변동 % 로 매핑. 빈 list / None 이면 leverage 자동
    # 산출 (sl_pct_for_leverage / tp_pct_4_levels_for_leverage).
    fixed_tp_pcts: list[float] = field(default_factory=list)  # 빈 list = 자동 산출
    fixed_sl_pct: float | None = None      # None = 자동 산출 (sl_pct_for_leverage)
    manual_tp_pcts: list[float] = field(default_factory=lambda: [0.5, 1.0, 1.5, 2.0])
    manual_sl_pct: float = 1.0

    # 분할 익절 비율 (합 100) — 4단계 균등 출발값, 사용자가 GUI 에서 조정
    tp_allocations: list[float] = field(default_factory=lambda: [25.0, 25.0, 25.0, 25.0])

    # 트레일링
    trailing_mode: TrailingMode = TrailingMode.MOVING_TARGET
    trailing_trigger_target: int = 2  # TP 2단계 도달 시 발동 (1단계는 너무 빠른 lock-in)
    trailing_trigger_pct: float = 2.0  # 또는 % 기반 발동 (fixed_sl 1배 = 1:1 RR)
    trailing_pct: float = 1.0  # 트레일링 거리


class PositionSize(NamedTuple):
    """포지션 사이즈 계산 결과 — 거래소 주문 + 시드 추적 + 분할 익절 모두 활용.

    Attributes:
        notional_usd: 명목 가치 (USD). 거래소 ``cost`` 파라미터에 자연스러움.
        margin_usd: 실제 묶이는 마진 (USD) = notional / leverage.
        coin_amount: 코인 수량 = notional / entry_price. ccxt ``amount`` 인자.
    """

    notional_usd: float
    margin_usd: float
    coin_amount: float


@dataclass(slots=True)
class RiskPlan:
    """진입 시 산출되는 리스크 계획 — SL/TP 가격 + 포지션 사이즈."""

    entry_price: float
    direction: str
    leverage: int
    position: PositionSize  # notional / margin / coin_amount 한 번에
    tp_prices: list[float]  # 4개 (분할 익절)
    sl_price: float
    trailing_mode: TrailingMode


def sl_pct_for_leverage(leverage: int) -> float:
    """레버리지별 SL ROI 거리 (마진 기준 손익률 %, v0.1.13 단위 명확화).

    단위 = ROI % (마진 기준). 가격 변동 % = ROI % / leverage.
    예: 10x · ROI 2% → 가격 변동 0.2%

    구간 분기:
        10x ~ 37x (보수): SL = 2 + (L - 10) / 27   (선형: 10x→ROI 2%, 37x→ROI 3%)
        38x ~ 50x (공격): SL = 0.08 × L            (50x→ROI 4%)

    근거:
        - 저배율(10~37x): 작은 ROI 손실로도 충분한 시그널 — SL 2~3% 충분.
        - 고배율(38~50x): 풀시드 수수료가 시드를 많이 갉아먹음 → ROI 손실 여유 필요.
        - 37x→38x 경계: 보수 → 공격 전환점 (의도된 점프).

    예시 (ROI):
        10x → 2.00% (가격 변동 0.20%)
        37x → 3.00% (가격 변동 0.081%)
        38x → 3.04% (가격 변동 0.080%)
        50x → 4.00% (가격 변동 0.080%)

    Note: 출발값이고 테스트 결과 따라 조정 가능.
    """
    if leverage <= 37:
        return 2.0 + (leverage - 10) / 27.0
    return 0.08 * leverage


def tp_pct_range_for_leverage(leverage: int) -> tuple[float, float]:
    """레버리지별 TP ROI 범위 (마진 기준 손익률 %, v0.1.13 단위 명확화).

    단위 = ROI % (마진 기준). 가격 변동 % = ROI % / leverage.

    구간 분기:
        10x ~ 37x: TP min/max 모두 선형 그래디언트 (SL 출발값 + 0.8 ~ 1.8)
            TP min(L) = SL(L) + 0.8        (10x → ROI 2.8%, 37x → ROI 3.8%)
            TP max(L) = SL(L) + 1.8        (10x → ROI 3.8%, 37x → ROI 4.8%)
        38x ~ 50x: TP = SL + 2 ~ 3
            TP min = SL + 2.0              (50x → ROI 6%)
            TP max = SL + 3.0              (50x → ROI 7%)

    저배율은 작은 ROI 익절을 빈도로 누적, 고배율은 큰 ROI 익절 (가격 변동은 작음).
    사용자가 min~max 범위 내에서 선택 (디폴트 mid).

    Returns:
        (tp_min_roi, tp_max_roi) 튜플 (단위 ROI %).

    예시 (ROI):
        10x → (2.80%, 3.80%)
        37x → (3.80%, 4.80%)
        38x → (5.04%, 6.04%)
        50x → (6.00%, 7.00%)
    """
    sl = sl_pct_for_leverage(leverage)
    if leverage <= 37:
        return (sl + 0.8, sl + 1.8)
    return (sl + 2.0, sl + 3.0)


def tp_pct_4_levels_for_leverage(leverage: int) -> list[float]:
    """레버리지별 TP 4단계 ROI % — tp_pct_range 의 min~max 4등분 (v0.1.13 신규).

    단위 = ROI %. 4단계 분할 익절 (TP1~TP4) 의 ROI 임계값. min, mid_low, mid_high, max.

    예시 (10x): tp_pct_range = (2.80%, 3.80%) → step=0.333
        TP1 = 2.80% / TP2 = 3.13% / TP3 = 3.47% / TP4 = 3.80%

    Returns:
        4단계 ROI % list (오름차순, long/short 무관).
    """
    tp_min, tp_max = tp_pct_range_for_leverage(leverage)
    step = (tp_max - tp_min) / 3
    return [tp_min, tp_min + step, tp_min + 2 * step, tp_max]


# === Deprecated alias (이전 이름) ===
def min_sl_pct_by_leverage(leverage: int) -> float:
    """Deprecated: sl_pct_for_leverage 를 사용할 것."""
    return sl_pct_for_leverage(leverage)


# ============================================================
# 포지션 사이즈 계산
# ============================================================

# 사용자 정책: 매 거래의 마진은 시드의 최소 ``MIN_SEED_PCT`` 이상 사용.
# risk-based 계산 결과가 너무 작으면 강제로 끌어올림 (수수료 비율 보호).
DEFAULT_MIN_SEED_PCT: float = 0.40


def calc_position_size(
    equity_usd: float,
    leverage: int,
    sl_distance_pct: float,
    entry_price: float,
    *,
    risk_pct: float | None = None,
    full_seed: bool = False,
    min_seed_pct: float = DEFAULT_MIN_SEED_PCT,
) -> PositionSize:
    """포지션 사이즈 계산 — risk-based 기본, 풀시드 옵션, 최소 시드 강제.

    두 모드:
        - **풀시드** (``full_seed=True``):
              ``notional = equity_usd × leverage`` (시드 전체 × 레버리지).
              ``risk_pct`` 무시. 마진은 항상 시드 100%.
        - **risk-based** (``full_seed=False``, default):
              ``risk_amount = equity_usd × risk_pct`` (거래당 최대 손실).
              ``notional = risk_amount / sl_distance_pct``.
              ``margin = notional / leverage``.

    **최소 시드 강제** (양 모드 공통):
        계산된 ``margin < equity_usd × min_seed_pct`` 면 강제로 ``min_seed_pct``
        마진으로 끌어올림. 기본 40% (사용자 정책: 너무 작은 진입은 수수료
        비율이 커져 손익비 망가짐 방지).

    Args:
        equity_usd: 시드 (사용 가능 자본금, USD).
        leverage: 레버리지 배율 (예: 10~50).
        sl_distance_pct: SL 까지 가격 변동 % (예: 0.04 = 4%).
        entry_price: 진입 가격 (코인 수량 환산용).
        risk_pct: 거래당 최대 손실 비율 (예: 0.01 = 1%). risk-based 모드에서 필수.
        full_seed: 풀시드 모드 사용 여부 (default False).
        min_seed_pct: 최소 진입 마진 비율 (default 0.40 = 시드의 40%).

    Returns:
        ``PositionSize(notional_usd, margin_usd, coin_amount)``.

    Raises:
        ValueError: 입력값이 양수가 아니거나 risk_pct 누락 (non-fullseed) 시.
    """
    if equity_usd <= 0:
        raise ValueError(f"equity_usd 는 양수여야 함 (받은: {equity_usd})")
    if leverage < 1:
        raise ValueError(f"leverage 는 1 이상 (받은: {leverage})")
    if entry_price <= 0:
        raise ValueError(f"entry_price 는 양수여야 함 (받은: {entry_price})")
    if not (0 <= min_seed_pct <= 1):
        raise ValueError(
            f"min_seed_pct 는 0~1 범위 (받은: {min_seed_pct})"
        )

    if full_seed:
        # 풀시드: 시드 전체 사용
        margin_usd = float(equity_usd)
        notional_usd = margin_usd * leverage
    else:
        if risk_pct is None or risk_pct <= 0:
            raise ValueError(
                f"risk-based 모드는 risk_pct 양수 필요 (받은: {risk_pct})"
            )
        if sl_distance_pct <= 0:
            raise ValueError(
                f"risk-based 모드는 sl_distance_pct 양수 필요 (받은: {sl_distance_pct})"
            )
        risk_amount = equity_usd * risk_pct
        notional_usd = risk_amount / sl_distance_pct
        margin_usd = notional_usd / leverage

        # 최소 시드 강제 (margin 너무 작으면 끌어올림)
        min_margin = equity_usd * min_seed_pct
        if margin_usd < min_margin:
            margin_usd = min_margin
            notional_usd = margin_usd * leverage

    coin_amount = notional_usd / entry_price
    return PositionSize(
        notional_usd=notional_usd,
        margin_usd=margin_usd,
        coin_amount=coin_amount,
    )


# ============================================================
# 통합 리스크 플랜 빌더
# ============================================================


def build_risk_plan(
    entry_price: float,
    direction: str,
    leverage: int,
    equity_usd: float,
    config: TpSlConfig,
    atr: float | None = None,
    *,
    risk_pct: float = 0.01,
    full_seed: bool = False,
    min_seed_pct: float = DEFAULT_MIN_SEED_PCT,
    bb_upper: float | None = None,
    bb_lower: float | None = None,
    bb_buffer_pct: float = 0.003,
    structural_sl_price: float | None = None,
) -> RiskPlan:
    """진입 시점에 SL/TP 가격 + 포지션 사이즈를 한 번에 계산.

    SL/TP 거리는 ``config.mode`` 따라 결정:
        - ``ATR``: ``sl = atr × atr_sl_multiplier``,
                   ``tp[i] = atr × atr_tp_multipliers[i]``.
        - ``FIXED_PCT``: ``sl = entry × fixed_sl_pct%``,
                          ``tp[i] = entry × fixed_tp_pcts[i]%``.
        - ``MANUAL``: ``sl = entry × manual_sl_pct%``,
                       ``tp[i] = entry × manual_tp_pcts[i]%``.

    **BB Structural SL override (v0.1.42)**:
        ``bb_upper`` / ``bb_lower`` 둘 다 주어지면 (BB 신호 진입), SL 가격은
        모드 무관하게 BB 라인 ± ``bb_buffer_pct`` 로 override:
            - short: ``sl_price = bb_upper × (1 + bb_buffer_pct)``
            - long:  ``sl_price = bb_lower × (1 - bb_buffer_pct)``
        TP 는 그대로 모드별 산출. Why: BB 진입 신호의 자연스러운 손절 = BB
        이탈. 호가 noise (0.08%) 위 안전 폭 (0.3%) 으로 사고팔고 사이클 차단
        (사용자 보고 v0.1.41 무한 루프 fix).

    방향별 가격 산출:
        - long: ``sl_price = entry - sl_dist``, ``tp_price[i] = entry + tp_dist[i]``.
        - short: ``sl_price = entry + sl_dist``, ``tp_price[i] = entry - tp_dist[i]``.

    Args:
        entry_price: 진입 가격.
        direction: 'long' 또는 'short' (대소문자 무관).
        leverage: 레버리지 배율.
        equity_usd: 시드.
        config: TP/SL 설정 (모드 + 모드별 파라미터 + 트레일링).
        atr: ATR 값 (``mode=ATR`` 일 때만 필요).
        risk_pct: risk-based 모드의 거래당 최대 손실 비율.
        full_seed: 풀시드 모드 사용.
        min_seed_pct: 최소 진입 마진 비율.
        bb_upper: BB 신호 진입 시 진입 시점 BB 상단 (v0.1.42, optional).
        bb_lower: BB 신호 진입 시 진입 시점 BB 하단 (v0.1.42, optional).
        bb_buffer_pct: BB 이탈 buffer (default 0.003 = 0.3%).

    Returns:
        ``RiskPlan`` (entry/direction/leverage/position/tp_prices/sl_price/trailing).

    Raises:
        ValueError: 잘못된 direction / mode / atr 누락 등.
    """
    direction_norm = direction.lower()
    if direction_norm not in ("long", "short"):
        raise ValueError(
            f"direction 은 'long' 또는 'short' (받은: {direction!r})"
        )
    if entry_price <= 0:
        raise ValueError(f"entry_price 는 양수여야 함 (받은: {entry_price})")

    # 모드별 SL/TP 거리 산출
    if config.mode == TpSlMode.ATR:
        if atr is None or atr <= 0:
            raise ValueError("ATR 모드는 atr 양수 필요")
        sl_dist = atr * config.atr_sl_multiplier
        tp_dists = [atr * m for m in config.atr_tp_multipliers]
    elif config.mode == TpSlMode.FIXED_PCT:
        # FIXED_PCT 의 fixed_sl_pct / fixed_tp_pcts 는 ROI % 단위 (v0.1.13).
        # 가격 변동 % = ROI % / leverage. None 또는 빈 list 면 leverage 자동 산출.
        sl_roi = (
            config.fixed_sl_pct
            if config.fixed_sl_pct is not None
            else sl_pct_for_leverage(leverage)
        )
        tp_rois = (
            config.fixed_tp_pcts
            if config.fixed_tp_pcts
            else tp_pct_4_levels_for_leverage(leverage)
        )
        sl_dist = entry_price * (sl_roi / leverage / 100.0)
        tp_dists = [entry_price * (p / leverage / 100.0) for p in tp_rois]
    elif config.mode == TpSlMode.MANUAL:
        # MANUAL 도 ROI % 단위로 통일 (v0.1.13).
        sl_dist = entry_price * (config.manual_sl_pct / leverage / 100.0)
        tp_dists = [
            entry_price * (p / leverage / 100.0) for p in config.manual_tp_pcts
        ]
    else:
        raise ValueError(f"unknown TpSlMode: {config.mode}")

    # 방향별 가격 — SL 은 구조적 신호 진입 시 override.
    # 우선순위 (v0.1.44): structural_sl_price (일반화, Ichimoku/EMA 등) >
    # bb_upper/bb_lower (v0.1.42 BB 전용) > 기존 ROI 기반.
    if direction_norm == "long":
        if structural_sl_price is not None and structural_sl_price > 0:
            sl_price = structural_sl_price
        elif bb_lower is not None and bb_lower > 0:
            # BB Structural SL override — long 진입 시 BB 하단 - buffer (v0.1.42)
            sl_price = bb_lower * (1.0 - bb_buffer_pct)
        else:
            sl_price = entry_price - sl_dist
        tp_prices = [entry_price + d for d in tp_dists]
    else:
        if structural_sl_price is not None and structural_sl_price > 0:
            sl_price = structural_sl_price
        elif bb_upper is not None and bb_upper > 0:
            # BB Structural SL override — short 진입 시 BB 상단 + buffer (v0.1.42)
            sl_price = bb_upper * (1.0 + bb_buffer_pct)
        else:
            sl_price = entry_price + sl_dist
        tp_prices = [entry_price - d for d in tp_dists]

    # sl_distance_pct 재산출 (SL override 후 변경 가능). position size 계산 입력.
    sl_distance_pct = abs(sl_price - entry_price) / entry_price
    if sl_distance_pct <= 0:
        # SL 가격이 진입가와 같거나 잘못된 경우 fallback (BB 가 진입가 부근일 때)
        sl_distance_pct = sl_dist / entry_price

    # 포지션 사이즈
    position = calc_position_size(
        equity_usd=equity_usd,
        leverage=leverage,
        sl_distance_pct=sl_distance_pct,
        entry_price=entry_price,
        risk_pct=risk_pct,
        full_seed=full_seed,
        min_seed_pct=min_seed_pct,
    )

    return RiskPlan(
        entry_price=entry_price,
        direction=direction_norm,
        leverage=leverage,
        position=position,
        tp_prices=tp_prices,
        sl_price=sl_price,
        trailing_mode=config.trailing_mode,
    )


# ============================================================
# 트레일링 SL 갱신 (5가지 모드)
# ============================================================


def update_trailing_sl(
    current_sl: float,
    plan: RiskPlan,
    config: TpSlConfig,
    tp_hits: int,
    highest_since_entry: float,
    lowest_since_entry: float,
) -> float:
    """트레일링 모드에 따라 SL 갱신 — 5가지 모드 + OFF.

    SL 은 단방향만 이동 (롱은 위로, 숏은 아래로). 즉 새 SL 이 현재 SL 보다
    "유리한" 쪽이 아니면 그대로 유지.

    모드:
        - **OFF**: SL 갱신 없음.
        - **MOVING_TARGET**: TP n단계 도달 시 SL 을 (n-1) 가격으로 이동.
            · n=1: SL = entry_price (브레이크이븐)
            · n=2: SL = tp_prices[0]
            · n=3: SL = tp_prices[1]
            · n=4: SL = tp_prices[2]
        - **MOVING_2_TARGET**: TP n단계 도달 시 SL 을 (n-2) 가격으로 이동.
            · n=2: SL = entry_price
            · n=3: SL = tp_prices[0]
            · n=4: SL = tp_prices[1]
        - **BREAKEVEN**: ``trailing_trigger_target`` 단계 도달 시 SL = entry_price.
        - **PERCENT_BELOW_TRIGGERS**: trigger 도달 후부터 활성화.
            · 롱: SL = highest_since_entry × (1 − trailing_pct/100)
            · 숏: SL = lowest_since_entry × (1 + trailing_pct/100)
        - **PERCENT_BELOW_HIGHEST**: 진입 직후부터 활성화 (no trigger).
            · 롱: SL = highest_since_entry × (1 − trailing_pct/100)
            · 숏: SL = lowest_since_entry × (1 + trailing_pct/100)

    Args:
        current_sl: 현재 SL 가격.
        plan: 진입 시 산출한 ``RiskPlan``.
        config: 트레일링 설정.
        tp_hits: 도달한 TP 단계 수 (0~4).
        highest_since_entry: 진입 후 최고가 (롱 트레일링용).
        lowest_since_entry: 진입 후 최저가 (숏 트레일링용).

    Returns:
        갱신된 SL 가격 (단방향 보장: 롱이면 ``max(current_sl, new_sl)``,
        숏이면 ``min(current_sl, new_sl)``).
    """
    is_long = plan.direction == "long"
    entry = plan.entry_price
    new_sl = current_sl

    if config.trailing_mode == TrailingMode.OFF:
        return current_sl

    elif config.trailing_mode == TrailingMode.MOVING_TARGET:
        if tp_hits >= 1:
            # n=1 → entry, n=2 → tp[0], n=3 → tp[1], n=4 → tp[2]
            target_idx = tp_hits - 2
            if target_idx < 0:
                new_sl = entry
            elif target_idx < len(plan.tp_prices):
                new_sl = plan.tp_prices[target_idx]
            else:
                # 마지막 TP 단계 도달 후엔 마지막 tp[-1] 유지
                new_sl = plan.tp_prices[-1]

    elif config.trailing_mode == TrailingMode.MOVING_2_TARGET:
        if tp_hits >= 2:
            target_idx = tp_hits - 3
            if target_idx < 0:
                new_sl = entry
            elif target_idx < len(plan.tp_prices):
                new_sl = plan.tp_prices[target_idx]
            else:
                new_sl = plan.tp_prices[-1]

    elif config.trailing_mode == TrailingMode.BREAKEVEN:
        if tp_hits >= config.trailing_trigger_target:
            new_sl = entry

    elif config.trailing_mode == TrailingMode.PERCENT_BELOW_TRIGGERS:
        if tp_hits >= config.trailing_trigger_target:
            pct = config.trailing_pct / 100.0
            if is_long:
                new_sl = highest_since_entry * (1.0 - pct)
            else:
                new_sl = lowest_since_entry * (1.0 + pct)

    elif config.trailing_mode == TrailingMode.PERCENT_BELOW_HIGHEST:
        pct = config.trailing_pct / 100.0
        if is_long:
            new_sl = highest_since_entry * (1.0 - pct)
        else:
            new_sl = lowest_since_entry * (1.0 + pct)

    # 단방향 보장: SL 은 유리한 방향으로만 이동
    if is_long:
        return max(current_sl, new_sl)
    return min(current_sl, new_sl)
