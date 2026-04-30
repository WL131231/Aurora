"""스모크 테스트 — 패키지가 import 되는지만 확인."""

from __future__ import annotations


def test_package_import() -> None:
    import aurora

    assert aurora.__version__ == "0.1.0"


def test_config_loads() -> None:
    from aurora.config import settings

    assert settings.run_mode in ("paper", "demo", "live")
