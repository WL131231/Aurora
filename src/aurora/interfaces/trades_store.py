"""거래내역 영속화 저장소 — JSON 파일 기반 (v0.1.25).

봇 재시작 / .exe 종료 후에도 ``ClosedTrade`` 기록이 살아남도록 disk persist.

저장 경로: ``~/.aurora/closed_trades.json``
    - ``config_store`` 와 같은 폴더 (``~/.aurora/`` hidden)
    - 사용자 홈 → 휴대성 + .gitignore 무관 + LocalAppData/Aurora (런처 데이터) 와 분리

흐름:
    - 봇 시작 시 ``load()`` 한 번 호출 → 기존 buffer 채움
    - 매 close_position 시 in-memory deque append + ``save(list(deque))`` 호출
    - 디스크 쓰기는 거래 발생 빈도 (분~시간 단위) 라 통째 rewrite OK

상한:
    - in-memory buffer = ``deque(maxlen=100)`` (UI 표 표시용)
    - persist buffer = ``MAX_PERSIST=500`` (재시작 후 100 보장 + 통계 풍부)

원자적 쓰기:
    tmp 파일 → ``Path.replace()`` (POSIX/Windows 모두 atomic).
    중간 crash 시 기존 파일 유지 (부분 write 손실 X).

담당: 정용우
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from aurora.exchange.execution import ClosedTrade

logger = logging.getLogger(__name__)


# 영속 buffer 상한 — 메모리 deque(100) 보다 크게 → 재시작 후도 100 채움 + 통계 더 풍부.
# 500 record × ~300 bytes = ~150 KB, 디스크 부담 무시 가능.
MAX_PERSIST = 500


def _path() -> Path:
    """저장 파일 경로 — ``~/.aurora/closed_trades.json``."""
    return Path.home() / ".aurora" / "closed_trades.json"


def load() -> list[ClosedTrade]:
    """파일에서 trade 리스트 복원. 파일 없거나 손상 시 빈 리스트.

    호환성: 신규 필드 추가 시 누락된 record 는 default 채워 복원되도록
    ``ClosedTrade(**item)`` 사용 — dataclass field default 가 fallback.
    필드 누락이 default 없는 record 는 record 자체 skip + warn.
    """
    p = _path()
    if not p.exists():
        return []
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("trades_store load 실패 (빈 리스트 복원): %s", e)
        return []

    if not isinstance(data, list):
        logger.warning("trades_store: list 아님 — 빈 리스트 복원")
        return []

    out: list[ClosedTrade] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            out.append(ClosedTrade(**item))
        except TypeError as e:
            # 신규 필드 누락 / 정의 안 된 필드 — record skip
            logger.debug("trades_store record skip: %s", e)
            continue
    return out


def save(trades: list[ClosedTrade]) -> None:
    """trade 리스트를 파일에 저장 — 통째 rewrite, atomic (tmp + replace).

    ``MAX_PERSIST`` 초과분은 잘림 (가장 오래된 것 drop). 호출자 책임 X.
    """
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)

    # 최근 MAX_PERSIST 만 (가장 오래된 trade drop)
    recent = trades[-MAX_PERSIST:] if len(trades) > MAX_PERSIST else trades
    payload = [asdict(t) for t in recent]

    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp.replace(p)  # atomic — 기존 파일 유지 (write 도중 crash 안전)
    except OSError as e:
        logger.warning("trades_store save 실패: %s", e)
        # tmp 잔재 정리 (best-effort)
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
