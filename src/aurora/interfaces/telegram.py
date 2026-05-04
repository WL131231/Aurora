"""Telegram 봇 — 원격 명령 + 진입/청산/일일 브리핑 알림.

python-telegram-bot v21+ Application 패턴 사용.
백그라운드 daemon thread 에서 asyncio loop 실행.
bot_instance 진입/청산 콜백 수신 → send_alert_threadsafe 로 메시지 전송.

실행 흐름:
    main.py 에서 ``launch_in_background()`` 호출
    → daemon thread 생성
    → ``asyncio.run(bot.start())``
    → Application polling 시작 + 일일 브리핑 JobQueue 등록

토큰 미설정 (settings.telegram_bot_token == "") 시 noop.

담당: 정용우
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from aurora.config import settings

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")
_SEP = "━━━━━━━━━━━━━━━━━━"


def _kst_now_str() -> str:
    """현재 KST 시각 문자열."""
    return datetime.now(_KST).strftime("%Y-%m-%d %H:%M KST")


# ============================================================
# 알림 메시지 포맷 헬퍼
# ============================================================

def _fmt_entry(data: dict) -> str:
    """진입 알림 메시지 포맷.

    Args:
        data: bot_instance._fire_trade_alert("entry", ...) payload.
    """
    plan = data.get("plan")
    direction = (data.get("direction") or "?").upper()
    symbol = data.get("symbol") or "?"
    price: float = data.get("entry_price") or 0.0
    leverage: int = data.get("leverage") or 0

    icon = "🟢" if direction == "LONG" else "🔴"
    lines = [
        _SEP,
        f"{icon} 신규 진입",
        _SEP,
        f"페어: {symbol}",
        f"방향: {direction}",
        f"진입가: ${price:,.2f}",
        f"레버리지: {leverage}x",
        _SEP,
    ]
    if plan is not None:
        try:
            sl = plan.sl_price
            sl_pct = (abs(price - sl) / price * 100) if price > 0 else 0
            sl_sign = "+" if direction == "SHORT" else "-"
            lines.append(f"SL: ${sl:,.2f} ({sl_sign}{sl_pct:.2f}%)")
            for i, tp in enumerate(plan.tp_prices or []):
                tp_pct = (abs(tp - price) / price * 100) if price > 0 else 0
                tp_sign = "-" if direction == "SHORT" else "+"
                lines.append(f"TP{i + 1}: ${tp:,.2f} ({tp_sign}{tp_pct:.2f}%)")
        except AttributeError:
            pass
    lines += [_SEP, _kst_now_str()]
    return "\n".join(lines)


def _fmt_exit(trade) -> str:
    """청산 알림 메시지 포맷.

    Args:
        trade: ClosedTrade 인스턴스.
    """
    try:
        reason_map = {
            "sl": "SL (손절)",
            "tp_full": "TP 전량 익절",
            "tp_partial": "TP 부분 익절",
            "reverse": "REVERSE 신호 청산",
            "manual": "수동 청산",
            "exit_signal": "신호 청산",
        }
        reason = reason_map.get(trade.reason or "", trade.reason or "청산")
        direction = (trade.direction or "?").upper()
        icon = "📉" if (trade.reason or "").startswith("sl") else "📈"
        pnl: float = trade.pnl_usd
        pnl_str = f"${pnl:+,.2f}"
        roi_str = f"{trade.roi_pct:+.2f}%"

        return "\n".join([
            _SEP,
            f"{icon} 포지션 청산",
            _SEP,
            f"페어: {trade.symbol}",
            f"방향: {direction}",
            f"청산 이유: {reason}",
            _SEP,
            f"실현 PnL: {pnl_str}  ({roi_str})",
            _SEP,
            _kst_now_str(),
        ])
    except Exception:
        return f"{_SEP}\n포지션 청산\n{_SEP}\n{_kst_now_str()}"


# ============================================================
# TelegramBot
# ============================================================

class TelegramBot:
    """Telegram 봇 — Application + 명령어 핸들러 + 알림 전송.

    start() 를 daemon thread 에서 asyncio.run() 으로 호출.
    send_alert_threadsafe() 는 어느 스레드에서도 안전 (asyncio.run_coroutine_threadsafe).
    """

    def __init__(self) -> None:
        self.token: str = settings.telegram_bot_token
        self.chat_id: str = settings.telegram_chat_id
        self._app = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ─── 권한 ─────────────────────────────────────────────

    def _is_authorized(self, chat_id: int | str) -> bool:
        """등록된 chat_id 와 일치 여부 (단일 사용자, Phase 1)."""
        return str(chat_id) == str(self.chat_id)

    # ─── 알림 전송 ────────────────────────────────────────

    async def _send(self, text: str) -> None:
        """단일 메시지 전송 (내부 코루틴)."""
        try:
            await self._app.bot.send_message(chat_id=self.chat_id, text=text)
        except Exception as e:
            logger.warning("Telegram 알림 전송 실패: %s", e)

    def send_alert_threadsafe(self, text: str) -> None:
        """다른 스레드에서 안전하게 알림 전송 (fire-and-forget).

        bot_instance._fire_trade_alert 콜백에서 호출.
        """
        if self._loop and self._app and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._send(text), self._loop)

    def on_trade_alert(self, event: str, data: dict) -> None:
        """bot_instance 진입/청산 콜백 수신 → 포맷 후 전송.

        Args:
            event: "entry" | "exit".
            data: 이벤트 payload (bot_instance._fire_trade_alert 참조).
        """
        try:
            if event == "entry":
                text = _fmt_entry(data)
            elif event == "exit":
                text = _fmt_exit(data.get("trade"))
            else:
                return
            self.send_alert_threadsafe(text)
        except Exception as e:
            logger.warning("Telegram on_trade_alert 처리 실패: %s", e)

    # ─── 핸들러 등록 ──────────────────────────────────────

    def _register_handlers(self) -> None:
        from telegram.ext import CommandHandler

        pairs = [
            ("start", self.cmd_start),
            ("stop", self.cmd_stop),
            ("status", self.cmd_status),
            ("setlev", self.cmd_setlev),
            ("togglebb", self.cmd_togglebb),
            ("togglemacross", self.cmd_togglemacross),
            ("toggleharmonic", self.cmd_toggleharmonic),
            ("toggleichimoku", self.cmd_toggleichimoku),
            ("positions", self.cmd_positions),
            ("equity", self.cmd_equity),
        ]
        for cmd, fn in pairs:
            self._app.add_handler(CommandHandler(cmd, fn))

    # ─── 라이프사이클 ─────────────────────────────────────

    async def start(self) -> None:
        """봇 초기화 + polling 시작 (블로킹 — daemon thread 에서 실행)."""
        from telegram.ext import Application

        self._app = Application.builder().token(self.token).build()
        self._register_handlers()
        self._loop = asyncio.get_event_loop()

        # 일일 브리핑 09:00 KST
        self._app.job_queue.run_daily(
            self._daily_briefing,
            time=dt_time(9, 0, tzinfo=_KST),
        )

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        logger.info("Telegram 봇 polling 시작 (chat_id=%s)", self.chat_id)

        # daemon thread 가 종료되지 않도록 대기 (webview 종료 시 자동 해제)
        await asyncio.Event().wait()

    async def shutdown(self) -> None:
        """안전한 종료."""
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    # ─── 일일 브리핑 ──────────────────────────────────────

    async def _daily_briefing(self, _context) -> None:  # type: ignore[no-untyped-def]
        """매일 09:00 KST 자동 브리핑."""
        from aurora.interfaces import bot_instance

        inst = bot_instance.get_instance()
        running = inst.running
        mode = settings.run_mode

        try:
            eq = await inst.client.get_equity() if inst.client else None
            equity_str = f"${eq.total_usd:,.2f}" if eq else "—"
            unrealized_str = f"${eq.used_usd:+,.2f}" if eq else "—"
        except Exception:
            equity_str = "—"
            unrealized_str = "—"

        try:
            positions = await inst.client.get_positions() if inst.client else []
        except Exception:
            positions = []

        today = datetime.now(_KST).date()
        today_trades = [
            t for t in inst.closed_trades
            if getattr(t, "closed_at_ts", None)
            and datetime.fromtimestamp(t.closed_at_ts / 1000, tz=_KST).date() == today
        ]
        today_pnl = sum(getattr(t, "pnl_usd", 0.0) or 0.0 for t in today_trades)

        text = "\n".join([
            _SEP,
            "📊 Aurora 일일 브리핑",
            _SEP,
            _kst_now_str(),
            f"모드: {mode} | 봇: {'▶ 실행 중' if running else '■ 중지'}",
            _SEP,
            f"잔고: {equity_str}",
            f"미실현 PnL: {unrealized_str}",
            f"오픈 포지션: {len(positions)}개",
            _SEP,
            f"오늘 거래: {len(today_trades)}건",
            f"오늘 실현 PnL: ${today_pnl:+,.2f}",
            _SEP,
        ])
        await self._send(text)

    # ─── 상태 명령어 ──────────────────────────────────────

    async def cmd_start(self, update, _context) -> None:  # type: ignore[no-untyped-def]
        """/start — 봇 시작."""
        if not self._is_authorized(update.effective_chat.id):
            return
        from aurora.interfaces import bot_instance

        inst = bot_instance.get_instance()
        if inst.running:
            await update.message.reply_text("이미 실행 중.")
            return
        await inst.start()
        await update.message.reply_text("봇 시작됨. ▶")

    async def cmd_stop(self, update, _context) -> None:  # type: ignore[no-untyped-def]
        """/stop — 봇 중지."""
        if not self._is_authorized(update.effective_chat.id):
            return
        from aurora.interfaces import bot_instance

        inst = bot_instance.get_instance()
        if not inst.running:
            await update.message.reply_text("이미 중지됨.")
            return
        await inst.stop()
        await update.message.reply_text("봇 중지됨. ■")

    async def cmd_status(self, update, _context) -> None:  # type: ignore[no-untyped-def]
        """/status — 봇 상태 + 잔고 + 포지션 수."""
        if not self._is_authorized(update.effective_chat.id):
            return
        from aurora.interfaces import bot_instance

        inst = bot_instance.get_instance()
        try:
            eq = await inst.client.get_equity() if inst.client else None
            equity_str = f"${eq.total_usd:,.2f}" if eq else "—"
        except Exception:
            equity_str = "—"
        try:
            pos = await inst.client.get_positions() if inst.client else []
        except Exception:
            pos = []

        text = "\n".join([
            _SEP,
            "📊 봇 상태",
            _SEP,
            f"상태: {'▶ 실행 중' if inst.running else '■ 중지'}",
            f"모드: {settings.run_mode}",
            f"잔고: {equity_str}",
            f"오픈 포지션: {len(pos)}개",
            _SEP,
            _kst_now_str(),
        ])
        await update.message.reply_text(text)

    # ─── 설정 명령어 ──────────────────────────────────────

    async def cmd_setlev(self, update, context) -> None:  # type: ignore[no-untyped-def]
        """/setlev <배율> — 레버리지 변경 (10~50)."""
        if not self._is_authorized(update.effective_chat.id):
            return
        try:
            lev = int(context.args[0])
        except (IndexError, ValueError):
            await update.message.reply_text("사용법: /setlev <10~50>")
            return
        if not (10 <= lev <= 50):
            await update.message.reply_text("레버리지 범위: 10~50")
            return
        from aurora.interfaces import bot_instance, config_store

        cfg = config_store.load() or {}
        cfg["leverage"] = lev
        config_store.save(cfg)
        bot_instance.get_instance().apply_live_config(cfg)
        await update.message.reply_text(f"레버리지 → {lev}x 적용됨.")

    async def _toggle_indicator(
        self,
        update,  # type: ignore[no-untyped-def]
        _context,
        key: str,
        label: str,
    ) -> None:
        """지표 on/off 토글 공통 처리."""
        if not self._is_authorized(update.effective_chat.id):
            return
        from aurora.interfaces import bot_instance, config_store

        cfg = config_store.load() or {}
        cfg[key] = not cfg.get(key, False)
        config_store.save(cfg)
        bot_instance.get_instance().apply_live_config(cfg)
        state = "ON ✓" if cfg[key] else "OFF ✕"
        await update.message.reply_text(f"{label} → {state}")

    async def cmd_togglebb(self, update, context) -> None:  # type: ignore[no-untyped-def]
        """/togglebb — Bollinger Bands on/off."""
        await self._toggle_indicator(update, context, "use_bollinger", "Bollinger Bands")

    async def cmd_togglemacross(self, update, context) -> None:  # type: ignore[no-untyped-def]
        """/togglemacross — MA Cross on/off."""
        await self._toggle_indicator(update, context, "use_ma_cross", "MA Cross")

    async def cmd_toggleharmonic(self, update, context) -> None:  # type: ignore[no-untyped-def]
        """/toggleharmonic — Harmonic 패턴 on/off."""
        await self._toggle_indicator(update, context, "use_harmonic", "Harmonic")

    async def cmd_toggleichimoku(self, update, context) -> None:  # type: ignore[no-untyped-def]
        """/toggleichimoku — Ichimoku Cloud on/off."""
        await self._toggle_indicator(update, context, "use_ichimoku", "Ichimoku")

    # ─── 정보 명령어 ──────────────────────────────────────

    async def cmd_positions(self, update, _context) -> None:  # type: ignore[no-untyped-def]
        """/positions — 현재 오픈 포지션 목록."""
        if not self._is_authorized(update.effective_chat.id):
            return
        from aurora.interfaces import bot_instance

        inst = bot_instance.get_instance()
        if not inst.client:
            await update.message.reply_text("거래소 연결 없음 (configure 미완료).")
            return
        try:
            positions = await inst.client.get_positions()
        except Exception as e:
            await update.message.reply_text(f"포지션 조회 실패: {e}")
            return
        if not positions:
            await update.message.reply_text("오픈 포지션 없음.")
            return

        lines = [_SEP, "📋 오픈 포지션", _SEP]
        for p in positions:
            icon = "🟢" if p.side == "long" else "🔴"
            pnl = getattr(p, "unrealized_pnl", None)
            pnl_str = f"${pnl:+,.2f}" if pnl is not None else "—"
            lines.append(
                f"{icon} {p.symbol} {p.side.upper()}\n"
                f"   진입가: ${p.entry_price:,.2f} | qty: {p.qty}\n"
                f"   미실현 PnL: {pnl_str}"
            )
        lines += [_SEP, _kst_now_str()]
        await update.message.reply_text("\n".join(lines))

    async def cmd_equity(self, update, _context) -> None:  # type: ignore[no-untyped-def]
        """/equity — 거래소 잔고 + 미실현 손익 요약."""
        if not self._is_authorized(update.effective_chat.id):
            return
        from aurora.interfaces import bot_instance

        inst = bot_instance.get_instance()
        if not inst.client:
            await update.message.reply_text("거래소 연결 없음 (configure 미완료).")
            return
        try:
            eq = await inst.client.get_equity()
        except Exception as e:
            await update.message.reply_text(f"잔고 조회 실패: {e}")
            return

        text = "\n".join([
            _SEP,
            "💰 잔고",
            _SEP,
            f"총 잔고: ${eq.total_usd:,.2f}",
            f"가용 잔고: ${eq.free_usd:,.2f}",
            f"미실현 손익: ${eq.used_usd:+,.2f}",
            _SEP,
            _kst_now_str(),
        ])
        await update.message.reply_text(text)


# ============================================================
# 모듈 싱글톤 + main.py 훅
# ============================================================

_bot: TelegramBot | None = None


def get_bot() -> TelegramBot:
    """TelegramBot 싱글톤 접근자."""
    global _bot
    if _bot is None:
        _bot = TelegramBot()
    return _bot


def launch_in_background() -> None:
    """Telegram 봇을 백그라운드 daemon thread 에서 시작.

    main.py 에서 webview launch() 전 호출.
    TELEGRAM_BOT_TOKEN 미설정 시 noop (토큰 없으면 Telegram 기능 비활성).
    """
    bot = get_bot()
    if not bot.token:
        logger.info("TELEGRAM_BOT_TOKEN 미설정 — Telegram 봇 비활성")
        return

    def _run() -> None:
        asyncio.run(bot.start())

    thread = threading.Thread(target=_run, daemon=True, name="telegram-bot")
    thread.start()
    logger.info("Telegram 봇 백그라운드 스레드 시작 (chat_id=%s)", bot.chat_id)
