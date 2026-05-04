"""주문 실행 — RiskPlan 기반 진입·트레일링·청산 (DESIGN.md §6).

단일 심볼 Executor. ``BotInstance._run_loop`` 가 매 step 마다 ``update_trailing_sl``
+ ``should_close`` 호출 → 가격 도달 시 ``close_position``.

Phase 1 단순화 (DESIGN.md E-6):
    - 거래소 측 SL/TP 등록 X — 봇 측 polling 만
    - SL/TP 가격은 in-memory ``_plan`` 에 저장 → 매 step 갱신
    - 봇 다운 시 SL 미적용 위험은 Phase 2 demo 검증 단계에서 평가, Phase 3
      에서 거래소 측 SL attach 추가 검토

분할 익절:
    - ``close_position(qty=...)`` partial 지원 — 호출자(BotInstance)가
      ``config.tp_allocations`` 기반으로 부분 청산 호출
    - ``_remaining_qty`` state 추적 → 0 도달 시 자동 reset

영역: ChoYoon (어댑터 PR 위임 받음 2026-05-03)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from aurora.core.risk import (
    RiskPlan,
    TpSlConfig,
)
from aurora.core.risk import (
    update_trailing_sl as _risk_update_trailing_sl,
)
from aurora.exchange.base import ExchangeClient, Order

logger = logging.getLogger(__name__)


# 청산 사유 타입 — should_close 반환값 + close_position 인자 통일
CloseReason = Literal["sl", "tp_partial", "tp_full", "manual", "exit_signal", "reverse"]


@dataclass(slots=True)
class ClosedTrade:
    """청산된 trade 기록 — 거래내역 표 + PnL 카드 표시용 (v0.1.20).

    매 ``close_position`` 호출 시 한 record 생성. 잔여 0 (전량 청산) 만 X — partial
    청산도 row 한 개 (사용자가 청산 흐름 추적 가능).
    """

    symbol: str
    direction: str                       # "long" / "short"
    leverage: int
    qty: float                           # 청산된 수량 (이 record 의 close qty)
    entry_price: float
    exit_price: float
    opened_at_ts: int                    # 진입 시각 (ms)
    closed_at_ts: int                    # 청산 시각 (ms)
    reason: str                          # CloseReason 또는 "external" 등
    pnl_usd: float                       # USDT pnl
    roi_pct: float                       # ROI % (마진 기준)
    triggered_by: list[str] = field(default_factory=list)  # 진입 트리거 (예: ["EMA"])


class Executor:
    """단일 심볼 주문 실행기 — RiskPlan 받아 진입·트레일링·청산.

    Lifecycle:
        >>> executor = Executor(client, "BTC/USDT:USDT", tpsl_config)
        >>> order = await executor.open_position(plan)
        >>> while running:
        >>>     await executor.update_trailing_sl(current_market)
        >>>     reason = executor.should_close(current_market)
        >>>     if reason:
        >>>         await executor.close_position(reason=reason)
        >>>         break

    Args:
        client: ``ExchangeClient`` (CcxtClient 등) — place_order / set_leverage 호출.
        symbol: ccxt 표준 (예: ``"BTC/USDT:USDT"``).
        config: ``TpSlConfig`` — 트레일링 모드 / TP allocations 등.
    """

    def __init__(
        self,
        client: ExchangeClient,
        symbol: str,
        config: TpSlConfig,
    ) -> None:
        self._client = client
        self._symbol = symbol
        self._config = config
        # 활성 포지션 state — open_position 시 채움, close 시 None 으로 reset
        self._plan: RiskPlan | None = None
        self._remaining_qty: float = 0.0
        # 진입 트리거 (어떤 지표로 진입했는지) — UI 표시용
        # signal.py 의 EntryDecision.triggered_by 그대로 보존 (예: ["EMA", "RSI"])
        self._triggered_by: list[str] = []
        # 진입 시각 (ms) — ClosedTrade.opened_at_ts 에 사용 (v0.1.20)
        self._opened_at_ts: int = 0
        # 트레일링 SL 입력 — 진입 후 매 step current_market 으로 갱신
        self._highest_since_entry: float = 0.0
        self._lowest_since_entry: float = 0.0
        # 도달한 TP 단계 (0~len(tp_prices))
        self._tp_hits: int = 0

    @property
    def has_position(self) -> bool:
        """현재 활성 포지션 보유 여부 — BotInstance 가 진입 중복 방지에 사용."""
        return self._plan is not None

    @property
    def triggered_by(self) -> list[str]:
        """진입 시 발동된 지표 목록 — UI 표시용 (예: ["EMA", "RSI"])."""
        return list(self._triggered_by)

    def set_client(self, client: ExchangeClient) -> None:
        """ccxt 세션 재주입 — BotInstance stop/start 사이클 시 사용.

        Why: stop() 이 client.close() 호출하면 이전 async 세션이 죽지만, ``_plan``
        등 포지션 state 는 보존되어야 자기 진입한 포지션을 잊지 않음.
        새 client 만 갈아끼우면 SL/TP 트레일링 + 청산 호출 그대로 작동.
        """
        self._client = client

    def reset_position(self) -> None:
        """포지션 state 초기화 — 외부 청산 (사용자 직접 / liquidation) 감지 시 호출.

        Why: 봇이 ``_plan`` 살아있다고 믿는데 거래소 측엔 포지션 없으면 has_position
        이 영원히 True → 트레일링 + 청산 분기만 돌고 신규 진입 평가 안 함 → 봇 멈춤.
        BotInstance ``_step`` 가 fetch_position 으로 sync → 사라짐 감지 시 본 메서드.
        """
        self._plan = None
        self._remaining_qty = 0.0
        self._triggered_by = []
        self._tp_hits = 0
        self._highest_since_entry = 0.0
        self._lowest_since_entry = 0.0

    @property
    def remaining_qty(self) -> float:
        """남은 포지션 수량 (분할 청산 후 잔여) — read-only."""
        return self._remaining_qty

    @property
    def tp_hits(self) -> int:
        """도달한 TP 단계 수 (0~4) — read-only, BotInstance 부분 청산 판정용."""
        return self._tp_hits

    # ============================================================
    # 진입
    # ============================================================

    async def open_position(
        self,
        plan: RiskPlan,
        triggered_by: list[str] | None = None,
    ) -> Order:
        """진입 — leverage 설정 + 시장가 주문.

        Phase 1 = 거래소 측 SL/TP 등록 X (DESIGN.md E-6). SL/TP 는 ``plan``
        에 in-memory 보관, 매 ``update_trailing_sl`` step 마다 갱신.

        Args:
            plan: ``core.risk.build_risk_plan`` 산출 결과.
                ``plan.position.coin_amount`` 가 거래소 주문 qty.
            triggered_by: 진입 발동 지표 목록 (예: ``["EMA", "RSI"]``). UI 표시용.
                None 이면 빈 list. ``signal.py`` 의 ``EntryDecision.triggered_by``
                직접 전달 가정.

        Returns:
            거래소 응답 Order (paper 모드는 가짜 'filled' Order).

        Raises:
            RuntimeError: 이미 활성 포지션 보유 중.
        """
        if self._plan is not None:
            raise RuntimeError(
                f"open_position 호출했는데 활성 포지션 존재 — "
                f"{self._symbol} {self._plan.direction} qty={self._remaining_qty}",
            )

        # 1. 레버리지 설정 (paper 모드는 noop)
        await self._client.set_leverage(self._symbol, plan.leverage)

        # 2. 시장가 진입
        # Why: long → buy, short → sell (방향 매핑 표준)
        side: Literal["buy", "sell"] = "buy" if plan.direction == "long" else "sell"
        qty = plan.position.coin_amount
        order = await self._client.place_order(
            symbol=self._symbol,
            side=side,
            qty=qty,
            price=None,         # 시장가
            reduce_only=False,
        )

        # 3. state 초기화
        import time
        self._plan = plan
        self._remaining_qty = qty
        self._triggered_by = list(triggered_by) if triggered_by else []
        self._opened_at_ts = int(time.time() * 1000)  # v0.1.20 — ClosedTrade.opened_at_ts
        self._highest_since_entry = plan.entry_price
        self._lowest_since_entry = plan.entry_price
        self._tp_hits = 0

        logger.info(
            "Executor.open_position: %s %s qty=%.6f entry=%.2f sl=%.2f leverage=%dx triggered_by=%s",
            self._symbol, plan.direction, qty,
            plan.entry_price, plan.sl_price, plan.leverage, self._triggered_by,
        )
        return order

    # ============================================================
    # 트레일링 SL + TP hit 검출
    # ============================================================

    async def update_trailing_sl(self, current_market: float) -> bool:
        """현재 시장가 받아 high/low/tp_hits 갱신 + SL 트레일링 적용.

        호출 패턴 (BotInstance loop):
            매 step 호출. SL 변경 시 in-memory ``plan.sl_price`` 갱신만 (거래소
            측 등록 X — Phase 1 단순화).

        Args:
            current_market: 현재 시장가 (시세 polling 결과).

        Returns:
            True 면 SL 변경됨 (호출자 알림용).
            활성 포지션 없으면 False.
        """
        if self._plan is None:
            return False

        # high/low 갱신 — 트레일링 PERCENT_BELOW_* 모드 입력
        if current_market > self._highest_since_entry:
            self._highest_since_entry = current_market
        if current_market < self._lowest_since_entry:
            self._lowest_since_entry = current_market

        # TP hit 검출 — 봇 측 polling
        # Why: long 은 가격 ≥ tp, short 은 가격 ≤ tp 도달 시 단계 카운트 ↑
        is_long = self._plan.direction == "long"
        for i, tp_price in enumerate(self._plan.tp_prices):
            hit = (
                (is_long and current_market >= tp_price)
                or (not is_long and current_market <= tp_price)
            )
            if hit and (i + 1) > self._tp_hits:
                self._tp_hits = i + 1

        # SL 갱신 (트레일링 5모드 + OFF — risk.update_trailing_sl 위임)
        old_sl = self._plan.sl_price
        new_sl = _risk_update_trailing_sl(
            current_sl=old_sl,
            plan=self._plan,
            config=self._config,
            tp_hits=self._tp_hits,
            highest_since_entry=self._highest_since_entry,
            lowest_since_entry=self._lowest_since_entry,
        )
        if new_sl != old_sl:
            self._plan.sl_price = new_sl
            logger.info(
                "Executor.update_trailing_sl: %s SL %.2f → %.2f (tp_hits=%d)",
                self._symbol, old_sl, new_sl, self._tp_hits,
            )
            return True
        return False

    # ============================================================
    # 청산 트리거 검출 + 청산
    # ============================================================

    def should_close(self, current_market: float) -> CloseReason | None:
        """현재 시장가가 청산 조건 도달했는지 검사.

        반환:
            ``"sl"``       — SL 가격 도달 (전체 청산)
            ``"tp_full"``  — 모든 TP 단계 도달 (전체 청산)
            ``None``       — 청산 조건 미도달

        Note:
            ``"tp_partial"`` 은 본 메서드에서 반환 X. 호출자(BotInstance)가
            ``executor.tp_hits`` 와 ``config.tp_allocations`` 비교해서
            부분 청산 시점 판단.
        """
        if self._plan is None:
            return None

        is_long = self._plan.direction == "long"
        # SL 도달 — long 은 가격 ≤ sl, short 은 가격 ≥ sl
        if (is_long and current_market <= self._plan.sl_price) or (
            not is_long and current_market >= self._plan.sl_price
        ):
            return "sl"

        # TP 4단계 모두 도달 — 잔여 전량 청산
        if self._tp_hits >= len(self._plan.tp_prices):
            return "tp_full"

        return None

    async def close_position(
        self,
        qty: float | None = None,
        reason: CloseReason = "manual",
    ) -> tuple[Order, ClosedTrade]:
        """청산 — reduce_only 시장가 주문 + ClosedTrade 기록 반환 (v0.1.20).

        Args:
            qty: 청산할 수량. ``None`` 이면 잔여 전량 (``_remaining_qty``).
                부분 청산 시 호출자가 ``config.tp_allocations`` 기반으로 명시.
            reason: 청산 사유 (로그·통계 metadata).

        Returns:
            ``(Order, ClosedTrade)`` tuple. BotInstance 가 ClosedTrade 를
            ``_closed_trades`` 에 추가 (rolling buffer).

        Raises:
            RuntimeError: 활성 포지션 없음 / qty 가 잔여보다 큼.
        """
        import time

        if self._plan is None:
            raise RuntimeError("close_position 호출했는데 활성 포지션 없음")

        close_qty = qty if qty is not None else self._remaining_qty
        if close_qty > self._remaining_qty + 1e-9:  # 부동소수 epsilon
            raise RuntimeError(
                f"close qty={close_qty} > remaining={self._remaining_qty} "
                f"({self._symbol} {self._plan.direction})",
            )

        # Why: long 청산 → sell, short 청산 → buy (반대 방향 reduce_only)
        side: Literal["buy", "sell"] = "sell" if self._plan.direction == "long" else "buy"
        order = await self._client.place_order(
            symbol=self._symbol,
            side=side,
            qty=close_qty,
            price=None,
            reduce_only=True,
        )

        # ClosedTrade 기록 산출 (state reset 전에 plan 데이터 사용)
        plan = self._plan
        is_long = plan.direction == "long"
        # exit_price = 거래소 응답 price 또는 plan 기준 (paper 모드 대응)
        exit_price = order.price if order.price is not None else plan.entry_price
        # raw pnl: long 은 (exit-entry), short 는 (entry-exit)
        sign = 1.0 if is_long else -1.0
        pnl_per_coin = (exit_price - plan.entry_price) * sign
        pnl_usd = pnl_per_coin * close_qty
        # ROI = pnl / margin × 100. margin = (entry × qty) / leverage
        margin = (plan.entry_price * close_qty) / max(plan.leverage, 1)
        roi_pct = (pnl_usd / margin * 100.0) if margin > 0 else 0.0

        closed = ClosedTrade(
            symbol=self._symbol,
            direction=plan.direction,
            leverage=plan.leverage,
            qty=close_qty,
            entry_price=plan.entry_price,
            exit_price=exit_price,
            opened_at_ts=self._opened_at_ts,
            closed_at_ts=int(time.time() * 1000),
            reason=reason,
            pnl_usd=pnl_usd,
            roi_pct=roi_pct,
            triggered_by=list(self._triggered_by),
        )

        self._remaining_qty -= close_qty
        logger.info(
            "Executor.close_position: %s %s qty=%.6f reason=%s pnl=%.4f USDT (%.2f%% ROI) remaining=%.6f",
            self._symbol, plan.direction, close_qty, reason, pnl_usd, roi_pct,
            self._remaining_qty,
        )

        # 잔여 0 (또는 epsilon 이하) — state 전체 reset
        if self._remaining_qty <= 1e-9:
            self._reset_state()
        return order, closed

    # ============================================================
    # 내부
    # ============================================================

    def _reset_state(self) -> None:
        """포지션 종료 후 state 초기화 — open_position 가능 상태로 복귀."""
        self._plan = None
        self._remaining_qty = 0.0
        self._triggered_by = []
        self._highest_since_entry = 0.0
        self._lowest_since_entry = 0.0
        self._tp_hits = 0
