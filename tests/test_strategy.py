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


def _bb_df(closes: list[float]) -> pd.DataFrame:
    s = pd.Series(closes, dtype=float)
    return pd.DataFrame({"open": s, "high": s, "low": s, "close": s})


def test_bb_touch_no_signal_when_inside_band() -> None:
    """가격이 밴드 안쪽이면 신호 X."""
    rng = np.random.RandomState(0)
    closes = list(rng.randn(50).cumsum() * 0.1 + 100)  # 100 근처에서 약하게 흔들림
    closes[-1] = 100.0  # 마지막을 정확히 평균값으로
    df = _bb_df(closes)
    config = StrategyConfig()
    assert detect_bollinger_touch(df, config) == []


def test_bb_touch_short_at_upper() -> None:
    """종가가 상단 이상 → 숏 신호.

    expansion 임계값 높여서 확장 필터 효과 제거 → 터치 룰만 검증.
    """
    rng = np.random.RandomState(0)
    closes = list(rng.randn(40) * 1.0 + 100)  # 정상 변동성
    # 마지막 close 를 상단 약간 위로 배치 (직전 값 + 5)
    closes.append(float(closes[-1]) + 5.0)
    df = _bb_df(closes)
    config = StrategyConfig(bollinger_expansion_threshold=100.0)
    signals = detect_bollinger_touch(df, config)
    assert any(s.direction == Direction.SHORT and s.source == "bollinger_upper" for s in signals)


def test_bb_touch_long_at_lower() -> None:
    """종가가 하단 이하 → 롱 신호 (확장 필터 효과 제거)."""
    rng = np.random.RandomState(7)
    closes = list(rng.randn(40) * 1.0 + 100)
    closes.append(float(closes[-1]) - 5.0)
    df = _bb_df(closes)
    config = StrategyConfig(bollinger_expansion_threshold=100.0)
    signals = detect_bollinger_touch(df, config)
    assert any(s.direction == Direction.LONG and s.source == "bollinger_lower" for s in signals)


def test_bb_touch_holds_on_extreme_expansion() -> None:
    """폭 확장 임계값을 매우 낮게 설정해 보류 동작 강제."""
    rng = np.random.RandomState(1)
    closes = list(rng.randn(40) * 1.0 + 100)
    closes.append(float(closes[-1]) + 5.0)  # 점프
    df = _bb_df(closes)
    # 매우 낮은 임계값 → 사소한 확장도 보류
    config = StrategyConfig(bollinger_expansion_threshold=1.01)
    signals = detect_bollinger_touch(df, config)
    assert signals == []


def test_bb_touch_empty_df() -> None:
    config = StrategyConfig()
    assert detect_bollinger_touch(pd.DataFrame(), config) == []


# ============================================================
# evaluate_selectable
# ============================================================


def test_evaluate_selectable_off_returns_empty() -> None:
    """모든 Selectable off → 빈 리스트."""
    closes = [100.0] * 30 + [99.0]  # BB 신호 가능한 데이터
    df_by_tf = {"1H": _bb_df(closes)}
    config = StrategyConfig(use_bollinger=False)
    assert evaluate_selectable(df_by_tf, config) == []


def test_evaluate_selectable_bollinger_on() -> None:
    """use_bollinger=True 면 BB 신호 라우팅됨."""
    rng = np.random.RandomState(7)
    closes = list(rng.randn(40) * 1.0 + 100)
    closes.append(float(closes[-1]) - 5.0)  # 하단 터치
    df_by_tf = {"1H": _bb_df(closes)}
    config = StrategyConfig(use_bollinger=True, bollinger_expansion_threshold=100.0)
    signals = evaluate_selectable(df_by_tf, config)
    longs = [s for s in signals if s.direction == Direction.LONG]
    assert len(longs) >= 1
    assert all(s.source.startswith("bollinger_") for s in longs)


def test_evaluate_selectable_no_1h_data() -> None:
    """BB 활성화여도 1H 데이터 없으면 신호 X."""
    config = StrategyConfig(use_bollinger=True)
    assert evaluate_selectable({"4H": _bb_df([100.0] * 30)}, config) == []
