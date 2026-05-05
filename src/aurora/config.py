"""전역 설정 — 환경변수와 기본값을 한 곳에서 관리.

데스크탑: pydantic-settings 로 .env 검증.
Android (Chaquopy): pydantic-core wheel 부재 → dataclass + os.getenv 로더.
모든 모듈은 ``from aurora.config import settings`` 형태로 접근.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

# Android 환경 판별 — aurora_bridge 가 모듈 로드 전 AURORA_PLATFORM=android 주입
# Why: `import android` bare import 는 Chaquopy 에서 ImportError 발생.
_IS_ANDROID = os.getenv("AURORA_PLATFORM") == "android"


if _IS_ANDROID:
    import dataclasses

    def _s(key: str, default: str = "") -> str:
        """문자열 env 읽기."""
        return os.getenv(key, default)

    def _b(key: str, default: bool = False) -> bool:
        """불리언 env 읽기 — '0'/'false'/'no' 이면 False."""
        v = os.getenv(key)
        if v is None:
            return default
        return v.lower() not in ("0", "false", "no")

    def _i(key: str, default: int = 0) -> int:
        """정수 env 읽기."""
        try:
            return int(os.getenv(key, ""))
        except (ValueError, TypeError):
            return default

    @dataclasses.dataclass
    class Settings:  # type: ignore[no-redef]
        """Android 전용 설정 — dataclass + os.getenv (pydantic 없이).

        aurora_bridge._load_env() 이후 인스턴스화되므로
        .env 의 모든 값이 os.getenv 로 조회 가능.
        """

        # ===== 실행 모드 =====
        run_mode: str = dataclasses.field(default_factory=lambda: _s("RUN_MODE", "demo"))

        # ===== 거래소 선택 =====
        default_exchange: str = dataclasses.field(
            default_factory=lambda: _s("DEFAULT_EXCHANGE", "bybit")
        )

        # ===== 거래소 키 =====
        bybit_api_key: str = dataclasses.field(default_factory=lambda: _s("BYBIT_API_KEY"))
        bybit_api_secret: str = dataclasses.field(default_factory=lambda: _s("BYBIT_API_SECRET"))
        # 안전 디폴트 True — 실거래 시 .env 에서 BYBIT_DEMO=false 명시
        bybit_demo: bool = dataclasses.field(default_factory=lambda: _b("BYBIT_DEMO", True))

        okx_api_key: str = dataclasses.field(default_factory=lambda: _s("OKX_API_KEY"))
        okx_api_secret: str = dataclasses.field(default_factory=lambda: _s("OKX_API_SECRET"))
        okx_passphrase: str = dataclasses.field(default_factory=lambda: _s("OKX_PASSPHRASE"))

        binance_api_key: str = dataclasses.field(default_factory=lambda: _s("BINANCE_API_KEY"))
        binance_api_secret: str = dataclasses.field(
            default_factory=lambda: _s("BINANCE_API_SECRET")
        )

        # ===== 텔레그램 =====
        telegram_bot_token: str = dataclasses.field(
            default_factory=lambda: _s("TELEGRAM_BOT_TOKEN")
        )
        telegram_chat_id: str = dataclasses.field(
            default_factory=lambda: _s("TELEGRAM_CHAT_ID")
        )

        # ===== API 서버 =====
        api_host: str = dataclasses.field(default_factory=lambda: _s("API_HOST", "127.0.0.1"))
        api_port: int = dataclasses.field(default_factory=lambda: _i("API_PORT", 8765))

        # ===== 로그 =====
        log_level: str = dataclasses.field(default_factory=lambda: _s("LOG_LEVEL", "INFO"))

        # ===== 시간대 =====
        timezone: str = dataclasses.field(
            default_factory=lambda: _s("TIMEZONE", "Asia/Seoul")
        )

        # ===== 포지션 룰 =====
        max_positions_per_pair: int = dataclasses.field(
            default_factory=lambda: _i("MAX_POSITIONS_PER_PAIR", 1)
        )

        # ===== 경로 =====
        # AURORA_DATA_DIR = aurora_bridge._load_env() 에서 filesDir 로 주입
        project_root: Path = dataclasses.field(
            default_factory=lambda: Path(
                os.getenv("AURORA_DATA_DIR") or "/data/data/com.aurora.trading/files"
            )
        )

        @property
        def data_dir(self) -> Path:
            """캔들 캐시 등 데이터 저장 경로."""
            return self.project_root / "data"

        @property
        def logs_dir(self) -> Path:
            """로그 파일 저장 경로."""
            return self.project_root / "logs"

    settings = Settings()

else:
    from pydantic import Field
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class Settings(BaseSettings):  # type: ignore[no-redef]
        """프로젝트 전역 설정."""

        model_config = SettingsConfigDict(
            env_file=".env",
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
