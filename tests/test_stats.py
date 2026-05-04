"""stats.py 단위 테스트 — 16 케이스 (DESIGN.md §5.2 + §11 D-1 spec 정합).

mock 0 — 결정론적 합성 입력만 사용. 외부 네트워크 X.

TradeRecord / BacktestStats dataclass + compute_session_stats / compute_r_multiples /
compute_drawdown 함수 3개 검증. 0 거래 / sl_distance=0 / 음수 시드 등 가드 회귀 보호.

담당: ChoYoon
"""

from __future__ import annotations

import logging

import pytest

from aurora.backtest.stats import (
    BacktestStats,
    TradeRecord,
    compute_drawdown,
    compute_r_multiples,
    compute_session_stats,
)
from aurora.core.risk import PositionSize, RiskPlan, TrailingMode

# ============================================================
# 헬퍼 — TradeRecord / RiskPlan 인스턴스 fast 생성
# ============================================================


def _make_trade(
    direction: str = "long",
    pnl: float = 0.05,
    r_multiple: float = 1.0,
    entry_price: float = 100.0,
    exit_price: float | None = None,
) -> TradeRecord:
    """헬퍼 — TradeRecord 인스턴스 (디폴트값으로 10 필드 자동 채움)."""
    return TradeRecord(
        entry_price=entry_price,
        entry_ts=0,
        exit_price=exit_price if exit_price is not None else entry_price * (1 + pnl),
        exit_ts=900_000,
        direction=direction,    # type: ignore[arg-type]
        leverage=10.0,
        pnl=pnl,
        r_multiple=r_multiple,
        duration_minutes=15,
    )


def _make_plan(entry: float = 100.0, sl: float = 98.0) -> RiskPlan:
    """헬퍼 — RiskPlan 인스턴스 (compute_r_multiples sl_distance 산출용)."""
    return RiskPlan(
        entry_price=entry,
        direction="long",
        leverage=10,
        position=PositionSize(notional_usd=1000.0, margin_usd=100.0, coin_amount=10.0),
        tp_prices=[entry * 1.01, entry * 1.02, entry * 1.03, entry * 1.04],
        sl_price=sl,
        trailing_mode=TrailingMode.OFF,
    )


# ============================================================
# TradeRecord — dataclass 필드 (2)
# ============================================================


def test_trade_record_fields():
    """TradeRecord 10 필드 + Direction Literal 정합 검증."""
    tr = TradeRecord(
        entry_price=100.0, entry_ts=1_700_000_000_000,
        exit_price=102.0, exit_ts=1_700_000_900_000,
        direction="long",
        leverage=10.0,
        pnl=0.18,
        r_multiple=2.0,
        duration_minutes=15,
        regime="TREND_UP",
    )
    assert tr.entry_price == 100.0
    assert tr.exit_price == 102.0
    assert tr.entry_ts == 1_700_000_000_000
    assert tr.exit_ts == 1_700_000_900_000
    assert tr.direction == "long"
    assert tr.leverage == 10.0
    assert tr.pnl == 0.18
    assert tr.r_multiple == 2.0
    assert tr.duration_minutes == 15
    assert tr.regime == "TREND_UP"


def test_trade_record_regime_default_none():
    """TradeRecord regime 디폴트 None — Stage 1B 단순화 단계 metadata 누락 허용."""
    tr = _make_trade()
    assert tr.regime is None


# ============================================================
# BacktestStats — dataclass 필드 + D-1 (2)
# ============================================================


def test_backtest_stats_fields():
    """BacktestStats 8 필수 + effective_r_pct 명시 — 모든 필드 정확히 set."""
    stats = BacktestStats(
        total_trades=10, win_rate=0.6, mdd=0.05, sharpe=1.2, expectancy=0.5,
        equity_curve=[1.0, 1.05, 1.10],
        total_pnl=0.10, fee_paid=0.004,
        effective_r_pct=[1.0, 1.0, -0.5],
    )
    assert stats.total_trades == 10
    assert stats.win_rate == 0.6
    assert stats.mdd == 0.05
    assert stats.sharpe == 1.2
    assert stats.expectancy == 0.5
    assert stats.equity_curve == [1.0, 1.05, 1.10]
    assert stats.total_pnl == 0.10
    assert stats.fee_paid == 0.004
    assert stats.effective_r_pct == [1.0, 1.0, -0.5]


def test_backtest_stats_effective_r_pct_default_none():
    """BacktestStats effective_r_pct 디폴트 None — D-1 단순화 (실효 R 비율 후속 PR)."""
    stats = BacktestStats(
        total_trades=0, win_rate=0.0, mdd=0.0, sharpe=0.0, expectancy=0.0,
        equity_curve=[], total_pnl=0.0, fee_paid=0.0,
    )
    assert stats.effective_r_pct is None


# ============================================================
# compute_session_stats — 정상 / 가드 / 산식 (5)
# ============================================================


def test_compute_session_stats_normal_3_trades():
    """정상 3 trades (long +5% / long +3% / short -2%) — 모든 필드 산식 정확."""
    trades = [
        _make_trade("long",  0.05, 1.5),
        _make_trade("long",  0.03, 1.0),
        _make_trade("short", -0.02, -0.5),
    ]
    stats = compute_session_stats(trades)
    assert stats.total_trades == 3
    assert stats.win_rate == pytest.approx(2 / 3)
    assert stats.expectancy == pytest.approx((1.5 + 1.0 - 0.5) / 3)
    assert stats.fee_paid == 0.0          # D-15 placeholder
    assert stats.effective_r_pct is None  # D-1 단순화

    # equity_curve 복리 — 1.0 × 1.05 × 1.03 × 0.98
    expected_end = 1.0 * 1.05 * 1.03 * 0.98
    assert stats.equity_curve[0] == 1.0
    assert stats.equity_curve[-1] == pytest.approx(expected_end)
    assert stats.total_pnl == pytest.approx(expected_end - 1.0)

    # mdd — peak 1.0815 → trough 1.05987
    peak = 1.0 * 1.05 * 1.03
    trough = peak * 0.98
    assert stats.mdd == pytest.approx((peak - trough) / peak)


def test_compute_session_stats_zero_trades_warns_caplog(caplog):
    """0 거래 세션 — 빈 BacktestStats + WARNING 로그 (D-11 가시성 회귀 보호)."""
    caplog.set_level(logging.WARNING, logger="aurora.backtest.stats")
    stats = compute_session_stats([])
    assert stats.total_trades == 0
    assert stats.win_rate == 0.0
    assert stats.mdd == 0.0
    assert stats.equity_curve == []
    assert stats.total_pnl == 0.0
    assert stats.fee_paid == 0.0
    assert stats.effective_r_pct is None
    assert len(caplog.records) == 1
    assert "0 거래 세션" in caplog.records[0].message


def test_compute_session_stats_single_trade_sharpe_zero():
    """단일 trade — N<2 Sharpe 산출 불가 → 0.0 fallback."""
    stats = compute_session_stats([_make_trade("long", 0.05, 1.5)])
    assert stats.total_trades == 1
    assert stats.sharpe == 0.0    # N<2 가드


def test_compute_session_stats_all_negative_pnl():
    """모든 PnL 음수 — 방향성 검증: win_rate=0, expectancy<0, mdd>0, total_pnl<0."""
    trades = [
        _make_trade("long", -0.02, -1.0),
        _make_trade("long", -0.03, -1.0),
        _make_trade("long", -0.01, -0.5),
    ]
    stats = compute_session_stats(trades)
    assert stats.win_rate == 0.0
    assert stats.expectancy < 0
    assert stats.mdd > 0
    assert stats.total_pnl < 0


def test_compute_session_stats_equity_curve_compound():
    """equity_curve 복리 누적 산식 — curve[i] = curve[i-1] × (1 + pnl_i) (D-17)."""
    trades = [
        _make_trade("long",  0.10, 1.0),
        _make_trade("long",  0.10, 1.0),
        _make_trade("long", -0.05, -0.5),
    ]
    stats = compute_session_stats(trades)
    # 복리: [1.0, 1.10, 1.21, 1.1495]
    assert stats.equity_curve[0] == 1.0
    assert stats.equity_curve[1] == pytest.approx(1.10)
    assert stats.equity_curve[2] == pytest.approx(1.21)
    assert stats.equity_curve[3] == pytest.approx(1.1495)
    assert len(stats.equity_curve) == 4    # 시작 1.0 + 3 trades


# ============================================================
# compute_r_multiples — 정상 + 가드 (4)
# ============================================================


def test_compute_r_multiples_long_profit():
    """long 양수 R — exit > entry, sl < entry → R = (exit - entry) / sl_distance × +1."""
    # entry=100, sl=98 → sl_distance=2 / exit=104 → r = 4/2 × +1 = 2.0
    trade = _make_trade("long", entry_price=100.0, exit_price=104.0)
    plan = _make_plan(entry=100.0, sl=98.0)
    r_mults = compute_r_multiples([trade], [plan])
    assert r_mults[0] == pytest.approx(2.0)


def test_compute_r_multiples_short_sign_inversion():
    """short 부호 반전 — 가격 하락이 short 양수 R, 가격 상승이 short 음수 R."""
    plan = _make_plan(entry=200.0, sl=204.0)    # sl_distance = 4

    # short profit — exit=196 → r = (196-200)/4 × -1 = +1.0
    profit = _make_trade("short", entry_price=200.0, exit_price=196.0)
    assert compute_r_multiples([profit], [plan])[0] == pytest.approx(1.0)

    # short loss — exit=204 → r = (204-200)/4 × -1 = -1.0
    loss = _make_trade("short", entry_price=200.0, exit_price=204.0)
    assert compute_r_multiples([loss], [plan])[0] == pytest.approx(-1.0)


def test_compute_r_multiples_sl_distance_zero_raises():
    """D-14 회귀 보호 — sl_distance=0 (entry==sl) ValueError raise."""
    trade = _make_trade("long", entry_price=100.0, exit_price=104.0)
    plan = _make_plan(entry=100.0, sl=100.0)    # sl_distance = 0
    with pytest.raises(ValueError, match="sl_distance == 0"):
        compute_r_multiples([trade], [plan])


def test_compute_r_multiples_length_mismatch_raises():
    """trades / plans 길이 불일치 — ValueError raise."""
    trades = [_make_trade(), _make_trade()]
    plans = [_make_plan()]
    with pytest.raises(ValueError, match="길이 불일치"):
        compute_r_multiples(trades, plans)


# ============================================================
# compute_drawdown — 정상 / 빈+단조 / 음수 시드 (3)
# ============================================================


def test_compute_drawdown_normal():
    """정상 곡선 — peak-to-trough max % 산출."""
    # [1.0, 1.20, 1.10, 1.30, 0.90] → peak=1.30, trough=0.90 → mdd=(1.30-0.90)/1.30
    assert compute_drawdown([1.0, 1.20, 1.10, 1.30, 0.90]) == pytest.approx(
        (1.30 - 0.90) / 1.30,
    )


def test_compute_drawdown_empty_and_monotonic_zero():
    """빈 곡선 + 단조 증가 — 둘 다 mdd=0.0 (가드 + drawdown 부재)."""
    assert compute_drawdown([]) == 0.0
    assert compute_drawdown([1.0, 1.05, 1.10, 1.15, 1.20]) == 0.0


def test_compute_drawdown_negative_seed_mdd_above_one():
    """D-18 회귀 보호 — 음수 시드 도달 시 mdd>1.0 가능 (비정상 시뮬 신호).

    docstring 정정: "0.0~1.0" → "0.0 이상 (음수 시드 도달 시 1.0 초과 가능)".
    예: peak=1.0 → 전손 -0.5 도달 시 mdd = (1.0 - (-0.5)) / 1.0 = 1.5.

    추가: 음수 시드 시작 (peak<=0) 은 ZeroDivisionError 가드 발동 → mdd=0.0.
    """
    mdd = compute_drawdown([1.0, 0.5, 0.0, -0.5])
    assert mdd == pytest.approx(1.5)
    assert mdd > 1.0    # 회귀 보호 — 1.0 초과 자연 가능

    # 음수 시드 시작 — peak<=0 가드 발동
    assert compute_drawdown([-0.5, -0.2, -1.0]) == 0.0
