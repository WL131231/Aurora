"""engine.py 단위 테스트 — 22 함수 / 23 collected (DESIGN.md §6.2 + §11 D-1~D-26 정합).

mock 0 — 결정론적 합성 입력만 사용. 외부 네트워크 X.

BacktestConfig + Position dataclass + BacktestEngine ``__init__`` / ``step()`` /
``run()`` + 헬퍼 9 개 (``_to_record_direction`` / ``_open`` / ``_close`` /
``_partial_close`` / ``_check_exits`` / ``_update_peak`` / ``_check_max_dd`` /
``_tick_pause`` / ``_force_close_at_end``) 단위 회귀. 0 거래 / sl_distance=0 /
clamp / 가드 분기 모두 caplog 또는 직접 assertion 으로 검증.

sanity 22 inline (단계 1·2·3) → 정식 pytest 변환. DESIGN §8 본문 매핑.

담당: ChoYoon
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest

from aurora.backtest.engine import BacktestConfig, BacktestEngine
from aurora.backtest.stats import TradeRecord
from aurora.core.risk import (
    PositionSize,
    RiskPlan,
    TpSlConfig,
    TpSlMode,
    TrailingMode,
    build_risk_plan,
)
from aurora.core.strategy import StrategyConfig

# ============================================================
# 헬퍼 — fast 인스턴스 생성 (test_stats.py / test_replay.py 패턴 정합)
# ============================================================


def _make_synthetic_1m_df(
    start_ms: int = 1_700_000_000_000, n: int = 5, price: float = 100.0,
    prices: list[float] | None = None,
) -> pd.DataFrame:
    """평탄 OHLCV 1m DataFrame — 신호 비유발 (EMA 터치 X 보장) 또는 가격
    시퀀스 engineering (REVERSE 분기 발동 등).

    DatetimeIndex (ms epoch 기반) — ``BacktestEngine.run()`` 입력 정합.

    Args:
        start_ms: 첫 1m 봉 ts (ms epoch). default 1_700_000_000_000 = 2023-11-14
            22:13:20 UTC (1H bucket open=22:00 → 첫 1H 닫힘은 봉 47, ts 23:00).
        n: 봉 수 (``prices`` 미지정 시만 사용).
        price: 평탄 봉 가격 (``prices`` 미지정 시만 사용).
        prices: 봉별 close 시퀀스 (volume=1.0, OHLC=close 평탄). 지정 시 ``n``
            은 ``len(prices)`` 로 override. REVERSE 분기 시나리오 등 1m 별 가격
            engineering 용.
    """
    if prices is not None:
        n = len(prices)
        idx = pd.DatetimeIndex(
            [pd.Timestamp(start_ms + i * 60_000, unit="ms") for i in range(n)],
        )
        return pd.DataFrame(
            {
                "open": prices, "high": prices, "low": prices, "close": prices,
                "volume": [1.0] * n,
            },
            index=idx,
        )
    idx = pd.DatetimeIndex(
        [pd.Timestamp(start_ms + i * 60_000, unit="ms") for i in range(n)],
    )
    return pd.DataFrame(
        {"open": price, "high": price, "low": price, "close": price, "volume": 1.0},
        index=idx,
    )


def _open_long(
    engine: BacktestEngine, entry: float = 100.0,
    ts_ms: int = 1_700_000_000_000,
) -> None:
    """LONG 포지션 진입 헬퍼 — ``build_risk_plan`` + ``_open``."""
    plan = build_risk_plan(
        entry_price=entry, direction="long", leverage=engine.config.leverage,
        equity_usd=engine.balance, config=engine._risk_config,
        risk_pct=engine.config.risk_pct,
    )
    engine._open(plan=plan, ts_ms=ts_ms)


def _open_short(
    engine: BacktestEngine, entry: float = 100.0,
    ts_ms: int = 1_700_000_000_000,
) -> None:
    """SHORT 포지션 진입 헬퍼 — clamp 활성 케이스 (raw_pct < -1) 검증용."""
    plan = build_risk_plan(
        entry_price=entry, direction="short", leverage=engine.config.leverage,
        equity_usd=engine.balance, config=engine._risk_config,
        risk_pct=engine.config.risk_pct,
    )
    engine._open(plan=plan, ts_ms=ts_ms)


def _open_with_zero_sl(
    engine: BacktestEngine, entry: float = 100.0,
    ts_ms: int = 1_700_000_000_000,
) -> None:
    """``sl_price = entry_price`` 인 ``RiskPlan`` 직접 박음 — sl_distance=0 fallback 검증.

    ``build_risk_plan`` 경유 X — ``calc_position_size`` 가 ``sl_distance_pct=0``
    검증에서 ``ValueError`` 발생. RiskPlan 직접 구성으로 우회.
    """
    plan = RiskPlan(
        entry_price=entry, direction="long", leverage=10,
        position=PositionSize(
            notional_usd=40_000.0, margin_usd=4_000.0, coin_amount=400.0,
        ),
        tp_prices=[entry * 1.01, entry * 1.02, entry * 1.03, entry * 1.04],
        sl_price=entry,                                # sl_distance=0 인위 트리거
        trailing_mode=TrailingMode.OFF,
    )
    engine._open(plan=plan, ts_ms=ts_ms)


# ============================================================
# Group A — dataclass + __init__ (3)
# ============================================================


def test_backtest_config_defaults() -> None:
    """``BacktestConfig`` 디폴트 — D-8 사용자 노출 + replay 차용 가드 임계값 정합."""
    cfg = BacktestConfig()
    assert cfg.symbol == "BTCUSDT"
    assert cfg.timeframes == ["1H", "2H", "4H", "1D", "1W"]
    assert cfg.initial_capital == 10_000.0
    assert cfg.leverage == 10
    assert cfg.risk_pct == 0.01
    assert cfg.max_dd_stop_pct == 0.15
    assert cfg.consec_sl_pause_threshold == 2
    assert cfg.consec_sl_pause_minutes == 1440
    assert cfg.risk_config is None
    assert cfg.strategy_config is None


def test_engine_init_auto_risk_config_leverage_10() -> None:
    """``__init__`` 자동 산출 — leverage=10 → sl=2.0, tps=[2.8, 3.13.., 3.46.., 3.8] (D-3)."""
    engine = BacktestEngine(BacktestConfig(leverage=10))
    cfg = engine._risk_config
    assert cfg.mode == TpSlMode.FIXED_PCT
    assert cfg.fixed_sl_pct == pytest.approx(2.0)
    expected_tps = [2.8, 2.8 + 1.0 / 3.0, 2.8 + 2.0 / 3.0, 3.8]
    assert cfg.fixed_tp_pcts == pytest.approx(expected_tps)


def test_engine_init_explicit_risk_config_passthrough_no_mutate() -> None:
    """명시 ``risk_config`` → ``self._risk_config`` 동일 인스턴스 + config 보존 (Q2 불변성)."""
    explicit = TpSlConfig(mode=TpSlMode.MANUAL, manual_sl_pct=1.5)
    cfg = BacktestConfig(risk_config=explicit)
    engine = BacktestEngine(cfg)
    assert engine._risk_config is explicit                  # id 정합
    assert cfg.risk_config is explicit                      # 외부 인스턴스 mutate X


# ============================================================
# Group B — 헬퍼 단위 (8)
# ============================================================


def test_to_record_direction_normalization() -> None:
    """direction 격리 — 'long'/'LONG'/'Long' → 'LONG' (D-19 cost.Direction Literal)."""
    engine = BacktestEngine(BacktestConfig())
    assert engine._to_record_direction("long") == "LONG"
    assert engine._to_record_direction("LONG") == "LONG"
    assert engine._to_record_direction("Long") == "LONG"
    assert engine._to_record_direction("short") == "SHORT"
    assert engine._to_record_direction("SHORT") == "SHORT"
    assert engine._to_record_direction("sHoRt") == "SHORT"


def test_to_record_direction_invalid_raises() -> None:
    """direction 불정 입력 → ``ValueError`` + 한국어 메시지."""
    engine = BacktestEngine(BacktestConfig())
    with pytest.raises(ValueError, match="잘못된 direction"):
        engine._to_record_direction("buy")
    with pytest.raises(ValueError, match="잘못된 direction"):
        engine._to_record_direction("")


def test_open_guards_double_position() -> None:
    """이미 보유 중 ``_open`` → ``RuntimeError`` (D-20 페어당 1 포지션 정책)."""
    engine = BacktestEngine(BacktestConfig())
    _open_long(engine, entry=100.0, ts_ms=1_700_000_000_000)
    plan2 = build_risk_plan(
        entry_price=101.0, direction="long", leverage=10,
        equity_usd=engine.balance, config=engine._risk_config, risk_pct=0.01,
    )
    with pytest.raises(RuntimeError, match="이미 보유 포지션"):
        engine._open(plan=plan2, ts_ms=1_700_000_001_000)


def test_close_tp4_resets_consec_sl() -> None:
    """``_close(reason='TP4')`` → ``consec_sl`` reset (D-2 익절 카테고리)."""
    engine = BacktestEngine(BacktestConfig())
    engine.consec_sl = 1
    _open_long(engine, entry=100.0)
    engine._last_high = 104.0
    engine._last_low = 100.0
    engine._last_close = 104.0
    trade = engine._close(fill=104.0, ts_ms=1_700_000_010_000, reason="TP4")
    assert engine.consec_sl == 0
    assert engine.position is None
    assert trade.direction == "LONG"
    assert engine.trades == [trade]


def test_close_sl_pause_triggered_at_threshold() -> None:
    """``_close(reason='SL')`` consec_sl threshold 도달 → ``pause_bars`` 발동 (D-2)."""
    cfg = BacktestConfig(consec_sl_pause_threshold=2, consec_sl_pause_minutes=1440)
    engine = BacktestEngine(cfg)
    engine.consec_sl = 1                                    # threshold-1
    _open_long(engine, entry=100.0)
    engine._last_high = 100.0
    engine._last_low = 98.0
    engine._last_close = 98.0
    engine._close(fill=98.0, ts_ms=1_700_000_010_000, reason="SL")
    assert engine.pause_bars == 1440
    assert engine.consec_sl == 0


def test_close_reverse_keeps_consec_sl_count() -> None:
    """``_close(reason='REVERSE')`` → ``consec_sl`` 유지 (D-2 봇 능동 카테고리)."""
    engine = BacktestEngine(BacktestConfig())
    engine.consec_sl = 1
    _open_long(engine, entry=100.0)
    engine._last_high = 100.0
    engine._last_low = 99.0
    engine._last_close = 99.0
    engine._close(fill=99.0, ts_ms=1_700_000_010_000, reason="REVERSE")
    assert engine.consec_sl == 1                            # 유지 (D-2)


def test_partial_close_idx_out_of_range_raises() -> None:
    """``_partial_close(idx >= 3)`` → ``ValueError`` (TP4 = ``_close`` 책임, D-21)."""
    engine = BacktestEngine(BacktestConfig())
    _open_long(engine, entry=100.0)
    engine._last_high = 103.0
    engine._last_low = 100.0
    engine._last_close = 103.0
    with pytest.raises(ValueError, match=r"TP4 \(idx=3\)"):
        engine._partial_close(idx=3, fill=103.0, ts_ms=1_700_000_010_000)


def test_check_exits_long_gap_fill_unfavorable_sl() -> None:
    """LONG gap-fill — open(<SL) 통과 시 fill = min(open, SL_price) (replay L443-445)."""
    engine = BacktestEngine(BacktestConfig())
    _open_long(engine, entry=100.0)
    sl_price = engine.position.plan.sl_price                # ≈ 98 (sl 2%)
    # 1m bar: open 이 SL 아래로 통과 + low 더 하락 (gap-fill 시나리오)
    open_ = sl_price - 1.0
    low = sl_price - 3.0
    high = sl_price - 0.5
    close = sl_price - 1.0
    engine._last_high = high
    engine._last_low = low
    engine._last_close = close
    trade = engine._check_exits(
        ts_ms=1_700_000_010_000, open_=open_, high=high, low=low, close=close,
    )
    assert trade is not None
    # gap-fill 적용 → exit_price 가 SL 보다 명확히 아래 (open 부근 + slip)
    assert trade.exit_price < sl_price - 0.5
    assert engine.position is None


# ============================================================
# Group C — 가드 + step() (6)
# ============================================================


def test_step_no_closed_tf_early_return() -> None:
    """5 분 1m df → 1H 닫힘 X → balance 불변 + position None (DESIGN §6.2 step 5)."""
    engine = BacktestEngine(BacktestConfig())
    df = _make_synthetic_1m_df(n=5)
    trades = engine.run(df)
    assert engine.balance == 10_000.0
    assert engine.position is None
    assert trades == []


def test_step_pause_guard_skips_entry() -> None:
    """``pause_bars > 0`` → 진입 skip + ``_tick_pause`` 자연 감소 (1m unit)."""
    engine = BacktestEngine(BacktestConfig())
    engine.pause_bars = 100
    df = _make_synthetic_1m_df(n=10)
    engine.run(df)
    assert engine.position is None
    assert engine.balance == 10_000.0
    assert engine.pause_bars == 90                          # 10 회 _tick_pause


def test_step_stopped_guard_skips_entry() -> None:
    """``stopped=True`` → 진입 skip (영구 정지 가드, _check_max_dd 동기)."""
    engine = BacktestEngine(BacktestConfig())
    engine.stopped = True
    df = _make_synthetic_1m_df(n=10)
    engine.run(df)
    assert engine.position is None
    assert engine.balance == 10_000.0


def test_step_atr_mode_no_4h_closed_skips_entry() -> None:
    """ATR 모드 + 4H 미닫힘 → 진입 skip (D-4 정합, ``build_risk_plan(atr=None)`` 호출 X)."""
    atr_cfg = TpSlConfig(mode=TpSlMode.ATR, atr_sl_multiplier=1.5)
    cfg = BacktestConfig(risk_config=atr_cfg)
    engine = BacktestEngine(cfg)
    df = _make_synthetic_1m_df(n=10)                        # 1H / 4H 모두 미닫힘
    engine.run(df)
    assert engine.position is None
    assert engine._risk_config.mode == TpSlMode.ATR


def test_check_max_dd_permanent_stop_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """시드 -15% 도달 → ``stopped=True`` + WARNING 1 회 (중복 호출 시 추가 X)."""
    engine = BacktestEngine(
        BacktestConfig(initial_capital=10_000.0, max_dd_stop_pct=0.15),
    )
    engine.balance = 8_500.0                                # 시드 -15% 정확히
    caplog.set_level(logging.WARNING, logger="aurora.backtest.engine")
    assert engine._check_max_dd() is True
    assert engine.stopped is True
    assert len(caplog.records) == 1
    assert "MDD" in caplog.records[0].message
    # 두 번째 호출 → 추가 로그 X (가드: not self.stopped)
    engine._check_max_dd()
    assert len(caplog.records) == 1


def test_tick_pause_decrements_above_zero() -> None:
    """``pause_bars`` 0 에서 정지 (음수 X). 매 1m 호출 docstring contract 정합."""
    engine = BacktestEngine(BacktestConfig())
    engine.pause_bars = 2
    engine._tick_pause()
    assert engine.pause_bars == 1
    engine._tick_pause()
    assert engine.pause_bars == 0
    engine._tick_pause()                                    # 0 에서 추가 호출
    assert engine.pause_bars == 0                           # 음수 X


# ============================================================
# Group D — run() 통합 (2)
# ============================================================


def test_run_empty_dataframe_returns_empty_trades() -> None:
    """0 봉 df → ``trades=[]`` + balance 불변 + force_close skip (last_ts=0 가드)."""
    engine = BacktestEngine(BacktestConfig())
    empty = pd.DataFrame(
        columns=["open", "high", "low", "close", "volume"],
        index=pd.DatetimeIndex([]),
    )
    trades = engine.run(empty)
    assert trades == []
    assert engine.balance == 10_000.0
    assert engine.position is None


def test_run_force_close_at_end_keeps_consec_sl() -> None:
    """마지막 봉 보유 → FORCE_END trade + ``consec_sl`` 유지 (D-2 봇 능동, D-25)."""
    engine = BacktestEngine(BacktestConfig())
    engine.consec_sl = 2                                    # 임의 카운트
    _open_long(engine, entry=100.0, ts_ms=1_699_999_999_000)
    df = _make_synthetic_1m_df(n=5)
    engine.run(df)
    assert engine.position is None
    assert len(engine.trades) == 1
    assert engine.consec_sl == 2                            # 유지 (D-2)


# ============================================================
# Group E — edge / regression (3 함수, 4 collected)
# ============================================================


def test_close_sl_distance_zero_warns_caplog(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``sl_distance=0`` RiskPlan → ``r_multiple=0.0`` fallback + WARNING 1 회."""
    engine = BacktestEngine(BacktestConfig())
    _open_with_zero_sl(engine, entry=100.0)
    engine._last_high = 100.0
    engine._last_low = 100.0
    engine._last_close = 100.0
    caplog.set_level(logging.WARNING, logger="aurora.backtest.engine")
    trade = engine._close(fill=100.0, ts_ms=1_700_000_010_000, reason="SL")
    assert trade.r_multiple == 0.0
    assert any("sl_distance=0" in rec.message for rec in caplog.records)


@pytest.mark.parametrize(
    ("raw_pct", "expected_pnl_lo", "expected_pnl_hi"),
    [
        # v0.1.13 ROI 단위 변경 후 size_pct 가 10배 (0.05→0.5) — clamp 한도 -5.0.
        # raw=-0.90 → naive -4.5, cost ~-0.01 → -4.51 (clamp 미발동)
        # raw=-1.50 → naive -7.5, clamp 발동 → -5.0 정확
        (-0.90, -4.520, -4.500),                            # clamp 미발동 (cost 포함)
        (-1.50, -5.001, -4.999),                            # clamp 발동 (-5.0)
    ],
)
def test_close_clamp_at_extreme_drop(
    raw_pct: float, expected_pnl_lo: float, expected_pnl_hi: float,
) -> None:
    """clamp 한도 = ``-size_pct × leverage``. ``|raw_pct| ≥ 1.0`` 일 때만 발동.

    SHORT 사용 — LONG 은 raw_pct<-1 시 fill 음수 필요 (가격 무효). SHORT 면
    fill > entry × 2 로 자연 양수 (entry=100, raw=-1.5 → fill=250).

    v0.1.13: SL/TP 단위 = ROI %. 동일 sl_pct 명목 값에서 가격 변동 % = ROI / leverage
    이므로 sl_distance_pct 가 1/leverage 만큼 작아짐 → qty / size_pct 가 leverage 배.
    clamp 한도도 -size_pct × leverage 로 leverage 배 (-0.05×10=-0.5 → -0.5×10=-5).
    """
    engine = BacktestEngine(BacktestConfig(leverage=10))
    _open_short(engine, entry=100.0)
    # SHORT: raw_pnl_pct = (entry - exit)/entry. raw_pct<0 → exit > entry.
    fill = 100.0 * (1.0 - raw_pct)
    engine._last_high = fill
    engine._last_low = 100.0
    engine._last_close = fill
    trade = engine._close(fill=fill, ts_ms=1_700_000_010_000, reason="SL")
    assert expected_pnl_lo <= trade.pnl <= expected_pnl_hi


def test_engine_init_explicit_strategy_config_passthrough() -> None:
    """명시 ``strategy_config`` → ``self._strategy_config`` 동일 인스턴스 + config 보존."""
    custom = StrategyConfig(use_bollinger=True, ema_periods=(50, 100))
    cfg = BacktestConfig(strategy_config=custom)
    engine = BacktestEngine(cfg)
    assert engine._strategy_config is custom
    assert cfg.strategy_config is custom


# ============================================================
# Group F — step() REVERSE 분기 통합 (1, DESIGN §8.4 커버리지 갭 해소)
# ============================================================


def test_step_reverse_branch_synthetic_ohlcv() -> None:
    """``step()`` REVERSE 분기 (D-24, engine.py:351-354) 통합 line coverage.

    합성 1m 300 봉 + ``StrategyConfig(ema_periods=(2,3))`` (EMA 안정 ~5 봉,
    default 200/480 대비 ~100 배 빠름) + ``timeframes=["1H"]`` (1m 60→1H 1봉,
    5 회 닫힘) 시나리오:

        bar 0 닫힘 (봉 47, ts 23:00:20): close=100, EMA=100, distance=0 →
            LONG EMA touch (close ≥ EMA) → ``_open(LONG @ 100)``
        bar 1·2 닫힘: close=100 평탄 → 자기 방향 LONG signal → REVERSE X
        bar 3 닫힘 (봉 227, ts 02:00:20): bar3 OHLC ``open=100/high=100/
            low=99.85/close=99.85`` (180 봉~ 100→99.85 하락). EMA(2)≈99.9 +
            close 99.85 < EMA → distance 0.05% ≤ 0.3% → SHORT signal →
            ``compose_exit(LONG, [SHORT])`` True → ``_close(reason="REVERSE")``
        bar 3 직후: ``position=None``, 다음 1m 부터 신규 진입 가능 (D-20)
        bar 4 닫힘 (봉 287): SHORT signal 지속 → ``_open(SHORT @ 99.85)``
        마지막 봉 (봉 299): ``_force_close_at_end(reason="FORCE_END")``

    가격 하락 폭 0.15% 는 v0.1.13 ROI 단위 SL 2% / leverage 10 = 가격 변동
    0.2% 임계 미달 → SL 미발동 → REVERSE 분기 도달 보장 (가격 0.5% 하락 시
    SL 99.8 통과 → REVERSE 진입 전 SL 청산).

    DESIGN §8.4 커버리지 갭 1 건 해소. ``_close`` reason self-spy 로 직접
    검증 (mock 외부 의존 X — 자기 객체 wrapper 패턴, ``test_engine.py``
    mock 0 정책 정합).
    """
    cfg = BacktestConfig(
        timeframes=["1H"],
        strategy_config=StrategyConfig(
            ema_periods=(2, 3),
            ema_touch_tolerance=0.003,
        ),
    )
    engine = BacktestEngine(cfg)
    engine.consec_sl = 1                                    # REVERSE 후 유지 verify

    # _close reason 캡처 — self-spy (mock X, 자기 객체 wrapper 패턴).
    # Why: TradeRecord 에 reason 필드 부재 → 분기 식별 위해 호출 인자 추적.
    close_reasons: list[str] = []
    original_close = engine._close

    def spy_close(*, fill: float, ts_ms: int, reason: str) -> TradeRecord:
        close_reasons.append(reason)
        return original_close(fill=fill, ts_ms=ts_ms, reason=reason)

    engine._close = spy_close                               # type: ignore[method-assign]

    prices = [100.0] * 180 + [99.85] * 120
    df = _make_synthetic_1m_df(prices=prices)

    trades = engine.run(df)

    # REVERSE 분기 발동 verify — 첫 청산 = REVERSE
    assert "REVERSE" in close_reasons
    assert close_reasons[0] == "REVERSE"

    # trade 시퀀스 — LONG REVERSE close → 신규 SHORT → FORCE_END close
    assert len(trades) == 2
    assert trades[0].direction == "LONG"
    assert trades[1].direction == "SHORT"

    # bar 3 닫힘 시점 청산 verify (마지막 봉 ts 가 아님 → FORCE_END 아님)
    bar3_close_ts = 1_700_000_000_000 + 227 * 60_000        # 봉 227 = bar 3 닫힘
    last_bar_ts = 1_700_000_000_000 + 299 * 60_000          # 마지막 봉
    assert trades[0].exit_ts == bar3_close_ts
    assert trades[0].exit_ts != last_bar_ts

    # consec_sl 유지 — D-2 봇 능동 카테고리 (REVERSE / FORCE_END 둘 다)
    assert engine.consec_sl == 1


def test_run_multi_trade_end_to_end_scenario() -> None:
    """``run()`` 멀티 trade end-to-end 통합 — DESIGN §8.4 커버리지 갭 1 건 추가.

    LONG entry → TP1 partial → TP2 partial → BE close → SHORT entry →
    REVERSE close → LONG entry → FORCE_END close 의 8 단계 라이프사이클을
    한 ``run()`` 호출로 검증. 단계 2 self-spy 패턴 정합 — ``_close`` +
    ``_partial_close`` 둘 다 wrapper 로 호출 인자 캡처 (mock 외부 의존 X).

    가격 시퀀스 (v0.1.13 ROI 단위, leverage=10 → SL 가격 0.2% / TP[0] 가격
    0.28% 임계 정합):

        봉 0~46:    close=100.0 평탄 (bar 0 진행)
        봉 47:      close=100.0 → bar 0 닫힘 → LONG entry @ 100
                    (SL=99.8, TPs=[100.28, 100.31, 100.35, 100.38])
        봉 48:      close=100.30 → high ≥ TP1 → _partial_close(idx=0).
                    tp_hits=1, trailing MOVING_TARGET → SL=entry=100
        봉 49:      close=100.32 → high ≥ TP2 → _partial_close(idx=1).
                    tp_hits=2, trailing → SL=tp[0]=100.28
        봉 50:      close=99.78 → low ≤ SL 100.28 → _close(reason="BE")
                    (tp_hits=2 ≥ 1 → BE 분류, D-2 봇 능동 카운트 유지)
        봉 51~106:  close=99.50 평탄 (position=None)
        봉 107:     bar 1 닫힘 → close 99.50 < EMA(2) 99.667 → SHORT
                    entry @ 99.50 (SL=99.70, TP1=99.22)
        봉 108~166: close=99.50 평탄 (SHORT 보유 — SL/TP 미달)
        봉 167:     bar 2 닫힘 → SHORT signal (자기 방향 → REVERSE X)
        봉 168~226: close=99.65 평탄 (high 99.65 < SHORT SL 99.70)
        봉 227:     bar 3 닫힘 → close 99.65 > EMA(2) ≈ 99.61 → LONG
                    signal → compose_exit(SHORT, [LONG]) True →
                    _close(reason="REVERSE")
        봉 228~286: close=99.65 평탄 (position=None)
        봉 287:     bar 4 닫힘 → LONG entry @ 99.65
        봉 288~298: close=99.65 평탄
        봉 299:     마지막 봉 → _force_close_at_end(reason="FORCE_END")

    Assertion 8 가지 — D-2 reason 매핑 + 멀티 trade 누적 + 방향 분포 +
    consec_sl 흐름 + balance 변동:
        - partial_idx_log == [0, 1]      # TP1 → TP2 순차 partial
        - close_reasons == ["BE", "REVERSE", "FORCE_END"]
        - len(trades) == 5               # partial 2 + close 3
        - 방향 분포 LONG 4 (TP1/TP2/BE/FORCE_END) + SHORT 1 (REVERSE)
        - trades[0/1] partial: entry=100 + exit > entry (LONG TP)
        - trades[2] BE: exit < entry (LONG SL trail)
        - trades[3] SHORT REVERSE: bar 3 닫힘 시점 == 봉 227 ts
        - consec_sl == 0 (TP1/TP2 partial reset → BE/REVERSE/FORCE_END
                          모두 카운트 유지 → 0 그대로)
        - balance < initial_capital     # 누적 손실 (단일 BE + 두 SHORT
                                        # 사이 변동 + FORCE_END 마이너 fee)
    """
    cfg = BacktestConfig(
        timeframes=["1H"],
        strategy_config=StrategyConfig(
            ema_periods=(2, 3),
            ema_touch_tolerance=0.003,
        ),
    )
    engine = BacktestEngine(cfg)

    # self-spy — _close 와 _partial_close 둘 다 캡처 (D-21 partial idx 추적)
    close_reasons: list[str] = []
    original_close = engine._close

    def spy_close(*, fill: float, ts_ms: int, reason: str) -> TradeRecord:
        close_reasons.append(reason)
        return original_close(fill=fill, ts_ms=ts_ms, reason=reason)

    engine._close = spy_close                               # type: ignore[method-assign]

    partial_idx_log: list[int] = []
    original_partial = engine._partial_close

    def spy_partial(*, idx: int, fill: float, ts_ms: int) -> TradeRecord:
        partial_idx_log.append(idx)
        return original_partial(idx=idx, fill=fill, ts_ms=ts_ms)

    engine._partial_close = spy_partial                     # type: ignore[method-assign]

    prices = (
        [100.0] * 47                                        # 봉 0~46
        + [100.0]                                           # 봉 47, bar 0 닫힘
        + [100.30]                                          # 봉 48, TP1
        + [100.32]                                          # 봉 49, TP2
        + [99.78]                                           # 봉 50, BE close
        + [99.50] * 56                                      # 봉 51~106
        + [99.50] * 60                                      # 봉 107~166
        + [99.65] * 60                                      # 봉 167~226
        + [99.65] * 60                                      # 봉 227~286
        + [99.65] * 13                                      # 봉 287~299
    )
    assert len(prices) == 300                               # 사전 계산 verify
    df = _make_synthetic_1m_df(prices=prices)

    trades = engine.run(df)

    # (1) D-21 TP partial 순차 — TP1 → TP2 (한 봉 1 청산 한계 정합)
    assert partial_idx_log == [0, 1]

    # (2) D-2 close reason 매핑 — BE / REVERSE / FORCE_END
    assert close_reasons == ["BE", "REVERSE", "FORCE_END"]

    # (3) trade 누적 — partial 2 + close 3 = 5 (>= 4 사용자 임계)
    assert len(trades) == 5

    # (4) 방향 분포 — LONG 4 (TP1/TP2/BE/FORCE_END) + SHORT 1 (REVERSE)
    directions = [t.direction for t in trades]
    assert directions == ["LONG", "LONG", "LONG", "SHORT", "LONG"]
    assert directions.count("LONG") == 4
    assert directions.count("SHORT") == 1

    # (5) trades[0/1] LONG TP partial — exit > entry (익절)
    assert trades[0].entry_price == pytest.approx(100.0)
    assert trades[0].exit_price > trades[0].entry_price
    assert trades[1].exit_price > trades[1].entry_price

    # (6) trades[2] LONG BE close — exit < entry (SL trail 100.28 통과 청산)
    assert trades[2].entry_price == pytest.approx(100.0)
    assert trades[2].exit_price < trades[2].entry_price

    # (7) trades[3] SHORT REVERSE close — bar 3 닫힘 시점 (봉 227)
    bar3_close_ts = 1_700_000_000_000 + 227 * 60_000
    assert trades[3].direction == "SHORT"
    assert trades[3].exit_ts == bar3_close_ts

    # (8) consec_sl == 0 — _partial_close reset → BE/REVERSE/FORCE_END 유지
    assert engine.consec_sl == 0

    # balance 변동 — 누적 손실 (BE 청산 + SHORT REVERSE pnl 음수 + fee)
    assert engine.balance < cfg.initial_capital
    assert engine.position is None                          # FORCE_END 후 정리
