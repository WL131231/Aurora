"""BotInstance 단위 테스트 — lifecycle + configure + 매매 사이클.

기존 (PR-C) 5 케이스: lifecycle / 싱글톤 / 호환성
신규 (Stage 2E C): configure / _step 매매 분기 / property 노출
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from aurora.exchange.base import Balance, Order
from aurora.interfaces import bot_instance


@pytest.fixture(autouse=True)
def _reset() -> None:
    bot_instance.reset_for_test()


# ============================================================
# 기존 PR-C 5 케이스 — 호환성 보존
# ============================================================


@pytest.mark.asyncio
async def test_start_stop_lifecycle() -> None:
    bot = bot_instance.get_instance()
    assert not bot.running
    await bot.start()
    assert bot.running
    await bot.stop()
    assert not bot.running


@pytest.mark.asyncio
async def test_double_start_warns() -> None:
    bot = bot_instance.get_instance()
    await bot.start()
    await bot.start()  # 두 번째는 무시
    assert bot.running
    await bot.stop()


@pytest.mark.asyncio
async def test_double_stop_warns() -> None:
    bot = bot_instance.get_instance()
    await bot.stop()  # 이미 중지 상태
    assert not bot.running


def test_get_instance_returns_singleton() -> None:
    a = bot_instance.get_instance()
    b = bot_instance.get_instance()
    assert a is b


def test_reset_for_test_clears_singleton() -> None:
    a = bot_instance.get_instance()
    bot_instance.reset_for_test()
    b = bot_instance.get_instance()
    assert a is not b


# ============================================================
# configure — 신규 (Stage 2E C)
# ============================================================


def _make_mock_client(*, ohlcv_rows: list | None = None) -> MagicMock:
    """매매 사이클 검증용 mock client — 모든 어댑터 메서드 AsyncMock."""
    client = MagicMock()
    rows = ohlcv_rows if ohlcv_rows is not None else []
    if rows:
        df = pd.DataFrame(
            rows,
            columns=["timestamp_ms", "open", "high", "low", "close", "volume"],
        )
        df.index = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
        df = df[["open", "high", "low", "close", "volume"]]
    else:
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    client.fetch_ohlcv = AsyncMock(return_value=df)
    client.fetch_position = AsyncMock(return_value=None)
    client.fetch_positions = AsyncMock(return_value=[])
    client.get_positions = AsyncMock(return_value=[])
    client.get_equity = AsyncMock(
        return_value=Balance(total_usd=10000.0, free_usd=10000.0, used_usd=0.0),
    )
    client.place_order = AsyncMock(
        return_value=Order(
            order_id="test-1", symbol="BTC/USDT:USDT", side="buy", qty=0.001,
            price=None, status="filled", timestamp_ms=0,
        ),
    )
    client.set_leverage = AsyncMock(return_value=None)
    client.cancel_all = AsyncMock(return_value=None)
    client.close = AsyncMock(return_value=None)
    return client


def test_is_configured_initially_false() -> None:
    """configure 호출 전 — is_configured False."""
    bot = bot_instance.get_instance()
    assert bot.is_configured is False
    assert bot.has_position is False


def test_configure_sets_client_and_options() -> None:
    """configure(client, ...) — 어댑터 + 설정 inject."""
    bot = bot_instance.get_instance()
    client = _make_mock_client()
    bot.configure(
        client=client,
        symbol="ETH/USDT:USDT",
        timeframes=["1H", "4H"],
        leverage=20,
        risk_pct=0.02,
    )
    assert bot.is_configured
    assert bot._symbol == "ETH/USDT:USDT"
    assert bot._timeframes == ["1H", "4H"]
    assert bot._leverage == 20
    assert bot._risk_pct == 0.02


@pytest.mark.asyncio
async def test_configure_blocked_during_running() -> None:
    """running 중 configure → RuntimeError (도중 inject 차단)."""
    bot = bot_instance.get_instance()
    await bot.start()
    try:
        with pytest.raises(RuntimeError, match="running 중 configure"):
            bot.configure(client=_make_mock_client())
    finally:
        await bot.stop()


# ============================================================
# 매매 사이클 (_step) — 신규
# ============================================================


def _make_ohlcv_rows(start_ts_ms: int, count: int, tf_minutes: int, base: float = 100.0):
    """결정론적 OHLCV row list (가격 base 고정)."""
    return [
        [start_ts_ms + i * tf_minutes * 60_000, base, base + 1, base - 1, base + 0.5, 10.0]
        for i in range(count)
    ]


@pytest.mark.asyncio
async def test_start_with_configure_warmups_cache() -> None:
    """configure 후 start — 어댑터 생성 + warmup fetch 호출."""
    bot = bot_instance.get_instance()
    rows = _make_ohlcv_rows(start_ts_ms=1_700_000_000_000, count=10, tf_minutes=60)
    client = _make_mock_client(ohlcv_rows=rows)
    bot.configure(client=client, symbol="BTC/USDT:USDT", timeframes=["1H"])
    await bot.start()
    # warmup 1회 fetch (1H)
    assert client.fetch_ohlcv.call_count >= 1
    await bot.stop()


@pytest.mark.asyncio
async def test_stop_closes_client() -> None:
    """stop → client.close() + cache None 정리. Executor state 는 보존 (포지션 살림)."""
    bot = bot_instance.get_instance()
    rows = _make_ohlcv_rows(start_ts_ms=1_700_000_000_000, count=5, tf_minutes=60)
    client = _make_mock_client(ohlcv_rows=rows)
    bot.configure(client=client, timeframes=["1H"])
    await bot.start()
    await bot.stop()
    client.close.assert_called_once()
    assert bot._client is None
    assert bot._cache is None
    # Executor 는 보존 — _plan 살리려고 (v0.1.6 Executor state 보존 fix).
    # 자기 포지션 보유 중에 stop → start 시 has_position 유지가 정합.
    assert bot._executor is not None


@pytest.mark.asyncio
async def test_step_resets_position_when_externally_closed() -> None:
    """거래소 측 포지션 사라지면 (사용자 직접 청산) Executor state reset.

    v0.1.7 fix: 이전엔 _plan 영원히 살아 has_position=True → 트레일링만 돌고
    신규 진입 평가 안 함 → 봇 멈춤. _step 시작 부분에서 fetch_position 으로
    sync → 거래소 측 None 면 reset_position 호출.
    """
    from aurora.core.risk import TpSlConfig, build_risk_plan

    bot = bot_instance.get_instance()
    rows = _make_ohlcv_rows(start_ts_ms=1_700_000_000_000, count=200, tf_minutes=60)
    client = _make_mock_client(ohlcv_rows=rows)
    # 거래소 측은 처음부터 포지션 없음 (사용자 직접 청산 후 시점 시뮬)
    client.fetch_position = AsyncMock(return_value=None)
    bot.configure(client=client, timeframes=["1H"], tpsl_config=TpSlConfig())

    # Executor 에 가짜 _plan 직접 주입 (봇 자기 진입 후 시점)
    plan = build_risk_plan(
        entry_price=78000.0, direction="long", leverage=10,
        equity_usd=10000.0, config=TpSlConfig(), risk_pct=0.01,
    )
    await bot.start()
    bot._executor._plan = plan
    bot._executor._remaining_qty = plan.position.coin_amount
    assert bot._executor.has_position

    # _step 1회 — fetch_position=None 감지 → reset_position 호출
    await bot._step()

    # Executor state reset 확인
    assert not bot._executor.has_position
    assert bot._executor._plan is None

    await bot.stop()


@pytest.mark.asyncio
async def test_stop_start_cycle_preserves_executor_position() -> None:
    """stop → start 사이클 시 Executor._plan 보존 → has_position 유지.

    v0.1.6 fix: 이전엔 stop 시 _executor=None → 재 start 시 새 Executor → _plan=None
    → has_position=False → 진입 시도 → InsufficientFunds 무한 루프.
    """
    from aurora.core.risk import TpSlConfig, build_risk_plan

    bot = bot_instance.get_instance()
    rows = _make_ohlcv_rows(start_ts_ms=1_700_000_000_000, count=5, tf_minutes=60)
    client = _make_mock_client(ohlcv_rows=rows)
    bot.configure(client=client, timeframes=["1H"], tpsl_config=TpSlConfig())
    await bot.start()

    # Executor 에 가짜 포지션 직접 주입 — open_position 까지 안 가도 _plan 만 set
    plan = build_risk_plan(
        entry_price=78000.0, direction="long", leverage=10,
        equity_usd=10000.0, config=TpSlConfig(), risk_pct=0.01,
    )
    bot._executor._plan = plan
    bot._executor._remaining_qty = plan.position.coin_amount
    assert bot._executor.has_position

    # stop → start 사이클
    await bot.stop()
    new_client = _make_mock_client(ohlcv_rows=rows)
    bot.configure(client=new_client, timeframes=["1H"], tpsl_config=TpSlConfig())
    await bot.start()

    # Executor state 보존 검증
    assert bot._executor is not None
    assert bot._executor.has_position  # _plan 살아있음 → 자기 포지션 인식
    assert bot._executor._client is new_client  # set_client 로 새 client 주입됨

    await bot.stop()


@pytest.mark.asyncio
async def test_manual_configure_no_auto_reconfigure_after_stop() -> None:
    """수동 configure(mock inject) 한 봇은 stop 후 재 start 시 reconfigure 안 함.

    Why: mock 환경에서 두 번째 start 가 configure_from_settings 부르면 실 ccxt
    만들려 시도 → 테스트 격리 깨짐. 수동 inject 케이스는 _auto_configured=False.
    """
    bot = bot_instance.get_instance()
    rows = _make_ohlcv_rows(start_ts_ms=1_700_000_000_000, count=5, tf_minutes=60)
    client = _make_mock_client(ohlcv_rows=rows)
    bot.configure(client=client, timeframes=["1H"])
    await bot.start()
    await bot.stop()
    # 재 start — client 는 None 그대로 (mock 보존 X), reconfigure 호출 X
    await bot.start()
    assert bot._client is None  # 자동 reconfigure 트리거 안 됨
    assert bot.running  # noop loop 로 진입 (lifecycle flag 만)
    await bot.stop()


@pytest.mark.asyncio
async def test_auto_configure_reconfigures_on_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """configure_from_settings 한 봇은 stop 후 재 start 시 자동 reconfigure.

    실제 사용자 흐름 — main.py 가 configure_from_settings → 사용자 ▶ 시작 → ■ 중지
    → ▶ 시작 사이클. 두 번째 start 가 client 다시 만들어야 포지션 표시 유지.
    """
    bot = bot_instance.get_instance()
    rows = _make_ohlcv_rows(start_ts_ms=1_700_000_000_000, count=5, tf_minutes=60)

    # configure_from_settings 가 호출되면 mock client inject (실 ccxt 안 만듦)
    call_count = {"n": 0}

    def fake_configure() -> None:
        call_count["n"] += 1
        client = _make_mock_client(ohlcv_rows=rows)
        bot.configure(client=client, timeframes=["1H"])
        bot._auto_configured = True  # 자동 configure 마커

    fake_configure()  # 첫 호출 — main.py 가 한 자동 configure 흉내
    await bot.start()
    assert bot.running
    assert call_count["n"] == 1

    await bot.stop()
    assert bot._client is None  # stop 이 정리

    # start() 가 _auto_configured=True 보고 configure_from_settings 호출 시도.
    # 본 테스트는 진짜 configure_from_settings 호출 — 그 안에서 settings.bybit_api_key
    # 가 비어있어도 CcxtClient 생성 자체는 됨 (실 호출 시점에 에러).
    # 핵심 검증 = client 가 None 아닌 상태로 복원됨.
    monkeypatch.setattr(bot, "configure_from_settings", fake_configure)
    await bot.start()
    assert call_count["n"] == 2  # 자동 reconfigure 호출됨
    assert bot._client is not None  # 새 mock client 복원
    await bot.stop()


@pytest.mark.asyncio
async def test_step_noop_when_not_configured() -> None:
    """configure 없이 _step() 호출 — noop (예외 없음, fetch 0회)."""
    bot = bot_instance.get_instance()
    # _step 직접 호출 (start 없이)
    await bot._step()  # noop
    # 예외 없이 완료


@pytest.mark.asyncio
async def test_has_position_reflects_executor_state() -> None:
    """has_position property — executor.has_position 그대로 반영."""
    bot = bot_instance.get_instance()
    rows = _make_ohlcv_rows(start_ts_ms=1_700_000_000_000, count=5, tf_minutes=60)
    client = _make_mock_client(ohlcv_rows=rows)
    bot.configure(client=client, timeframes=["1H"])
    assert bot.has_position is False  # configure 만, executor 아직
    await bot.start()
    assert bot.has_position is False  # executor 생성됐지만 진입 X
    await bot.stop()


@pytest.mark.asyncio
async def test_step_skips_strategy_when_position_open() -> None:
    """포지션 보유 시 _step 은 트레일링/청산만, strategy 평가 X.

    Why: 동시에 진입+청산 평가하면 같은 봉에서 close+open 가능.
    Aurora 정책 (페어당 1개) 위반 방지.
    """
    bot = bot_instance.get_instance()
    rows = _make_ohlcv_rows(start_ts_ms=1_700_000_000_000, count=10, tf_minutes=60)
    client = _make_mock_client(ohlcv_rows=rows)
    bot.configure(client=client, timeframes=["1H"])
    await bot.start()

    # executor 가짜 포지션 상태로
    bot._executor._plan = MagicMock()                   # type: ignore[union-attr]
    bot._executor._plan.direction = "long"              # type: ignore[union-attr]
    bot._executor._plan.tp_prices = [110, 120, 130, 140]  # type: ignore[union-attr]
    bot._executor._plan.sl_price = 90                   # type: ignore[union-attr]
    bot._executor._remaining_qty = 0.001                # type: ignore[union-attr]
    bot._executor._highest_since_entry = 100            # type: ignore[union-attr]
    bot._executor._lowest_since_entry = 100             # type: ignore[union-attr]

    # get_equity 호출 카운트 baseline (warmup 후)
    baseline_equity_calls = client.get_equity.call_count

    # _step 직접 호출 — 포지션 있으므로 트레일링만, get_equity (진입용) X
    await bot._step()

    # get_equity 호출 X (진입 평가 안 됨)
    assert client.get_equity.call_count == baseline_equity_calls

    await bot.stop()
