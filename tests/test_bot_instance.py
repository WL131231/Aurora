"""BotInstance 단위 테스트 — lifecycle start/stop 동작 검증."""

from __future__ import annotations

import pytest

from aurora.interfaces import bot_instance


@pytest.fixture(autouse=True)
def _reset() -> None:
    bot_instance.reset_for_test()


@pytest.mark.asyncio
async def test_start_stop_lifecycle() -> None:
    bot = bot_instance.get_instance()
    assert not bot.running
    await bot.start()
    assert bot.running
    await bot.stop()
    assert not bot.running


@pytest.mark.asyncio
async def test_double_start_warns() -> None:
    bot = bot_instance.get_instance()
    await bot.start()
    await bot.start()  # 두 번째는 무시
    assert bot.running
    await bot.stop()


@pytest.mark.asyncio
async def test_double_stop_warns() -> None:
    bot = bot_instance.get_instance()
    await bot.stop()  # 이미 중지 상태
    assert not bot.running


def test_get_instance_returns_singleton() -> None:
    a = bot_instance.get_instance()
    b = bot_instance.get_instance()
    assert a is b


def test_reset_for_test_clears_singleton() -> None:
    a = bot_instance.get_instance()
    bot_instance.reset_for_test()
    b = bot_instance.get_instance()
    assert a is not b
