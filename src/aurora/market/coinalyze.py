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
from dataclasses import dataclass, field
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


def _interval_to_seconds(interval: str) -> int:
    """v0.1.84: Coinalyze interval 문자열 → 초 단위 변환.

    Coinalyze API 측 박힌 interval 들 (verify 측 모두 동작):
    1min/5min/15min/30min/1hour/2hour/4hour/6hour/12hour/daily.
    """
    table = {
        "1min": 60, "5min": 300, "15min": 900, "30min": 1800,
        "1hour": 3600, "2hour": 7200, "4hour": 14400,
        "6hour": 21600, "12hour": 43200, "daily": 86400,
    }
    return table.get(interval, 3600)


@dataclass(slots=True)
class MarketTrend:
    """Coinalyze 5분 cycle 추세 분석 결과 — multi-timeframe (v0.1.84).

    score 범위 -4~+4 (매매일지 정합 — OI/가격 ±2, CVD ±2, Funding ±1).
    합산 강도:
        ≥ 2 → 강한 롱 (long_strong)
        == 1 → 약한 롱 (long_weak)
        == 0 → 중립 (neutral)
        == -1 → 약한 숏 (short_weak)
        ≤ -2 → 강한 숏 (short_strong)

    v0.1.84: 단기 (15m) / 중단기 (4h) / 중기 (1D) 3 timeframe 박음. 진입 평가:
        - 차단: 중기 (1D) 기준 — macro 추세 거역 진입 차단 (가장 보수적)
        - booster: 셋 다 일치 → ×2.0 / 둘 일치 → ×1.5 / 한 개 → ×1.0 / 셋 다 반대 → ×0.5

    legacy `score` / `direction` / `strong` 필드 = 중기 (1D) 측 박음 (이전 24h 동일).
    """

    coin: str  # "BTC" / "ETH"
    # 중기 (1D) 측 — legacy 호환
    score: int  # -4 ~ +4
    direction: TrendDir
    strong: bool  # |score| >= 2
    reasons: list[str]
    fetched_at_ms: int

    # v0.1.84: multi-timeframe scores (단기 / 중단기 / 중기)
    score_short: int = 0       # 15m 단기
    score_mid_short: int = 0   # 4h 중단기
    score_mid: int = 0         # 1D 중기 (= score 와 동일)
    direction_short: TrendDir = "neutral"
    direction_mid_short: TrendDir = "neutral"
    direction_mid: TrendDir = "neutral"
    reasons_short: list[str] = field(default_factory=list)
    reasons_mid_short: list[str] = field(default_factory=list)
    reasons_mid: list[str] = field(default_factory=list)

    # 원본 데이터 (디버깅 + UI 표시용) — 1D 기준
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
        # v0.1.84: multi-timeframe — 단기 (15min × 1봉) / 중단기 (4hour × 1봉) / 중기 (daily × 1봉)
        tfs = [
            ("short", "15min", 1),       # 15분 전 vs 현재
            ("mid_short", "4hour", 1),   # 4시간 전 vs 현재
            ("mid", "daily", 1),         # 1일 전 vs 현재
        ]
        results: dict[str, dict] = {}

        try:
            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SEC)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for tf_name, interval, lookback in tfs:
                    price, cvd_f, price_prev, cvd_f_prev = await self._get_ohlcv(
                        session, agg_sym, interval=interval, lookback_bars=lookback,
                    )
                    oi, oi_prev = await self._get_oi(
                        session, agg_sym, interval=interval, lookback_bars=lookback,
                    )
                    fr, _ = await self._get_funding(
                        session, agg_sym, interval=interval, lookback_bars=lookback,
                    )
                    # CVD Spot — 1day 측만 fetch (나머지 timeframe 측 동일 추정,
                    # spot trades feed 시간 단위 fetch X 라 cvd_spot 측 재계산 X).
                    cvd_s, _ = await self._get_cvd_spot_sum(
                        session, CVD_SPOT[coin], price, price_prev,
                    ) if tf_name == "mid" else (None, None)

                    score, direction, strong, reasons = _interpret_score(
                        oi=oi, oi_24h=oi_prev, price=price, price_24h=price_prev,
                        cvd_spot=cvd_s, cvd_futures=cvd_f, funding_rate=fr,
                    )
                    results[tf_name] = {
                        "score": score, "direction": direction, "strong": strong,
                        "reasons": reasons,
                        "price": price, "price_prev": price_prev,
                        "oi": oi, "oi_prev": oi_prev,
                        "cvd_spot": cvd_s, "cvd_futures": cvd_f,
                        "funding_rate": fr,
                    }
        except Exception as e:  # noqa: BLE001 — 네트워크 실패 시 None 반환
            logger.warning("Coinalyze fetch 실패 (%s): %s", coin, e)
            return None

        # 중기 (1D) 측 = legacy `score` / `direction` / `strong` 그대로 박음
        mid = results.get("mid", {})
        short = results.get("short", {})
        mid_short = results.get("mid_short", {})

        trend = MarketTrend(
            coin=coin,
            score=mid.get("score", 0),
            direction=mid.get("direction", "neutral"),
            strong=mid.get("strong", False),
            reasons=mid.get("reasons", []),
            fetched_at_ms=int(time.time() * 1000),
            # multi-tf scores
            score_short=short.get("score", 0),
            score_mid_short=mid_short.get("score", 0),
            score_mid=mid.get("score", 0),
            direction_short=short.get("direction", "neutral"),
            direction_mid_short=mid_short.get("direction", "neutral"),
            direction_mid=mid.get("direction", "neutral"),
            reasons_short=short.get("reasons", []),
            reasons_mid_short=mid_short.get("reasons", []),
            reasons_mid=mid.get("reasons", []),
            # legacy 24h 원본 (UI)
            price=mid.get("price"), price_24h=mid.get("price_prev"),
            oi=mid.get("oi"), oi_24h=mid.get("oi_prev"),
            cvd_spot=mid.get("cvd_spot"), cvd_futures=mid.get("cvd_futures"),
            funding_rate=mid.get("funding_rate"),
        )
        self._cache[coin] = trend
        self._cache_ts[coin] = time.time()
        logger.info(
            "Coinalyze trend %s [multi-tf]: 단기(15m)=%+d / 중단기(4h)=%+d / 중기(1D)=%+d",
            coin, trend.score_short, trend.score_mid_short, trend.score_mid,
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
        interval: str = "1hour", lookback_bars: int = 24,
    ) -> tuple[float | None, float | None, float | None, float | None]:
        """OHLCV history — 마지막 봉 + lookback_bars 전 봉 → price + CVD futures.

        v0.1.84: interval / lookback_bars 파라미터화 — multi-timeframe 박음.
        - 단기: interval=15min, lookback_bars=1 (15분 전 vs 현재)
        - 중단기: interval=4hour, lookback_bars=1 (4시간 전 vs 현재)
        - 중기: interval=daily, lookback_bars=1 (1일 전 vs 현재)
        - legacy: interval=1hour, lookback_bars=24 (24시간 전 vs 현재)
        """
        now = int(time.time())
        # 충분한 봉 받기 위해 시간 범위 박음 — interval 별 봉 단위 시간
        interval_sec = _interval_to_seconds(interval)
        from_ts = now - interval_sec * (lookback_bars + 5)  # 여유분 박음
        data = await self._fetch(session, "ohlcv-history", {
            "symbols": symbol, "interval": interval,
            "from": from_ts, "to": now,
        })
        if not isinstance(data, list) or not data:
            return None, None, None, None
        history = data[0].get("history", [])
        if len(history) < 2:
            return None, None, None, None
        last = history[-1]
        # lookback_bars+1 위치 측 봉 (현재 -1 - lookback_bars 위치)
        prev_idx = -(lookback_bars + 1)
        prev = history[prev_idx] if len(history) >= lookback_bars + 1 else history[0]

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
        interval: str = "1hour", lookback_bars: int = 24,
    ) -> tuple[float | None, float | None]:
        """Open Interest 현재 + lookback 전. v0.1.84: multi-tf 파라미터화."""
        now = int(time.time())
        data_now = await self._fetch(session, "open-interest", {
            "symbols": symbol, "convert_to_usd": "true",
        })
        oi_now = data_now[0].get("value") if isinstance(data_now, list) and data_now else None
        interval_sec = _interval_to_seconds(interval)
        from_ts = now - interval_sec * (lookback_bars + 1)
        to_ts = now - interval_sec * lookback_bars + 60  # 봉 1개 폭 박음
        data_hist = await self._fetch(session, "open-interest-history", {
            "symbols": symbol, "interval": interval,
            "from": from_ts, "to": to_ts, "convert_to_usd": "true",
        })
        oi_prev = None
        if isinstance(data_hist, list) and data_hist:
            history = data_hist[0].get("history", [])
            if history:
                oi_prev = history[-1].get("c")
        return oi_now, oi_prev

    async def _get_funding(
        self, session: aiohttp.ClientSession, symbol: str,
        interval: str = "1hour", lookback_bars: int = 24,
    ) -> tuple[float | None, float | None]:
        """Funding rate 현재 + lookback 전. v0.1.84: multi-tf 파라미터화."""
        now = int(time.time())
        data_now = await self._fetch(session, "funding-rate", {"symbols": symbol})
        fr_now = data_now[0].get("value") if isinstance(data_now, list) and data_now else None
        interval_sec = _interval_to_seconds(interval)
        from_ts = now - interval_sec * (lookback_bars + 1)
        to_ts = now - interval_sec * lookback_bars + 60
        data_hist = await self._fetch(session, "funding-rate-history", {
            "symbols": symbol, "interval": interval,
            "from": from_ts, "to": to_ts,
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

    v0.1.58: 약한 추세 반대도 차단.
    v0.1.84: multi-tf — **중기 (1D) 기준** 차단. 단기 (15m) 측 노이즈 차단 X
    본질 정합. 사용자 결정 — "macro 추세 거역 차단".

    Args:
        trend: 현재 시장 추세 (None 이면 차단 X — 데이터 없으면 양방향 허용).
        signal_direction: "long" 또는 "short".

    Returns:
        True 면 차단 (진입 X), False 면 통과.
    """
    if trend is None or trend.direction_mid == "neutral" or trend.score_mid == 0:
        return False  # 데이터 없음 / 중기 중립 — 차단 X
    if trend.direction_mid == "long" and signal_direction == "short":
        return True  # 중기 추세 롱 → 숏 차단
    if trend.direction_mid == "short" and signal_direction == "long":
        return True  # 중기 추세 숏 → 롱 차단
    return False


def trend_score_multiplier(trend: MarketTrend | None, signal_direction: str) -> float:
    """진입 신호 score 가중치 — multi-tf 정렬 일치 본질 (v0.1.84).

    단기 (15m) / 중단기 (4h) / 중기 (1D) 측 진입 방향 일치 개수 측 booster:

    | 일치 개수 | 배율 |
    |-----------|------|
    | 셋 다 일치 (3 개) | 2.0 (강 정렬) |
    | 둘 일치 (2 개) | 1.5 |
    | 한 개 일치 (1 개) | 1.0 |
    | 셋 다 반대 (0 개) | 0.5 (강 반대) |

    Args:
        trend: 현재 시장 추세 (None 이면 1.0).
        signal_direction: "long" 또는 "short".
    """
    if trend is None:
        return 1.0
    matches = 0
    opposites = 0
    for tf_dir in (
        trend.direction_short,
        trend.direction_mid_short,
        trend.direction_mid,
    ):
        if tf_dir == signal_direction:
            matches += 1
        elif tf_dir != "neutral":
            opposites += 1
    if matches == 3:
        return 2.0  # 셋 다 일치 — 강 정렬
    if matches == 2:
        return 1.5
    if matches == 1:
        return 1.0
    if opposites >= 2:
        return 0.5  # 둘 이상 반대 — 강 반대
    return 1.0  # 셋 다 중립
