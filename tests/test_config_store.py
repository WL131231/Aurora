"""config_store 단위 테스트 — load/save 동작 및 파일 I/O 검증.

⚠️ 진짜 ``~/.aurora/config.json`` 을 절대 건들지 않도록 monkeypatch 로
``_config_path`` 를 tmp_path 하위 경로로 교체해서 격리.

담당: 정용우
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aurora.interfaces import config_store


@pytest.fixture()
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``_config_path`` 를 tmp_path 하위 가짜 경로로 교체하고 해당 Path 반환.

    디렉토리는 아직 생성하지 않음 — save() 가 만드는지 테스트하기 위함.
    """
    fake_path = tmp_path / ".aurora" / "config.json"
    monkeypatch.setattr(config_store, "_config_path", lambda: fake_path)
    return fake_path


# ============================================================
# 테스트
# ============================================================


def test_load_returns_empty_when_file_not_exists(isolated_config: Path) -> None:
    """파일이 없을 때 load() 가 빈 dict 를 반환해야 한다."""
    assert not isolated_config.exists()
    result = config_store.load()
    assert result == {}


def test_save_creates_file_and_dir(isolated_config: Path) -> None:
    """save() 호출 시 디렉토리와 파일이 생성돼야 한다."""
    config_store.save({"leverage": 20})
    assert isolated_config.parent.is_dir()
    assert isolated_config.is_file()


def test_save_then_load_round_trip(isolated_config: Path) -> None:
    """save() 후 load() 하면 동일한 dict 가 반환돼야 한다."""
    data = {
        "use_bollinger": True,
        "use_ma_cross": False,
        "leverage": 30,
        "risk_pct": 0.02,
        "full_seed": True,
    }
    config_store.save(data)
    loaded = config_store.load()
    assert loaded == data


def test_save_unicode_korean(isolated_config: Path) -> None:
    """한국어 값이 깨지지 않고 저장/로드돼야 한다 (ensure_ascii=False 검증)."""
    data = {"message": "봇 시작됨", "mode": "실거래"}
    config_store.save(data)

    # 파일 원문에도 한국어가 이스케이프 없이 들어있어야 함.
    raw_text = isolated_config.read_text(encoding="utf-8")
    assert "봇 시작됨" in raw_text
    assert "실거래" in raw_text

    loaded = config_store.load()
    assert loaded["message"] == "봇 시작됨"
    assert loaded["mode"] == "실거래"


def test_load_corrupt_json_returns_empty(isolated_config: Path) -> None:
    """JSON 파싱 실패 — 빈 dict 반환 (시작 차단 X)."""
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text("not-json {{{", encoding="utf-8")
    assert config_store.load() == {}


def test_load_corrupt_json_logs_warning(isolated_config: Path, caplog) -> None:
    """JSON 파싱 실패 시 WARNING 로그 남김."""
    import logging
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text("broken", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="aurora.interfaces.config_store"):
        config_store.load()
    assert any("설정 파일 로드 실패" in r.message for r in caplog.records)
