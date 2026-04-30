"""Telegram 봇 — 원격 명령 + 알림.

담당: 팀원 D
"""

from __future__ import annotations

from aurora.config import settings


class TelegramBot:
    """Telegram 봇 래퍼."""

    def __init__(self) -> None:
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        # TODO(D): python-telegram-bot Application 인스턴스 생성

    async def start(self) -> None:
        """봇 polling 시작."""
        # TODO(D): 명령어 핸들러 등록 + start_polling
        raise NotImplementedError

    async def send_alert(self, text: str) -> None:
        """알림 메시지 전송."""
        # TODO(D)
        raise NotImplementedError

    # 명령어 핸들러 예시 (팀원 D가 채울 것)
    # async def cmd_start(...): ...
    # async def cmd_stop(...): ...
    # async def cmd_status(...): ...
    # async def cmd_setlev(...): ...
    # async def cmd_togglebb(...): ...
