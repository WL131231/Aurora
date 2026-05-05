"""Coinalyze API 어댑터 — 선물 OI / CVD Spot+Futures / Funding rate (v0.1.53).

매매일지 사이트 (CoinData.py) 의 추세 판단 로직을 Aurora 라이브 봇에 이식.

흐름:
    1. 5분 주기 fetch (BTC + ETH 동시) — 무료 tier 한도 안 (40 calls/min)
    2. ``interpret_summary`` 와 동일한 score 산출 (-2~+2)
    3. ``BotInstance`` 가 캐싱 + 진입 평가 시 활용:
       - |score| ≥ 2 + 진입 반대 → 진입 차단 (강한 추세 필터)
       - 그 외 → score 가중치 부스트 (일치 ×1.3 / 중립 ×1.0 / 약 반대 ×0.7)

API 한도:
    - 40 calls/min per API key (free tier)
    - 5분 주기 + BTC/ETH 각 5 endpoint = 10 calls/cycle = 2 calls/min (5%)

영역: 장수 + 오터 (라이브 봇 진입 결정 보강).
백테스트 영향 X (라이브만 활용, default boost=1.0).

사용자 결정 (2026-05-05):
    - 5분 주기 + 매매일지 score 그대로 + 강한 추세 필터 + 가중치 부스트.

담당: 장수 + 오터.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

import aiohttp

logger = logging.getLogger(__name__)

# Coinalyze API endpoint
BASE_URL = "https://api.coinalyze.net/v1"
HTTP_TIMEOUT_SEC = 10

# Aggregated 선물 심볼 (매매일지 정합)
FUTURES_AGG: dict[str, str] = {
    "BTC": "BTCUSDT_PERP.A",
    "ETH": "ETHUSDT_PERP.A",
}

# CVD Futures 합산용 거래소별 심볼 (매매일지 정합 — 18+ 거래소)
CVD_FUTURES: dict[str, list[str]] = {
    "BTC": [
        "BTCUSDT.6", "BTCUSD.6", "PERP_BTC_USDT.W", "BTCUSD_PERP.4",
        "pf_xbtusd.K", "BTC_USDT.Y", "BTCUSD_PERP.3", "BTCUSDT_PERP.4",
        "BTCUSDT_PERP.F", "BTCUSDT_PERP.0", "1.T", "BTCUSDT_PERP.3",
        "BTC.H", "BTC-USD.8", "BTCUSD.7", "BTCUSDT.S",
        "BTC-PERPETUAL.2", "BTC_USD.Y", "BTCUSD_PERP.0",
    ],
    "ETH": [
        "ETHUSD.6", "ETHUSDT.6", "ETH-PERPETUAL.2", "pf_ethusd.K",
        "ETHUSDT_PERP.4", "ETH-USD.8", "ETHUSDT_PERP.F", "ETHUSDT_PERP.0",
        "ETHUSD_PERP.0", "ETHUSDT.S", "ETH_USDT.Y", "ETHUSD_PERP.3",
        "PERP_ETH_USDT.W", "0.T", "cETHUSD.7", "ETHUSDT_PERP.3",
        "ETHUSD_PERP.4", "ETH.H",
    ],
}

# CVD Spot 합산용
CVD_SPOT: dict[str, list[str]] = {
    "BTC": [
        "BTCUSD.A", "BTCUSD.P", "BTCUSDT.B", "BTCUSDT.C",
        "BTCUSDT.F", "BTCUSDT.K", "sBTCUSDT.6", "sBTCUSDT.7",
        "SPOT_BTC_USDT.W", "XBT_USDT.0",
    ],
    "ETH": [
        "ETHUSD.A", "ETHUSD.P", "ETHUSDT.B", "ETHUSDT.C",
        "ETHUSDT.F", "ETHUSDT.K", "sETHUSDT.6", "sETHUSDT.7",
        "SPOT_ETH_USDT.W", "ETH_USDT.0",
    ],
}


TrendDir = Literal["long", "short", "neutral"]


@dataclass(slots=True)
class MarketTrend:
    """Coinalyze 5분 cycle 추세 분석 결과.

    score 범위 -4~+4 (매매일지 정합 — OI/가격 ±2, CVD ±2, Funding ±1).
    합산 강도:
        ≥ 2 → 강한 롱 (long_strong)
        == 1 → 약한 롱 (long_weak)
        == 0 → 중립 (neutral)
        == -1 → 약한 숏 (short_weak)
        ≤ -2 → 강한 숏 (short_strong)

    Aurora 진입 평가에 활용:
        - 강한 추세 (|score| ≥ 2) + 진입 방향 반대 → 차단
        - 그 외 → 신호 score × 가중치 부스트
    """

    coin: str  # "BTC" / "ETH"
    score: int  # -4 ~ +4
    direction: TrendDir
    strong: bool  # |score| >= 2
    reasons: list[str]
    fetched_at_ms: int

    # 원본 데이터 (디버깅 + UI 표시용)
    price: float | None = None
    price_24h: float | None = None
    oi: float | None = None
    oi_24h: float | None = None
    cvd_spot: float | None = None
    cvd_futures: float | None = None
    funding_rate: float | None = None


def _coin_from_symbol(symbol: str) -> str | None:
    """ccxt 심볼 ('BTC/USDT:USDT') → Coinalyze coin key ('BTC')."""
    base = symbol.split("/")[0].upper()
    if base in FUTURES_AGG:
        return base
    return None


class CoinalyzeClient:
    """Coinalyze API 비동기 클라이언트 — Aurora 라이브 봇 추세 인지용.

    한 인스턴스가 여러 코인 (BTC + ETH) fetch + score 산출. 5분 cache 박혀있어
    매 진입 평가마다 호출해도 안전 (cache hit 시 호출 X).

    Usage:
        client = CoinalyzeClient(api_key="...")
        trend = await client.fetch_trend("BTC")
        if trend.strong and trend.direction == "long":
            # 강한 롱 추세 — short 신호 차단 가능
            ...
    """

    def __init__(self, api_key: str, cache_ttl_sec: int = 300) -> None:
        if not api_key:
            raise ValueError("Coinalyze API key required")
        self._api_key = api_key
        self._cache_ttl_sec = cache_ttl_sec
        self._cache: dict[str, MarketTrend] = {}  # coin → MarketTrend
        self._cache_ts: dict[str, float] = {}      # coin → fetched_at (epoch sec)

    def _cache_valid(self, coin: str) -> bool:
        ts = self._cache_ts.get(coin)
        if ts is None:
            return False
        return (time.time() - ts) < self._cache_ttl_sec

    async def fetch_trend(self, coin: str) -> MarketTrend | None:
        """주어진 coin 의 추세 fetch + score 산출.

        cache 유효 시 즉시 반환 (호출 X). 무효 시 API 5 호출 (price/OI/Funding/
        CVD Futures sum/CVD Spot sum) + score 계산.

        Args:
            coin: "BTC" 또는 "ETH" (FUTURES_AGG 키).

        Returns:
            MarketTrend / 호출 실패 시 None.
        """
        coin = coin.upper()
        if coin not in FUTURES_AGG:
            logger.debug("Coinalyze: 지원 안 하는 코인: %s", coin)
            return None

        if self._cache_valid(coin):
            return self._cache.get(coin)

        agg_sym = FUTURES_AGG[coin]
        try:
            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SEC)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                price, cvd_f, price_24h, cvd_f_24h = await self._get_ohlcv(
                    session, agg_sym,
                )
                oi, oi_24h = await self._get_oi(session, agg_sym)
                fr, _ = await self._get_funding(session, agg_sym)
                cvd_s, _ = await self._get_cvd_spot_sum(
                    session, CVD_SPOT[coin], price, price_24h,
                )
        except Exception as e:  # noqa: BLE001 — 네트워크 실패 시 None 반환
            logger.warning("Coinalyze fetch 실패 (%s): %s", coin, e)
            return None

        score, direction, strong, reasons = _interpret_score(
            oi=oi, oi_24h=oi_24h, price=price, price_24h=price_24h,
            cvd_spot=cvd_s, cvd_futures=cvd_f, funding_rate=fr,
        )

        trend = MarketTrend(
            coin=coin, score=score, direction=direction, strong=strong,
            reasons=reasons,
            fetched_at_ms=int(time.time() * 1000),
            price=price, price_24h=price_24h,
            oi=oi, oi_24h=oi_24h,
            cvd_spot=cvd_s, cvd_futures=cvd_f,
            funding_rate=fr,
        )
        self._cache[coin] = trend
        self._cache_ts[coin] = time.time()
        logger.info(
            "Coinalyze trend %s: score=%+d (%s, %s) — %s",
            coin, score, direction, "강함" if strong else "약함/중립",
            " / ".join(reasons[:3]),
        )
        return trend

    async def fetch_trend_for_symbol(self, symbol: str) -> MarketTrend | None:
        """ccxt 심볼 ('BTC/USDT:USDT') → 대응 coin 추세."""
        coin = _coin_from_symbol(symbol)
        if coin is None:
            return None
        return await self.fetch_trend(coin)

    # ============================================================
    # Coinalyze API endpoint 호출 (매매일지 CoinData.py 정합)
    # ============================================================

    async def _fetch(
        self, session: aiohttp.ClientSession, endpoint: str, params: dict,
    ) -> object:
        params = dict(params)
        params["api_key"] = self._api_key
        async with session.get(f"{BASE_URL}/{endpoint}", params=params) as resp:
            return await resp.json()

    async def _get_ohlcv(
        self, session: aiohttp.ClientSession, symbol: str,
    ) -> tuple[float | None, float | None, float | None, float | None]:
        """OHLCV history (1h) — 마지막 봉 + 24시간 전 봉 → price + CVD futures 산출."""
        now = int(time.time())
        data = await self._fetch(session, "ohlcv-history", {
            "symbols": symbol, "interval": "1hour",
            "from": now - 90000, "to": now,
        })
        if not isinstance(data, list) or not data:
            return None, None, None, None
        history = data[0].get("history", [])
        if len(history) < 2:
            return None, None, None, None
        last = history[-1]
        prev = history[-25] if len(history) >= 25 else history[0]

        def calc(candle: dict) -> tuple[float | None, float | None]:
            price = candle.get("c")
            bv = candle.get("bv", 0) or 0
            v = candle.get("v", 0) or 0
            cvd = bv - (v - bv)
            cvd_usd = cvd * price if price else cvd
            return price, cvd_usd

        price_now, cvd_now = calc(last)
        price_prev, cvd_prev = calc(prev)
        return price_now, cvd_now, price_prev, cvd_prev

    async def _get_oi(
        self, session: aiohttp.ClientSession, symbol: str,
    ) -> tuple[float | None, float | None]:
        """Open Interest 현재 + 24시간 전."""
        now = int(time.time())
        data_now = await self._fetch(session, "open-interest", {
            "symbols": symbol, "convert_to_usd": "true",
        })
        oi_now = data_now[0].get("value") if isinstance(data_now, list) and data_now else None
        data_hist = await self._fetch(session, "open-interest-history", {
            "symbols": symbol, "interval": "1hour",
            "from": now - 90000, "to": now - 82800, "convert_to_usd": "true",
        })
        oi_prev = None
        if isinstance(data_hist, list) and data_hist:
            history = data_hist[0].get("history", [])
            if history:
                oi_prev = history[-1].get("c")
        return oi_now, oi_prev

    async def _get_funding(
        self, session: aiohttp.ClientSession, symbol: str,
    ) -> tuple[float | None, float | None]:
        """Funding rate 현재 + 24시간 전."""
        now = int(time.time())
        data_now = await self._fetch(session, "funding-rate", {"symbols": symbol})
        fr_now = data_now[0].get("value") if isinstance(data_now, list) and data_now else None
        data_hist = await self._fetch(session, "funding-rate-history", {
            "symbols": symbol, "interval": "1hour",
            "from": now - 90000, "to": now - 82800,
        })
        fr_prev = None
        if isinstance(data_hist, list) and data_hist:
            history = data_hist[0].get("history", [])
            if history:
                fr_prev = history[-1].get("c")
        return fr_now, fr_prev

    async def _get_cvd_spot_sum(
        self, session: aiohttp.ClientSession, symbols: list[str],
        price: float | None, price_24h: float | None,
    ) -> tuple[float | None, float | None]:
        """현물 거래소 다수 CVD 합산 — 매매일지 정합."""
        now = int(time.time())
        sym_str = ",".join(symbols)

        async def calc_sum(from_ts: int, to_ts: int) -> float | None:
            total = 0.0
            found = False
            try:
                data = await self._fetch(session, "ohlcv-history", {
                    "symbols": sym_str, "interval": "1hour",
                    "from": from_ts, "to": to_ts,
                })
                if isinstance(data, list):
                    for item in data:
                        history = item.get("history", [])
                        if history:
                            last = history[-1]
                            bv = last.get("bv", 0) or 0
                            v = last.get("v", 0) or 0
                            total += bv - (v - bv)
                            found = True
            except Exception as e:  # noqa: BLE001
                logger.debug("CVD Spot 배치 실패: %s", e)
            return total if found else None

        cvd_now_raw = await calc_sum(now - 7200, now)
        cvd_prev_raw = await calc_sum(now - 90000, now - 82800)
        cvd_now = cvd_now_raw * price if cvd_now_raw is not None and price else cvd_now_raw
        cvd_prev = cvd_prev_raw * price_24h if cvd_prev_raw is not None and price_24h else cvd_prev_raw
        return cvd_now, cvd_prev


# ============================================================
# 추세 score 산출 (매매일지 interpret_summary 정합)
# ============================================================


def _interpret_score(
    oi: float | None, oi_24h: float | None,
    price: float | None, price_24h: float | None,
    cvd_spot: float | None, cvd_futures: float | None,
    funding_rate: float | None,
) -> tuple[int, TrendDir, bool, list[str]]:
    """매매일지 ``CoinData.interpret_summary`` 와 동일한 score 산출.

    Returns:
        (score, direction, strong, reasons).
        - score: -4 ~ +4
        - direction: "long" (≥1) / "short" (≤-1) / "neutral" (0)
        - strong: |score| >= 2
    """
    score = 0
    reasons: list[str] = []

    # 1. OI vs 가격
    if oi is not None and oi_24h is not None and price is not None and price_24h is not None:
        oi_up = oi > oi_24h
        price_up = price > price_24h
        if oi_up and price_up:
            score += 2
            reasons.append("OI·가격 동반 상승(신규 롱 유입)")
        elif oi_up and not price_up:
            score -= 2
            reasons.append("OI↑·가격↓(숏 베팅 강화)")
        elif not oi_up and price_up:
            score += 1
            reasons.append("OI↓·가격↑(숏 청산 반등)")
        else:
            score -= 1
            reasons.append("OI·가격 동반 하락(롱 청산)")

    # 2. CVD Spot + Futures
    cvd_s = cvd_spot or 0
    cvd_f = cvd_futures or 0
    if cvd_s > 0 and cvd_f > 0:
        score += 2
        reasons.append("현물·선물 CVD 모두 매수 우세")
    elif cvd_s < 0 and cvd_f < 0:
        score -= 2
        reasons.append("현물·선물 CVD 모두 매도 우세")
    elif cvd_s > 0:
        score += 1
        reasons.append("현물 CVD 매수 우세")
    elif cvd_f > 0:
        score += 1
        reasons.append("선물 CVD 매수 우세")
    elif cvd_s < 0:
        score -= 1
        reasons.append("현물 CVD 매도 우세")
    elif cvd_f < 0:
        score -= 1
        reasons.append("선물 CVD 매도 우세")

    # 3. Funding rate
    if funding_rate is not None:
        pct = funding_rate * 100
        if pct > 0.1:
            score -= 1
            reasons.append("펀딩 롱 과열(스퀴즈 위험)")
        elif pct < -0.1:
            score += 1
            reasons.append("펀딩 숏 극단(급등 경계)")
        elif pct >= 0:
            reasons.append("펀딩 중립")
        else:
            score -= 1
            reasons.append("펀딩 숏 우세")

    direction: TrendDir
    if score >= 1:
        direction = "long"
    elif score <= -1:
        direction = "short"
    else:
        direction = "neutral"
    strong = abs(score) >= 2
    return score, direction, strong, reasons


# ============================================================
# 진입 평가 활용 헬퍼 (BotInstance 가 호출)
# ============================================================


def trend_filter(trend: MarketTrend | None, signal_direction: str) -> bool:
    """추세 방향 필터 — 진입 차단 여부.

    v0.1.58: 약한 추세 반대도 차단. 사용자 요청 — "추세 = 롱/강한 롱 이면
    무조건 롱만, 추세 = 숏/강한 숏 이면 무조건 숏만". 중립 (score=0) 만 양방향 허용.

    Why: BTC 강한 롱 / ETH 롱 추세인데 봇이 가격 매매로 SHORT 진입 후 -4.71% 손실.
    약한 추세도 반대 방향 진입은 평균적으로 EV 음. trend.strong 조건 제거.

    Args:
        trend: 현재 시장 추세 (None 이면 차단 X — 데이터 없으면 양방향 허용).
        signal_direction: "long" 또는 "short".

    Returns:
        True 면 차단 (진입 X), False 면 통과.
    """
    if trend is None or trend.direction == "neutral" or trend.score == 0:
        return False  # 데이터 없음 / 중립 — 차단 X
    if trend.direction == "long" and signal_direction == "short":
        return True  # 추세 롱 (강/약 무관) → 숏 차단
    if trend.direction == "short" and signal_direction == "long":
        return True  # 추세 숏 (강/약 무관) → 롱 차단
    return False


def trend_score_multiplier(trend: MarketTrend | None, signal_direction: str) -> float:
    """진입 신호 score 가중치 — 추세 일치/중립/반대 따라 부스트.

    | 일치 정도 | 배율 |
    |-----------|------|
    | 강한 추세 일치 (|score|≥2 + 같은 방향) | 1.5 |
    | 약한 추세 일치 (|score|=1 + 같은 방향) | 1.3 |
    | 중립 (score=0) | 1.0 |
    | 약한 추세 반대 (|score|=1 + 다른 방향) | 0.7 |
    | 강한 추세 반대 — trend_filter 가 차단 처리 (여기 도달 X) | 1.0 fallback |

    Args:
        trend: 현재 시장 추세 (None 이면 1.0).
        signal_direction: "long" 또는 "short".
    """
    if trend is None:
        return 1.0
    if trend.direction == "neutral" or trend.score == 0:
        return 1.0
    if trend.direction == signal_direction:
        return 1.5 if trend.strong else 1.3
    # 반대 방향 — 강한 반대는 trend_filter 가 차단했어야 (fallback 1.0)
    if trend.strong:
        return 1.0
    return 0.7
