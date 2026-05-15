"""core.risk 단위 테스트 — 포지션 사이즈 / 리스크 플랜 / 트레일링 SL."""

from __future__ import annotations

import pytest

from aurora.core.risk import (
    DEFAULT_MIN_SEED_PCT,
    PositionSize,
    RiskPlan,
    TpSlConfig,
    TpSlMode,
    TrailingMode,
    build_risk_plan,
    calc_position_size,
    min_sl_pct_by_leverage,
    sl_pct_for_leverage,
    tp_pct_4_levels_for_leverage,
    tp_pct_range_for_leverage,
    update_trailing_sl,
)

# ============================================================
# sl_pct_for_leverage / tp_pct_range_for_leverage (이미 구현되어 있던 부분 회귀 검증)
# ============================================================


def test_sl_pct_low_leverage_boundary() -> None:
    """10x → 2.0%, 37x → 3.0% (보수 영역 끝)."""
    assert sl_pct_for_leverage(10) == pytest.approx(2.0)
    assert sl_pct_for_leverage(37) == pytest.approx(3.0)


def test_sl_pct_high_leverage_boundary() -> None:
    """38x → 3.04%, 50x → 4.0% (공격 영역)."""
    assert sl_pct_for_leverage(38) == pytest.approx(3.04)
    assert sl_pct_for_leverage(50) == pytest.approx(4.0)


def test_tp_range_low_leverage() -> None:
    """10x → (2.8%, 3.8%) — SL + 0.8 ~ SL + 1.8."""
    tp_min, tp_max = tp_pct_range_for_leverage(10)
    assert tp_min == pytest.approx(2.8)
    assert tp_max == pytest.approx(3.8)


def test_tp_range_high_leverage() -> None:
    """50x → (6.0%, 7.0%)."""
    tp_min, tp_max = tp_pct_range_for_leverage(50)
    assert tp_min == pytest.approx(6.0)
    assert tp_max == pytest.approx(7.0)


# ============================================================
# calc_position_size — risk-based / 풀시드 / 최소 시드 강제
# ============================================================


def test_position_size_returns_named_tuple() -> None:
    """반환값이 PositionSize NamedTuple 이고 셋 다 채워짐."""
    pos = calc_position_size(
        equity_usd=1000.0,
        leverage=10,
        sl_distance_pct=0.04,
        entry_price=50000.0,
        risk_pct=0.40,  # min_seed 강제 회피용 큰 값
    )
    assert isinstance(pos, PositionSize)
    assert pos.notional_usd > 0
    assert pos.margin_usd > 0
    assert pos.coin_amount > 0


def test_position_size_risk_based_formula() -> None:
    """risk-based: notional = (equity × risk_pct) / sl_distance_pct.

    시드 1000, risk 50% (=$500 위험), SL 4%, leverage 10:
        notional = 500 / 0.04 = 12500
        margin   = 12500 / 10 = 1250 (시드 초과... 강제 안 됨, 그대로 12500)

    근데 margin 1250 > equity 1000 도 OK (계산상). 실제 거래소가 거부할 거지만
    계산 함수 자체는 입력대로 산출.
    """
    pos = calc_position_size(
        equity_usd=1000.0,
        leverage=10,
        sl_distance_pct=0.04,
        entry_price=100.0,
        risk_pct=0.50,
    )
    # risk_amount = 1000 × 0.5 = 500
    # notional = 500 / 0.04 = 12500
    # margin = 12500 / 10 = 1250
    assert pos.notional_usd == pytest.approx(12500.0)
    assert pos.margin_usd == pytest.approx(1250.0)
    assert pos.coin_amount == pytest.approx(125.0)


def test_position_size_min_seed_enforced() -> None:
    """risk-based 결과가 min_seed_pct 미만이면 강제로 끌어올림.

    시드 1000, risk 1%, SL 4%, leverage 10:
        risk_amount = $10
        notional    = 250
        margin      = 25 (= 시드 2.5%) → < 시드 40% → 강제 $400 으로 끌어올림
        결과 margin = 400, notional = 4000
    """
    pos = calc_position_size(
        equity_usd=1000.0,
        leverage=10,
        sl_distance_pct=0.04,
        entry_price=100.0,
        risk_pct=0.01,
    )
    # 강제 적용 후
    assert pos.margin_usd == pytest.approx(400.0)  # 1000 × 0.40
    assert pos.notional_usd == pytest.approx(4000.0)  # 400 × 10


def test_position_size_min_seed_disabled_with_zero_threshold() -> None:
    """min_seed_pct=0 이면 강제 안 됨 (계산값 그대로)."""
    pos = calc_position_size(
        equity_usd=1000.0,
        leverage=10,
        sl_distance_pct=0.04,
        entry_price=100.0,
        risk_pct=0.01,
        min_seed_pct=0.0,
    )
    assert pos.margin_usd == pytest.approx(25.0)
    assert pos.notional_usd == pytest.approx(250.0)


def test_position_size_full_seed_mode() -> None:
    """풀시드: notional = equity × leverage, margin = equity."""
    pos = calc_position_size(
        equity_usd=1000.0,
        leverage=50,
        sl_distance_pct=0.04,  # 무시됨
        entry_price=100.0,
        full_seed=True,
    )
    assert pos.margin_usd == pytest.approx(1000.0)  # 시드 100%
    assert pos.notional_usd == pytest.approx(50000.0)
    assert pos.coin_amount == pytest.approx(500.0)


def test_position_size_full_seed_ignores_risk_pct() -> None:
    """풀시드 모드는 risk_pct 무시 (None 이어도 OK)."""
    pos = calc_position_size(
        equity_usd=1000.0,
        leverage=10,
        sl_distance_pct=0.04,
        entry_price=100.0,
        full_seed=True,
        risk_pct=None,
    )
    assert pos.margin_usd == pytest.approx(1000.0)


def test_position_size_default_min_seed_pct_is_40() -> None:
    """디폴트 min_seed_pct = 0.40 (사용자 정책)."""
    assert DEFAULT_MIN_SEED_PCT == pytest.approx(0.40)


def test_position_size_invalid_equity_raises() -> None:
    with pytest.raises(ValueError):
        calc_position_size(equity_usd=0, leverage=10, sl_distance_pct=0.04, entry_price=100.0, risk_pct=0.01)


def test_position_size_invalid_leverage_raises() -> None:
    with pytest.raises(ValueError):
        calc_position_size(equity_usd=1000, leverage=0, sl_distance_pct=0.04, entry_price=100.0, risk_pct=0.01)


def test_position_size_invalid_entry_price_raises() -> None:
    with pytest.raises(ValueError):
        calc_position_size(equity_usd=1000, leverage=10, sl_distance_pct=0.04, entry_price=0, risk_pct=0.01)


def test_position_size_risk_based_requires_risk_pct() -> None:
    """risk-based 모드는 risk_pct 필요 (None / 0 / 음수 모두 raise)."""
    with pytest.raises(ValueError):
        calc_position_size(equity_usd=1000, leverage=10, sl_distance_pct=0.04, entry_price=100.0, risk_pct=None)
    with pytest.raises(ValueError):
        calc_position_size(equity_usd=1000, leverage=10, sl_distance_pct=0.04, entry_price=100.0, risk_pct=-0.01)


def test_position_size_risk_based_requires_sl_distance() -> None:
    """risk-based 모드는 sl_distance_pct 양수 필요."""
    with pytest.raises(ValueError):
        calc_position_size(
            equity_usd=1000, leverage=10, sl_distance_pct=0,
            entry_price=100.0, risk_pct=0.01,
        )


def test_position_size_min_seed_pct_out_of_range_raises() -> None:
    with pytest.raises(ValueError):
        calc_position_size(
            equity_usd=1000, leverage=10, sl_distance_pct=0.04, entry_price=100.0,
            risk_pct=0.01, min_seed_pct=1.5,
        )


# ============================================================
# build_risk_plan — TpSlMode 3 모드 통합
# ============================================================


def test_build_risk_plan_fixed_pct_long() -> None:
    """FIXED_PCT 롱: SL 은 entry 아래, TP 는 entry 위.

    v0.1.13: fixed_sl_pct / fixed_tp_pcts 단위 = ROI %.
    가격 변동 % = ROI / leverage.
        sl=2.0 ROI / 10x → 가격 변동 0.2% → 100 × (1-0.002) = 99.8
        tp=[1,2,3,4] ROI / 10x → 가격 [0.1, 0.2, 0.3, 0.4]% → [100.1, 100.2, 100.3, 100.4]

    Note: v0.1.45 SL floor (0.3%) 는 ``apply_sl_floor=True`` (라이브 봇 한정)
    에서만 발동. 본 테스트는 default False 라 floor 영향 X.
    """
    cfg = TpSlConfig(
        mode=TpSlMode.FIXED_PCT,
        fixed_sl_pct=2.0,
        fixed_tp_pcts=[1.0, 2.0, 3.0, 4.0],
    )
    plan = build_risk_plan(
        entry_price=100.0,
        direction="long",
        leverage=10,
        equity_usd=1000.0,
        config=cfg,
        risk_pct=0.50,  # min_seed 강제 회피
    )
    assert plan.entry_price == 100.0
    assert plan.direction == "long"
    assert plan.leverage == 10
    assert plan.sl_price == pytest.approx(99.8)  # 100 × (1 - 0.002) = ROI 2% / 10x
    assert plan.tp_prices == pytest.approx([100.1, 100.2, 100.3, 100.4])
    assert plan.trailing_mode == cfg.trailing_mode
    assert isinstance(plan.position, PositionSize)


def test_build_risk_plan_fixed_pct_short() -> None:
    """FIXED_PCT 숏: SL 은 entry 위, TP 는 entry 아래 (v0.1.13 ROI 단위)."""
    cfg = TpSlConfig(
        mode=TpSlMode.FIXED_PCT,
        fixed_sl_pct=2.0,
        fixed_tp_pcts=[1.0, 2.0, 3.0, 4.0],
    )
    plan = build_risk_plan(
        entry_price=100.0, direction="SHORT",  # 대소문자 무관
        leverage=10, equity_usd=1000.0,
        config=cfg, risk_pct=0.50,
    )
    assert plan.direction == "short"
    assert plan.sl_price == pytest.approx(100.2)  # ROI 2% / 10x → 가격 0.2% 위
    assert plan.tp_prices == pytest.approx([99.9, 99.8, 99.7, 99.6])


def test_build_risk_plan_atr_mode() -> None:
    """ATR 모드: 거리 = ATR × multiplier."""
    cfg = TpSlConfig(
        mode=TpSlMode.ATR,
        atr_sl_multiplier=1.5,
        atr_tp_multipliers=[1.0, 2.0, 3.0, 4.0],
    )
    plan = build_risk_plan(
        entry_price=100.0, direction="long",
        leverage=10, equity_usd=1000.0,
        config=cfg, atr=2.0, risk_pct=0.50,
    )
    # SL = 100 - 2 × 1.5 = 97
    # TP = 100 + 2, +4, +6, +8 = 102, 104, 106, 108
    assert plan.sl_price == pytest.approx(97.0)
    assert plan.tp_prices == pytest.approx([102.0, 104.0, 106.0, 108.0])


def test_build_risk_plan_manual_mode() -> None:
    """MANUAL 모드: manual_sl_pct / manual_tp_pcts (ROI 단위, v0.1.13).

    Note: v0.1.45 SL floor 는 ``apply_sl_floor=True`` (라이브 한정) 에서만 발동.
    본 테스트는 default False 라 floor 영향 X.
    """
    cfg = TpSlConfig(
        mode=TpSlMode.MANUAL,
        manual_sl_pct=1.0,
        manual_tp_pcts=[0.5, 1.0, 1.5, 2.0],
    )
    plan = build_risk_plan(
        entry_price=100.0, direction="long",
        leverage=10, equity_usd=1000.0,
        config=cfg, risk_pct=0.50,
    )
    # ROI 1% / 10x = 가격 0.1%, ROI [0.5,1,1.5,2] / 10x = 가격 [0.05,0.1,0.15,0.2]%
    assert plan.sl_price == pytest.approx(99.9)
    assert plan.tp_prices == pytest.approx([100.05, 100.1, 100.15, 100.2])


def test_build_risk_plan_bb_structural_sl_short_v0_1_42() -> None:
    """v0.1.42: BB 신호 진입 시 SL = BB upper × (1 + buffer) override (short).

    기존 ROI 기반 SL 무시하고 BB 라인 기반 SL. 호가 noise (~0.08%) 위 안전.
    Why: 사용자 보고 v0.1.41 사고팔고 무한 루프 fix.
    """
    cfg = TpSlConfig(mode=TpSlMode.FIXED_PCT)
    plan = build_risk_plan(
        entry_price=80_500.0, direction="short",
        leverage=34, equity_usd=1000.0,
        config=cfg, risk_pct=0.01,
        bb_upper=80_650.0, bb_lower=80_200.0,
        bb_buffer_pct=0.003,
    )
    # SL = 80,650 × 1.003 = 80,891.95 (진입가 +391.95 USDT, ~0.49% 위)
    assert plan.sl_price == pytest.approx(80_650.0 * 1.003)
    # TP 는 기존 ROI 기반 그대로 (override X)
    assert len(plan.tp_prices) == 4
    assert plan.tp_prices[0] < 80_500.0  # short 의 TP 는 진입가 아래


def test_build_risk_plan_bb_structural_sl_long_v0_1_42() -> None:
    """v0.1.42: BB 신호 진입 시 SL = BB lower × (1 - buffer) override (long)."""
    cfg = TpSlConfig(mode=TpSlMode.FIXED_PCT)
    plan = build_risk_plan(
        entry_price=80_300.0, direction="long",
        leverage=34, equity_usd=1000.0,
        config=cfg, risk_pct=0.01,
        bb_upper=80_650.0, bb_lower=80_200.0,
        bb_buffer_pct=0.003,
    )
    # SL = 80,200 × 0.997 = 79,959.4 (진입가 -340.6 USDT, ~0.42% 아래)
    assert plan.sl_price == pytest.approx(80_200.0 * 0.997)
    assert plan.tp_prices[0] > 80_300.0  # long 의 TP 는 진입가 위


def test_build_risk_plan_structural_sl_price_override_v0_1_44() -> None:
    """v0.1.44: ``structural_sl_price`` 인자가 BB / ROI 기반 SL 보다 우선.

    Why: Ichimoku / EMA 등 strategy 측에서 진입 시점 SL 가격 직접 계산해 박는
    일반화 메커니즘. BB 인자 (bb_upper/bb_lower) 와 동시 주어지면 structural
    값이 우선 (더 명시적).
    """
    cfg = TpSlConfig(mode=TpSlMode.FIXED_PCT)

    # SHORT — Ichimoku cloud_lower × 1.006 같은 명시 SL 가격
    plan_s = build_risk_plan(
        entry_price=80_500.0, direction="short",
        leverage=34, equity_usd=1000.0,
        config=cfg, risk_pct=0.01,
        structural_sl_price=81_000.0,
    )
    assert plan_s.sl_price == pytest.approx(81_000.0)

    # LONG — Ichimoku cloud_upper × 0.994 같은 명시 SL 가격
    plan_l = build_risk_plan(
        entry_price=80_500.0, direction="long",
        leverage=34, equity_usd=1000.0,
        config=cfg, risk_pct=0.01,
        structural_sl_price=80_000.0,
    )
    assert plan_l.sl_price == pytest.approx(80_000.0)

    # 우선순위 — structural_sl_price 가 BB 인자보다 우선 (SHORT)
    plan_p = build_risk_plan(
        entry_price=80_500.0, direction="short",
        leverage=34, equity_usd=1000.0,
        config=cfg, risk_pct=0.01,
        bb_upper=80_650.0, bb_lower=80_200.0,  # BB 도 같이 주어졌지만
        structural_sl_price=81_500.0,            # structural 이 우선
    )
    assert plan_p.sl_price == pytest.approx(81_500.0)

    # 우선순위 — structural_sl_price 가 bb_lower 보다 우선 (LONG)
    # Why: v0.1.44 priority 정책 LONG 방향 검증 (SHORT 는 위에서 확인).
    # bb_lower × (1 - 0.003) = 79,958.4 vs structural = 80,000 → structural 채택.
    plan_l_combined = build_risk_plan(
        entry_price=80_500.0, direction="long",
        leverage=34, equity_usd=1000.0,
        config=cfg, risk_pct=0.01,
        bb_upper=80_650.0, bb_lower=80_200.0,  # BB 하단 제공되지만
        structural_sl_price=80_000.0,            # structural 이 우선
    )
    assert plan_l_combined.sl_price == pytest.approx(80_000.0)


def test_build_risk_plan_sl_noise_floor_v0_1_45() -> None:
    """v0.1.45: SL 폭이 0.3% 미만이면 강제 floor 적용 — 라이브 봇 호가 noise 안전망.

    Why: 사용자가 좁은 SL config (예: ROI 1% × lev 34 = 가격 0.029%) 박아도
    호가 떨림 (~0.08%) 에 SL hit 무한 사이클 차단. structural SL (BB/Ichimoku
    /EMA/MA cross) 은 이미 buffer >= 0.3% 라 floor 영향 X. ``apply_sl_floor=True``
    (라이브 봇 호출 한정) 에서만 발동 — 백테스트는 봉 단위 평가라 floor 무관.
    """
    cfg = TpSlConfig(mode=TpSlMode.MANUAL, manual_sl_pct=1.0)  # ROI 1%

    # lev 34 — 가격 변동 0.029% → floor 0.3% 강제 적용 (라이브 봇 호출)
    plan_long = build_risk_plan(
        entry_price=80_500.0, direction="long",
        leverage=34, equity_usd=1000.0,
        config=cfg, risk_pct=0.01,
        apply_sl_floor=True,
    )
    # SL = entry × (1 - 0.003) = 80,258.5 (floor 0.3% 강제)
    assert plan_long.sl_price == pytest.approx(80_500.0 * 0.997)

    plan_short = build_risk_plan(
        entry_price=80_500.0, direction="short",
        leverage=34, equity_usd=1000.0,
        config=cfg, risk_pct=0.01,
        apply_sl_floor=True,
    )
    # SL = entry × (1 + 0.003) = 80,741.5
    assert plan_short.sl_price == pytest.approx(80_500.0 * 1.003)


def test_build_risk_plan_sl_floor_default_off_for_backtest_v0_1_45() -> None:
    """v0.1.45: ``apply_sl_floor`` default False — 백테스트는 floor 무관.

    Why: 백테스트는 봉 단위 close 가격 평가라 호가 noise 가 의미 없음.
    BacktestEngine 의 build_risk_plan 호출은 그대로 두고 (영역 침범 X), default
    False 로 회귀 0 보장. BotInstance 만 명시 True 호출 (라이브 한정).
    """
    cfg = TpSlConfig(mode=TpSlMode.MANUAL, manual_sl_pct=1.0)
    plan = build_risk_plan(
        entry_price=80_500.0, direction="long",
        leverage=34, equity_usd=1000.0,
        config=cfg, risk_pct=0.01,
        # apply_sl_floor 안 박음 → default False
    )
    # floor 미발동 — ROI 1% (manual_sl_pct=1.0) / 34x = 가격 변동 1/34/100 = 0.0294%
    expected_sl = 80_500.0 * (1.0 - 1.0 / 34.0 / 100.0)
    assert plan.sl_price == pytest.approx(expected_sl, rel=1e-9)


def test_build_risk_plan_sl_floor_not_applied_to_wider_sl_v0_1_45() -> None:
    """v0.1.45: SL 폭이 floor 보다 넓으면 ``apply_sl_floor=True`` 여도 floor 영향 X."""
    cfg = TpSlConfig(mode=TpSlMode.MANUAL, manual_sl_pct=10.0)  # ROI 10%

    # lev 10 — 가격 변동 1.0% > floor 0.3% → floor 미적용
    plan = build_risk_plan(
        entry_price=100.0, direction="long",
        leverage=10, equity_usd=1000.0,
        config=cfg, risk_pct=0.50,
        apply_sl_floor=True,
    )
    assert plan.sl_price == pytest.approx(99.0)  # floor 무관 정상 산출


def test_build_risk_plan_atr_requires_atr_value() -> None:
    """ATR 모드인데 atr 인자 없으면 raise."""
    cfg = TpSlConfig(mode=TpSlMode.ATR)
    with pytest.raises(ValueError):
        build_risk_plan(
            entry_price=100.0, direction="long",
            leverage=10, equity_usd=1000.0,
            config=cfg,  # atr=None
        )


def test_build_risk_plan_invalid_direction_raises() -> None:
    cfg = TpSlConfig()
    with pytest.raises(ValueError):
        build_risk_plan(
            entry_price=100.0, direction="UPSIDE",
            leverage=10, equity_usd=1000.0,
            config=cfg,
        )


def test_build_risk_plan_full_seed() -> None:
    """풀시드 옵션이 plan 에 반영됨."""
    cfg = TpSlConfig(mode=TpSlMode.FIXED_PCT)
    plan = build_risk_plan(
        entry_price=100.0, direction="long",
        leverage=50, equity_usd=1000.0,
        config=cfg, full_seed=True,
    )
    assert plan.position.margin_usd == pytest.approx(1000.0)
    assert plan.position.notional_usd == pytest.approx(50000.0)


# ============================================================
# update_trailing_sl — 6모드 (OFF + 5)
# ============================================================


def _make_long_plan() -> RiskPlan:
    """롱 플랜 헬퍼: entry 100, tp [110, 120, 130, 140], sl 95."""
    return RiskPlan(
        entry_price=100.0,
        direction="long",
        leverage=10,
        position=PositionSize(notional_usd=1000.0, margin_usd=100.0, coin_amount=10.0),
        tp_prices=[110.0, 120.0, 130.0, 140.0],
        sl_price=95.0,
        trailing_mode=TrailingMode.OFF,
    )


def _make_short_plan() -> RiskPlan:
    """숏 플랜 헬퍼: entry 100, tp [90, 80, 70, 60], sl 105."""
    return RiskPlan(
        entry_price=100.0,
        direction="short",
        leverage=10,
        position=PositionSize(notional_usd=1000.0, margin_usd=100.0, coin_amount=10.0),
        tp_prices=[90.0, 80.0, 70.0, 60.0],
        sl_price=105.0,
        trailing_mode=TrailingMode.OFF,
    )


def test_trailing_off_keeps_sl() -> None:
    """OFF 모드는 SL 갱신 안 함."""
    plan = _make_long_plan()
    cfg = TpSlConfig(trailing_mode=TrailingMode.OFF)
    new_sl = update_trailing_sl(95.0, plan, cfg, tp_hits=2,
                                 highest_since_entry=125.0, lowest_since_entry=100.0)
    assert new_sl == 95.0


def test_trailing_moving_target_step_1_breakeven() -> None:
    """MOVING_TARGET, n=1 도달 → SL = entry."""
    plan = _make_long_plan()
    cfg = TpSlConfig(trailing_mode=TrailingMode.MOVING_TARGET)
    new_sl = update_trailing_sl(95.0, plan, cfg, tp_hits=1,
                                 highest_since_entry=110.0, lowest_since_entry=100.0)
    assert new_sl == pytest.approx(100.0)  # entry


def test_trailing_moving_target_step_2_first_tp() -> None:
    """MOVING_TARGET, n=2 도달 → SL = tp_prices[0]."""
    plan = _make_long_plan()
    cfg = TpSlConfig(trailing_mode=TrailingMode.MOVING_TARGET)
    new_sl = update_trailing_sl(100.0, plan, cfg, tp_hits=2,
                                 highest_since_entry=120.0, lowest_since_entry=100.0)
    assert new_sl == pytest.approx(110.0)


def test_trailing_moving_target_step_4_third_tp() -> None:
    """MOVING_TARGET, n=4 도달 → SL = tp_prices[2]."""
    plan = _make_long_plan()
    cfg = TpSlConfig(trailing_mode=TrailingMode.MOVING_TARGET)
    new_sl = update_trailing_sl(120.0, plan, cfg, tp_hits=4,
                                 highest_since_entry=140.0, lowest_since_entry=100.0)
    assert new_sl == pytest.approx(130.0)


def test_trailing_moving_2_target_step_2_breakeven() -> None:
    """MOVING_2_TARGET, n=2 도달 → SL = entry."""
    plan = _make_long_plan()
    cfg = TpSlConfig(trailing_mode=TrailingMode.MOVING_2_TARGET)
    new_sl = update_trailing_sl(95.0, plan, cfg, tp_hits=2,
                                 highest_since_entry=120.0, lowest_since_entry=100.0)
    assert new_sl == pytest.approx(100.0)


def test_trailing_moving_2_target_step_1_no_change() -> None:
    """MOVING_2_TARGET, n=1 (trigger 미만) → 그대로."""
    plan = _make_long_plan()
    cfg = TpSlConfig(trailing_mode=TrailingMode.MOVING_2_TARGET)
    new_sl = update_trailing_sl(95.0, plan, cfg, tp_hits=1,
                                 highest_since_entry=110.0, lowest_since_entry=100.0)
    assert new_sl == 95.0


def test_trailing_breakeven_at_trigger() -> None:
    """BREAKEVEN: trailing_trigger_target 도달 시 SL = entry."""
    plan = _make_long_plan()
    cfg = TpSlConfig(trailing_mode=TrailingMode.BREAKEVEN, trailing_trigger_target=2)
    # tp_hits=1 (미달) → 그대로
    assert update_trailing_sl(95.0, plan, cfg, tp_hits=1,
                                highest_since_entry=110.0, lowest_since_entry=100.0) == 95.0
    # tp_hits=2 (도달) → entry
    assert update_trailing_sl(95.0, plan, cfg, tp_hits=2,
                                highest_since_entry=120.0, lowest_since_entry=100.0) == pytest.approx(100.0)


def test_trailing_percent_below_triggers_long() -> None:
    """PERCENT_BELOW_TRIGGERS 롱: trigger 후 highest 대비 trailing_pct% 아래."""
    plan = _make_long_plan()
    cfg = TpSlConfig(
        trailing_mode=TrailingMode.PERCENT_BELOW_TRIGGERS,
        trailing_trigger_target=2,
        trailing_pct=5.0,  # 5%
    )
    # trigger 미달 → 그대로
    assert update_trailing_sl(95.0, plan, cfg, tp_hits=1,
                                highest_since_entry=115.0, lowest_since_entry=100.0) == 95.0
    # trigger 도달 → highest × (1 - 0.05) = 130 × 0.95 = 123.5
    new_sl = update_trailing_sl(95.0, plan, cfg, tp_hits=2,
                                 highest_since_entry=130.0, lowest_since_entry=100.0)
    assert new_sl == pytest.approx(123.5)


def test_trailing_percent_below_highest_long() -> None:
    """PERCENT_BELOW_HIGHEST 롱: trigger 없이 진입 직후부터."""
    plan = _make_long_plan()
    cfg = TpSlConfig(
        trailing_mode=TrailingMode.PERCENT_BELOW_HIGHEST,
        trailing_pct=10.0,
    )
    # tp_hits=0 도 적용됨 (trigger 무관)
    new_sl = update_trailing_sl(95.0, plan, cfg, tp_hits=0,
                                 highest_since_entry=120.0, lowest_since_entry=100.0)
    # 120 × 0.9 = 108
    assert new_sl == pytest.approx(108.0)


def test_trailing_percent_below_highest_short() -> None:
    """PERCENT_BELOW_HIGHEST 숏: lowest × (1 + pct/100)."""
    plan = _make_short_plan()
    cfg = TpSlConfig(
        trailing_mode=TrailingMode.PERCENT_BELOW_HIGHEST,
        trailing_pct=10.0,
    )
    # lowest 80 → 80 × 1.10 = 88
    new_sl = update_trailing_sl(105.0, plan, cfg, tp_hits=0,
                                 highest_since_entry=100.0, lowest_since_entry=80.0)
    assert new_sl == pytest.approx(88.0)


def test_trailing_sl_unidirectional_long() -> None:
    """롱 SL 은 위로만 이동 — 새 SL 이 현재보다 작으면 현재 유지."""
    plan = _make_long_plan()
    cfg = TpSlConfig(
        trailing_mode=TrailingMode.PERCENT_BELOW_HIGHEST,
        trailing_pct=10.0,
    )
    # current_sl=115 (이미 상향), highest=120 → 새 SL = 108
    # 108 < 115 → 115 유지
    new_sl = update_trailing_sl(115.0, plan, cfg, tp_hits=0,
                                 highest_since_entry=120.0, lowest_since_entry=100.0)
    assert new_sl == 115.0


def test_trailing_sl_unidirectional_short() -> None:
    """숏 SL 은 아래로만 이동."""
    plan = _make_short_plan()
    cfg = TpSlConfig(
        trailing_mode=TrailingMode.PERCENT_BELOW_HIGHEST,
        trailing_pct=10.0,
    )
    # current_sl=90 (이미 하향), lowest=85 → 새 SL = 85 × 1.1 = 93.5
    # 93.5 > 90 → 90 유지
    new_sl = update_trailing_sl(90.0, plan, cfg, tp_hits=0,
                                 highest_since_entry=100.0, lowest_since_entry=85.0)
    assert new_sl == 90.0


# ============================================================
# min_sl_pct_by_leverage — deprecated alias 회귀 검증
# ============================================================


def test_min_sl_pct_by_leverage_matches_sl_pct_low() -> None:
    """10x 기준 sl_pct_for_leverage 와 동일한 값 반환."""
    assert min_sl_pct_by_leverage(10) == pytest.approx(sl_pct_for_leverage(10))


def test_min_sl_pct_by_leverage_matches_sl_pct_high() -> None:
    """50x 기준 sl_pct_for_leverage 와 동일한 값 반환."""
    assert min_sl_pct_by_leverage(50) == pytest.approx(sl_pct_for_leverage(50))


def test_min_sl_pct_by_leverage_returns_float() -> None:
    """반환 타입이 float."""
    assert isinstance(min_sl_pct_by_leverage(20), float)


# ============================================================
# tp_pct_4_levels_for_leverage
# ============================================================


def test_tp_pct_4_levels_length() -> None:
    """항상 4단계 반환."""
    for lev in (10, 20, 37, 38, 50):
        assert len(tp_pct_4_levels_for_leverage(lev)) == 4


def test_tp_pct_4_levels_ascending() -> None:
    """TP1 < TP2 < TP3 < TP4 오름차순."""
    for lev in (10, 25, 37, 38, 50):
        levels = tp_pct_4_levels_for_leverage(lev)
        assert levels[0] < levels[1] < levels[2] < levels[3]


def test_tp_pct_4_levels_10x_matches_range() -> None:
    """10x: 레인지 min/max 가 TP1/TP4."""
    tp_min, tp_max = tp_pct_range_for_leverage(10)
    levels = tp_pct_4_levels_for_leverage(10)
    assert abs(levels[0] - tp_min) < 1e-9
    assert abs(levels[-1] - tp_max) < 1e-9


def test_tp_pct_4_levels_50x_matches_range() -> None:
    """50x: 레인지 min/max 가 TP1/TP4."""
    tp_min, tp_max = tp_pct_range_for_leverage(50)
    levels = tp_pct_4_levels_for_leverage(50)
    assert abs(levels[0] - tp_min) < 1e-9
    assert abs(levels[-1] - tp_max) < 1e-9


def test_tp_pct_4_levels_equal_step() -> None:
    """4단계 간격이 동일 (3등분)."""
    for lev in (10, 37, 50):
        levels = tp_pct_4_levels_for_leverage(lev)
        step = (levels[-1] - levels[0]) / 3
        assert abs(levels[1] - (levels[0] + step)) < 1e-9
        assert abs(levels[2] - (levels[0] + 2 * step)) < 1e-9


# ============================================================
# build_risk_plan — 에러 경로 (lines 344, 374, 402)
# ============================================================


def test_build_risk_plan_entry_price_zero_raises() -> None:
    """entry_price <= 0 → ValueError (line 344)."""
    with pytest.raises(ValueError, match="entry_price"):
        build_risk_plan(
            config=TpSlConfig(),
            direction="long",
            entry_price=0.0,
            leverage=10,
            equity_usd=1000.0,
        )


def test_build_risk_plan_unknown_mode_raises() -> None:
    """알 수 없는 TpSlMode → ValueError (line 374).

    dataclass(slots=True) 는 런타임 타입 강제 X → 직접 할당으로 else 분기 유도.
    """
    config = TpSlConfig()
    config.mode = "not_a_mode"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="unknown TpSlMode"):
        build_risk_plan(
            config=config,
            direction="long",
            entry_price=100.0,
            leverage=10,
            equity_usd=1000.0,
        )


def test_build_risk_plan_sl_at_entry_uses_fallback_distance() -> None:
    """structural_sl_price == entry_price → sl_distance_pct = 0 → fallback (line 402)."""
    plan = build_risk_plan(
        config=TpSlConfig(),
        direction="long",
        entry_price=100.0,
        leverage=10,
        equity_usd=1000.0,
        structural_sl_price=100.0,  # entry 와 같음 → sl_distance_pct = 0
    )
    # fallback 적용 후 position size 계산이 정상 완료돼야 함
    assert plan.sl_price == pytest.approx(100.0)
    assert plan.position.coin_amount > 0


# ============================================================
# update_trailing_sl — 미커버 경로 (lines 506, 513-516, 528)
# ============================================================


def test_trailing_moving_target_beyond_last_tp_uses_last() -> None:
    """MOVING_TARGET: target_idx >= len(tp_prices) → tp[-1] 고정 (line 506)."""
    plan = _make_long_plan()  # tp=[110, 120, 130, 140]
    cfg = TpSlConfig(trailing_mode=TrailingMode.MOVING_TARGET)
    # tp_hits=6 → target_idx=4 ≥ len(4) → tp[-1]=140
    new_sl = update_trailing_sl(
        95.0, plan, cfg, tp_hits=6,
        highest_since_entry=150.0, lowest_since_entry=100.0,
    )
    assert new_sl == pytest.approx(140.0)


def test_trailing_moving_2_target_step_3_first_tp() -> None:
    """MOVING_2_TARGET: tp_hits=3 → target_idx=0 → tp[0] (lines 513-514)."""
    plan = _make_long_plan()  # tp=[110, 120, 130, 140]
    cfg = TpSlConfig(trailing_mode=TrailingMode.MOVING_2_TARGET)
    new_sl = update_trailing_sl(
        95.0, plan, cfg, tp_hits=3,
        highest_since_entry=130.0, lowest_since_entry=100.0,
    )
    assert new_sl == pytest.approx(110.0)


def test_trailing_moving_2_target_beyond_last_tp_uses_last() -> None:
    """MOVING_2_TARGET: target_idx >= len(tp_prices) → tp[-1] 고정 (lines 515-516)."""
    plan = _make_long_plan()  # tp=[110, 120, 130, 140]
    cfg = TpSlConfig(trailing_mode=TrailingMode.MOVING_2_TARGET)
    # tp_hits=7 → target_idx=4 ≥ len(4) → tp[-1]=140
    new_sl = update_trailing_sl(
        95.0, plan, cfg, tp_hits=7,
        highest_since_entry=150.0, lowest_since_entry=100.0,
    )
    assert new_sl == pytest.approx(140.0)


def test_trailing_percent_below_triggers_short() -> None:
    """PERCENT_BELOW_TRIGGERS 숏: trigger 후 lowest × (1 + pct/100) (line 528)."""
    plan = _make_short_plan()  # entry 100, sl 105
    cfg = TpSlConfig(
        trailing_mode=TrailingMode.PERCENT_BELOW_TRIGGERS,
        trailing_trigger_target=2,
        trailing_pct=5.0,
    )
    # trigger 도달 → lowest × 1.05 = 70 × 1.05 = 73.5
    new_sl = update_trailing_sl(
        105.0, plan, cfg, tp_hits=2,
        highest_since_entry=100.0, lowest_since_entry=70.0,
    )
    assert new_sl == pytest.approx(73.5)
