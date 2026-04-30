"""리플레이 엔진 — 1분봉을 한 봉씩 흘려보내며 다중 TF OHLCV 집계.

실시간 봇과 동일한 시그널 로직을 시뮬에서도 재현하기 위함.

기존 trading_bot/core/replay_engine.py 차용 (AI 부분 제거).

담당: 팀원 C
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import pandas as pd


# 분 단위 TF 매핑
TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15,
    "1H": 60, "2H": 120, "4H": 240, "1D": 1440, "1W": 10_080,
}


@dataclass(slots=True)
class AggregatedBar:
    """집계 결과 한 봉."""

    open_time: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float


class MultiTfAggregator:
    """1분봉 입력 → 사용자가 지정한 TF들로 자동 집계."""

    def __init__(self, timeframes: list[str], buffer_size: int = 1000) -> None:
        self.timeframes = timeframes
        self.buffers: dict[str, deque[AggregatedBar]] = {tf: deque(maxlen=buffer_size) for tf in timeframes}
        # 현재 형성 중인 봉 (TF별)
        self._current: dict[str, AggregatedBar | None] = {tf: None for tf in timeframes}

    def step(self, minute_bar: pd.Series) -> dict[str, AggregatedBar | None]:
        """1분봉 한 개 입력 → 닫힌 TF별 새 봉 dict 반환.

        Returns:
            {"1H": AggregatedBar(...), "4H": None, ...}
            None이면 해당 TF에서 아직 봉이 닫히지 않음.
        """
        # TODO(C):
        #   1. 각 TF별 _current 봉 갱신 (high/low/volume)
        #   2. TF의 분 경계 도달 시 → 봉 닫고 buffer에 push, 결과에 포함
        raise NotImplementedError

    def get_df(self, timeframe: str) -> pd.DataFrame:
        """현재까지 누적된 봉을 DataFrame으로."""
        # TODO(C)
        raise NotImplementedError
