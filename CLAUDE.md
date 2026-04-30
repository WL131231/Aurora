# UnTrack — AI 보조 작업 컨텍스트

이 파일은 팀원이 AI(Claude/ChatGPT 등)에게 작업 지시할 때 자동으로 읽히는 프로젝트 컨텍스트.

## 프로젝트 개요
- **이름**: UnTrack
- **목적**: 고배율 / 고빈도 / 멀티 거래소 자동매매 봇 (배포·구독 모델 대상)
- **언어**: Python 3.11
- **인터페이스**: `.exe` GUI (Pywebview + HTML/Tailwind/JS) + Telegram Bot

## 핵심 원칙
1. **AI API 호출 금지** — Claude/OpenAI 등 LLM API를 봇 런타임에서 부르지 않음. 100% 룰 기반.
2. **한국어 주석** — 모든 docstring과 inline comment는 한국어로 작성.
3. **타이트한 SL** — 고배율 봇이라 청산 방지가 최우선. 손절은 무조건 실행.
4. **단일 신호 진입 OK** — 사용자 설정대로 지표 OR 조합 시 한 개 신호만으로 진입 가능.

## 전략 스펙
**Fixed (필수)**
- EMA 200 / EMA 480 — 1H, 2H, 4H, 1D, 1W (멀티 TF)
- RSI Divergence — 1H

**Selectable (사용자 on/off)**
- ① Bollinger Bands (1H)
- ② MA Cross — Golden/Dead (1H, 2H, 4H)
- ③ Harmonic — Bat / Butterfly / Gartley (15m or 1H)
- ④ Ichimoku Cloud — Span A/B (1H, 2H, 4H)

**TP/SL (Tako 차용)**
- 3 모드: ATR Dynamic / Fixed % / Manual %
- 4단계 분할 익절 + allocation 사용자 조정
- 5가지 트레일링: Moving Target, Moving 2-Target, Breakeven, Percent Below Triggers, Percent Below Highest
- 트리거: Target / Percentage

## 폴더 구조 (담당)
- `src/untrack/core/` — 팀원 A (전략/지표/신호/리스크)
- `src/untrack/exchange/` — 팀원 B (ccxt/데이터/실행)
- `src/untrack/backtest/` — 팀원 C (백테스트/리플레이/통계)
- `src/untrack/interfaces/` — 팀원 D (API/Telegram/Webview)
- `ui/` — 팀원 D (HTML/CSS/JS)
- 공통: `__init__.py`, `config.py`, `main.py`, `pyproject.toml`, `tests/`

## 거래소 / 페어
- 거래소: Bybit / OKX / Binance (ccxt로 통합 — 사용자 선택)
- 페어: BTCUSDT, ETHUSDT (Phase 2에 알트 추가 가능)
- 레버리지: 사용자 설정, 10x ~ 50x

## 협업 규칙
- `main` 직접 커밋 금지 → feature 브랜치 + PR
- 작업 시작 전 `git pull`
- 다른 사람 영역 수정은 사전 공지 + PR
- 커밋 prefix: `feat:`, `fix:`, `refactor:`, `chore:`, `docs:`, `test:`

## Phase 계획
- **Phase 1 (현재)**: 단일 사용자 가정, 룰 기반 봇 + 백테스트 모듈 동시 개발
- **Phase 2**: Demo 거래 + 소액 실거래 검증
- **Phase 3**: 정식 배포 + 라이센스/구독 시스템

## 유의사항
- 레버리지별 SL 캡: 10x → 최소 7%, 50x → 최소 3% (반비례, 청산선 거리 기반)
- 펀딩비/수수료/슬리피지는 백테스트와 실거래 모두 반영 필요
- 동시 포지션 수 제한 (기본 1개, 사용자 설정 가능)
- 4 명 팀 모두 AI 위주 작업 → 명확한 모듈 경계 + 풍부한 docstring 유지
