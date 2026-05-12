"""market series 헬퍼 함수 단위 테스트.

_floor_day_ms / _floor_hour_ms (binance_series) +
_weighted_avg (series_aggregator) 순수 함수 직접 검증.

네트워크/거래소 호출 없음 — 합성 입력만 사용.

담당: 정용우
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aurora.market.exchanges.binance_series import (
    _floor_day_ms,
    _floor_hour_ms,
)
from aurora.market.series_aggregator import _weighted_avg

# ── 상수 ─────────────────────────────────────────────────────────────

_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000


# ── _floor_day_ms ─────────────────────────────────────────────────────


def test_floor_day_ms_exact_boundary():
    """정확히 00:00 UTC → 그대로 반환."""
    ts = _DAY_MS * 100  # 임의 날짜 00:00
    assert _floor_day_ms(ts) == ts


def test_floor_day_ms_mid_day_floors_to_midnight():
    """하루 중간 타임스탬프 → 당일 00:00 UTC."""
    # 2025-01-01 13:00 UTC
    midnight = _DAY_MS * 20089  # 임의 날짜 00:00
    ts = midnight + _HOUR_MS * 13 + 59_999  # 13:00:00.059
    assert _floor_day_ms(ts) == midnight


def test_floor_day_ms_end_of_day_minus_1ms():
    """23:59:59.999 → 같은 날 00:00 UTC."""
    midnight = _DAY_MS * 20089
    ts = midnight + _DAY_MS - 1  # 23:59:59.999
    assert _floor_day_ms(ts) == midnight


def test_floor_day_ms_next_day_boundary():
    """다음날 00:00:00.000 → 다음날 midnight."""
    midnight = _DAY_MS * 20089
    next_day = midnight + _DAY_MS
    assert _floor_day_ms(next_day) == next_day


def test_floor_day_ms_zero():
    """0 → 0 (epoch 기준)."""
    assert _floor_day_ms(0) == 0


# ── _floor_hour_ms ────────────────────────────────────────────────────


def test_floor_hour_ms_exact_hour_boundary():
    """정각 → 그대로 반환."""
    ts = _HOUR_MS * 500
    assert _floor_hour_ms(ts) == ts


def test_floor_hour_ms_mid_hour_floors():
    """시간 중간 → 해당 정각으로 floor."""
    ts = _HOUR_MS * 100 + 999_999  # 100시간 + 999.999초
    assert _floor_hour_ms(ts) == _HOUR_MS * 100


def test_floor_hour_ms_end_of_hour_minus_1ms():
    """59:59.999 → 같은 정각으로 floor."""
    ts = _HOUR_MS * 77 + _HOUR_MS - 1
    assert _floor_hour_ms(ts) == _HOUR_MS * 77


def test_floor_hour_ms_next_hour_boundary():
    """다음 정각 00:00 → 다음 정각 그대로."""
    ts = _HOUR_MS * 77 + _HOUR_MS
    assert _floor_hour_ms(ts) == _HOUR_MS * 78


def test_floor_hour_ms_zero():
    """0 → 0."""
    assert _floor_hour_ms(0) == 0


def test_floor_hour_ms_consistent_with_floor_day_ms():
    """_floor_day_ms == _floor_hour_ms(00:00 시각) — 두 함수 정합."""
    midnight = _DAY_MS * 365
    assert _floor_day_ms(midnight) == _floor_hour_ms(midnight)


# ── _weighted_avg ─────────────────────────────────────────────────────


def _bar(val, weight):
    """SimpleNamespace bar stub — val=field_name, wt=weight_field."""
    return SimpleNamespace(val=val, wt=weight)


def test_weighted_avg_basic():
    """기본 가중 평균 — 큰 가중치 쪽으로 치우침."""
    bars = [
        ("t1", _bar(10.0, 1.0)),
        ("t2", _bar(20.0, 3.0)),  # wt 3 → 더 무거움
    ]
    result = _weighted_avg(bars, "val", "wt")
    # (10*1 + 20*3) / (1+3) = 70/4 = 17.5
    assert result == pytest.approx(17.5)


def test_weighted_avg_equal_weights_equals_simple_mean():
    """동일 가중치 → 단순 평균과 동일."""
    bars = [("t1", _bar(4.0, 1.0)), ("t2", _bar(6.0, 1.0))]
    result = _weighted_avg(bars, "val", "wt")
    assert result == pytest.approx(5.0)


def test_weighted_avg_all_weights_none_falls_back_to_simple_mean():
    """가중치 전부 None → 단순 평균 fallback."""
    bars = [("t1", _bar(10.0, None)), ("t2", _bar(20.0, None))]
    result = _weighted_avg(bars, "val", "wt")
    assert result == pytest.approx(15.0)


def test_weighted_avg_all_weights_zero_falls_back_to_simple_mean():
    """가중치 전부 0 → 단순 평균 fallback."""
    bars = [("t1", _bar(10.0, 0.0)), ("t2", _bar(20.0, 0.0))]
    result = _weighted_avg(bars, "val", "wt")
    assert result == pytest.approx(15.0)


def test_weighted_avg_val_none_excluded():
    """val=None 항목 제외 — 나머지만 평균."""
    bars = [("t1", _bar(None, 1.0)), ("t2", _bar(20.0, 1.0))]
    result = _weighted_avg(bars, "val", "wt")
    assert result == pytest.approx(20.0)


def test_weighted_avg_all_vals_none_returns_none():
    """val 전부 None → None 반환."""
    bars = [("t1", _bar(None, 1.0)), ("t2", _bar(None, 2.0))]
    assert _weighted_avg(bars, "val", "wt") is None


def test_weighted_avg_empty_list_returns_none():
    """빈 리스트 → None 반환."""
    assert _weighted_avg([], "val", "wt") is None


def test_weighted_avg_missing_field_excluded():
    """field_name 속성 없는 bar → getattr None → 제외."""
    bars = [("t1", SimpleNamespace(wt=1.0)), ("t2", _bar(30.0, 2.0))]
    result = _weighted_avg(bars, "val", "wt")
    assert result == pytest.approx(30.0)


def test_weighted_avg_single_entry():
    """bar 1개 — 가중치 있어도 그 값 그대로."""
    bars = [("t1", _bar(42.0, 5.0))]
    assert _weighted_avg(bars, "val", "wt") == pytest.approx(42.0)


def test_weighted_avg_mixed_weight_some_none():
    """가중치 일부만 None — None bar 는 단순합에 포함, 가중합에는 제외."""
    # t1: val=10, wt=None → simple_vals 에 추가, 가중합 기여 X
    # t2: val=20, wt=4.0  → 가중합 기여 O
    # den=4 > 0 → 가중 평균 분기 → num/den = (20*4)/4 = 20.0
    # (t1 의 val 은 simple_vals 에만 들어가고 den>0 이면 단순 평균 분기 미사용)
    bars = [("t1", _bar(10.0, None)), ("t2", _bar(20.0, 4.0))]
    result = _weighted_avg(bars, "val", "wt")
    assert result == pytest.approx(20.0)
