# Interfaces 모듈 — 팀원 D 담당 영역

## 책임 범위
사용자가 봇을 컨트롤하는 모든 채널.
core/exchange/backtest 모듈은 이 모듈을 통해 외부에 노출됨.

## 파일별 역할
- `api.py` — FastAPI 백엔드 (HTML/JS 프론트가 호출)
- `telegram.py` — Telegram 봇 (원격 명령 + 알림)
- `webview.py` — Pywebview 윈도우 진입점 (.exe로 패키징될 부분)

## 추가 디렉토리
- `ui/` — HTML/CSS/JS 프론트엔드 (Pywebview가 띄움)
- `scripts/build_exe.py` — PyInstaller 빌드 스크립트

## 주요 스펙
- **GUI**: 게임 옵션창 스타일 (스크린샷 참고)
  - 카테고리 탭: 거래소 / 페어 / 레버리지 / 지표 / TP·SL / 트레일링
  - 슬라이더, 토글, 드롭다운
  - Tailwind CSS로 스타일링
- **Telegram 명령** (예시):
  - `/start` `/stop` `/status` `/positions`
  - `/setlev 20`, `/setpair BTCUSDT`
  - `/togglebb on`, `/togglemacross off`
- **API 엔드포인트** (FastAPI):
  - `GET /status`, `GET /positions`, `GET /config`
  - `POST /config`, `POST /start`, `POST /stop`
  - WebSocket `/ws/live` (실시간 차트 푸시)

## 패키징
- PyInstaller로 단일 exe 빌드
- ui/ 폴더는 PyInstaller `--add-data`로 포함
- WebView2 런타임은 Windows 10/11에 기본 설치되어 있다고 가정

## 외부 의존
- `fastapi`, `uvicorn`, `pywebview`, `python-telegram-bot`
- 출력: HTTP 응답 / Telegram 메시지 / GUI 이벤트

## 테스트
- API: pytest + httpx로 엔드포인트 테스트
- Telegram: 테스트 봇 토큰으로 통합 테스트
