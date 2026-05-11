"""사용자 전략 설정 영구 저장소 — JSON 파일 기반.

저장 경로: ``~/.aurora/config.json``
홈 디렉토리 아래에 숨김 폴더로 두어 실행 파일(.exe) 배포 시에도 OS 기본 정책에 맞게 동작.

담당: 정용우
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _config_path() -> Path:
    """설정 파일 절대 경로 반환."""
    return Path.home() / ".aurora" / "config.json"


def load() -> dict:
    """JSON 파일에서 설정 dict 로드.

    파일이 없으면 빈 dict 를 반환하여 호출자가 기본값을 쓰도록 위임.
    JSON parse 실패 시 빈 dict 반환 + warn (시작 차단 X).
    """
    path = _config_path()
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("config_store: 설정 파일 로드 실패 — 기본값 사용 (%s)", e)
        return {}


def save(config: dict) -> None:
    """설정 dict 를 JSON 파일에 저장.

    디렉토리가 없으면 자동 생성.
    """
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
