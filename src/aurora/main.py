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

from aurora.config import settings


def main() -> None:
    """Aurora 진입점 — Pywebview GUI 기동.

    추후 Telegram 본 구현 시 GUI + Telegram 동시 기동 hub 가 됨. 현재는 GUI 단독.
    """
    # 함수 내부 import: ``webview.py`` 는 ``import uvicorn`` 등 의존성 무거움.
    # 모듈 import 자체에 비용 없게 하려고 main() 호출 시점에만 로드.
    from aurora.interfaces.webview import launch

    print(f"Aurora v0.1.0 — run_mode={settings.run_mode} — GUI 기동")
    launch()


if __name__ == "__main__":
    main()
