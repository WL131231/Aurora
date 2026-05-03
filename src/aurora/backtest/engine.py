"""백테스트 엔진 — Walk-forward 시뮬.

기존 trading_bot/adaptive_backtest.py를 기반으로 AI 호출 부분 제거 후 이식.

담당: 팀원 C
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from aurora.backtest.stats import TradeRecord


@dataclass(slots=True)
class BacktestConfig:
    """백테스트 설정."""

    symbol: str = "BTCUSDT"
    timeframes: list[str] = field(default_factory=lambda: ["1H", "2H", "4H", "1D", "1W"])
    initial_capital: float = 10_000.0
    leverage: int = 10
    risk_pct: float = 1.0  # 1회당 자본 대비 리스크
    taker_fee_pct: float = 0.06  # 0.06% (Bybit 기본)
    slippage_pct: float = 0.02
    funding_rate_pct: float = 0.01  # 8시간 주기

    # Walk-forward
    window_days: int = 5
    step_days: int = 1


class BacktestEngine:
    """단일 페어 백테스트 엔진."""

    def __init__(self, config: BacktestConfig, data_path: Path) -> None:
        self.config = config
        self.data_path = data_path
        self.trades: list[TradeRecord] = []

    def load_data(self) -> pd.DataFrame:
        """1분봉 Parquet 로드."""
        # TODO(C)
        raise NotImplementedError

    def run(self) -> list[TradeRecord]:
        """전체 데이터에서 walk-forward 백테스트 실행."""
        # TODO(C):
        #   1. 1분봉 → 다중 TF 집계 (replay.py 사용)
        #   2. 윈도우별로 신호 검출 (core.strategy 호출)
        #   3. 진입/청산 시뮬 (수수료·슬리피지·펀딩비 반영)
        #   4. trades 리스트에 누적
        raise NotImplementedError
