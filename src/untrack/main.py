"""UnTrack 진입점.

실행 흐름:
    1. 설정 로드 (config.settings)
    2. 거래소 클라이언트 초기화 (exchange)
    3. 백엔드 API 서버 + 텔레그램 봇 + Pywebview 윈도우 동시 기동 (interfaces)

런타임 모드:
    - paper: 신호만 발생, 실제 주문 없음
    - demo: 거래소 테스트넷에서 주문
    - live: 실거래
"""

from __future__ import annotations

from untrack.config import settings


def main() -> None:
    """진입점 — 추후 D 멤버가 interfaces 모듈과 연결."""
    # TODO(D): interfaces.webview.launch() 호출 + API 서버 + Telegram 동시 기동
    print(f"UnTrack v0.1.0 — run_mode={settings.run_mode}")
    print("(아직 구현되지 않음)")


if __name__ == "__main__":
    main()
