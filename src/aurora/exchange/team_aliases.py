"""팀 단순화 alias → Bybit Demo 키 매핑 (testing 단계 한정).

사용자가 GUI 거래소 view 에 nickname (예: ``"장수"``) 입력 → ``resolve_alias``
가 ``data/team_aliases.json`` lookup → 실 ``(api_key, api_secret)`` 반환.
``BotInstance.configure_from_settings`` 가 alias 우선 적용, 매핑 실패 시 ``.env``
fallback.

⚠️ Testing 한정 (2026-05-03 발급, ~1~2주 사용 후 cleanup):
    - Bybit Demo 키만 매핑 (실 자금 X)
    - Aurora repo private 상태
    - cleanup: ``data/team_aliases.json`` 메타 ``cleanup_steps`` 참조

영역: 거래소 어댑터 의존성 (CcxtClient 사용 전 키 결정 단계)
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _aliases_path() -> Path:
    """매핑 파일 절대 경로 — 소스 트리 / PyInstaller 번들 모두 대응.

    PyInstaller ``--add-data "data;data"`` 빌드 시 ``sys._MEIPASS`` 아래.
    소스 실행 시 ``project_root/data/team_aliases.json``.
    """
    if hasattr(sys, "_MEIPASS"):
        # PyInstaller 환경 (onefile / folder)
        return Path(sys._MEIPASS) / "data" / "team_aliases.json"  # type: ignore[attr-defined]
    # 소스 트리: src/aurora/exchange/team_aliases.py 기준 ../../../data/...
    return Path(__file__).resolve().parents[3] / "data" / "team_aliases.json"


def load_aliases() -> dict[str, dict[str, str]]:
    """매핑 파일 로드 — 파일 없거나 손상 시 빈 dict (fallback 가능).

    Returns:
        ``{alias: {"api_key": ..., "api_secret": ...}}`` 형식.
        ``_meta`` 키는 자동 제외 (실제 alias 만).
    """
    path = _aliases_path()
    if not path.exists():
        logger.debug("team_aliases.json 미존재 (%s) — alias 매핑 비활성", path)
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("team_aliases.json 로드 실패 (%s): %s — fallback to .env", path, e)
        return {}
    # _meta 키 제외 + 형식 검증
    result: dict[str, dict[str, str]] = {}
    for alias, entry in raw.items():
        if alias.startswith("_"):
            continue
        if not isinstance(entry, dict):
            continue
        if "api_key" in entry and "api_secret" in entry:
            result[alias] = {
                "api_key": str(entry["api_key"]),
                "api_secret": str(entry["api_secret"]),
            }
    return result


def _load_user_aliases() -> dict[str, dict[str, str]]:
    """``config_store.json`` 의 ``user_aliases`` dict 로드 (외부 사용자 매핑).

    팀 alias (``data/team_aliases.json``) 와 분리 — 각 사용자 PC 한정 저장,
    repo 절대 commit X (``.gitignore`` 처리됨).

    Returns:
        ``{nickname: {"api_key": ..., "api_secret": ...}}`` 형식.
        ``config_store`` 비어있거나 ``user_aliases`` 키 없으면 빈 dict.
    """
    # 함수 내부 import — 순환 의존 회피 (config_store → exchange → team_aliases)
    from aurora.interfaces import config_store

    cfg = config_store.load() or {}
    raw = cfg.get("user_aliases", {})
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for nickname, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        if "api_key" in entry and "api_secret" in entry:
            result[nickname] = {
                "api_key": str(entry["api_key"]),
                "api_secret": str(entry["api_secret"]),
            }
    return result


def resolve_alias(alias: str) -> tuple[str, str] | None:
    """alias → ``(api_key, api_secret)`` lookup — 팀 우선, 외부 사용자 fallback.

    Lookup 순서:
        1. ``data/team_aliases.json`` (팀 4명, repo commit, testing 한정)
        2. ``config_store.json`` 의 ``user_aliases`` (외부 사용자, PC 한정)

    Args:
        alias: 사용자 nickname (예: ``"장수"`` / 외부 사용자 등록 nickname).
            빈 문자열이거나 양쪽 매핑 미존재 시 ``None``.

    Returns:
        ``(api_key, api_secret)`` 튜플. lookup 실패 시 ``None``.
    """
    if not alias:
        return None

    # 1순위: 팀 alias (공유 매핑)
    team = load_aliases()
    entry = team.get(alias)
    if entry is not None:
        return entry["api_key"], entry["api_secret"]

    # 2순위: 외부 사용자 alias (PC 한정)
    # Why: 팀 nickname 과 충돌 시 팀이 우선 — 외부 사용자가 같은 nickname 등록해도
    # 본인 PC 의 config_store 에는 저장되지만 lookup 시 팀 키 사용됨.
    user = _load_user_aliases()
    entry = user.get(alias)
    if entry is not None:
        return entry["api_key"], entry["api_secret"]

    return None


def list_aliases() -> list[str]:
    """등록된 alias 목록 — GUI 자동완성·디버깅용."""
    return list(load_aliases().keys())
