# UnTrack

고빈도 룰 기반 자동매매 봇 — 멀티 거래소 / 멀티 페어 / 고배율 / 배포 SaaS 모델.

## 핵심 정책
- **AI API 호출 금지** — 100% 룰 기반 의사결정
- 멀티 거래소 (보류 — 컨택 중): ccxt 통합으로 어떤 거래소든 연결 가능한 구조
- 페어: BTCUSDT, ETHUSDT (확장 예정)
- 레버리지: 사용자 설정 (10x ~ 50x)
- 인터페이스: `.exe` GUI (Pywebview + HTML/Tailwind/JS) + Telegram Bot

## 빠른 시작

### 1. 저장소 클론
```bash
git clone https://github.com/WL131231/UnTrack.git
cd UnTrack
```

### 2. 가상환경 + 의존성 설치
```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

pip install -r requirements-dev.txt
pip install -e .
```

### 3. 환경변수 설정
```bash
cp .env.example .env
# .env 열어서 본인 키 입력 (사용하는 거래소만)
```

### 4. 실행
```bash
# 패키지로 import 가능한지 확인
python -c "import untrack; print(untrack.__version__)"

# 테스트
pytest

# 메인 진입점 (아직 미구현 상태로 메시지만 출력)
python -m untrack.main

# GUI (Pywebview 윈도우)
python -m untrack.interfaces.webview
```

### 5. .exe 빌드 (배포용)
```bash
pip install pyinstaller
python scripts/build_exe.py
# → dist/UnTrack.exe
```

## 프로젝트 구조
```
UnTrack/
├── pyproject.toml             # 프로젝트 메타 + 의존성
├── requirements.txt           # 런타임 의존성
├── requirements-dev.txt       # 개발 의존성
├── CLAUDE.md                  # AI 보조 작업 시 자동 컨텍스트
├── .env.example               # 환경변수 템플릿
│
├── src/untrack/
│   ├── main.py                # 진입점
│   ├── config.py              # 전역 설정 (Pydantic)
│   ├── core/      [팀원 A]    # 전략 / 지표 / 신호 / 리스크
│   ├── exchange/  [팀원 B]    # ccxt / 데이터 / 실행
│   ├── backtest/  [팀원 C]    # 엔진 / 리플레이 / 통계
│   └── interfaces/[팀원 D]    # API / Telegram / Webview
│
├── ui/            [팀원 D]    # HTML / CSS / JS (Tailwind)
├── tests/                     # 모듈별 테스트
├── scripts/       [팀원 D]    # build_exe.py 등
└── docs/                      # 설계 문서
```

각 모듈 폴더에 `CLAUDE.md`가 있으니, AI에게 "이 폴더 보고 작업해"라고 시키면 컨텍스트를 자동으로 받음.

## 팀 분담
| 멤버 | GitHub | 역할 | 담당 영역 |
|---|---|---|---|
| **장수** | `WL131231` | Backend Core + Supervisor | `src/untrack/core/` |
| **ChoYoon** | `ChoYoon-Tier1` | Backend I/O + Analytics | `src/untrack/exchange/` + `src/untrack/backtest/` |
| **정용우** | `yongwoo2004` | Full-stack Frontend | `src/untrack/interfaces/`, `ui/`, `scripts/` |
| **WooJae** | `jwooo05` | Light Tasks (모바일·짧은 시간) | PR 리뷰, 이슈 triage, 문서·docstring 검수, 작은 CSS/텍스트 PR, 테스트 케이스 |

**공통** (수정 전 공지): `__init__.py`, `config.py`, `main.py`, `pyproject.toml`, `requirements*.txt`, `tests/`, `README.md`, `CLAUDE.md`, `.env.example`

## 협업 규칙 (필독)
1. **머지 방식**: Squash only (저장소 설정으로 강제 — Merge commit/Rebase 비활성)
2. **머지 후 feature 브랜치 자동 삭제**
3. **`main` 직접 커밋 금지** — 항상 feature 브랜치에서 작업
4. **작업 시작 전 `git pull` 먼저**
5. **`git push --force` 금지**
6. 자기 영역만 수정 (다른 영역 수정 시 사전 공지 + PR)
7. 커밋 prefix: `feat:`, `fix:`, `refactor:`, `chore:`, `docs:`, `test:`
8. 모든 코드 한국어 docstring/주석
9. 라이브러리 추가 시 `requirements.txt` 갱신 + 팀에 공지

## 전략 스펙 요약

### Fixed (필수)
- **EMA 200 / EMA 480** — 1H, 2H, 4H, 1D, 1W (멀티 TF 정렬)
- **RSI Divergence** — 1H

### Selectable (사용자 on/off)
- ① Bollinger Bands (1H)
- ② MA Cross — Golden / Dead (1H, 2H, 4H)
- ③ Harmonic — Bat / Butterfly / Gartley (15m or 1H)
- ④ Ichimoku Cloud — Span A/B (1H, 2H, 4H)

### TP/SL (Tako 차용)
- 3 모드: ATR Dynamic / Fixed % / Manual %
- 4단계 분할 익절 + allocation 사용자 조정
- 5가지 트레일링: Moving Target, Moving 2-Target, Breakeven, Percent Below Triggers, Percent Below Highest

### 진입 정책
- **단일 신호로도 진입 가능** (OR 조합)
- Selectable 지표는 사용자 on/off

## Phase 계획
- **Phase 1 (현재)**: 룰 기반 봇 + 백테스트 모듈 동시 개발
- **Phase 2**: Demo 거래 + 소액 실거래 검증
- **Phase 3**: 정식 배포 + 라이센스/구독 시스템

## 라이선스
**비공개 (Proprietary)** — 배포·구독 모델 대상. 외부 공개·재배포 금지.
