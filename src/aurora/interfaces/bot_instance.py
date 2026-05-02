"""봇 인스턴스 싱글톤 — /start /stop 이 제어할 실제 객체.

현재는 빈 껍데기 (lifecycle 만 관리). 추후 strategy + exchange 어댑터
연결되면 run_loop 안에서 실제 매매 사이클 돌림.

담당: 정용우
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class BotInstance:
    """봇 lifecycle — start/stop 플래그 + 백그라운드 task 관리."""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            logger.warning("BotInstance.start: 이미 실행 중")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("BotInstance: 시작")

    async def stop(self) -> None:
        if not self._running:
            logger.warning("BotInstance.stop: 이미 중지됨")
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("BotInstance: 중지")

    async def _run_loop(self) -> None:
        """봇 메인 루프 — 추후 strategy.evaluate() + exchange.execute() 연결."""
        while self._running:
            # TODO(정용우): strategy 실행, 신호 발생 시 매매. 지금은 1초 sleep.
            await asyncio.sleep(1)


# 모듈 레벨 싱글톤
_instance: BotInstance | None = None


def get_instance() -> BotInstance:
    """싱글톤 접근자 — 첫 호출 시 lazy 생성."""
    global _instance
    if _instance is None:
        _instance = BotInstance()
    return _instance


def reset_for_test() -> None:
    """테스트 격리용 — 싱글톤 초기화."""
    global _instance
    _instance = None
