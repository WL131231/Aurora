"""pytest 공용 fixture — 모든 test 자동 적용 (autouse).

격리 정책:
    - ``trades_store`` 의 디스크 경로를 tmp 로 redirect → 사용자 PC ``~/.aurora/closed_trades.json``
      절대 안 건드림 (read 도, write 도).
    - ``release_check`` 의 캐시 / 마지막 체크 시각 reset → 테스트간 상태 누수 방지.

담당: 정용우
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_trades_store(monkeypatch, tmp_path):
    """모든 test 의 ``trades_store._path`` 를 tmp 로 redirect.

    Why: ``BotInstance.__init__`` 가 영속화 load 하므로 격리 안 하면
    사용자 PC ``~/.aurora/closed_trades.json`` 을 읽고/쓰고 → test 후 사용자 데이터 변형.
    """
    test_path = tmp_path / "closed_trades.json"
    monkeypatch.setattr(
        "aurora.interfaces.trades_store._path",
        lambda: test_path,
    )


@pytest.fixture(autouse=True)
def _isolate_active_position_store(monkeypatch, tmp_path):
    """``active_position_store._path`` 를 tmp 로 redirect (v0.1.26).

    Why: BotInstance.start 가 영속 plan load 시도 → 사용자 PC
    ``~/.aurora/active_position.json`` 읽기/쓰기 → test 격리 깨짐.
    """
    test_path = tmp_path / "active_position.json"
    monkeypatch.setattr(
        "aurora.interfaces.active_position_store._path",
        lambda: test_path,
    )
