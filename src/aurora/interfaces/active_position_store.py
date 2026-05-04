"""활성 포지션 영속화 — 봇 재시작 후 자기 포지션 복원 (v0.1.26).

문제 (v0.1.25 까지):
    봇 진입 → ``_plan`` 메모리 보관 → .exe 종료 → 다시 시작 → ``_plan`` 휘발 →
    거래소 측엔 포지션 살아있는데 봇은 자기 거 모름 → "외부 포지션" 으로 잘못 식별 →
    Aurora 진입 영영 skip (사용자가 수동 청산해야만 재개). 사용자 마찰 큼.

해결 (v0.1.26):
    1. 진입 직후 ``RiskPlan`` + 메타 (symbol, triggered_by, opened_at_ts, remaining_qty,
       tp_hits) 를 ``~/.aurora/active_position.json`` 에 atomic 저장.
    2. 매 변경 시점 (partial 청산, TP hit) 다시 save.
    3. 청산 완료 시 clear (또는 외부 청산 감지 시 reset).
    4. 봇 시작 시 load → 거래소 측 fetch_position 으로 정합성 검증 → Executor 복원.

설계 노트:
    - **단일 활성 포지션 가정** (Phase 1 = 페어당 1개) → 단일 record JSON.
    - **dataclass 직렬화**: enum (TrailingMode) → ``.value``, NamedTuple → list.
    - **호환성**: 손상 / 호환 X record 는 None 반환 + warn (시작 차단 X).

담당: 정용우
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from aurora.core.risk import PositionSize, RiskPlan, TrailingMode

logger = logging.getLogger(__name__)


def _path() -> Path:
    """저장 파일 경로 — ``~/.aurora/active_position.json``."""
    return Path.home() / ".aurora" / "active_position.json"


def save(
    plan: RiskPlan,
    symbol: str,
    triggered_by: list[str],
    opened_at_ts: int,
    remaining_qty: float,
    tp_hits: int,
) -> None:
    """현재 활성 포지션 state 를 디스크에 atomic 저장.

    매 진입 / partial 청산 / TP hit 시 BotInstance 가 호출.
    """
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "symbol": symbol,
        "triggered_by": list(triggered_by),
        "opened_at_ts": int(opened_at_ts),
        "remaining_qty": float(remaining_qty),
        "tp_hits": int(tp_hits),
        "plan": {
            "entry_price": plan.entry_price,
            "direction": plan.direction,
            "leverage": plan.leverage,
            "position": [
                plan.position.notional_usd,
                plan.position.margin_usd,
                plan.position.coin_amount,
            ],
            "tp_prices": list(plan.tp_prices),
            "sl_price": plan.sl_price,
            "trailing_mode": plan.trailing_mode.value,
        },
    }

    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp.replace(p)  # atomic
    except OSError as e:
        logger.warning("active_position_store save 실패: %s", e)
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def load() -> dict | None:
    """파일에서 dict 복원. 파일 없거나 손상 시 None.

    plan 재구성은 ``reconstruct_plan()`` 별도 호출.
    """
    p = _path()
    if not p.exists():
        return None
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("active_position_store load 실패: %s", e)
        return None
    if not isinstance(data, dict):
        return None
    return data


def reconstruct_plan(plan_dict: dict) -> RiskPlan | None:
    """저장된 plan dict → ``RiskPlan`` 재구성. 호환 안 되면 None."""
    try:
        pos = plan_dict["position"]
        return RiskPlan(
            entry_price=float(plan_dict["entry_price"]),
            direction=str(plan_dict["direction"]),
            leverage=int(plan_dict["leverage"]),
            position=PositionSize(
                notional_usd=float(pos[0]),
                margin_usd=float(pos[1]),
                coin_amount=float(pos[2]),
            ),
            tp_prices=[float(x) for x in plan_dict["tp_prices"]],
            sl_price=float(plan_dict["sl_price"]),
            trailing_mode=TrailingMode(plan_dict["trailing_mode"]),
        )
    except (KeyError, ValueError, TypeError, IndexError) as e:
        logger.warning("active_position_store reconstruct_plan 실패: %s", e)
        return None


def clear() -> None:
    """저장 파일 제거 — 청산 완료 / 외부 변경 감지 시."""
    p = _path()
    if p.exists():
        try:
            p.unlink()
        except OSError as e:
            logger.warning("active_position_store clear 실패: %s", e)
