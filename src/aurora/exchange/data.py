"""Phase 1 멀티 TF 캔들 캐시 — 라이브 봇 인프라 (DESIGN.md §5).

단일 심볼 (예: ``BTC/USDT:USDT``) 의 다중 TF DataFrame 을 메모리 캐시로 유지.
``BotInstance._run_loop`` 가 매 1초 ``step()`` 호출해도 봉 경계 시점에만 fetch.

설계:
    - ``warmup(lookback_per_tf)`` — 봇 시작 시 각 TF history 적재 (전략 평가용)
    - ``step()`` — 봉 경계 검출 → 필요한 TF 만 fetch + cache append
    - ``get(tf)`` — read-only 접근

봉 갱신 주기 (DESIGN.md §5.3, ``TF_MINUTES`` 차용):
    - 15m: 매 15분 (분 == 0/15/30/45)
    - 1H:  매 시 (분 == 0)
    - 4H:  4시간마다 (시 == 0/4/8/12/16/20)
    - 1D:  UTC 0시

영역: ChoYoon (어댑터 PR 위임 받음 2026-05-03)
"""

from __future__ import annotations

import asyncio
import logging
import time

import pandas as pd

from aurora.backtest.replay import TF_MINUTES
from aurora.exchange.base import ExchangeClient

logger = logging.getLogger(__name__)


# step() 가 새 봉 fetch 시 안전 buffer — 단발 호출이 누락 봉 (네트워크 지연 등)
# 까지 모두 받아오게 5 봉. 호출 빈도가 봉 경계 즈음만이라 rate limit 영향 미미.
_REFRESH_LIMIT = 5


class MultiTfCache:
    """단일 심볼·다중 TF OHLCV 캐시 — DESIGN.md §5.

    Lifecycle:
        >>> cache = MultiTfCache(client, "BTC/USDT:USDT", ["1H", "4H"])
        >>> await cache.warmup({"1H": 500, "4H": 500})        # 봇 시작 시
        >>> while running:
        >>>     df_by_tf = await cache.step()                   # 매 1초
        >>>     signals = strategy.evaluate(df_by_tf, ...)

    Args:
        client: ExchangeClient (CcxtClient 등) — fetch_ohlcv 호출용.
        symbol: ccxt 표준 symbol (예: ``"BTC/USDT:USDT"`` for linear perpetual).
        timeframes: Aurora 포맷 TF 리스트 (``TF_MINUTES`` 키와 일치 필요).

    Raises:
        ValueError: ``timeframes`` 에 ``TF_MINUTES`` 미정의 TF 포함 시.
    """

    def __init__(
        self,
        client: ExchangeClient,
        symbol: str,
        timeframes: list[str],
    ) -> None:
        self._client = client
        self._symbol = symbol
        self._tfs = list(timeframes)
        # TF 검증 — 미정의 시 즉시 raise (런타임 silent 분기 방지)
        for tf in self._tfs:
            if tf not in TF_MINUTES:
                raise ValueError(
                    f"unknown timeframe: {tf!r} "
                    f"(TF_MINUTES 정의 없음, 지원: {sorted(TF_MINUTES.keys())})"
                )
        self._cache: dict[str, pd.DataFrame] = {}

    # ============================================================
    # warmup — 봇 시작 시 history 적재
    # ============================================================

    async def warmup(self, lookback_per_tf: dict[str, int] | None = None) -> None:
        """각 TF 별 lookback 봉 fetch — 전략 평가에 충분한 history 확보.

        모든 TF 병렬 fetch (``asyncio.gather``) — TF 4 개 면 round-trip 1회.

        Args:
            lookback_per_tf: ``{"1H": 500, "4H": 500}`` 형식.
                미명시 TF 는 default 500 봉. ``None`` 이면 모든 TF 500.
        """
        lookback_per_tf = lookback_per_tf or {}

        async def _fetch_one(tf: str) -> tuple[str, pd.DataFrame]:
            limit = lookback_per_tf.get(tf, 500)
            df = await self._client.fetch_ohlcv(self._symbol, tf, limit=limit)
            return tf, df

        results = await asyncio.gather(*[_fetch_one(tf) for tf in self._tfs])
        for tf, df in results:
            self._cache[tf] = df
            last_label = df.index[-1] if len(df) else "EMPTY"
            logger.info(
                "MultiTfCache.warmup: %s %s loaded %d bars (last=%s)",
                self._symbol, tf, len(df), last_label,
            )

    # ============================================================
    # step — 봉 경계 시 새 봉 fetch + append
    # ============================================================

    async def step(self, now_ts: int | None = None) -> dict[str, pd.DataFrame]:
        """봉 경계 시점에 새 봉 fetch — 모든 TF DataFrame 반환.

        호출 패턴 (BotInstance):
            매 1초 호출. 봉 경계가 아닌 timestamp 에 호출하면 fetch 0회 (idempotent).

        Args:
            now_ts: UTC ms timestamp (테스트 mock 용). None → ``time.time()×1000``.

        Returns:
            ``{tf: DataFrame}`` — 캐시된 모든 TF (warmup 안 한 TF 는 빈 DataFrame).
        """
        if now_ts is None:
            now_ts = int(time.time() * 1000)

        # 새 봉 발생 TF 만 fetch (병렬)
        tfs_to_refresh = [tf for tf in self._tfs if self._has_new_bar(tf, now_ts)]
        if tfs_to_refresh:
            await self._refresh_tfs(tfs_to_refresh)

        return {tf: self._cache.get(tf, pd.DataFrame()) for tf in self._tfs}

    def get(self, tf: str) -> pd.DataFrame:
        """캐시 read-only — warmup 후 호출.

        Raises:
            KeyError: ``tf`` 가 캐시에 없음 (warmup 호출 안 했거나 timeframe 오타).
        """
        if tf not in self._cache:
            raise KeyError(
                f"timeframe {tf!r} 가 cache 에 없음 — warmup() 호출 또는 timeframes 인자 확인"
            )
        return self._cache[tf]

    # ============================================================
    # 내부 — 봉 경계 검출 + 새 봉 append
    # ============================================================

    def _has_new_bar(self, tf: str, now_ts: int) -> bool:
        """봉 경계 검출 — 마지막 캐시 봉 이후 새 봉이 발생했는가.

        판정 로직:
            - 캐시 비어있음 → True (warmup 미호출 또는 첫 호출 — fetch 강제)
            - ``now_ts > last_bar_ts + tf_ms`` → 새 봉 시작됨 → True
            - 그 외 (현재 봉 진행 중) → False (fetch 건너뜀)

        ``last_bar_ts`` = ccxt 응답 마지막 row 의 open_time. ``+tf_ms`` 가 다음 봉
        open_time = 새 봉 시작 시점. 그 이후만 fetch.
        """
        df = self._cache.get(tf)
        if df is None or len(df) == 0:
            return True
        tf_ms = TF_MINUTES[tf] * 60_000
        last_bar_ts_ms = int(df.index[-1].timestamp() * 1000)
        return now_ts > last_bar_ts_ms + tf_ms

    async def _refresh_tfs(self, tfs: list[str]) -> None:
        """주어진 TF 들 새 봉 fetch + cache append (병렬)."""

        async def _fetch_and_append(tf: str) -> None:
            # v0.1.97: 빈 응답 1회 retry — 거래소 일시 장애 시 stale 봉 잔존 차단.
            # 거래소 측 가끔 빈 OHLCV 응답 (rate limit 임시 / 봉 경계 race).
            new_df = await self._client.fetch_ohlcv(
                self._symbol, tf, limit=_REFRESH_LIMIT,
            )
            if new_df.empty:
                logger.warning(
                    "MultiTfCache: %s %s fetch 빈 응답 — 1초 후 1회 retry",
                    self._symbol, tf,
                )
                await asyncio.sleep(1.0)
                new_df = await self._client.fetch_ohlcv(
                    self._symbol, tf, limit=_REFRESH_LIMIT,
                )
                if new_df.empty:
                    logger.warning(
                        "MultiTfCache: %s %s retry 후도 빈 응답 — stale 유지",
                        self._symbol, tf,
                    )
                    return
            old_df = self._cache.get(tf, pd.DataFrame())
            if len(old_df):
                # 기존 마지막 ts 이후 봉만 골라 append (중복 방지)
                last_ts = old_df.index[-1]
                appended = new_df[new_df.index > last_ts]
                if len(appended) == 0:
                    return  # 새 봉 아직 거래소에 없음
                merged = pd.concat([old_df, appended])
            else:
                appended = new_df
                merged = new_df
            # 회귀 안전: 중복 index 제거 + 정렬 (PR-2 #31 패턴 차용)
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
            self._cache[tf] = merged
            logger.info(
                "MultiTfCache.step: %s %s +%d bars (total %d)",
                self._symbol, tf, len(appended), len(merged),
            )

        await asyncio.gather(*[_fetch_and_append(tf) for tf in tfs])
