"""core.strategy 단위 테스트 — EMA 터치 진입 + RSI 다이버전스 진입."""

from __future__ import annotations

import numpy as np
import pandas as pd

from aurora.core.strategy import (
    Direction,
    EntrySignal,
    StrategyConfig,
    detect_ema_touch,
    detect_rsi_divergence,
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
