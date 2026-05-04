"""수수료·슬리피지 모델 — 백테스트 손익 정합성의 핵심 모듈.

PR-2 산출 OHLCV 를 BacktestEngine 이 시뮬할 때 적용할 거래 비용 상수와
slip/cost helper 함수를 한곳에 모음. 수치는 ``replay_engine.py`` (Binance
선물 taker 기준) 에서 그대로 차용 — Aurora 거래소 미정 (#B-3 / Issue #40)
상태이지만 검증된 실거래 환경 출발값으로 적정. 거래소 확정 시 override.

상세 spec: ``src/aurora/backtest/DESIGN.md`` §3.2 + §5.2.

담당: ChoYoon
"""

from __future__ import annotations

import logging
from typing import Literal

# ============================================================
# 거래 비용 상수 — Binance 선물 (replay_engine L38 단독 출처)
# #B-3 (Issue #40) 거래소 결정 시 TAKER_FEE_PCT override
# ============================================================

TAKER_FEE_PCT      = 0.0004    # taker (시장가 체결 즉시)
SLIP_NORMAL_PCT    = 0.0002    # 평상시 슬리피지 (변동성 봉 아닐 때)
SLIP_VOLATILE_PCT  = 0.0005    # 변동성 봉 슬리피지 (시장가 lookahead 위험 차단)
VOLATILE_THRESHOLD = 0.005     # (high - low) / close > 0.5% 면 변동성 봉 판정


# 모듈 logger — 비정상 봉 등 비치명 경고용 (D-11 caveat)
logger = logging.getLogger(__name__)


# ============================================================
# 타입 별칭 — 방향·체결 시점
# ============================================================

Direction = Literal["long", "short"]
Side = Literal["entry", "exit"]


# ============================================================
# 공개 함수 — 슬리피지
# ============================================================


def slip_pct(candle_high: float, candle_low: float, candle_close: float) -> float:
    """봉 변동성 기반 슬리피지 비율 반환.

    ``(high - low) / close`` 가 ``VOLATILE_THRESHOLD`` 초과면 변동성 봉으로
    판정해 ``SLIP_VOLATILE_PCT``, 아니면 ``SLIP_NORMAL_PCT`` 반환. ``close <= 0``
    같은 비정상 봉은 보수적으로 normal 슬립 반환 (replay_engine L536 동일 가드).

    Args:
        candle_high: 봉 고가.
        candle_low: 봉 저가.
        candle_close: 봉 종가 (변동성 판정 분모).

    Returns:
        슬리피지 비율 (``SLIP_NORMAL_PCT`` 또는 ``SLIP_VOLATILE_PCT``).
        비정상 봉 (close ≤ 0) 시 WARNING 로그 발생 후 NORMAL slip fallback.
    """
    # close 비정상 봉은 변동성 판정 스킵 → 평상 슬립 (replay_engine 패턴)
    if candle_close <= 0:
        logger.warning(
            "close 비정상 봉 발견 (close=%s) — NORMAL slip fallback",
            candle_close,
        )
        return SLIP_NORMAL_PCT
    rng = (candle_high - candle_low) / candle_close
    if rng > VOLATILE_THRESHOLD:
        return SLIP_VOLATILE_PCT
    return SLIP_NORMAL_PCT


def apply_slippage(
    price: float,
    direction: Direction,
    side: Side,
    slip: float,
) -> float:
    """슬리피지를 가격에 반영해 실제 체결가 반환 (unfavorable 방향).

    - long entry / short exit → 가격 ↑ (사야 하는 쪽이 비싸짐)
    - long exit / short entry → 가격 ↓ (팔아야 하는 쪽이 싸짐)

    백테스트 결과가 실거래보다 낙관적이지 않도록 항상 불리한 방향으로 체결 모델링.
    차용: replay_engine L543-552 (``_apply_slip``).

    Args:
        price: 슬리피지 미적용 가격 (시장가 봉 close 등).
        direction: 포지션 방향 (``"long"`` 또는 ``"short"`` —
            ``core.strategy.Direction`` StrEnum value 정합).
        side: 체결 시점 (``"entry"`` 또는 ``"exit"``).
        slip: 슬리피지 비율 (``slip_pct`` 산출값 권장).

    Returns:
        슬리피지 적용된 실제 체결가.
        잘못된 direction/side 시 AssertionError (Python -O 모드 시 자동 제거).
    """
    assert direction in ("long", "short"), f"잘못된 direction: {direction!r}"
    assert side in ("entry", "exit"), f"잘못된 side: {side!r}"
    # Why: long entry 와 short exit 는 가격 상승이 불리 (사거나 환매하므로 비싸게)
    unfavorable_up = (direction == "long" and side == "entry") or \
                     (direction == "short" and side == "exit")
    if unfavorable_up:
        return price * (1 + slip)
    return price * (1 - slip)


# ============================================================
# 공개 함수 — 수수료·레버리지 합산 PnL
# ============================================================


def apply_costs(
    raw_pnl_pct: float,
    size_pct: float,
    leverage: float,
    fee_pct: float = TAKER_FEE_PCT,
) -> tuple[float, float]:
    """레버리지·수수료 반영해 net PnL + 수수료 손실 산출.

    공식 (차용: replay_engine L487-492 / L981-986 동일):

    - ``notional = size_pct × leverage``
    - ``fee_loss = 2 × fee_pct × notional``  (진입 + 청산 2회 수수료)
    - ``lev_pnl  = raw_pnl_pct × notional − fee_loss``

    Args:
        raw_pnl_pct: 슬리피지 반영된 raw PnL 비율 (예: ``0.02`` = +2%, 음수도 허용).
        size_pct: 포지션 사이즈 (시드 대비, 0.0~1.0).
        leverage: 레버리지 (Aurora 정책: 10~50x).
        fee_pct: taker 수수료 비율. 디폴트 ``TAKER_FEE_PCT`` (Binance 선물).

    Returns:
        ``(lev_pnl, fee_loss)`` — 레버리지 적용 net PnL + 수수료 손실 (모두 시드 대비 비율).
    """
    notional = size_pct * leverage
    fee_loss = 2 * fee_pct * notional   # 2× = 진입 + 청산 (replay_engine 동일)
    lev_pnl = raw_pnl_pct * notional - fee_loss
    return lev_pnl, fee_loss
