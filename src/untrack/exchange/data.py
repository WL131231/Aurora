"""시세 데이터 수집 + 멀티 타임프레임 캐싱.

전략 모듈은 1H/2H/4H/1D/1W 동시 참조 → TF별 별도 캐시 유지.

담당: 팀원 B
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from untrack.exchange.base import ExchangeClient


class MultiTfCache:
    """심볼·TF 별 OHLCV DataFrame 캐시.

    내부 구조:
        cache[symbol][timeframe] = DataFrame
    """

    def __init__(self, client: ExchangeClient) -> None:
        self.client = client
        self._cache: dict[str, dict[str, pd.DataFrame]] = {}

    async def get(
        self,
        symbol: str,
        timeframe: str,
        refresh: bool = False,
    ) -> pd.DataFrame:
        """캐시된 DataFrame 반환. 없거나 refresh=True면 거래소에서 다시 받음."""
        # TODO(B): 마지막 캔들 시간 비교해서 부족분만 가져오기 (증분 갱신)
        raise NotImplementedError

    async def warmup(self, symbol: str, timeframes: list[str]) -> None:
        """봇 시작 시 필요한 TF 데이터를 미리 모두 받아둠."""
        # TODO(B)
        raise NotImplementedError


# WebSocket 실시간 구독은 별도 클래스로 분리할지 추후 결정
# TODO(B): WebSocketSubscriber 클래스 (필요 시)
