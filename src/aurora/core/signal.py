"""신호 합성기 — Fixed + Selectable 지표 결과를 가중치 합산해서 최종 진입/청산 결정.

HTF 가중치:
    높은 시간프레임(HTF) 신호일수록 큰 가중치.
    선형 비슷한 점진 (옵션 b 채택). 거듭제곱(2배)은 1W가 너무 셈, 순수 선형은 차이 약함.

담당: 장수
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aurora.core.strategy import Direction, EntrySignal

# ============================================================
# HTF 가중치 (선형 비슷한 점진)
# ============================================================

TF_WEIGHTS: dict[str, int] = {
    "15m":  1,   # 단기 노이즈 — 가중치 최소 (단일 신호 진입 임계 = 1)
    "1H":   2,
    "2H":   3,
    "4H":   5,   # 멀티 TF entry 평가 주축
    "6H":   7,   # 6H/12H 는 향후 장기 포지션 확장 placeholder (현재 replay.py 미포함, fallback weight=1 발생 안 함)
    "12H": 10,
    "1D":  15,   # 장기 추세 — 1H 의 7.5배
    "1W":  25,   # 가장 무거움. 거듭제곱(2배) 대신 선형 비슷한 점진 (옵션 b 채택)
}
"""TF 별 점수 가중치 — 백테스트로 추후 튜닝 가능."""

DEFAULT_ENTRY_THRESHOLD: float = 1.0
"""진입 점수 임계값 — 최저 가중치(15m=1) 만으로도 진입 가능 (단일 신호 진입 정책)."""

MULTI_SIGNAL_BOOST: float = 1.3
"""v0.1.78 (C): 같은 방향 unique source 수 ≥ 2 시 score booster.
Tako AND 컨펌 패턴 차용 — 다중 지표 동시 정렬 시 거짓 신호 ↓ 본질."""


# ============================================================
# 의사결정 dataclass
# ============================================================


@dataclass(slots=True)
class CompositeDecision:
    """여러 신호를 합산한 최종 의사결정."""

    enter: bool
    direction: Direction | None
    triggered_by: list[str] = field(default_factory=list)
    score: float = 0.0
    long_score: float = 0.0
    short_score: float = 0.0


# ============================================================
# 가중치 합산
# ============================================================


def weighted_score(signal: EntrySignal) -> float:
    """단일 신호의 가중 점수 = strength × TF 가중치.

    timeframe 이 ``TF_WEIGHTS`` 에 없으면 1 로 fallback.
    """
    weight = TF_WEIGHTS.get(signal.timeframe, 1)
    return signal.strength * weight


def compose_entry(
    signals: list[EntrySignal],
    threshold: float = DEFAULT_ENTRY_THRESHOLD,
) -> CompositeDecision:
    """여러 신호를 가중치 합산해서 진입 결정.

    로직:
        1. 각 신호 점수 = ``strength × TF 가중치``
        2. 방향별로 점수 합산 (long_score, short_score)
        3. 점수 큰 방향이 임계값 ``threshold`` 이상이면 진입
        4. 양 방향 점수 동률이거나 둘 다 임계값 미달이면 보류

    Args:
        signals: 지표들에서 산출된 진입 신호 리스트.
        threshold: 진입에 필요한 최소 합산 점수 (기본 1.0 = 15m 단일 신호).

    Returns:
        CompositeDecision — enter / direction / 트리거 소스 / 점수.
    """
    long_score = 0.0
    short_score = 0.0
    long_sources: list[str] = []
    short_sources: list[str] = []

    for sig in signals:
        score = weighted_score(sig)
        source_label = f"{sig.source}@{sig.timeframe}"
        if sig.direction == Direction.LONG:
            long_score += score
            long_sources.append(source_label)
        elif sig.direction == Direction.SHORT:
            short_score += score
            short_sources.append(source_label)

    # v0.1.78 (C): 다중 신호 동시 정렬 booster — Tako AND 컨펌 패턴 차용.
    # 같은 방향 unique source 수 ≥ 2 면 score × MULTI_SIGNAL_BOOST.
    # Why: 두 개 이상 지표가 동시 같은 방향 정렬 시 거짓 신호 ↓ — 강한 진입.
    # OR 진입 (단일 신호도 진입) 본질 유지 + 강 신호 시 score 부스트로 임계값
    # 명확 통과 + 사용자 시각 "강한 신호" 본질.
    long_unique = len({s.split("@")[0] for s in long_sources})
    short_unique = len({s.split("@")[0] for s in short_sources})
    if long_unique >= 2:
        long_score *= MULTI_SIGNAL_BOOST
    if short_unique >= 2:
        short_score *= MULTI_SIGNAL_BOOST

    # 진입 결정 — 큰 방향이 임계값 넘어야 진입.
    # Why: 양 방향 점수가 같거나 둘 다 임계 미달 = 보류. 헷갈리는 시장에선 안 들어감 (안전).
    if long_score > short_score and long_score >= threshold:
        return CompositeDecision(
            enter=True,
            direction=Direction.LONG,
            triggered_by=long_sources,
            score=long_score,
            long_score=long_score,
            short_score=short_score,
        )
    if short_score > long_score and short_score >= threshold:
        return CompositeDecision(
            enter=True,
            direction=Direction.SHORT,
            triggered_by=short_sources,
            score=short_score,
            long_score=long_score,
            short_score=short_score,
        )

    # 보류 (동률·임계값 미달·신호 없음)
    return CompositeDecision(
        enter=False,
        direction=None,
        triggered_by=long_sources + short_sources,
        score=max(long_score, short_score),
        long_score=long_score,
        short_score=short_score,
    )


def compose_exit(
    current_direction: Direction,
    signals: list[EntrySignal],
) -> bool:
    """청산 신호 합성 — 현재 포지션과 반대 방향 신호가 임계값 넘으면 True.

    예: 롱 보유 중 short 신호 점수 합 > threshold → True (청산).
    """
    decision = compose_entry(signals)
    if not decision.enter or decision.direction is None:
        return False
    return decision.direction != current_direction
