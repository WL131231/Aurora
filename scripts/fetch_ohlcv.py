"""Bybit perpetual에서 ccxt로 OHLCV를 페이지네이션으로 받아 parquet으로 저장하는 CLI 스크립트.

백테스트용 1m OHLCV 데이터 수집 도구.

설계 요약
---------
- 거래소: Bybit (ccxt 어댑터). 향후 다른 거래소로 swap 가능 (어댑터 한 줄 추가).
- 시장:   linear (USDT-margined perpetual) 기본 / spot 옵션.
- 페이지네이션: ``since`` 슬라이딩 1000봉 단위. 종료조건 — 빈 응답 또는 ``len(page) < limit``.
- Retry:  tenacity exponential backoff (1s ~ 30s, 5회). 네트워크/일시 거래소 오류 5종만 재시도.
- 저장:   ``data/{SYMBOL}_{TIMEFRAME}.parquet`` (slash 제거, timeframe 소문자).
- 시간:   timestamp는 UTC ms ``int64``로 저장. KST 변환은 backtest/ 단계 책임.

본 모듈은 Aurora Rule #2(봇 런타임 LLM API 호출 금지)에 해당하지 않음 — ccxt 거래소 API만 호출.
"""

import argparse
import logging
import sys
from pathlib import Path

import ccxt
import pandas as pd
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


def _init_exchange(market_type: str = "linear") -> ccxt.bybit:
    """Bybit ccxt 인스턴스를 생성한다.

    Parameters
    ----------
    market_type : str
        - ``"linear"`` — USDT-margined perpetual (defaultType=swap, defaultSubType=linear)
        - ``"spot"``   — 현물

    Returns
    -------
    ccxt.bybit
        ``enableRateLimit=True`` 설정된 인스턴스.

    Raises
    ------
    ValueError
        ``market_type``이 ``"linear"`` 또는 ``"spot"``이 아닐 때.
    """
    if market_type not in {"linear", "spot"}:
        raise ValueError(
            f"지원하지 않는 market_type: {market_type!r} ('linear' 또는 'spot'만 허용)"
        )

    inner_options: dict = {
        "defaultType": "swap" if market_type == "linear" else "spot",
    }
    if market_type == "linear":
        # Bybit perpetual에서 USDT-margined(linear)와 USDC/inverse를 구분하는 키.
        inner_options["defaultSubType"] = "linear"

    return ccxt.bybit({
        "enableRateLimit": True,
        "options": inner_options,
    })


@retry(
    retry=retry_if_exception_type((
        ccxt.NetworkError,
        ccxt.RequestTimeout,
        ccxt.ExchangeNotAvailable,
        ccxt.RateLimitExceeded,
        ccxt.DDoSProtection,
    )),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    reraise=True,
)
def _fetch_page(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    since_ms: int,
    limit: int,
) -> list[list]:
    """단일 페이지 OHLCV를 호출한다 (tenacity retry 적용).

    재시도 대상 (총 5종)
        - ``ccxt.NetworkError``
        - ``ccxt.RequestTimeout``
        - ``ccxt.ExchangeNotAvailable``
        - ``ccxt.RateLimitExceeded``
        - ``ccxt.DDoSProtection``

    재시도 비대상 (즉시 raise)
        ``ccxt.AuthenticationError`` / ``BadSymbol`` / ``InvalidOrder`` 등.

    Backoff: exponential, 1s ~ 30s, 최대 5회 시도 후 ``reraise=True``로 마지막 예외 그대로 전파.

    Returns
    -------
    list[list]
        ccxt 표준 OHLCV 행 리스트: ``[[ts_ms, open, high, low, close, volume], ...]``.
    """
    return exchange.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=limit)


def fetch_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    since_ms: int,
    until_ms: int,
    page_limit: int = 1000,
) -> pd.DataFrame:
    """``[since_ms, until_ms)`` 구간의 OHLCV를 페이지네이션으로 모아 DataFrame으로 반환한다.

    페이지네이션 종료조건
        1. ``_fetch_page``가 빈 리스트 반환.
        2. cursor가 진행 안 함 (안전가드 — 거래소 응답 이상 대비, 무한 루프 방지).
        3. ``cursor >= until_ms`` (while 조건으로 자연 종료).

    Note
        ``len(page) < page_limit``을 종료 조건으로 쓰지 않음. Bybit perpetual이
        ``since`` 인자 사용 시 항상 ``limit-1``을 반환하는 사례 발견 (2026-05-02).
        거래소는 ``limit``을 정확히 honor 하지 않을 수 있어 신뢰 불가.

    슬라이딩
        ``cursor = page[-1][0] + tf_ms`` (다음 호출의 ``since``).

    방어적 후처리
        - ``until_ms`` 이상 캔들은 컷 (마지막 페이지 over-fetch 대응).
        - ``drop_duplicates(subset="timestamp")`` — 페이지 경계 off-by-one 방어.
        - ``sort_values("timestamp")`` 후 ``reset_index``.
        - 컬럼 dtype 명시: ``timestamp=int64``, OHLCV는 ``float64``.

    Parameters
    ----------
    exchange : ccxt.Exchange
        ``_init_exchange``로 생성한 인스턴스.
    symbol : str
        ccxt 형식 (예: ``"BTC/USDT"``).
    timeframe : str
        ccxt 형식 소문자 (예: ``"1m"``, ``"1h"``, ``"1d"``).
    since_ms, until_ms : int
        UTC ms 시각, 반개구간 ``[since_ms, until_ms)``.
    page_limit : int, default 1000
        한 호출당 봉 수 (Bybit 최대 1000).

    Returns
    -------
    pd.DataFrame
        columns ``["timestamp", "open", "high", "low", "close", "volume"]``,
        timestamp 오름차순, 중복 없음.

    Raises
    ------
    ValueError
        ``since_ms >= until_ms``일 때.
    """
    if since_ms >= until_ms:
        raise ValueError(f"since_ms({since_ms}) >= until_ms({until_ms})")

    tf_ms = exchange.parse_timeframe(timeframe) * 1000
    rows: list[list] = []
    cursor = since_ms
    page_idx = 0

    while cursor < until_ms:
        page = _fetch_page(exchange, symbol, timeframe, cursor, page_limit)
        page_idx += 1

        if not page:
            logger.info("page %d: 빈 응답 — 종료", page_idx)
            break

        last_ts_raw = page[-1][0]
        raw_len = len(page)

        # until_ms 이상 캔들 컷 (마지막 페이지 over-fetch 대응)
        kept = [c for c in page if c[0] < until_ms]
        rows.extend(kept)

        logger.info(
            "page %d: kept=%d/raw=%d, last_ts=%d",
            page_idx, len(kept), raw_len, last_ts_raw,
        )

        # 슬라이딩: cut 전 마지막 ts + tf_ms
        # 안전가드 — 거래소가 같은 데이터 재반환 시 무한 루프 방지
        new_cursor = last_ts_raw + tf_ms
        if new_cursor <= cursor:
            logger.warning(
                "page %d: cursor 진행 없음 (last_ts=%d) — 안전 종료",
                page_idx, last_ts_raw,
            )
            break
        cursor = new_cursor

    df = pd.DataFrame(
        rows,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df = (
        df.drop_duplicates(subset="timestamp")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    df = df.astype({
        "timestamp": "int64",
        "open": "float64",
        "high": "float64",
        "low": "float64",
        "close": "float64",
        "volume": "float64",
    })
    return df


def save_parquet(df: pd.DataFrame, path: Path) -> None:
    """DataFrame을 parquet 파일로 저장한다.

    부모 디렉토리는 ``mkdir(parents=True, exist_ok=True)``로 자동 생성한다.

    Parameters
    ----------
    df : pd.DataFrame
        ``fetch_ohlcv`` 결과 권장.
    path : Path
        저장 경로 (예: ``data/BTCUSDT_1m.parquet``).

    Notes
    -----
    Engine: pyarrow, compression: snappy, ``index=False``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, engine="pyarrow", compression="snappy", index=False)


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점.

    Workflow
        ``argparse`` → ``_init_exchange`` → 기간 계산(``now - days``) →
        ``fetch_ohlcv`` → ``save_parquet``.

    Parameters
    ----------
    argv : list[str] | None
        ``None``이면 ``sys.argv[1:]`` 사용 (argparse 기본). 테스트에서 명시 주입 가능.

    Returns
    -------
    int
        성공 시 ``0``.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Bybit perpetual에서 OHLCV를 받아 parquet으로 저장하는 "
            "백테스트용 데이터 수집 도구."
        ),
    )
    parser.add_argument(
        "--symbol",
        required=True,
        help='ccxt format, e.g., "BTC/USDT", "ETH/USDT"',
    )
    parser.add_argument(
        "--days",
        type=int,
        default=730,
        help="default=730 (~2년치, 약 2~3분 소요)",
    )
    parser.add_argument(
        "--timeframe",
        default="1m",
        help="ccxt format (lowercase), e.g., 1m, 5m, 15m, 1h, 4h, 1d, 1w",
    )
    parser.add_argument(
        "--market-type",
        default="linear",
        choices=["linear", "spot"],
        help="linear=USDT-margined perpetual (default), spot=현물",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="default: data/{SYMBOL_NO_SLASH}_{TIMEFRAME}.parquet",
    )
    args = parser.parse_args(argv)

    # CLI 직접 실행 시 한 번만 핸들러 설정. basicConfig는 root에 핸들러 있으면 no-op.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    exchange = _init_exchange(args.market_type)
    until_ms = exchange.milliseconds()
    since_ms = until_ms - args.days * 86_400 * 1000

    logger.info(
        "fetch start — symbol=%s timeframe=%s market_type=%s days=%d",
        args.symbol, args.timeframe, args.market_type, args.days,
    )

    df = fetch_ohlcv(exchange, args.symbol, args.timeframe, since_ms, until_ms)

    output = args.output or (
        Path("data") / f"{args.symbol.replace('/', '')}_{args.timeframe}.parquet"
    )
    save_parquet(df, output)

    logger.info("saved %d rows to %s", len(df), output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
