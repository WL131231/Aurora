"""telegram — 메시지 포맷·알림 라우팅 단위 테스트 (v0.1.96).

외부 Telegram API 호출 없음 — 포맷 함수 직접 호출 + TelegramBot 메서드 stub.
네트워크 의존 없이 메시지 구조·내용만 검증.

담당: 정용우
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from aurora.interfaces.telegram import (
    TelegramBot,
    _fmt_entry,
    _fmt_exit,
)

# ── 헬퍼 ─────────────────────────────────────────────────────────

def _make_plan(sl: float, tps: list[float]):
    """TP/SL plan stub."""
    return SimpleNamespace(sl_price=sl, tp_prices=tps)


def _make_trade(
    symbol="BTC/USDT:USDT",
    direction="long",
    reason="sl",
    pnl_usd=-120.0,
    roi_pct=-2.5,
):
    """ClosedTrade stub."""
    return SimpleNamespace(
        symbol=symbol,
        direction=direction,
        reason=reason,
        pnl_usd=pnl_usd,
        roi_pct=roi_pct,
    )


# ── _fmt_entry ────────────────────────────────────────────────────


def test_fmt_entry_long_contains_green_icon():
    data = {"direction": "long", "symbol": "BTC/USDT", "entry_price": 80000.0, "leverage": 20}
    msg = _fmt_entry(data)
    assert "🟢" in msg
    assert "LONG" in msg
    assert "BTC/USDT" in msg
    assert "$80,000.00" in msg
    assert "20x" in msg


def test_fmt_entry_short_contains_red_icon():
    data = {"direction": "short", "symbol": "ETH/USDT", "entry_price": 3000.0, "leverage": 10}
    msg = _fmt_entry(data)
    assert "🔴" in msg
    assert "SHORT" in msg


def test_fmt_entry_with_plan_includes_sl_tp():
    """plan 있으면 SL/TP 라인 포함."""
    plan = _make_plan(sl=78000.0, tps=[82000.0, 84000.0])
    data = {
        "direction": "long",
        "symbol": "BTC/USDT",
        "entry_price": 80000.0,
        "leverage": 20,
        "plan": plan,
    }
    msg = _fmt_entry(data)
    assert "SL:" in msg
    assert "TP1:" in msg
    assert "TP2:" in msg


def test_fmt_entry_without_plan_no_sl_tp():
    """plan 없으면 SL/TP 라인 없음."""
    data = {"direction": "long", "symbol": "BTC/USDT", "entry_price": 80000.0, "leverage": 10}
    msg = _fmt_entry(data)
    assert "SL:" not in msg
    assert "TP1:" not in msg


def test_fmt_entry_missing_fields_safe():
    """필드 누락 — KeyError 없이 안전하게 처리."""
    msg = _fmt_entry({})
    assert "신규 진입" in msg


# ── _fmt_exit ─────────────────────────────────────────────────────


def test_fmt_exit_sl_reason_maps_correctly():
    trade = _make_trade(reason="sl", pnl_usd=-200.0, roi_pct=-3.0)
    msg = _fmt_exit(trade)
    assert "SL (손절)" in msg
    assert "📉" in msg
    assert "$-200.00" in msg
    assert "-3.00%" in msg


def test_fmt_exit_tp_full_maps_correctly():
    trade = _make_trade(reason="tp_full", pnl_usd=500.0, roi_pct=4.2)
    msg = _fmt_exit(trade)
    assert "TP 전량 익절" in msg
    assert "📈" in msg
    assert "$+500.00" in msg


def test_fmt_exit_unknown_reason_fallback():
    """reason_map 에 없는 값 — reason 원문 그대로 표시."""
    trade = _make_trade(reason="liquidated", pnl_usd=-999.0, roi_pct=-99.0)
    msg = _fmt_exit(trade)
    assert "liquidated" in msg


def test_fmt_exit_broken_trade_returns_safe_fallback():
    """AttributeError (필드 없는 객체) — 안전한 fallback 메시지."""
    msg = _fmt_exit(object())
    assert "포지션 청산" in msg


# ── TelegramBot._is_authorized ────────────────────────────────────


def test_is_authorized_matching_chat_id():
    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_id = "123456"
    assert bot._is_authorized(123456) is True
    assert bot._is_authorized("123456") is True


def test_is_authorized_wrong_chat_id():
    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_id = "123456"
    assert bot._is_authorized("999999") is False


# ── TelegramBot.on_trade_alert ────────────────────────────────────


def test_on_trade_alert_entry_calls_send():
    """entry 이벤트 → send_alert_threadsafe 호출."""
    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_id = "1"
    bot._loop = None
    bot._app = None
    sent = []
    bot.send_alert_threadsafe = lambda text: sent.append(text)

    data = {"direction": "long", "symbol": "BTC/USDT", "entry_price": 80000.0, "leverage": 10}
    bot.on_trade_alert("entry", data)
    assert len(sent) == 1
    assert "신규 진입" in sent[0]


# ============================================================
# _kst_now_str
# ============================================================


def test_kst_now_str_format() -> None:
    """_kst_now_str → 'YYYY-MM-DD HH:MM KST' 포맷."""
    import re

    from aurora.interfaces.telegram import _kst_now_str
    result = _kst_now_str()
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} KST$", result), f"Unexpected: {result!r}"


def test_kst_now_str_returns_str() -> None:
    """_kst_now_str → str 타입."""
    from aurora.interfaces.telegram import _kst_now_str
    assert isinstance(_kst_now_str(), str)


# ============================================================
# get_bot singleton
# ============================================================


def test_get_bot_returns_telegram_bot_instance() -> None:
    """get_bot() → TelegramBot 인스턴스."""
    import aurora.interfaces.telegram as tg_module
    tg_module._bot = None  # reset
    bot = tg_module.get_bot()
    assert isinstance(bot, TelegramBot)
    tg_module._bot = None  # cleanup


def test_get_bot_singleton_same_object() -> None:
    """두 번 호출 → 같은 인스턴스 (싱글톤)."""
    import aurora.interfaces.telegram as tg_module
    tg_module._bot = None  # reset
    a = tg_module.get_bot()
    b = tg_module.get_bot()
    assert a is b
    tg_module._bot = None  # cleanup


# ============================================================
# launch_in_background — no-op when no token
# ============================================================


def test_launch_in_background_noop_when_no_token() -> None:
    """TELEGRAM_BOT_TOKEN 미설정 → 스레드 안 띄움."""
    import threading
    from unittest.mock import patch

    import aurora.interfaces.telegram as tg_module
    tg_module._bot = None

    bot = TelegramBot.__new__(TelegramBot)
    bot.token = ""  # 빈 토큰 = 비활성
    bot.chat_id = ""
    bot._app = None
    bot._loop = None

    before = threading.active_count()
    with patch.object(tg_module, "get_bot", return_value=bot):
        tg_module.launch_in_background()
    after = threading.active_count()
    assert after == before  # 스레드 추가 없음
    tg_module._bot = None


def test_on_trade_alert_exit_calls_send():
    """exit 이벤트 → send_alert_threadsafe 호출."""
    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_id = "1"
    bot._loop = None
    bot._app = None
    sent = []
    bot.send_alert_threadsafe = lambda text: sent.append(text)

    trade = _make_trade(reason="tp_full", pnl_usd=300.0, roi_pct=2.5)
    bot.on_trade_alert("exit", {"trade": trade})
    assert len(sent) == 1
    assert "포지션 청산" in sent[0]


def test_on_trade_alert_unknown_event_ignored():
    """알 수 없는 이벤트 — 전송 없음."""
    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_id = "1"
    bot._loop = None
    bot._app = None
    sent = []
    bot.send_alert_threadsafe = lambda text: sent.append(text)

    bot.on_trade_alert("unknown_event", {})
    assert sent == []


def test_on_trade_alert_exception_does_not_propagate():
    """내부 예외 — 호출자에게 전파되지 않음 (봇 안정성)."""
    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_id = "1"
    bot._loop = None
    bot._app = None
    bot.send_alert_threadsafe = MagicMock(side_effect=RuntimeError("net fail"))

    data = {"direction": "long", "symbol": "BTC/USDT", "entry_price": 80000.0, "leverage": 10}
    bot.on_trade_alert("entry", data)  # 예외 없이 통과해야 함


# ── send_alert_threadsafe — loop 없을 때 noop ─────────────────────


def test_send_alert_threadsafe_noop_when_no_loop():
    """_loop=None 이면 전송 시도 없음 — AttributeError 발생 X."""
    bot = TelegramBot.__new__(TelegramBot)
    bot._loop = None
    bot._app = None
    bot.chat_id = "1"
    bot.send_alert_threadsafe("test message")  # 아무 예외 없이 통과


# ── cmd_update ────────────────────────────────────────────────────


def _make_update(chat_id: int) -> SimpleNamespace:
    """cmd_update 용 Telegram Update stub — reply_text 는 async."""
    replied = []

    async def _reply(text: str) -> None:
        replied.append(text)

    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        message=SimpleNamespace(reply_text=_reply),
        _replied=replied,
    )
    return update


def test_cmd_update_no_pending_replies_latest():
    """/update — pending 없음 → '최신 버전' 메시지."""
    import asyncio

    from aurora.interfaces import release_check

    release_check.reset_state()

    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_id = "1"
    bot._loop = None
    bot._app = None

    update = _make_update(1)
    asyncio.run(bot.cmd_update(update, None))
    assert len(update._replied) == 1
    assert "최신 버전" in update._replied[0]


def test_cmd_update_with_pending_replies_update_info():
    """/update — pending 박힘 → 새 버전 tag + URL 포함 메시지."""
    import asyncio

    from aurora.interfaces import release_check

    release_check.reset_state()
    release_check._state["pending_release"] = {
        "tag": "v999.0.0",
        "name": "Big Release",
        "body": "",
        "html_url": "https://github.com/WL131231/Aurora/releases/tag/v999.0.0",
        "published_at": "",
    }

    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_id = "1"
    bot._loop = None
    bot._app = None

    update = _make_update(1)
    asyncio.run(bot.cmd_update(update, None))
    assert len(update._replied) == 1
    assert "v999.0.0" in update._replied[0]
    assert "업데이트 가능" in update._replied[0]


def test_cmd_update_unauthorized_no_reply():
    """/update — 미인가 chat_id → 응답 없음."""
    import asyncio

    from aurora.interfaces import release_check

    release_check.reset_state()

    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_id = "999999"
    bot._loop = None
    bot._app = None

    update = _make_update(1)
    asyncio.run(bot.cmd_update(update, None))
    assert update._replied == []


# ── _on_new_release ───────────────────────────────────────────────


def test_on_new_release_calls_send_alert():
    """새 버전 발견 콜백 → send_alert_threadsafe 호출 + tag 포함."""
    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_id = "1"
    bot._loop = None
    bot._app = None

    sent = []
    bot.send_alert_threadsafe = lambda text: sent.append(text)

    pending = {
        "tag": "v999.0.0",
        "html_url": "https://github.com/WL131231/Aurora/releases/tag/v999.0.0",
    }
    bot._on_new_release(pending)

    assert len(sent) == 1
    assert "v999.0.0" in sent[0]
    assert "새 버전 발견" in sent[0]


def test_on_new_release_includes_current_version():
    """콜백 메시지에 현재 버전(__version__) 포함."""
    from aurora import __version__

    bot = TelegramBot.__new__(TelegramBot)
    bot.chat_id = "1"
    bot._loop = None
    bot._app = None

    sent = []
    bot.send_alert_threadsafe = lambda text: sent.append(text)

    bot._on_new_release({"tag": "v999.0.0", "html_url": "https://x"})

    assert f"v{__version__}" in sent[0]
