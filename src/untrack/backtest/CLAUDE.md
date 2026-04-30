# Backtest 모듈 — 팀원 C 담당 영역

## 책임 범위
과거 데이터로 전략 룰 검증 + 파라미터 튜닝.
core 모듈의 신호 함수를 그대로 호출해서 시뮬레이션.

## 파일별 역할
- `engine.py` — Walk-forward 백테스트 엔진 (기존 `adaptive_backtest.py` 기반)
- `replay.py` — 1분봉 → 다중 TF 집계 + 실시간 시뮬 (기존 `replay_engine.py` 차용)
- `stats.py` — 승률, 손익비, MDD, Sharpe 등 통계 산출

## 핵심 기능
- **Walk-forward**: 5일 윈도우 롤링, 과적합 방지
- **수수료/슬리피지/펀딩비 반영** ← 고배율 봇이라 필수
- **레버리지 10~50x 시뮬**: 청산 가격 정확히 계산
- **멀티 페어**: BTCUSDT, ETHUSDT 동시 지원
- **결과 저장**: JSON 또는 SQLite (logs/backtest/)

## 입력
- OHLCV 1분봉 Parquet (data/{symbol}_1m.parquet)
- 전략 설정 (`StrategyConfig`, `TpSlConfig`)
- 시뮬 설정 (자본, 레버, 수수료율)

## 출력
- per-trade 로그 (entry/exit/pnl/reason)
- 요약 통계 (승률, MDD, Sharpe 등)
- 파라미터 권장치 (auto-tuning)

## 차용 출처
- `C:\Users\지영민\Desktop\trading_bot\adaptive_backtest.py` (Walk-forward 골격)
- `C:\Users\지영민\Desktop\trading_bot\core\replay_engine.py` (OHLCV 집계 + 수수료 모델)

## 절대 금지
- AI API 호출 (Claude 등) — 기존 코드에 포함되어 있으나 모두 제거하고 가져올 것
- 실거래소 호출 — 백테스트는 로컬 데이터만

## 테스트
- 합성 OHLCV로 결정론적 결과 검증
- 알려진 전략(예: 단순 EMA 크로스)으로 sanity check
