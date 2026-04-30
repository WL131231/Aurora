"""core.strategy 단위 테스트 — EMA 터치 진입 + RSI 다이버전스 진입."""

from __future__ import annotations

import numpy as np
import pandas as pd

from aurora.core.strategy import (
    Direction,
    EntrySignal,
    StrategyConfig,
    detect_bollinger_touch,
    detect_ema_touch,
    detect_ma_cross,
    detect_rsi_divergence,
    evaluate_selectable,
)


def _make_df(close_values: list[float]) -> pd.DataFrame:
    """OHLC DataFrame 헬퍼 — high/low 는 close ±0.5%."""
    s = pd.Series(close_values, dtype=float)
    return pd.DataFrame({
        "open": s,
        "high": s * 1.005,
        "low": s * 0.995,
        "close": s,
    })


# ============================================================
# detect_ema_touch
# ============================================================


def test_ema_touch_no_signal_when_far_from_ema() -> None:
    """가격이 EMA에서 멀리 떨어져 있으면 신호 X."""
    # 일정 가격 100 으로 EMA 수렴 → 마지막을 130 으로 점프 (멀어짐)
    closes = [100.0] * 250 + [130.0]
    df_by_tf = {"1H": _make_df(closes)}
    config = StrategyConfig()
    signals = detect_ema_touch(df_by_tf, config)
    assert signals == []


def test_ema_touch_long_when_close_just_above_ema() -> None:
    """종가 ≥ EMA, 터치 거리 이내 → 롱 신호 (지지)."""
    # EMA가 100 으로 수렴, 마지막 close = 100.2 (0.2% 위, 0.3% 이내)
    closes = [100.0] * 250 + [100.2]
    df_by_tf = {"1H": _make_df(closes)}
    config = StrategyConfig()
    signals = detect_ema_touch(df_by_tf, config)
    # EMA 200, EMA 480 둘 다 신호 가능 (둘 다 100 근처)
    longs = [s for s in signals if s.direction == Direction.LONG]
    assert len(longs) >= 1
    assert all(s.timeframe == "1H" for s in longs)
    assert all(s.source.startswith("ema_touch_") for s in longs)


def test_ema_touch_short_when_close_just_below_ema() -> None:
    """종가 < EMA, 터치 거리 이내 → 숏 신호 (저항)."""
    closes = [100.0] * 250 + [99.8]  # 0.2% 아래
    df_by_tf = {"1H": _make_df(closes)}
    config = StrategyConfig()
    signals = detect_ema_touch(df_by_tf, config)
    shorts = [s for s in signals if s.direction == Direction.SHORT]
    assert len(shorts) >= 1


def test_ema_touch_multi_tf() -> None:
    """여러 TF 동시 터치 → 각 TF 별 신호 산출."""
    closes = [100.0] * 250 + [100.1]
    df_by_tf = {
        "15m": _make_df(closes),
        "1H":  _make_df(closes),
        "4H":  _make_df(closes),
    }
    config = StrategyConfig()
    signals = detect_ema_touch(df_by_tf, config)
    tfs = {s.timeframe for s in signals}
    assert {"15m", "1H", "4H"}.issubset(tfs)


def test_ema_touch_skips_empty_df() -> None:
    df_by_tf = {"1H": pd.DataFrame()}
    config = StrategyConfig()
    assert detect_ema_touch(df_by_tf, config) == []


def test_ema_touch_skips_short_df_nan_ema() -> None:
    """데이터 짧아서 EMA NaN인 경우 신호 X — 단, EMA 자체는 첫 값=입력 첫 값이라
    NaN 이 나오진 않음. 대신 close ≤ 0 케이스 검증."""
    df_by_tf = {"1H": _make_df([0.0] * 5)}  # 모든 가격 0
    config = StrategyConfig()
    assert detect_ema_touch(df_by_tf, config) == []


# ============================================================
# detect_rsi_divergence (단순 smoke + 빈 DF 처리)
# ============================================================


def test_rsi_divergence_empty_df() -> None:
    config = StrategyConfig()
    assert detect_rsi_divergence(pd.DataFrame(), config) == []


def test_rsi_divergence_runs_on_random_data() -> None:
    """무작위 데이터에서 호출만 동작 (검출 여부는 데이터 의존)."""
    rng = np.random.RandomState(42)
    closes = list(rng.randn(200).cumsum() + 100)
    df = pd.DataFrame({
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low":  [c * 0.99 for c in closes],
        "close": closes,
    })
    config = StrategyConfig()
    result = detect_rsi_divergence(df, config)
    # 결과는 0~1 개의 EntrySignal
    assert isinstance(result, list)
    for s in result:
        assert isinstance(s, EntrySignal)
        assert s.timeframe == "1H"


# ============================================================
# detect_bollinger_touch
# ============================================================


def _bb_df(
    closes: list[float],
    *,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> pd.DataFrame:
    """OHLC DataFrame 헬퍼. highs/lows 미지정 시 close 와 동일."""
    s = pd.Series(closes, dtype=float)
    h = pd.Series(highs if highs is not None else closes, dtype=float)
    low = pd.Series(lows if lows is not None else closes, dtype=float)
    return pd.DataFrame({"open": s, "high": h, "low": low, "close": s})


# ─── Tier 1: Squeeze 보류 ───


def test_bb_squeeze_holds() -> None:
    """폭/middle 이 squeeze_threshold 이하면 진입 보류."""
    # 일정 가격 → BB 폭 0 → narrowness=0 → squeeze
    closes = [100.0] * 30 + [99.5]
    df = _bb_df(closes)
    config = StrategyConfig()  # squeeze_threshold=0.015 기본
    assert detect_bollinger_touch(df, config) == []


# ─── Tier 2: Reversal (찢어짐 회귀) ───


def test_bb_reversal_short_after_upper_break() -> None:
    """직전 봉 종가 > upper + 현재 봉 종가 ≤ upper → SHORT (강도 1.5)."""
    rng = np.random.RandomState(42)
    closes = list(rng.randn(40) * 1.0 + 100)  # 변동성 (squeeze 안 걸림)
    closes.append(float(np.mean(closes)) + 8.0)   # 직전 봉: upper 위로 찢어짐
    closes.append(float(np.mean(closes[:-1])))    # 현재 봉: 안쪽 회귀
    df = _bb_df(closes)
    config = StrategyConfig()
    signals = detect_bollinger_touch(df, config)
    assert any(
        s.direction == Direction.SHORT
        and s.source == "bollinger_reversal_upper"
        and s.strength == 1.5
        for s in signals
    )


def test_bb_reversal_long_after_lower_break() -> None:
    """직전 봉 종가 < lower + 현재 봉 종가 ≥ lower → LONG (강도 1.5)."""
    rng = np.random.RandomState(7)
    closes = list(rng.randn(40) * 1.0 + 100)
    closes.append(float(np.mean(closes)) - 8.0)
    closes.append(float(np.mean(closes[:-1])))
    df = _bb_df(closes)
    config = StrategyConfig()
    signals = detect_bollinger_touch(df, config)
    assert any(
        s.direction == Direction.LONG
        and s.source == "bollinger_reversal_lower"
        and s.strength == 1.5
        for s in signals
    )


# ─── Tier 3: 찢어짐 보류 (현재 봉 종가가 BB 밖) ───


def test_bb_holds_on_close_outside() -> None:
    """현재 봉 종가가 upper 위 (직전 봉은 안쪽이라 reversal 아님) → 보류."""
    rng = np.random.RandomState(0)
    closes = list(rng.randn(40) * 1.0 + 100)
    # 마지막만 위로 점프 (직전은 안쪽)
    closes.append(float(np.mean(closes)) + 8.0)
    df = _bb_df(closes)
    config = StrategyConfig()
    signals = detect_bollinger_touch(df, config)
    assert signals == []


# ─── Tier 4: Proximity 터치 (high/low 기반) ───


def test_bb_touch_short_when_high_in_upper_zone() -> None:
    """high 가 upper zone 진입 + 종가 안쪽 → SHORT (강도 1.0)."""
    rng = np.random.RandomState(11)
    closes = list(rng.randn(40) * 1.0 + 100)
    # 직전 봉(인덱스 -1) 안쪽, 마지막 봉의 high 가 upper 근처, close 안쪽
    last_close = float(np.mean(closes[-20:]))  # 안쪽 (middle 근처)
    closes.append(last_close)
    # 마지막 봉 high 만 upper 위쪽까지 spike
    highs = list(closes[:-1]) + [last_close + 5.0]
    lows = list(closes)
    df = _bb_df(closes, highs=highs, lows=lows)
    config = StrategyConfig()
    signals = detect_bollinger_touch(df, config)
    assert any(
        s.direction == Direction.SHORT
        and s.source == "bollinger_upper"
        and s.strength == 1.0
        for s in signals
    )


def test_bb_touch_long_when_low_in_lower_zone() -> None:
    """low 가 lower zone 진입 + 종가 안쪽 → LONG (강도 1.0)."""
    rng = np.random.RandomState(13)
    closes = list(rng.randn(40) * 1.0 + 100)
    last_close = float(np.mean(closes[-20:]))
    closes.append(last_close)
    highs = list(closes)
    lows = list(closes[:-1]) + [last_close - 5.0]
    df = _bb_df(closes, highs=highs, lows=lows)
    config = StrategyConfig()
    signals = detect_bollinger_touch(df, config)
    assert any(
        s.direction == Direction.LONG
        and s.source == "bollinger_lower"
        and s.strength == 1.0
        for s in signals
    )


def test_bb_no_signal_when_high_low_far_from_band() -> None:
    """high/low 가 zone 밖이고 종가 안쪽이면 신호 X.

    직전·현재 봉 모두 명시적으로 middle 에 두어 reversal 우연 발동 방지.
    """
    rng = np.random.RandomState(15)
    closes = list(rng.randn(38) * 1.0 + 100)
    mean_val = float(np.mean(closes))
    closes.extend([mean_val, mean_val])  # 직전·현재 봉 모두 mean (안쪽 확정)
    df = _bb_df(closes)  # high=low=close (모두 안쪽)
    config = StrategyConfig()
    signals = detect_bollinger_touch(df, config)
    assert signals == []


# ─── 그 외 ───


def test_bb_touch_empty_df() -> None:
    config = StrategyConfig()
    assert detect_bollinger_touch(pd.DataFrame(), config) == []


def test_bb_touch_missing_columns() -> None:
    """high/low 컬럼 없으면 빈 결과."""
    df = pd.DataFrame({"close": [100.0] * 30})
    config = StrategyConfig()
    assert detect_bollinger_touch(df, config) == []


# ============================================================
# evaluate_selectable
# ============================================================


def test_evaluate_selectable_off_returns_empty() -> None:
    """모든 Selectable off → 빈 리스트."""
    rng = np.random.RandomState(7)
    closes = list(rng.randn(40) * 1.0 + 100)
    closes.append(float(np.mean(closes)) - 8.0)
    closes.append(float(np.mean(closes[:-1])))
    df_by_tf = {"1H": _bb_df(closes)}
    config = StrategyConfig(use_bollinger=False)
    assert evaluate_selectable(df_by_tf, config) == []


def test_evaluate_selectable_bollinger_on() -> None:
    """use_bollinger=True 면 BB reversal 신호 라우팅됨."""
    rng = np.random.RandomState(7)
    closes = list(rng.randn(40) * 1.0 + 100)
    closes.append(float(np.mean(closes)) - 8.0)  # 직전: 아래로 찢어짐
    closes.append(float(np.mean(closes[:-1])))    # 현재: 안쪽 회귀
    df_by_tf = {"1H": _bb_df(closes)}
    config = StrategyConfig(use_bollinger=True)
    signals = evaluate_selectable(df_by_tf, config)
    longs = [s for s in signals if s.direction == Direction.LONG]
    assert len(longs) >= 1
    assert any(s.source.startswith("bollinger_") for s in longs)


def test_evaluate_selectable_no_1h_data() -> None:
    """BB 활성화여도 1H 데이터 없으면 신호 X."""
    config = StrategyConfig(use_bollinger=True)
    assert evaluate_selectable({"4H": _bb_df([100.0] * 30)}, config) == []


# ============================================================
# detect_ma_cross
# ============================================================


def _trend_close_df(closes: list[float]) -> pd.DataFrame:
    """OHLC DataFrame — close 만 의미 있음 (high/low/open 동일)."""
    s = pd.Series(closes, dtype=float)
    return pd.DataFrame({"open": s, "high": s, "low": s, "close": s})


def test_ma_cross_no_data_returns_empty() -> None:
    config = StrategyConfig()
    assert detect_ma_cross({}, config) == []


def test_ma_cross_golden_on_1h() -> None:
    """1H 에서 V자 형성 → golden → LONG 신호."""
    closes = list(np.linspace(100, 50, 30)) + list(np.linspace(50, 100, 30))
    df_by_tf = {"1H": _trend_close_df(closes)}
    config = StrategyConfig(ma_cross_fast=5, ma_cross_slow=10)
    signals = detect_ma_cross(df_by_tf, config)
    longs = [s for s in signals if s.source == "ma_cross_golden"]
    # cross 가 마지막 봉에 정확히 발생할 때만 emit. 아닐 수 있어 0~1개 허용.
    for s in longs:
        assert s.direction == Direction.LONG
        assert s.timeframe == "1H"
        assert s.strength == 1.0


def test_ma_cross_emits_at_cross_bar() -> None:
    """cross 가 마지막 봉에 정확히 발생 → 신호 발동.

    fast=3, slow=5. 하락 후 바닥 hover, 마지막 봉에서 큰 점프로 cross.
    """
    closes = [100.0, 100.0, 100.0, 100.0, 100.0,   # 0-4 안정
              90.0, 80.0, 70.0, 60.0, 50.0,         # 5-9 하락 (fast<slow)
              50.0, 50.0, 50.0, 50.0,               # 10-13 바닥 hover (diff 0 도달)
              150.0]                                 # 14 큰 점프 → 마지막 봉 cross
    df_by_tf = {"4H": _trend_close_df(closes)}
    config = StrategyConfig(ma_cross_fast=3, ma_cross_slow=5)
    signals = detect_ma_cross(df_by_tf, config)
    goldens = [s for s in signals if s.source == "ma_cross_golden" and s.timeframe == "4H"]
    assert len(goldens) == 1
    assert goldens[0].direction == Direction.LONG


def test_ma_cross_dead_emits_short() -> None:
    """상승 후 천장 hover, 마지막 봉에서 큰 하락 → dead cross."""
    closes = [100.0, 100.0, 100.0, 100.0, 100.0,
              110.0, 120.0, 130.0, 140.0, 150.0,
              150.0, 150.0, 150.0, 150.0,
              50.0]
    df_by_tf = {"2H": _trend_close_df(closes)}
    config = StrategyConfig(ma_cross_fast=3, ma_cross_slow=5)
    signals = detect_ma_cross(df_by_tf, config)
    deads = [s for s in signals if s.source == "ma_cross_dead" and s.timeframe == "2H"]
    assert len(deads) == 1
    assert deads[0].direction == Direction.SHORT


def test_ma_cross_no_signal_on_constant() -> None:
    """일정 가격 → 크로스 없음."""
    df_by_tf = {tf: _trend_close_df([100.0] * 50) for tf in ("1H", "2H", "4H")}
    config = StrategyConfig(ma_cross_fast=5, ma_cross_slow=10)
    assert detect_ma_cross(df_by_tf, config) == []


def test_ma_cross_only_targeted_tfs() -> None:
    """1H/2H/4H 만 검사. 다른 TF 데이터는 무시."""
    # 다른 TF (15m) 에 명백한 cross 데이터
    closes = [100.0] * 5 + [90, 80, 70, 60, 50] + [60, 80, 100, 120, 140]
    df_by_tf = {"15m": _trend_close_df(closes)}
    config = StrategyConfig(ma_cross_fast=3, ma_cross_slow=5)
    assert detect_ma_cross(df_by_tf, config) == []


# ============================================================
# evaluate_selectable — MA Cross 라우팅
# ============================================================


def test_evaluate_selectable_ma_cross_off() -> None:
    """use_ma_cross=False 면 MA 신호 X."""
    closes = [100.0] * 5 + [90, 80, 70, 60, 50] + [50, 50, 50, 50] + [150]
    df_by_tf = {"4H": _trend_close_df(closes)}
    config = StrategyConfig(
        use_ma_cross=False,
        ma_cross_fast=3,
        ma_cross_slow=5,
    )
    signals = evaluate_selectable(df_by_tf, config)
    assert all(not s.source.startswith("ma_cross") for s in signals)


def test_evaluate_selectable_ma_cross_on() -> None:
    """use_ma_cross=True 면 라우팅됨."""
    closes = [100.0] * 5 + [90, 80, 70, 60, 50] + [50, 50, 50, 50] + [150]
    df_by_tf = {"4H": _trend_close_df(closes)}
    config = StrategyConfig(
        use_ma_cross=True,
        ma_cross_fast=3,
        ma_cross_slow=5,
    )
    signals = evaluate_selectable(df_by_tf, config)
    goldens = [s for s in signals if s.source == "ma_cross_golden"]
    assert len(goldens) == 1
    assert goldens[0].direction == Direction.LONG
    assert goldens[0].timeframe == "4H"
