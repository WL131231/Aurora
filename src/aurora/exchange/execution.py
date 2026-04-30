"""주문 실행 + 포지션 관리.

core 모듈이 산출한 RiskPlan을 받아 실제 주문 발행 + 4단계 TP/SL 등록.

담당: 팀원 B
"""

from __future__ import annotations

from aurora.config import settings
from aurora.exchange.base import ExchangeClient


class Executor:
    """주문 실행기.

    - paper 모드: 실제 주문 안 보냄, 메모리 가상 포지션만 추적
    - demo 모드: 거래소 testnet
    - live 모드: 실거래
    """

    def __init__(self, client: ExchangeClient) -> None:
        self.client = client
        self.mode = settings.run_mode

    async def open_position(
        self,
        symbol: str,
        direction: str,
        qty: float,
        leverage: int,
        tp_prices: list[float],
        sl_price: float,
    ) -> None:
        """진입 + TP 4단계 + SL 등록."""
        # TODO(B):
        #   1. set_leverage
        #   2. 시장가 진입
        #   3. TP 4개 limit 주문 (각 비율로 분할)
        #   4. SL stop 주문
        raise NotImplementedError

    async def update_sl(self, symbol: str, new_sl: float) -> None:
        """트레일링 SL 갱신."""
        # TODO(B): 기존 SL 취소 + 새 SL 등록
        raise NotImplementedError

    async def close_position(self, symbol: str) -> None:
        """전체 청산."""
        # TODO(B)
        raise NotImplementedError
