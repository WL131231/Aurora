"""전역 설정 — 환경변수와 기본값을 한 곳에서 관리.

.env 파일 또는 환경변수에서 값을 읽어 Pydantic으로 검증한다.
모든 모듈은 `from aurora.config import settings` 형태로 접근.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_file_candidates() -> tuple[str, ...]:
    """``.env`` 검색 위치 우선순위 (v0.1.57 다중 path).

    Why: dev 환경 / frozen 환경 / 사용자 홈 모두 cover. pydantic-settings 가
    리스트 받으면 순차 검색 — 첫 발견 파일 사용 (dev > exe 옆 > 사용자 홈).

    검색 순서 (사용자 보고 v0.1.55 frozen 환경 .env 인식 X 사례):
        1. ``./.env`` — cwd (dev 환경 또는 launcher 옆)
        2. ``<sys.executable_dir>/.env`` — frozen .exe 옆
        3. ``~/.aurora/.env`` — 사용자 홈 (LocalAppData 와 별개)
        4. ``%LOCALAPPDATA%/Aurora/.env`` — Windows 표준 hidden 위치
    """
    paths: list[str] = [".env"]

    # frozen 환경 — Aurora.exe 옆
    try:
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            paths.append(str(exe_dir / ".env"))
    except OSError:
        pass

    # 사용자 홈 ~/.aurora/.env (가장 사용자 친화)
    try:
        paths.append(str(Path.home() / ".aurora" / ".env"))
    except (OSError, RuntimeError):
        pass

    # Windows LocalAppData
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        paths.append(str(Path(local_app) / "Aurora" / ".env"))

    return tuple(paths)


class Settings(BaseSettings):
    """프로젝트 전역 설정."""

    model_config = SettingsConfigDict(
        # v0.1.57: 다중 path 검색 (dev / frozen / 홈 / LocalAppData)
        env_file=_env_file_candidates(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ===== 실행 모드 =====
    # 디폴트 = demo (testing 단계, 실 자금 위험 X). 외부 사용자가 .exe 만 다운로드해도
    # .env 안 만들고 GUI alias nickname 입력만으로 즉시 demo 매매 시작 가능.
    # paper (신호만 검증) 또는 live (실 자금) 는 .env 명시해야 함 — 의도 강제로 안전.
    run_mode: Literal["paper", "demo", "live"] = "demo"

    # ===== 거래소 선택 =====
    # 데모 트레이딩 (Phase 2 진입) = Bybit 확정 (2026-05-03).
    # 실거래 (Phase 3) = 미정 (장수 협상 중).
    # 어댑터는 ccxt 통합이라 거래소 변경 시 이 값만 바꾸면 됨.
    default_exchange: Literal["bybit", "okx", "binance"] = "bybit"

    # ===== 거래소 키 (사용하는 것만 채우면 됨) =====
    bybit_api_key: str = ""
    bybit_api_secret: str = ""
    # Bybit Demo Trading 모드 — bybit.com 의 Demo Trading 기능 (≠ testnet.bybit.com).
    # Demo = $1M 가상 자금으로 실 시장 데이터 거래, 별도 API endpoint.
    # 안전 디폴트 True (실거래 시 .env 에서 명시 false). run_mode='demo' 와 짝.
    bybit_demo: bool = True

    okx_api_key: str = ""
    okx_api_secret: str = ""
    okx_passphrase: str = ""

    binance_api_key: str = ""
    binance_api_secret: str = ""

    # ===== 시장 메타 데이터 (선물 추세 인지, v0.1.53) =====
    # Coinalyze API key — 선물 OI / CVD / Funding 5분 주기 polling.
    # 무료 tier (40 calls/min) 충분. 미설정 시 라이브 봇 추세 인지 비활성 (default).
    # 발급: https://coinalyze.net/account/api/
    coinalyze_api_key: str = ""

    # ===== 텔레그램 =====
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ===== API 서버 =====
    api_host: str = "127.0.0.1"
    api_port: int = 8765

    # ===== 로그 =====
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ===== 시간대 =====
    timezone: str = "Asia/Seoul"  # KST. 모든 표시 시각 기준 (거래소 데이터는 UTC).

    # ===== 포지션 룰 =====
    max_positions_per_pair: int = 1  # 페어당 최대 동시 포지션 (Long+Short 동시 보유 불가)

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
