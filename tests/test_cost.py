"""cost.py 단위 테스트 — 13 케이스 (DESIGN.md §3.2 + §5.2 spec 정합).

mock 0 — 결정론적 합성 입력만 사용. 외부 네트워크 X.

담당: ChoYoon
"""

from __future__ import annotations

import logging

import pytest

from aurora.backtest.cost import (
    SLIP_NORMAL_PCT,
    SLIP_VOLATILE_PCT,
    TAKER_FEE_PCT,
    VOLATILE_THRESHOLD,
    apply_costs,
    apply_slippage,
    slip_pct,
)

# ============================================================
# slip_pct — 봉 변동성 분기 + close 비정상 fallback (5)
# ============================================================


def test_slip_pct_normal_candle():
    """변동성 임계값 미만 봉 — SLIP_NORMAL_PCT 반환."""
    # range = (100.2 - 99.8) / 100 = 0.004 = 0.4% < VOLATILE_THRESHOLD
    assert slip_pct(100.2, 99.8, 100.0) == SLIP_NORMAL_PCT


def test_slip_pct_volatile_candle():
    """변동성 임계값 초과 봉 — SLIP_VOLATILE_PCT 반환."""
    # range = (101 - 99) / 100 = 0.02 = 2% > VOLATILE_THRESHOLD
    assert slip_pct(101.0, 99.0, 100.0) == SLIP_VOLATILE_PCT


def test_slip_pct_boundary_strict_gt():
    """경계 케이스 — range == VOLATILE_THRESHOLD 정확히 같으면 NORMAL.

    DESIGN.md §3.2 분기 조건: ``rng > VOLATILE_THRESHOLD`` (strict >).
    Equality 시 변동성 봉 X — 회귀 보호 케이스.
    """
    # 입력 setup 검증 — VOLATILE_THRESHOLD 값 변경 시 자연 catch
    assert (100.25 - 99.75) / 100.0 == VOLATILE_THRESHOLD
    assert slip_pct(100.25, 99.75, 100.0) == SLIP_NORMAL_PCT


def test_slip_pct_close_zero_fallback(caplog):
    """close == 0 비정상 봉 — 보수적 NORMAL fallback + WARNING 로그 발생.

    D-11 caveat 회귀 보호: silent fallback 가시성 확보 (replay_engine L536 패턴 + 로그).
    """
    caplog.set_level(logging.WARNING, logger="aurora.backtest.cost")
    assert slip_pct(100.0, 99.0, 0.0) == SLIP_NORMAL_PCT
    assert len(caplog.records) == 1
    assert "close 비정상" in caplog.records[0].message


def test_slip_pct_close_negative_fallback(caplog):
    """close < 0 (방어) — ``<= 0`` 가드 자연 통과해 NORMAL fallback + WARNING 로그.

    D-11 caveat 회귀 보호: 음수 close 도 가시성 확보.
    """
    caplog.set_level(logging.WARNING, logger="aurora.backtest.cost")
    assert slip_pct(100.0, 99.0, -1.0) == SLIP_NORMAL_PCT
    assert len(caplog.records) == 1
    assert "close 비정상" in caplog.records[0].message


# ============================================================
# apply_slippage — 4 조합 (LONG/SHORT × entry/exit) + slip=0 (2)
# ============================================================


def test_apply_slippage_four_combinations():
    """4 조합 모두 unfavorable 방향 — LONG entry / SHORT exit ↑, LONG exit / SHORT entry ↓."""
    base = 100.0
    slip = 0.001
    # (direction, side, expected_price_after_slippage)
    cases = [
        ("LONG",  "entry", base * (1 + slip)),  # 사야 함 → 비싸게
        ("LONG",  "exit",  base * (1 - slip)),  # 팔아야 함 → 싸게
        ("SHORT", "entry", base * (1 - slip)),  # 빌려 팔아야 함 → 싸게
        ("SHORT", "exit",  base * (1 + slip)),  # 환매 → 비싸게
    ]
    for direction, side, expected in cases:
        result = apply_slippage(base, direction, side, slip)  # type: ignore[arg-type]
        assert result == pytest.approx(expected), (
            f"{direction} {side}: got {result}, expected {expected}"
        )


def test_apply_slippage_zero_slip_unchanged():
    """slip=0 경계 — 가격 변화 X (4 조합 모두)."""
    base = 100.0
    for direction in ("LONG", "SHORT"):
        for side in ("entry", "exit"):
            result = apply_slippage(base, direction, side, 0.0)  # type: ignore[arg-type]
            assert result == base, f"{direction} {side} slip=0: got {result}"


# ============================================================
# apply_costs — PnL 부호·디폴트·override·경계 (6)
# ============================================================


def test_apply_costs_positive_pnl():
    """양수 raw PnL — lev_pnl 양수, fee_loss 항상 양수."""
    # notional = 0.5 × 10 = 5
    # fee_loss = 2 × 0.0004 × 5 = 0.004
    # lev_pnl  = 0.02 × 5 - 0.004 = 0.096
    lev_pnl, fee_loss = apply_costs(raw_pnl_pct=0.02, size_pct=0.5, leverage=10)
    assert lev_pnl == pytest.approx(0.096)
    assert fee_loss == pytest.approx(0.004)


def test_apply_costs_negative_pnl():
    """음수 raw PnL — lev_pnl 음수 (수수료 추가 차감)."""
    # notional = 1.0 × 20 = 20
    # fee_loss = 2 × 0.0004 × 20 = 0.016
    # lev_pnl  = -0.01 × 20 - 0.016 = -0.216
    lev_pnl, fee_loss = apply_costs(raw_pnl_pct=-0.01, size_pct=1.0, leverage=20)
    assert lev_pnl == pytest.approx(-0.216)
    assert fee_loss == pytest.approx(0.016)


def test_apply_costs_fee_loss_formula():
    """fee_loss = 2 × fee_pct × (size × leverage) — 다양한 size/lev 조합 검증."""
    # (size_pct, leverage, expected_fee_loss_with_default_fee)
    cases = [
        (0.25, 10, 2 * TAKER_FEE_PCT * 0.25 * 10),
        (0.50, 25, 2 * TAKER_FEE_PCT * 0.50 * 25),
        (1.00, 50, 2 * TAKER_FEE_PCT * 1.00 * 50),
    ]
    for size_pct, leverage, expected_fee in cases:
        _, fee_loss = apply_costs(
            raw_pnl_pct=0.0, size_pct=size_pct, leverage=leverage,
        )
        assert fee_loss == pytest.approx(expected_fee), (
            f"size={size_pct} lev={leverage}: got {fee_loss}, expected {expected_fee}"
        )


def test_apply_costs_default_fee_pct_binds_to_taker():
    """fee_pct 디폴트 = TAKER_FEE_PCT — 디폴트 vs 명시 호출 결과 동일.

    Group 2 검토 답 #5 검증: import 시점 default bind, 양 호출 동일 결과.
    """
    args = {"raw_pnl_pct": 0.01, "size_pct": 0.5, "leverage": 10}
    default_result = apply_costs(**args)
    explicit_result = apply_costs(**args, fee_pct=TAKER_FEE_PCT)
    assert default_result == explicit_result


def test_apply_costs_fee_pct_override():
    """#B-3 후속 시나리오 — 거래소 결정 후 fee_pct 명시 override 자연 반영.

    DESIGN.md §3.2: TAKER_FEE_PCT 는 Binance 기준 출발값. 거래소 변경 시
    호출 시점 override 만으로 손익 모델 갱신 (cost.py 수정 X).
    """
    # 가상 거래소 fee = 0.06% 시나리오
    custom_fee = 0.0006
    # notional = 0.5 × 10 = 5
    # fee_loss = 2 × 0.0006 × 5 = 0.006
    # lev_pnl  = 0.02 × 5 - 0.006 = 0.094
    lev_pnl, fee_loss = apply_costs(
        raw_pnl_pct=0.02, size_pct=0.5, leverage=10, fee_pct=custom_fee,
    )
    assert fee_loss == pytest.approx(0.006)
    assert lev_pnl == pytest.approx(0.094)


def test_apply_costs_zero_raw_pnl():
    """raw=0 경계 — 무손익이라도 fee_loss 만큼 lev_pnl 차감."""
    lev_pnl, fee_loss = apply_costs(raw_pnl_pct=0.0, size_pct=0.5, leverage=10)
    assert lev_pnl == pytest.approx(-fee_loss)
    assert fee_loss > 0    # 수수료는 항상 양수 (방향성 검증)
