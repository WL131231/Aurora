"""전역 설정 — 환경변수와 기본값을 한 곳에서 관리.

.env 파일 또는 환경변수에서 값을 읽어 Pydantic으로 검증한다.
모든 모듈은 `from untrack.config import settings` 형태로 접근.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """프로젝트 전역 설정."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ===== 실행 모드 =====
    run_mode: Literal["paper", "demo", "live"] = "paper"

    # ===== 거래소 키 (사용하는 것만 채우면 됨) =====
    bybit_api_key: str = ""
    bybit_api_secret: str = ""

    okx_api_key: str = ""
    okx_api_secret: str = ""
    okx_passphrase: str = ""

    binance_api_key: str = ""
    binance_api_secret: str = ""

    # ===== 텔레그램 =====
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ===== API 서버 =====
    api_host: str = "127.0.0.1"
    api_port: int = 8765

    # ===== 로그 =====
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ===== 경로 =====
    project_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2])

    @property
    def data_dir(self) -> Path:
        """캔들 캐시 등 데이터 저장 경로."""
        return self.project_root / "data"

    @property
    def logs_dir(self) -> Path:
        """로그 파일 저장 경로."""
        return self.project_root / "logs"


settings = Settings()
