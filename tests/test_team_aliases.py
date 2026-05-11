"""team_aliases.py — alias 매핑 로더 단위 테스트.

매핑 파일 동작 + meta 키 제외 + 잘못된 입력 fallback 검증.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from aurora.exchange.team_aliases import (
    _load_user_aliases,
    list_aliases,
    load_aliases,
    resolve_alias,
)


@pytest.fixture
def tmp_aliases_file(tmp_path: Path):
    """임시 team_aliases.json — _aliases_path 패치로 lookup 경로 override."""
    fake_path = tmp_path / "team_aliases.json"
    payload = {
        "_meta": {"purpose": "test fixture"},
        "장수": {"api_key": "key-jangsu", "api_secret": "secret-jangsu"},
        "정용우": {"api_key": "key-yongwoo", "api_secret": "secret-yongwoo"},
        "_internal": {"api_key": "should-skip", "api_secret": "x"},  # _ prefix 무시
    }
    fake_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with patch("aurora.exchange.team_aliases._aliases_path", return_value=fake_path):
        yield fake_path


def test_resolve_alias_returns_key_pair(tmp_aliases_file):
    """등록된 alias → (api_key, api_secret) 반환."""
    result = resolve_alias("장수")
    assert result == ("key-jangsu", "secret-jangsu")


def test_resolve_alias_unknown_returns_none(tmp_aliases_file):
    """미등록 alias → None (호출자 fallback 가능)."""
    assert resolve_alias("unknown") is None


def test_resolve_alias_empty_returns_none(tmp_aliases_file):
    """빈 문자열 → None (early exit)."""
    assert resolve_alias("") is None


def test_load_aliases_excludes_meta_keys(tmp_aliases_file):
    """``_meta`` / ``_internal`` 등 ``_`` prefix 키 제외."""
    aliases = load_aliases()
    assert "_meta" not in aliases
    assert "_internal" not in aliases
    assert set(aliases.keys()) == {"장수", "정용우"}


def test_list_aliases_returns_alias_names(tmp_aliases_file):
    """list_aliases — alias name 리스트 (순서 보존 X)."""
    names = list_aliases()
    assert sorted(names) == ["장수", "정용우"]


def test_load_aliases_missing_file_returns_empty(tmp_path: Path):
    """매핑 파일 미존재 → 빈 dict (fallback path 활성)."""
    nonexistent = tmp_path / "nope.json"
    with patch("aurora.exchange.team_aliases._aliases_path", return_value=nonexistent):
        assert load_aliases() == {}
        assert resolve_alias("장수") is None


def test_load_aliases_corrupted_json_returns_empty(tmp_path: Path):
    """JSON 파싱 실패 → 빈 dict (silent fallback)."""
    corrupted = tmp_path / "broken.json"
    corrupted.write_text("{ not valid json", encoding="utf-8")
    with patch("aurora.exchange.team_aliases._aliases_path", return_value=corrupted):
        assert load_aliases() == {}


def test_load_aliases_skips_malformed_entries(tmp_path: Path):
    """``api_key`` / ``api_secret`` 누락 entry 는 skip (회귀 보호)."""
    fake = tmp_path / "partial.json"
    payload = {
        "장수": {"api_key": "k", "api_secret": "s"},  # 정상
        "broken1": {"api_key": "k"},                   # secret 누락
        "broken2": "not a dict",                       # 타입 오류
    }
    fake.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with patch("aurora.exchange.team_aliases._aliases_path", return_value=fake):
        aliases = load_aliases()
        assert aliases == {"장수": {"api_key": "k", "api_secret": "s"}}


# ============================================================
# _load_user_aliases
# ============================================================


def test_load_user_aliases_returns_valid_entries(tmp_path, monkeypatch) -> None:
    """config_store 에 user_aliases 있으면 api_key/api_secret 쌍 반환."""
    from aurora.interfaces import config_store
    monkeypatch.setattr(config_store, "_config_path", lambda: tmp_path / "cfg.json")
    import json
    cfg = {"user_aliases": {"외부유저": {"api_key": "k1", "api_secret": "s1"}}}
    (tmp_path / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    result = _load_user_aliases()
    assert result == {"외부유저": {"api_key": "k1", "api_secret": "s1"}}


def test_load_user_aliases_skips_missing_secret(tmp_path, monkeypatch) -> None:
    """api_key 만 있고 api_secret 없는 항목 제외."""
    from aurora.interfaces import config_store
    monkeypatch.setattr(config_store, "_config_path", lambda: tmp_path / "cfg.json")
    import json
    cfg = {"user_aliases": {"불완전": {"api_key": "k1"}}}
    (tmp_path / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    assert _load_user_aliases() == {}


def test_load_user_aliases_returns_empty_when_no_key(tmp_path, monkeypatch) -> None:
    """config_store 에 user_aliases 키 없으면 빈 dict."""
    from aurora.interfaces import config_store
    monkeypatch.setattr(config_store, "_config_path", lambda: tmp_path / "cfg.json")
    import json
    (tmp_path / "cfg.json").write_text(json.dumps({"use_bollinger": False}), encoding="utf-8")
    assert _load_user_aliases() == {}


def test_load_user_aliases_wrong_type_returns_empty(tmp_path, monkeypatch) -> None:
    """user_aliases 가 dict 가 아니면 빈 dict."""
    from aurora.interfaces import config_store
    monkeypatch.setattr(config_store, "_config_path", lambda: tmp_path / "cfg.json")
    import json
    cfg = {"user_aliases": "not a dict"}
    (tmp_path / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    assert _load_user_aliases() == {}
