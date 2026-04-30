"""신호 합성기 — Fixed + Selectable 지표 결과를 OR로 합쳐 최종 진입/청산 결정.

담당: 팀원 A
"""

from __future__ import annotations

from dataclasses import dataclass

from aurora.core.strategy import Direction, EntrySignal


@dataclass(slots=True)
class CompositeDecision:
    """최종 의사결정."""

    enter: bool
    direction: Direction | None
    triggered_by: list[str]  # 어떤 신호들이 발동했는지
    score: float  # 신호 강도 합산


def compose_entry(signals: list[EntrySignal]) -> CompositeDecision:
    """여러 신호를 OR 방식으로 합쳐 진입 의사결정.

    한 개라도 신호가 있으면 진입 (단일 신호 진입 정책).
    같은 방향 신호 여러 개면 strength 합산해서 점수화.
    반대 방향 신호 충돌 시 보류.
    """
    # TODO(A)
    raise NotImplementedError


def compose_exit(
    current_direction: Direction,
    signals: list[EntrySignal],
) -> bool:
    """청산 신호 합성 — 반대 방향 신호 발생 시 True."""
    # TODO(A)
    raise NotImplementedError
