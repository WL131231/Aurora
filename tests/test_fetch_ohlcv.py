"""``scripts/fetch_ohlcv.py`` 단위 테스트.

목표
----
mock 기반 결정론적 테스트. **외부 네트워크 호출 0**.
페이지네이션 / retry / parquet 저장 / CLI e2e 17 케이스 커버.

테스트 그룹
----------
- 페이지네이션 (4): 기본, 종료조건 2종, since 슬라이딩
- until cut + dedup (2): until_ms 컷, 페이지 경계 중복
- retry (5): NetworkError / max attempts / Auth 즉시 raise / RateLimit / DDoS
- save_parquet (2): roundtrip, 다단계 디렉토리 생성
- 입력 검증 (2): invalid market_type, since>=until
- 엣지 (1): 빈 결과 dtype 보존
- CLI e2e (1): main 인자 파싱 + 파일 생성

mock 헬퍼
---------
- ``_make_candles(n, start_ms, tf_ms)`` — ccxt 표준 6필드 캔들 n개 생성.
- ``fake_exchange`` 픽스처 — ``parse_timeframe`` / ``milliseconds`` 만 stub.
- ``no_sleep`` 픽스처 — tenacity retry의 실제 sleep 무력화 (테스트 속도 보장).
"""

from unittest.mock import MagicMock, patch

import ccxt
import pandas as pd
import pytest

import scripts.fetch_ohlcv as m

# =====================================================================
# 공통 상수 / 헬퍼 / 픽스처
# =====================================================================

TF_MS = 60_000  # 1m timeframe = 60_000 ms
START_MS = 1_700_000_000_000  # 2023-11-14 22:13:20 UTC 부근의 임의 기준점
LARGE_UNTIL = START_MS + 10_000_000_000  # 페이지네이션이 자연 종료되도록 충분히 큰 until


def _make_candles(
    n: int,
    start_ms: int,
    tf_ms: int = TF_MS,
    *,
    base_price: float = 100.0,
) -> list[list]:
    """단위 테스트용 OHLCV 캔들 ``n``개를 ccxt 표준 6필드 형식으로 생성한다.

    형식: ``[ts_ms, open, high, low, close, volume]``.
    ``ts_ms``는 ``start_ms``부터 ``tf_ms``씩 증가.
    """
    return [
        [
            start_ms + i * tf_ms,
            base_price,
            base_price + 1.0,
            base_price - 1.0,
            base_price + 0.5,
            1.5,
        ]
        for i in range(n)
    ]


@pytest.fixture
def fake_exchange():
    """``parse_timeframe`` / ``milliseconds`` / ``fetch_ohlcv``만 stub된 가짜 exchange."""
    ex = MagicMock()
    ex.parse_timeframe.return_value = 60  # 1m = 60s
    ex.milliseconds.return_value = START_MS + 86_400_000  # 1일 뒤
    return ex


@pytest.fixture
def no_sleep(monkeypatch):
    """tenacity retry의 실제 sleep을 무력화 (재시도 테스트가 실시간 대기하지 않도록)."""
    monkeypatch.setattr(m._fetch_page.retry, "sleep", lambda _seconds: None)


# =====================================================================
# 페이지네이션 (4)
# =====================================================================


def test_pagination_basic(fake_exchange):
    """2 페이지(1000+500) + 빈 응답으로 종료, 단조증가 unique 1500행 DataFrame을 반환한다."""
    page1 = _make_candles(1000, START_MS)
    page2 = _make_candles(500, START_MS + 1000 * TF_MS)
    with patch(
        "scripts.fetch_ohlcv._fetch_page",
        side_effect=[page1, page2, []],
    ) as mock_fp:
        df = m.fetch_ohlcv(
            fake_exchange, "BTC/USDT", "1m",
            since_ms=START_MS, until_ms=LARGE_UNTIL,
        )
    assert mock_fp.call_count == 3  # 빈 응답까지 가서 종료
    assert len(df) == 1500
    assert df["timestamp"].is_monotonic_increasing
    assert df["timestamp"].is_unique
    assert df["timestamp"].dtype == "int64"
    assert df["open"].dtype == "float64"
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]


def test_pagination_advances_through_short_pages(fake_exchange):
    """``page_limit`` 미만 페이지(999)가 와도 종료 안 하고 다음 페이지로 진행한다.

    Bybit perpetual이 ``since`` 인자 사용 시 항상 limit-1(예: 999/1000)을 반환하는
    실측 동작에 대한 회귀 보호. 종료는 빈 응답 또는 cursor>=until_ms 로만 결정.
    """
    page1 = _make_candles(999, START_MS)
    page2 = _make_candles(999, START_MS + 999 * TF_MS)
    with patch(
        "scripts.fetch_ohlcv._fetch_page",
        side_effect=[page1, page2, []],
    ) as mock_fp:
        df = m.fetch_ohlcv(
            fake_exchange, "BTC/USDT", "1m",
            since_ms=START_MS, until_ms=LARGE_UNTIL,
        )
    assert mock_fp.call_count == 3  # 999 미만이라고 종료 안 함
    assert len(df) == 1998


def test_pagination_terminates_when_empty(fake_exchange):
    """page2 = ``[]`` (빈 응답)이면 2회 호출 후 종료, 결과 1000행."""
    page1 = _make_candles(1000, START_MS)
    with patch("scripts.fetch_ohlcv._fetch_page", side_effect=[page1, []]) as mock_fp:
        df = m.fetch_ohlcv(
            fake_exchange, "BTC/USDT", "1m",
            since_ms=START_MS, until_ms=LARGE_UNTIL,
        )
    assert mock_fp.call_count == 2
    assert len(df) == 1000


def test_pagination_since_advances(fake_exchange):
    """두 번째 호출의 ``since_ms`` = 첫 페이지 마지막 ts + ``tf_ms`` 이어야 한다."""
    page1 = _make_candles(1000, START_MS)
    page2 = _make_candles(500, START_MS + 1000 * TF_MS)
    expected_second_since = page1[-1][0] + TF_MS
    with patch(
        "scripts.fetch_ohlcv._fetch_page",
        side_effect=[page1, page2, []],
    ) as mock_fp:
        m.fetch_ohlcv(
            fake_exchange, "BTC/USDT", "1m",
            since_ms=START_MS, until_ms=LARGE_UNTIL,
        )
    # _fetch_page(exchange, symbol, timeframe, since_ms, limit) — 4번째 positional
    second_call_since = mock_fp.call_args_list[1].args[3]
    assert second_call_since == expected_second_since


# =====================================================================
# until_ms 컷 + 페이지 경계 중복 제거 (2)
# =====================================================================


def test_until_ms_cutoff(fake_exchange):
    """``until_ms`` 이상의 캔들은 결과에서 제외되어야 한다."""
    # 100개 캔들 받지만 50개 시점에 until_ms 컷 → 결과 50행
    page = _make_candles(100, START_MS)
    until_ms = START_MS + 50 * TF_MS  # 50번째 ts (포함되지 않음, 반개구간)
    with patch("scripts.fetch_ohlcv._fetch_page", side_effect=[page]):
        df = m.fetch_ohlcv(
            fake_exchange, "BTC/USDT", "1m",
            since_ms=START_MS, until_ms=until_ms,
        )
    assert len(df) == 50
    assert df["timestamp"].max() < until_ms


def test_pagination_safety_guard_when_cursor_stuck(fake_exchange):
    """거래소가 같은 ``last_ts``를 반복 반환할 때 안전가드로 break — 무한 루프 방지.

    Option A 도입에 따른 신규 보호. ``new_cursor <= cursor`` 조건이 발동되어야 한다.
    """
    # 두 페이지 모두 동일 시작 ts + 동일 길이 → last_ts 동일 → new_cursor 진행 X
    page1 = _make_candles(100, START_MS)
    page2 = _make_candles(100, START_MS)
    with patch(
        "scripts.fetch_ohlcv._fetch_page",
        side_effect=[page1, page2],  # 3번째 호출이 일어나면 StopIteration → 발생 시 가드 미작동 의미
    ) as mock_fp:
        df = m.fetch_ohlcv(
            fake_exchange, "BTC/USDT", "1m",
            since_ms=START_MS, until_ms=LARGE_UNTIL,
        )
    assert mock_fp.call_count == 2  # iter2에서 안전가드 발동, 3번째 호출 X
    assert df["timestamp"].is_unique  # 동일 ts 중복은 drop_duplicates로 정리
    assert len(df) == 100


def test_dedup_at_page_boundary(fake_exchange):
    """페이지 경계에서 ts 중복이 발생해도 ``drop_duplicates``로 제거된다."""
    page1 = _make_candles(1000, START_MS)
    # page2가 page1의 마지막 ts와 동일 ts에서 시작 (의도적 중복 시뮬레이션)
    overlap_start = page1[-1][0]
    page2 = _make_candles(500, overlap_start)  # page2[0].ts == page1[-1].ts
    with patch(
        "scripts.fetch_ohlcv._fetch_page",
        side_effect=[page1, page2, []],
    ):
        df = m.fetch_ohlcv(
            fake_exchange, "BTC/USDT", "1m",
            since_ms=START_MS, until_ms=LARGE_UNTIL,
        )
    # 1000 + 500 - 1(중복 1행) = 1499
    assert len(df) == 1499
    assert df["timestamp"].is_unique
    assert df["timestamp"].is_monotonic_increasing


# =====================================================================
# retry (5)
# =====================================================================


def test_retry_on_network_error(no_sleep):
    """1차 ``NetworkError``, 2차 성공 → 정상 반환, ``fetch_ohlcv`` 2회 호출."""
    ex = MagicMock()
    candles = _make_candles(10, START_MS)
    ex.fetch_ohlcv.side_effect = [ccxt.NetworkError("transient"), candles]

    result = m._fetch_page(ex, "BTC/USDT", "1m", START_MS, 1000)

    assert result == candles
    assert ex.fetch_ohlcv.call_count == 2


def test_retry_max_attempts_exceeded(no_sleep):
    """모든 호출 ``NetworkError`` → 5회 시도 후 ``reraise=True``로 그대로 raise."""
    ex = MagicMock()
    ex.fetch_ohlcv.side_effect = ccxt.NetworkError("permanent")

    with pytest.raises(ccxt.NetworkError):
        m._fetch_page(ex, "BTC/USDT", "1m", START_MS, 1000)

    assert ex.fetch_ohlcv.call_count == 5


def test_no_retry_on_auth_error(no_sleep):
    """``AuthenticationError``는 retry 비대상 → 즉시 raise, 호출 1회."""
    ex = MagicMock()
    ex.fetch_ohlcv.side_effect = ccxt.AuthenticationError("bad key")

    with pytest.raises(ccxt.AuthenticationError):
        m._fetch_page(ex, "BTC/USDT", "1m", START_MS, 1000)

    assert ex.fetch_ohlcv.call_count == 1


def test_retry_on_rate_limit_exceeded(no_sleep):
    """``RateLimitExceeded`` → 재시도 후 성공 (Stage 1A v2 보강)."""
    ex = MagicMock()
    candles = _make_candles(5, START_MS)
    ex.fetch_ohlcv.side_effect = [ccxt.RateLimitExceeded("slow down"), candles]

    result = m._fetch_page(ex, "BTC/USDT", "1m", START_MS, 1000)

    assert result == candles
    assert ex.fetch_ohlcv.call_count == 2


def test_retry_on_ddos_protection(no_sleep):
    """``DDoSProtection`` → 재시도 후 성공 (Stage 1A v2 보강)."""
    ex = MagicMock()
    candles = _make_candles(5, START_MS)
    ex.fetch_ohlcv.side_effect = [ccxt.DDoSProtection("ddos"), candles]

    result = m._fetch_page(ex, "BTC/USDT", "1m", START_MS, 1000)

    assert result == candles
    assert ex.fetch_ohlcv.call_count == 2


# =====================================================================
# save_parquet (2)
# =====================================================================


def test_save_parquet_basic(tmp_path):
    """저장 → ``read_parquet`` 라운드트립이 동일 DataFrame을 복원한다."""
    df = pd.DataFrame({
        "timestamp": pd.array([1, 2, 3], dtype="int64"),
        "open": [1.0, 2.0, 3.0],
        "high": [1.1, 2.1, 3.1],
        "low": [0.9, 1.9, 2.9],
        "close": [1.05, 2.05, 3.05],
        "volume": [10.0, 20.0, 30.0],
    })
    path = tmp_path / "out.parquet"

    m.save_parquet(df, path)

    assert path.exists()
    df2 = pd.read_parquet(path)
    assert df.equals(df2)


def test_save_parquet_creates_dir(tmp_path):
    """미존재 다단계 디렉토리에 저장 시 부모를 자동 생성한다."""
    df = pd.DataFrame({
        "timestamp": pd.array([1], dtype="int64"),
        "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0],
    })
    nested = tmp_path / "a" / "b" / "c" / "deep.parquet"
    assert not nested.parent.exists()

    m.save_parquet(df, nested)

    assert nested.exists()
    assert nested.parent.is_dir()


# =====================================================================
# 입력 검증 (2)
# =====================================================================


def test_invalid_market_type_raises():
    """``_init_exchange``에 ``linear``/``spot`` 외 입력 → 한국어 ``ValueError``."""
    with pytest.raises(ValueError, match="지원하지 않는 market_type"):
        m._init_exchange("futures")


def test_since_ms_ge_until_ms_raises(fake_exchange):
    """``since_ms >= until_ms`` → ``ValueError`` (Group 2 추가 검증, 회귀 방지)."""
    with pytest.raises(ValueError, match="since_ms"):
        m.fetch_ohlcv(fake_exchange, "BTC/USDT", "1m", since_ms=200, until_ms=100)
    with pytest.raises(ValueError, match="since_ms"):
        # 동일 값(반개구간이라 빈 결과 가능하지만 명시 거절이 디버깅 친화)
        m.fetch_ohlcv(fake_exchange, "BTC/USDT", "1m", since_ms=100, until_ms=100)


# =====================================================================
# 엣지 케이스 (1)
# =====================================================================


def test_empty_result_dtype_preserved(fake_exchange):
    """첫 호출이 빈 페이지여도 컬럼/dtype은 유지되어야 한다 (스키마 안정성)."""
    with patch("scripts.fetch_ohlcv._fetch_page", side_effect=[[]]):
        df = m.fetch_ohlcv(
            fake_exchange, "BTC/USDT", "1m",
            since_ms=START_MS, until_ms=START_MS + 1_000_000,
        )
    assert len(df) == 0
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert df["timestamp"].dtype == "int64"
    assert df["open"].dtype == "float64"
    assert df["volume"].dtype == "float64"


# =====================================================================
# CLI e2e (1)
# =====================================================================


def test_main_e2e_with_mock(tmp_path):
    """``argv`` 파싱 → mock 거래소/페치 → ``--output`` 경로에 parquet 생성, ``return 0``."""
    fake_ex = MagicMock()
    fake_ex.parse_timeframe.return_value = 60
    fake_ex.milliseconds.return_value = START_MS + 86_400_000  # 1일 뒤
    candles = _make_candles(100, START_MS)
    out_path = tmp_path / "BTCUSDT_1m.parquet"

    with patch("scripts.fetch_ohlcv._init_exchange", return_value=fake_ex), \
         patch("scripts.fetch_ohlcv._fetch_page", side_effect=[candles, []]):
        rc = m.main([
            "--symbol", "BTC/USDT",
            "--days", "1",
            "--timeframe", "1m",
            "--output", str(out_path),
        ])

    assert rc == 0
    assert out_path.exists()
    df = pd.read_parquet(out_path)
    assert len(df) == 100
    assert df["timestamp"].dtype == "int64"
