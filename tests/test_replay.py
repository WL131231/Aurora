"""backtest.replay.MultiTfAggregator 단위 테스트.

Aurora 봇 런타임은 100% 룰 기반 — 본 테스트도 외부 API/LLM 호출 없이 합성
OHLCV 만으로 결정론적 검증.

테스트 날짜는 2024-01-08 (월요일) 기준 — 1W bucket 정렬 검증과 일관성 유지.

담당: ChoYoon
"""

from __future__ import annotations

import pandas as pd
import pytest

from aurora.backtest.replay import (
    TF_MINUTES,
    MultiTfAggregator,
    _bucket_open_time,
)

# ============================================================
# 헬퍼 — 1 분봉 Series 생성 fixture
# ============================================================


def make_minute_bar(
    ts: pd.Timestamp | str,
    o: float,
    h: float,
    lo: float,
    c: float,
    v: float,
) -> pd.Series:
    """``name=open_time`` + OHLCV 값을 가진 1 분봉 Series 생성.

    실제 환경에서 ``DataFrame.iterrows()`` 가 산출하는 형태와 동일.

    Args:
        ts: 봉의 open_time (Timestamp 또는 그 문자열 표현).
        o: open.
        h: high.
        lo: low (E741 회피 위해 ``l`` 대신 ``lo``).
        c: close.
        v: volume.
    """
    if isinstance(ts, str):
        ts = pd.Timestamp(ts)
    return pd.Series(
        {"open": o, "high": h, "low": lo, "close": c, "volume": v},
        name=ts,
    )


# ============================================================
# 1. 5m boundary 정확성
# ============================================================


def test_5m_closes_at_boundary() -> None:
    """5m TF 가 09:05 / 09:10 에서만 닫힘 (사이는 None)."""
    agg = MultiTfAggregator(timeframes=["5m"], buffer_size=10)

    # 09:00 ~ 09:10 분봉 11 개를 흘려보내며 결과 수집
    closed_at: dict[pd.Timestamp, object] = {}
    for minute in range(11):
        ts = pd.Timestamp(f"2024-01-08 09:{minute:02d}:00")
        result = agg.step(make_minute_bar(ts, 100.0, 100.0, 100.0, 100.0, 1.0))
        closed_at[ts] = result["5m"]

    # 09:00 ~ 09:04: cold start + 같은 bucket → 모두 None
    for minute in range(5):
        ts = pd.Timestamp(f"2024-01-08 09:{minute:02d}:00")
        assert closed_at[ts] is None, f"{ts}: 봉이 닫힘 (예상: None)"

    # 09:05: 09:00 봉 마감
    bar0 = closed_at[pd.Timestamp("2024-01-08 09:05:00")]
    assert bar0 is not None
    assert bar0.open_time == pd.Timestamp("2024-01-08 09:00:00")
    assert bar0.close_ts == pd.Timestamp("2024-01-08 09:05:00")

    # 09:06 ~ 09:09: None
    for minute in range(6, 10):
        ts = pd.Timestamp(f"2024-01-08 09:{minute:02d}:00")
        assert closed_at[ts] is None

    # 09:10: 09:05 봉 마감
    bar1 = closed_at[pd.Timestamp("2024-01-08 09:10:00")]
    assert bar1 is not None
    assert bar1.open_time == pd.Timestamp("2024-01-08 09:05:00")
    assert bar1.close_ts == pd.Timestamp("2024-01-08 09:10:00")


# ============================================================
# 2. 비-boundary 분봉은 None
# ============================================================


def test_non_boundary_returns_none() -> None:
    """5m bucket 진행 중 분봉(09:01 ~ 09:04) 은 모두 None 반환."""
    agg = MultiTfAggregator(timeframes=["5m"])
    for minute in range(1, 5):
        ts = pd.Timestamp(f"2024-01-08 09:{minute:02d}:00")
        result = agg.step(make_minute_bar(ts, 100.0, 100.0, 100.0, 100.0, 1.0))
        assert result["5m"] is None


# ============================================================
# 3. 여러 TF 동시 마감
# ============================================================


def test_multiple_tfs_close_simultaneously() -> None:
    """1H/2H/4H 의 마감 시점이 봉 경계에 따라 정확히 갈라짐.

    08:00 ~ 12:00 분단위로 흘려보낼 때:
    - 10:00: 1H(09:00 봉) + 2H(08:00 봉) 마감, 4H 진행 중
    - 11:00: 1H(10:00 봉) 만 마감
    - 12:00: 1H(11:00 봉) + 2H(10:00 봉) + 4H(08:00 봉) 모두 마감
    """
    agg = MultiTfAggregator(timeframes=["1H", "2H", "4H"])

    for total_min in range(0, 4 * 60 + 1):
        ts = pd.Timestamp("2024-01-08 08:00:00") + pd.Timedelta(minutes=total_min)
        result = agg.step(make_minute_bar(ts, 100.0, 100.0, 100.0, 100.0, 1.0))

        if ts == pd.Timestamp("2024-01-08 10:00:00"):
            assert result["1H"] is not None
            assert result["1H"].open_time == pd.Timestamp("2024-01-08 09:00:00")
            assert result["2H"] is not None
            assert result["2H"].open_time == pd.Timestamp("2024-01-08 08:00:00")
            assert result["4H"] is None

        elif ts == pd.Timestamp("2024-01-08 11:00:00"):
            assert result["1H"] is not None
            assert result["1H"].open_time == pd.Timestamp("2024-01-08 10:00:00")
            assert result["2H"] is None
            assert result["4H"] is None

        elif ts == pd.Timestamp("2024-01-08 12:00:00"):
            assert result["1H"] is not None
            assert result["1H"].open_time == pd.Timestamp("2024-01-08 11:00:00")
            assert result["2H"] is not None
            assert result["2H"].open_time == pd.Timestamp("2024-01-08 10:00:00")
            assert result["4H"] is not None
            assert result["4H"].open_time == pd.Timestamp("2024-01-08 08:00:00")


# ============================================================
# 4. 데이터 갭 — 부분봉 emit, 상위 TF bucket 누락
# ============================================================


def test_data_gap_skips_higher_tf() -> None:
    """1H: 09:00, 09:01 후 11:30 으로 점프 → 09:00 부분봉만 emit, 10:00 봉 누락.

    24/7 운영 시장 가정에서 갭은 fabrication 하지 않음. 갭이 완전히 포함된 10:00
    H1 bucket 의 봉은 buffer 에 들어가지 않음.
    """
    agg = MultiTfAggregator(timeframes=["1H"])

    agg.step(make_minute_bar("2024-01-08 09:00:00", 100, 105, 95, 102, 10.0))
    agg.step(make_minute_bar("2024-01-08 09:01:00", 102, 108, 100, 106, 5.0))

    # 11:30 점프 → 09:00 H1 봉 마감 (09:00 ~ 09:01 의 부분봉)
    result = agg.step(make_minute_bar("2024-01-08 11:30:00", 200, 200, 200, 200, 1.0))
    bar = result["1H"]
    assert bar is not None
    assert bar.open_time == pd.Timestamp("2024-01-08 09:00:00")
    assert bar.open == 100.0
    assert bar.high == 108.0
    assert bar.low == 95.0
    assert bar.close == 106.0
    assert bar.volume == pytest.approx(15.0)

    # 10:00 H1 봉은 emit 되지 않음 → buffer 에 09:00 봉 1 개만
    df = agg.get_df("1H")
    assert len(df) == 1
    assert df.index[0] == pd.Timestamp("2024-01-08 09:00:00")


# ============================================================
# 5. cold start (첫 분봉이 bucket 중간)
# ============================================================


def test_cold_start_first_bar() -> None:
    """첫 1 분봉이 09:03 (5m bucket 09:00 의 중간) → 부분봉이 09:05 에 emit.

    호출자가 warmup 으로 폐기할 수 있도록 의도적으로 부분봉을 emit (fabrication X).
    """
    agg = MultiTfAggregator(timeframes=["5m"])

    r1 = agg.step(make_minute_bar("2024-01-08 09:03:00", 100, 105, 95, 102, 1.0))
    assert r1["5m"] is None

    r2 = agg.step(make_minute_bar("2024-01-08 09:04:00", 102, 110, 101, 108, 2.0))
    assert r2["5m"] is None

    r3 = agg.step(make_minute_bar("2024-01-08 09:05:00", 108, 108, 108, 108, 1.0))
    bar = r3["5m"]
    assert bar is not None
    assert bar.open_time == pd.Timestamp("2024-01-08 09:00:00")  # bucket 시작 시각
    assert bar.open == 100.0  # 09:03 분봉의 open (부분봉이라 09:00 데이터 없음)
    assert bar.high == 110.0
    assert bar.low == 95.0
    assert bar.close == 108.0
    assert bar.volume == pytest.approx(3.0)


# ============================================================
# 6. buffer maxlen 초과 시 가장 오래된 봉 자동 삭제
# ============================================================


def test_buffer_maxlen_drops_oldest() -> None:
    """buffer_size=2 → 5m 봉 5 개가 닫혀도 가장 최근 2 개만 buffer 에 남음."""
    agg = MultiTfAggregator(timeframes=["5m"], buffer_size=2)

    # 09:00 ~ 09:25 분단위 26 step → 5m 봉 5 개 마감
    # (09:00, 09:05, 09:10, 09:15, 09:20 봉 — 09:25 분봉이 09:20 봉 마감 트리거)
    for minute in range(0, 26):
        ts = pd.Timestamp("2024-01-08 09:00:00") + pd.Timedelta(minutes=minute)
        agg.step(make_minute_bar(ts, 100.0, 100.0, 100.0, 100.0, 1.0))

    df = agg.get_df("5m")
    assert len(df) == 2  # maxlen 만큼만 남음
    assert df.index[0] == pd.Timestamp("2024-01-08 09:15:00")
    assert df.index[1] == pd.Timestamp("2024-01-08 09:20:00")


# ============================================================
# 7. OHLCV 집계 정확성
# ============================================================


def test_aggregation_correctness() -> None:
    """5m 봉 안 5 개 1 분봉의 OHLCV 집계가 정확한지 — open=첫, high=max, low=min,
    close=마지막, volume=합."""
    agg = MultiTfAggregator(timeframes=["5m"])

    bars = [
        ("2024-01-08 09:00:00", 100, 105, 99, 102, 1.0),     # open=100
        ("2024-01-08 09:01:00", 102, 110, 101, 107, 2.0),    # high=110 (전체 최고)
        ("2024-01-08 09:02:00", 107, 108, 95, 100, 3.0),     # low=95 (전체 최저)
        ("2024-01-08 09:03:00", 100, 103, 98, 102, 1.5),
        ("2024-01-08 09:04:00", 102, 104, 100, 103, 0.5),    # close=103 (마지막)
    ]
    for ts, o, h, lo, c, v in bars:
        agg.step(make_minute_bar(ts, o, h, lo, c, v))

    # 09:05 분봉 → 09:00 봉 마감
    result = agg.step(make_minute_bar("2024-01-08 09:05:00", 103, 103, 103, 103, 0.0))
    bar = result["5m"]
    assert bar is not None
    assert bar.open == 100.0
    assert bar.high == 110.0
    assert bar.low == 95.0
    assert bar.close == 103.0
    assert bar.volume == pytest.approx(8.0)  # 1 + 2 + 3 + 1.5 + 0.5


# ============================================================
# 8. 잘못된 timeframe → ValueError
# ============================================================


def test_invalid_timeframe_raises() -> None:
    """TF_MINUTES 에 없는 키는 명시적 ValueError (한국어 메시지)."""
    with pytest.raises(ValueError, match="지원하지 않는 timeframe"):
        MultiTfAggregator(timeframes=["7m"])


# ============================================================
# 9. 중복 timeframe → ValueError (volume 이중 집계 방지)
# ============================================================


def test_duplicate_timeframes_raises() -> None:
    """동일 TF 가 여러 번 들어오면 step() 내부에서 같은 1 분봉을 두 번 처리해
    volume 이중 집계 등 silent 버그가 발생하므로 fail-fast."""
    with pytest.raises(ValueError, match="중복"):
        MultiTfAggregator(timeframes=["1H", "1H", "5m"])


# ============================================================
# 10. 등록되지 않은 TF 로 get_df → ValueError
# ============================================================


def test_get_df_unknown_tf_raises() -> None:
    """__init__ 에 등록 안 한 TF 는 get_df 가 ValueError."""
    agg = MultiTfAggregator(timeframes=["5m"])
    with pytest.raises(ValueError, match="등록되지 않은 timeframe"):
        agg.get_df("1H")


# ============================================================
# 11. 빈 buffer 의 get_df
# ============================================================


def test_get_df_empty_returns_empty_df() -> None:
    """봉이 한 개도 닫히지 않은 상태 → 빈 DataFrame, 6 컬럼 + open_time 인덱스."""
    agg = MultiTfAggregator(timeframes=["5m"])
    df = agg.get_df("5m")
    assert len(df) == 0
    assert list(df.columns) == ["open", "high", "low", "close", "volume", "close_ts"]
    assert df.index.name == "open_time"  # 정상 케이스와 일관성


# ============================================================
# 12. close_ts 필드 정확성
# ============================================================


def test_close_ts_field_correct() -> None:
    """1H 봉의 close_ts == open_time + 60 분."""
    agg = MultiTfAggregator(timeframes=["1H"])

    # 09:00 ~ 10:00 분단위 → 10:00 분봉이 09:00 봉 마감을 트리거
    target_close: object = None
    for minute in range(0, 61):
        ts = pd.Timestamp("2024-01-08 09:00:00") + pd.Timedelta(minutes=minute)
        result = agg.step(make_minute_bar(ts, 100.0, 100.0, 100.0, 100.0, 1.0))
        if ts == pd.Timestamp("2024-01-08 10:00:00"):
            target_close = result["1H"]

    assert target_close is not None
    assert target_close.open_time == pd.Timestamp("2024-01-08 09:00:00")
    assert target_close.close_ts == pd.Timestamp("2024-01-08 10:00:00")
    assert target_close.close_ts - target_close.open_time == pd.Timedelta(minutes=60)


# ============================================================
# 13. 1W bucket 월요일 정렬 회귀
# ============================================================


def test_weekly_anchored_to_monday() -> None:
    """1W bucket 이 월요일 00:00 에 정렬 (1970-01-05 epoch 기준).

    pandas 기본 ``.floor("7D")`` 는 1970-01-01(목) 기준이라 거래소 관행과 어긋남.
    명시적 epoch 사용으로 월요일 정렬을 보장하는지 회귀 테스트.
    """
    # 2024-01-09 (화) 14:30 → 직전 월요일 2024-01-08 00:00 으로 정렬
    bucket = _bucket_open_time(pd.Timestamp("2024-01-09 14:30:00"), TF_MINUTES["1W"])
    assert bucket == pd.Timestamp("2024-01-08 00:00:00")
    assert bucket.weekday() == 0  # 0 = Monday

    # 2024-01-15 (월) 12:00 → 같은 날 00:00 으로 정렬
    bucket2 = _bucket_open_time(pd.Timestamp("2024-01-15 12:00:00"), TF_MINUTES["1W"])
    assert bucket2 == pd.Timestamp("2024-01-15 00:00:00")
    assert bucket2.weekday() == 0


# ============================================================
# 14. _bucket_open_time — 1H / 4H / 1D 정렬 회귀
# ============================================================

from aurora.backtest.replay import _bucket_open_time  # noqa: E402 (already imported above)


def test_bucket_1h_exact_start_unchanged() -> None:
    """1H bucket — 정각에 시작하는 봉은 그 자신이 open_time."""
    ts = pd.Timestamp("2024-01-08 14:00:00")
    assert _bucket_open_time(ts, TF_MINUTES["1H"]) == ts


def test_bucket_1h_mid_hour_returns_hour_start() -> None:
    """1H bucket — 14:37 → 14:00 으로 floor."""
    ts = pd.Timestamp("2024-01-08 14:37:00")
    expected = pd.Timestamp("2024-01-08 14:00:00")
    assert _bucket_open_time(ts, TF_MINUTES["1H"]) == expected


def test_bucket_4h_returns_correct_window_start() -> None:
    """4H bucket — 14:30 → 12:00 bucket (12:00~16:00 구간)."""
    ts = pd.Timestamp("2024-01-08 14:30:00")
    result = _bucket_open_time(ts, TF_MINUTES["4H"])
    expected = pd.Timestamp("2024-01-08 12:00:00")
    assert result == expected


def test_bucket_1d_exact_midnight_unchanged() -> None:
    """1D bucket — 자정 00:00 은 그 날 자정 그대로."""
    ts = pd.Timestamp("2024-01-08 00:00:00")
    assert _bucket_open_time(ts, TF_MINUTES["1D"]) == ts


def test_bucket_1d_noon_returns_midnight() -> None:
    """1D bucket — 12:00 → 그 날 00:00 으로 floor."""
    ts = pd.Timestamp("2024-01-08 12:00:00")
    expected = pd.Timestamp("2024-01-08 00:00:00")
    assert _bucket_open_time(ts, TF_MINUTES["1D"]) == expected


def test_bucket_result_is_multiple_of_tf_minutes() -> None:
    """결과 open_time 은 항상 tf_minutes 배수 (epoch 기준)."""
    from aurora.backtest.replay import _BUCKET_EPOCH
    for tf in ("1H", "4H", "1D"):
        ts = pd.Timestamp("2024-01-10 17:23:00")
        result = _bucket_open_time(ts, TF_MINUTES[tf])
        delta_minutes = int((result - _BUCKET_EPOCH).total_seconds() // 60)
        assert delta_minutes % TF_MINUTES[tf] == 0
