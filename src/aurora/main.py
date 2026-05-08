"""Aurora 진입점 — Pywebview GUI (+ 추후 Telegram) 통합 hub.

실행 흐름:
    1. ``webview.launch()`` — FastAPI(daemon thread) + Pywebview 윈도우 기동.
       내부에서 ``log_buffer.install()`` 자체 호출 (root logger BufferHandler).
    2. (추후) ``telegram.start()`` — Telegram 봇 동시 기동.
       현재는 stub 이라 보류, telegram.py 본 구현 시 여기서 같이 띄움.

런타임 모드 (``config.settings.run_mode``):
    - paper: 신호만 발생, 실제 주문 없음
    - demo:  거래소 테스트넷 주문 (Bybit 확정, 2026-05)
    - live:  실거래 (거래소 미정 — 장수 협상 중)

실행:
    # dev 환경 (venv 활성 후)
    python -m aurora.main

    # .exe 빌드 (PyInstaller — scripts/build_exe.py)
    # 현재 build_exe entry 는 webview.py 직접 → main.py 와 등가.
    # telegram 본 구현 후 entry 를 main.py 로 변경 권장 (통합 hub 일원화).

담당: 정용우 (D 통합)
"""

from __future__ import annotations

import argparse

from aurora.config import settings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aurora 자동매매 봇")
    parser.add_argument("--headless", action="store_true", help="pywebview 없이 uvicorn 만 실행 (Termux / Linux)")
    parser.add_argument("--host", default=None, help="API 바인딩 호스트 (headless 전용, 기본 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="API 포트 (headless 전용, 기본 8765)")
    return parser.parse_args()


def main() -> None:
    """Aurora 진입점 — 자동 업데이트 적용 + Pywebview GUI 기동 + BotInstance configure.

    실행 순서:
        1. ``apply_pending_update()`` — 직전 실행에서 다운된 새 버전 있으면 swap +
           재시작 (이 함수 호출 안 돌아옴). 없으면 통과.
        2. ``start_background_check()`` — 백그라운드 thread 로 GitHub Releases 체크
           + 새 버전 있으면 다운로드 (다음 시작 시 적용).
        3. BotInstance configure + GUI 기동.

    BotInstance configure 시점:
        진입점에서 ``configure_from_settings()`` 호출 → settings (.env) +
        config_store (GUI 저장값) 결합 → 사용자가 GUI ▶ 시작 누르면 즉시 매매.
        configure 실패 (ccxt 인스턴스 생성 등) 시 GUI 는 정상 띄우되 ▶ 시작 시
        에러 노출 (사용자가 .env 점검 가능).
    """
    # 함수 내부 import: ``webview.py`` 는 ``import uvicorn`` 등 의존성 무거움.
    # 모듈 import 자체에 비용 없게 하려고 main() 호출 시점에만 로드.
    args = _parse_args()

    from aurora import __version__, updater
    from aurora.interfaces import bot_instance, log_buffer
    from aurora.interfaces.webview import (
        _setup_body_file_logging,
        launch,
        launch_headless,
    )

    # v0.1.99: file log 박기 가장 먼저 — apply_pending_update / configure 단계 측
    # 예외도 디스크 박힘. webview.launch() 안 (이전 위치) 박는 패턴 측 main 초반
    # crash 시 로그 자체 X 본질.
    log_buffer.install()
    log_file = _setup_body_file_logging()
    if log_file is not None:
        import logging
        logging.getLogger(__name__).info(
            "Aurora body main() 진입 — file log: %s", log_file,
        )

    # 1. 자동 업데이트 적용 (직전 실행에서 다운된 .new 가 있으면 swap + 재시작)
    updater.apply_pending_update()

    # 2. 백그라운드 update check (새 버전 있으면 다운로드, 다음 시작 시 적용)
    updater.start_background_check()

    mode_label = "headless" if args.headless else "GUI"
    print(f"Aurora v{__version__} — run_mode={settings.run_mode} — {mode_label} 기동")

    # BotInstance 자동 configure — settings + config_store 결합
    # Why: GUI ▶ 시작 누를 때마다 configure 안 해도 진입점에서 한 번 처리.
    # 실패해도 GUI 는 띄움 (사용자가 .env 점검 + 재시작 가능).
    try:
        bot_instance.get_instance().configure_from_settings()
        print("BotInstance: configured (Bybit Demo 또는 .env 기반)")
    except Exception as e:  # noqa: BLE001 — 모든 예외 catch 의도 (GUI 기동 우선)
        print(f"⚠ BotInstance configure 실패 (GUI 만 기동): {e}")

    # Telegram 봇 백그라운드 시작 + 진입/청산 알림 콜백 등록
    # TELEGRAM_BOT_TOKEN 미설정 시 launch_in_background 내부에서 noop.
    from aurora.interfaces import bot_instance as _bi_mod
    from aurora.interfaces.telegram import get_bot, launch_in_background

    _bi_mod.register_trade_alert_callback(get_bot().on_trade_alert)
    launch_in_background()

    if args.headless:
        launch_headless(host=args.host, port=args.port)
    else:
        launch()


if __name__ == "__main__":
    main()
