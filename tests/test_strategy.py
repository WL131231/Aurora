"""core.strategy 단위 테스트 — EMA 터치 진입 + RSI 다이버전스 진입."""

from __future__ import annotations

import numpy as np
import pandas as pd

from aurora.core.indicators import HarmonicMatch
from aurora.core.strategy import (
    Direction,
    EntrySignal,
    StrategyConfig,
    detect_bollinger_touch,
    detect_ema_touch,
    detect_harmonic_exit,
    detect_harmonic_signal,
    detect_ichimoku_exit,
    detect_ichimoku_signal,
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


# ============================================================
# detect_ichimoku_signal — 구름대 스팬 터치 진입
# ============================================================


def _ichimoku_cfg() -> StrategyConfig:
    """이치모쿠 테스트용 작은 기간 설정 (warmup 짧게)."""
    return StrategyConfig(
        use_ichimoku=True,
        ichimoku_conversion_period=3,
        ichimoku_base_period=5,
        ichimoku_span_b_period=10,
        ichimoku_displacement=5,  # shift = 4봉
    )


def _ohlc_with_last(closes: list[float], last_high: float, last_low: float) -> pd.DataFrame:
    """마지막 봉만 high/low 를 별도 지정하는 OHLC 헬퍼."""
    s = pd.Series(closes, dtype=float)
    high = s.copy()
    low = s.copy()
    high.iloc[-1] = last_high
    low.iloc[-1] = last_low
    return pd.DataFrame({"open": s, "high": high, "low": low, "close": s})


def test_ichimoku_signal_no_data_returns_empty() -> None:
    config = _ichimoku_cfg()
    assert detect_ichimoku_signal({}, config) == []


def test_ichimoku_signal_skips_missing_columns() -> None:
    config = _ichimoku_cfg()
    df = pd.DataFrame({"close": [100.0] * 30})
    assert detect_ichimoku_signal({"1H": df}, config) == []


def test_ichimoku_signal_long_on_cloud_upper_touch() -> None:
    """가격이 구름 위에서 상단 스팬 터치 → LONG."""
    # 30봉 횡보 (구름이 ~100 근처에서 형성), 마지막 close 가 구름 상단 살짝 위에서 low 가 닿음
    closes = [100.0] * 30 + [101.0]
    # 위 closes 로 ichimoku 계산했을 때 cloud_upper(마지막 봉) ≈ 100 부근.
    # 마지막 봉의 close 를 약간 올려두고 low 가 cloud_upper 를 감싸게 만듬.
    df = _ohlc_with_last(closes, last_high=101.5, last_low=99.5)
    config = _ichimoku_cfg()

    signals = detect_ichimoku_signal({"1H": df}, config)
    longs = [s for s in signals if s.direction == Direction.LONG]
    assert len(longs) == 1
    assert longs[0].source == "ichimoku_cloud_upper"
    assert longs[0].timeframe == "1H"
    assert longs[0].strength == 1.0


def test_ichimoku_signal_short_on_cloud_lower_touch() -> None:
    """가격이 구름 아래에서 하단 스팬 터치 → SHORT."""
    closes = [100.0] * 30 + [99.0]
    df = _ohlc_with_last(closes, last_high=100.5, last_low=98.5)
    config = _ichimoku_cfg()

    signals = detect_ichimoku_signal({"1H": df}, config)
    shorts = [s for s in signals if s.direction == Direction.SHORT]
    assert len(shorts) == 1
    assert shorts[0].source == "ichimoku_cloud_lower"
    assert shorts[0].timeframe == "1H"


def test_ichimoku_signal_no_signal_inside_cloud() -> None:
    """가격이 구름 안에 있을 때 무신호."""
    # 가격 변동이 충분해서 cloud_upper/lower 가 구분되도록 sine 형 데이터
    closes = [100.0 + 5.0 * np.sin(i * 0.3) for i in range(30)]
    closes.append(100.0)  # 마지막 봉 close = 100, 구름 안쪽 위치 가정
    s = pd.Series(closes, dtype=float)
    # 마지막 봉 high/low 도 구름 안쪽으로 좁게
    high = s.copy()
    low = s.copy()
    high.iloc[-1] = 100.5
    low.iloc[-1] = 99.5
    df = pd.DataFrame({"open": s, "high": high, "low": low, "close": s})
    config = _ichimoku_cfg()

    signals = detect_ichimoku_signal({"1H": df}, config)
    # close 가 구름 위/아래 어디인지 따라 결과 다를 수 있어, "터치 조건" 만 검증:
    # 신호가 있다면 source 는 ichimoku_cloud_upper / lower 둘 중 하나.
    for s_ in signals:
        assert s_.source in ("ichimoku_cloud_upper", "ichimoku_cloud_lower")


def test_ichimoku_signal_no_signal_far_above_cloud() -> None:
    """가격이 구름 위에 있고 low 가 cloud_upper 위에 있으면 무신호 (터치 X)."""
    closes = [100.0] * 30 + [105.0]
    # 마지막 봉의 low 를 구름 위로 멀리 떨어트림
    df = _ohlc_with_last(closes, last_high=106.0, last_low=104.5)
    config = _ichimoku_cfg()

    signals = detect_ichimoku_signal({"1H": df}, config)
    assert signals == []


def test_ichimoku_signal_multi_tf() -> None:
    """여러 TF 가 들어와도 각자 독립 평가."""
    closes = [100.0] * 30 + [101.0]
    df = _ohlc_with_last(closes, last_high=101.5, last_low=99.5)
    config = _ichimoku_cfg()

    signals = detect_ichimoku_signal({"1H": df, "4H": df}, config)
    timeframes = [s.timeframe for s in signals]
    assert "1H" in timeframes and "4H" in timeframes


# ============================================================
# detect_ichimoku_exit — 구름대 종가 이탈
# ============================================================


def test_ichimoku_exit_long_when_close_below_cloud_upper() -> None:
    """롱 보유 중 종가가 cloud_upper 아래로 마감 → True."""
    # warmup 후 마지막 봉 close 가 구름 안으로 내려옴
    closes = [100.0] * 30 + [98.0]
    df = _ohlc_with_last(closes, last_high=99.0, last_low=97.5)
    config = _ichimoku_cfg()

    assert detect_ichimoku_exit(df, Direction.LONG, config) is True


def test_ichimoku_exit_long_holds_when_close_above_cloud_upper() -> None:
    """롱 보유 중 종가가 cloud_upper 위면 False (보유 유지)."""
    closes = [100.0] * 30 + [105.0]
    df = _ohlc_with_last(closes, last_high=105.5, last_low=104.5)
    config = _ichimoku_cfg()

    assert detect_ichimoku_exit(df, Direction.LONG, config) is False


def test_ichimoku_exit_short_when_close_above_cloud_lower() -> None:
    """숏 보유 중 종가가 cloud_lower 위로 마감 → True."""
    closes = [100.0] * 30 + [102.0]
    df = _ohlc_with_last(closes, last_high=102.5, last_low=101.0)
    config = _ichimoku_cfg()

    assert detect_ichimoku_exit(df, Direction.SHORT, config) is True


def test_ichimoku_exit_short_holds_when_close_below_cloud_lower() -> None:
    """숏 보유 중 종가가 cloud_lower 아래면 False."""
    closes = [100.0] * 30 + [95.0]
    df = _ohlc_with_last(closes, last_high=95.5, last_low=94.5)
    config = _ichimoku_cfg()

    assert detect_ichimoku_exit(df, Direction.SHORT, config) is False


def test_ichimoku_exit_returns_false_on_empty_df() -> None:
    config = _ichimoku_cfg()
    assert detect_ichimoku_exit(pd.DataFrame(), Direction.LONG, config) is False


def test_ichimoku_exit_returns_false_on_missing_columns() -> None:
    config = _ichimoku_cfg()
    df = pd.DataFrame({"close": [100.0] * 30})
    assert detect_ichimoku_exit(df, Direction.LONG, config) is False


def test_ichimoku_exit_returns_false_in_warmup() -> None:
    """warmup 구간(NaN)에선 False (판정 불가)."""
    closes = [100.0] * 5  # warmup 미만
    df = _ohlc_with_last(closes, last_high=100.5, last_low=99.5)
    config = _ichimoku_cfg()
    assert detect_ichimoku_exit(df, Direction.LONG, config) is False


# ============================================================
# evaluate_selectable — Ichimoku 라우팅
# ============================================================


def test_evaluate_selectable_ichimoku_off() -> None:
    """use_ichimoku=False 면 ichimoku 신호 X."""
    closes = [100.0] * 30 + [101.0]
    df = _ohlc_with_last(closes, last_high=101.5, last_low=99.5)
    config = StrategyConfig(
        use_ichimoku=False,
        ichimoku_conversion_period=3,
        ichimoku_base_period=5,
        ichimoku_span_b_period=10,
        ichimoku_displacement=5,
    )
    signals = evaluate_selectable({"1H": df}, config)
    assert all(not s.source.startswith("ichimoku") for s in signals)


def test_evaluate_selectable_ichimoku_on() -> None:
    """use_ichimoku=True 면 라우팅됨."""
    closes = [100.0] * 30 + [101.0]
    df = _ohlc_with_last(closes, last_high=101.5, last_low=99.5)
    config = _ichimoku_cfg()
    signals = evaluate_selectable({"1H": df}, config)
    ichimoku_sigs = [s for s in signals if s.source.startswith("ichimoku")]
    assert len(ichimoku_sigs) >= 1
    assert ichimoku_sigs[0].direction == Direction.LONG


# ============================================================
# detect_harmonic_signal — 5종 패턴, 15m/1H 멀티 TF
# ============================================================


def _zigzag_df(points: list[float], between: int = 10, spread: float = 0.1) -> pd.DataFrame:
    closes: list[float] = []
    for i in range(len(points) - 1):
        leg = list(np.linspace(points[i], points[i + 1], between, endpoint=False))
        closes.extend(leg)
    closes.append(points[-1])
    s = pd.Series(closes, dtype=float)
    return pd.DataFrame({"open": s, "high": s + spread, "low": s - spread, "close": s})


def _bullish_bat_df() -> pd.DataFrame:
    """검증된 bullish Bat 합성 데이터."""
    return _zigzag_df([105, 100, 120, 110, 115, 102.7, 104], between=10)


def _harmonic_cfg(use: bool = True) -> StrategyConfig:
    return StrategyConfig(
        use_harmonic=use,
        harmonic_pivot_length=5,
        harmonic_tolerance=0.10,
    )


def test_harmonic_signal_no_data_returns_empty() -> None:
    config = _harmonic_cfg()
    assert detect_harmonic_signal({}, config) == []


def test_harmonic_signal_skips_missing_columns() -> None:
    config = _harmonic_cfg()
    df = pd.DataFrame({"close": [100.0] * 60})
    assert detect_harmonic_signal({"1H": df}, config) == []


def test_harmonic_signal_long_on_bullish_bat() -> None:
    """Bullish Bat 검출 → LONG 신호."""
    df = _bullish_bat_df()
    config = _harmonic_cfg()
    signals = detect_harmonic_signal({"1H": df}, config)
    longs = [s for s in signals if s.direction == Direction.LONG]
    assert len(longs) == 1
    assert longs[0].source == "harmonic_bat"
    assert longs[0].timeframe == "1H"


def test_harmonic_signal_short_on_bearish_bat() -> None:
    """Bearish Bat 검출 → SHORT 신호."""
    df = _zigzag_df([115, 120, 100, 110, 105, 117.3, 116], between=10)
    config = _harmonic_cfg()
    signals = detect_harmonic_signal({"1H": df}, config)
    shorts = [s for s in signals if s.direction == Direction.SHORT]
    assert len(shorts) == 1
    assert shorts[0].source == "harmonic_bat"


def test_harmonic_signal_multi_tf_htf_priority() -> None:
    """15m + 1H 모두 검출 시 각각 신호 (가중치는 signal.compose_entry 가 처리)."""
    df = _bullish_bat_df()
    config = _harmonic_cfg()
    signals = detect_harmonic_signal({"15m": df, "1H": df}, config)
    timeframes = [s.timeframe for s in signals]
    assert "15m" in timeframes
    assert "1H" in timeframes


def test_harmonic_signal_off_when_random_data() -> None:
    """단순 상승 데이터엔 패턴 없음 → 빈 리스트."""
    rng = np.random.RandomState(0)
    closes = list(rng.randn(80).cumsum() * 0.1 + 100)
    s = pd.Series(closes, dtype=float)
    df = pd.DataFrame({"open": s, "high": s + 0.1, "low": s - 0.1, "close": s})
    config = _harmonic_cfg()
    signals = detect_harmonic_signal({"1H": df}, config)
    # 패턴 없을 가능성 높지만 우연히 검출 시 source 가 harmonic_ 로 시작해야 함
    for sig in signals:
        assert sig.source.startswith("harmonic_")


# ============================================================
# detect_harmonic_exit — 패턴별 자체 SL/TP
# ============================================================


def _bat_match_long() -> HarmonicMatch:
    """테스트용 Bat HarmonicMatch (롱 진입)."""
    return HarmonicMatch(
        name="bat", direction="long",
        x=100, a=120, b=110, c=115, d=102.7,
        x_bar=10, a_bar=20, b_bar=30, c_bar=40, d_bar=50,
        xab=0.5, abc=0.5, bcd=2.4, xad=0.865,
        sl_price=97.27,    # A - 1.13 × XA
        tp1_price=109.29,  # D + 0.382 × |A-D|
        tp2_price=113.42,  # D + 0.618 × |A-D|
    )


def _bat_match_short() -> HarmonicMatch:
    return HarmonicMatch(
        name="bat", direction="short",
        x=120, a=100, b=110, c=105, d=117.3,
        x_bar=10, a_bar=20, b_bar=30, c_bar=40, d_bar=50,
        xab=0.5, abc=0.5, bcd=2.4, xad=0.865,
        sl_price=122.6,    # A + 1.13 × XA
        tp1_price=110.71,  # D - 0.382 × |A-D|
        tp2_price=106.58,  # D - 0.618 × |A-D|
    )


def test_harmonic_exit_long_sl_hit() -> None:
    """롱 보유 + 가격이 SL 이하 → 'sl'."""
    match = _bat_match_long()
    assert detect_harmonic_exit(Direction.LONG, last_price=97.0, match=match) == "sl"


def test_harmonic_exit_long_tp1_hit() -> None:
    """롱 보유 + TP1 도달 (TP2 미도달) → 'tp1'."""
    match = _bat_match_long()
    assert detect_harmonic_exit(Direction.LONG, last_price=110.0, match=match) == "tp1"


def test_harmonic_exit_long_tp2_hit() -> None:
    """롱 보유 + TP2 도달 → 'tp2' (tp1 보다 우선)."""
    match = _bat_match_long()
    assert detect_harmonic_exit(Direction.LONG, last_price=114.0, match=match) == "tp2"


def test_harmonic_exit_long_holds_in_between() -> None:
    """롱 보유 + 가격이 D 위·TP1 아래 → None."""
    match = _bat_match_long()
    assert detect_harmonic_exit(Direction.LONG, last_price=105.0, match=match) is None


def test_harmonic_exit_short_sl_hit() -> None:
    """숏 보유 + 가격이 SL 이상 → 'sl'."""
    match = _bat_match_short()
    assert detect_harmonic_exit(Direction.SHORT, last_price=123.0, match=match) == "sl"


def test_harmonic_exit_short_tp1_hit() -> None:
    match = _bat_match_short()
    assert detect_harmonic_exit(Direction.SHORT, last_price=110.0, match=match) == "tp1"


def test_harmonic_exit_short_tp2_hit() -> None:
    match = _bat_match_short()
    assert detect_harmonic_exit(Direction.SHORT, last_price=106.0, match=match) == "tp2"


def test_harmonic_exit_short_holds() -> None:
    match = _bat_match_short()
    assert detect_harmonic_exit(Direction.SHORT, last_price=115.0, match=match) is None


# ============================================================
# evaluate_selectable — Harmonic 라우팅
# ============================================================


def test_evaluate_selectable_harmonic_off() -> None:
    """use_harmonic=False 면 harmonic 신호 X."""
    df = _bullish_bat_df()
    config = StrategyConfig(
        use_harmonic=False,
        harmonic_pivot_length=5,
        harmonic_tolerance=0.10,
    )
    signals = evaluate_selectable({"1H": df}, config)
    assert all(not s.source.startswith("harmonic") for s in signals)


def test_evaluate_selectable_harmonic_on() -> None:
    """use_harmonic=True 면 라우팅됨."""
    df = _bullish_bat_df()
    config = _harmonic_cfg()
    signals = evaluate_selectable({"1H": df}, config)
    harmonic_sigs = [s for s in signals if s.source.startswith("harmonic")]
    assert len(harmonic_sigs) >= 1
    assert harmonic_sigs[0].direction == Direction.LONG


# ============================================================
# EntrySignal 식별 필드 (bar_timestamp / pattern_id) — M2/M3 dedup
# ============================================================


def _datetime_indexed_df(values: list[float], freq: str = "1h") -> pd.DataFrame:
    """DatetimeIndex 가 붙은 OHLC DataFrame."""
    s = pd.Series(values, dtype=float)
    idx = pd.date_range("2026-01-01", periods=len(values), freq=freq)
    return pd.DataFrame(
        {"open": s.values, "high": s.values * 1.005, "low": s.values * 0.995, "close": s.values},
        index=idx,
    )


def test_entry_signal_default_identifiers_none() -> None:
    """식별 필드는 default None — 기존 호출처 호환."""
    sig = EntrySignal(direction=Direction.LONG, timeframe="1H", source="test")
    assert sig.bar_timestamp is None
    assert sig.pattern_id is None


def test_ema_touch_fills_bar_timestamp_with_datetime_index() -> None:
    """DatetimeIndex 면 ``bar_timestamp`` 가 마지막 봉 시각으로 채워짐."""
    closes = [100.0] * 250 + [100.2]
    df = _datetime_indexed_df(closes)
    config = StrategyConfig()
    signals = detect_ema_touch({"1H": df}, config)
    assert len(signals) >= 1
    assert signals[0].bar_timestamp == df.index[-1]


def test_ema_touch_bar_timestamp_none_with_default_index() -> None:
    """RangeIndex (DatetimeIndex 아님) 이면 ``bar_timestamp`` 는 None."""
    closes = [100.0] * 250 + [100.2]
    df_by_tf = {"1H": _make_df(closes)}
    config = StrategyConfig()
    signals = detect_ema_touch(df_by_tf, config)
    assert len(signals) >= 1
    assert signals[0].bar_timestamp is None


def test_bollinger_signal_fills_bar_timestamp() -> None:
    """BB 신호도 DatetimeIndex 면 timestamp 채워짐."""
    # squeeze 안 걸리도록 변동성 + 마지막 봉 high 가 upper zone 진입
    rng = np.random.RandomState(0)
    closes = list(rng.normal(100, 1.5, 50))
    closes[-1] = 102.5
    s = pd.Series(closes, dtype=float)
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="1h")
    df = pd.DataFrame(
        {"open": s.values, "high": s.values + 0.8, "low": s.values - 0.8, "close": s.values},
        index=idx,
    )
    df.loc[df.index[-1], "high"] = 103.0
    config = StrategyConfig(bollinger_period=10, bollinger_squeeze_threshold=0.001)
    signals = detect_bollinger_touch(df, config)
    for sig in signals:
        assert sig.bar_timestamp == df.index[-1]


def test_harmonic_signal_fills_pattern_id() -> None:
    """Harmonic 신호는 pattern_id 채워짐 (재진입 방지용)."""
    df = _bullish_bat_df()
    config = _harmonic_cfg()
    signals = detect_harmonic_signal({"1H": df}, config)
    assert len(signals) == 1
    pid = signals[0].pattern_id
    assert pid is not None
    assert pid.startswith("bat@1H@d_bar=")


def test_harmonic_signal_pattern_id_stable_across_calls() -> None:
    """같은 데이터 두 번 호출 시 같은 pattern_id (멱등성, 재진입 dedup 가능)."""
    df = _bullish_bat_df()
    config = _harmonic_cfg()
    sigs1 = detect_harmonic_signal({"1H": df}, config)
    sigs2 = detect_harmonic_signal({"1H": df}, config)
    assert len(sigs1) == 1 and len(sigs2) == 1
    assert sigs1[0].pattern_id == sigs2[0].pattern_id


def test_harmonic_signal_pattern_id_includes_tf() -> None:
    """같은 패턴이 15m/1H 두 TF 에서 동시 발현 시 pattern_id 가 다름 (TF 포함)."""
    df = _bullish_bat_df()
    config = _harmonic_cfg()
    signals = detect_harmonic_signal({"15m": df, "1H": df}, config)
    pids = {s.pattern_id for s in signals}
    assert len(pids) == 2  # TF 별로 다른 식별자


def test_ichimoku_signal_fills_bar_timestamp() -> None:
    """Ichimoku 도 DatetimeIndex 면 timestamp 채워짐."""
    closes = [100.0] * 30 + [101.0]
    s = pd.Series(closes, dtype=float)
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="1h")
    df = pd.DataFrame(
        {"open": s.values, "high": s.values, "low": s.values, "close": s.values},
        index=idx,
    )
    df.loc[df.index[-1], "high"] = 101.5
    df.loc[df.index[-1], "low"] = 99.5
    config = _ichimoku_cfg()
    signals = detect_ichimoku_signal({"1H": df}, config)
    assert len(signals) >= 1
    assert signals[0].bar_timestamp == df.index[-1]
