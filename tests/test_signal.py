"""core.signal 단위 테스트 — TF 가중치 + 합성 의사결정."""

from __future__ import annotations

import pytest

from aurora.core.signal import (
    DEFAULT_ENTRY_THRESHOLD,
    TF_WEIGHTS,
    compose_entry,
    compose_exit,
    weighted_score,
)
from aurora.core.strategy import Direction, EntrySignal


def _sig(direction: Direction, tf: str, strength: float = 1.0, source: str = "test") -> EntrySignal:
    return EntrySignal(direction=direction, timeframe=tf, source=source, strength=strength)


# ============================================================
# 가중치 테이블
# ============================================================


def test_tf_weights_monotonic_increasing() -> None:
    """TF 가중치는 시간프레임 순서대로 단조 증가해야 함."""
    order = ["15m", "1H", "2H", "4H", "6H", "12H", "1D", "1W"]
    weights = [TF_WEIGHTS[tf] for tf in order]
    assert weights == sorted(weights)
    assert weights[0] < weights[-1]


def test_tf_weights_match_spec() -> None:
    """선형 비슷한 점진 (옵션 b) 값 확정."""
    expected = {
        "15m": 1, "1H": 2, "2H": 3, "4H": 5,
        "6H": 7, "12H": 10, "1D": 15, "1W": 25,
    }
    assert TF_WEIGHTS == expected


# ============================================================
# weighted_score
# ============================================================


def test_weighted_score_uses_tf_weight() -> None:
    s = _sig(Direction.LONG, "4H", strength=1.0)
    assert weighted_score(s) == pytest.approx(5.0)  # 4H = 5


def test_weighted_score_strength_multiplied() -> None:
    s = _sig(Direction.LONG, "1D", strength=0.5)
    assert weighted_score(s) == pytest.approx(7.5)  # 1D = 15, ×0.5


def test_weighted_score_unknown_tf_falls_back_to_1() -> None:
    s = _sig(Direction.LONG, "ZZ", strength=1.0)
    assert weighted_score(s) == pytest.approx(1.0)


# ============================================================
# compose_entry
# ============================================================


def test_compose_no_signals_holds() -> None:
    d = compose_entry([])
    assert d.enter is False
    assert d.direction is None


def test_compose_single_15m_signal_enters() -> None:
    """15m 단독 신호 (점수 1) = 임계값 1.0 충족 → 진입."""
    d = compose_entry([_sig(Direction.LONG, "15m")])
    assert d.enter is True
    assert d.direction == Direction.LONG
    assert d.score == pytest.approx(1.0)


def test_compose_high_tf_dominates() -> None:
    """HTF 신호 점수가 LTF 신호보다 큼."""
    d = compose_entry([_sig(Direction.SHORT, "1W")])  # 25점
    assert d.enter is True
    assert d.direction == Direction.SHORT
    assert d.score == pytest.approx(25.0)


def test_compose_conflicting_directions_higher_score_wins() -> None:
    """양 방향 충돌 시 점수 큰 쪽 승리."""
    signals = [
        _sig(Direction.LONG, "15m"),    # 1
        _sig(Direction.SHORT, "1H"),    # 2
    ]
    d = compose_entry(signals)
    assert d.enter is True
    assert d.direction == Direction.SHORT
    assert d.long_score == pytest.approx(1.0)
    assert d.short_score == pytest.approx(2.0)


def test_compose_tied_directions_holds() -> None:
    """양 방향 점수 동률이면 보류."""
    signals = [
        _sig(Direction.LONG, "1H"),     # 2
        _sig(Direction.SHORT, "1H"),    # 2
    ]
    d = compose_entry(signals)
    assert d.enter is False


def test_compose_below_threshold_holds() -> None:
    """점수 < threshold 면 보류."""
    d = compose_entry([_sig(Direction.LONG, "15m", strength=0.5)], threshold=2.0)
    # 0.5 × 1 = 0.5 < 2.0 → 보류
    assert d.enter is False


def test_compose_aggregates_multi_tf_same_direction() -> None:
    """같은 방향 신호 여러 TF 점수 합산."""
    signals = [
        _sig(Direction.LONG, "1H"),     # 2
        _sig(Direction.LONG, "4H"),     # 5
        _sig(Direction.LONG, "1D"),     # 15
    ]
    d = compose_entry(signals)
    assert d.enter is True
    assert d.direction == Direction.LONG
    assert d.score == pytest.approx(22.0)
    assert len(d.triggered_by) == 3


def test_compose_default_threshold() -> None:
    """기본 임계값 = 1.0 (15m 단일 신호 가능)."""
    assert DEFAULT_ENTRY_THRESHOLD == pytest.approx(1.0)


# ============================================================
# compose_exit
# ============================================================


def test_compose_exit_long_position_sees_short_signal() -> None:
    """롱 보유 중 short 점수가 임계값 이상이면 청산."""
    signals = [_sig(Direction.SHORT, "1D")]  # 15점
    assert compose_exit(Direction.LONG, signals) is True


def test_compose_exit_long_position_no_short_signal() -> None:
    signals = [_sig(Direction.LONG, "1H")]
    assert compose_exit(Direction.LONG, signals) is False


def test_compose_exit_no_signals() -> None:
    assert compose_exit(Direction.LONG, []) is False
