# Exchange 모듈 — ChoYoon 담당 영역 (backtest 모듈도 함께 담당)

## 책임 범위
거래소 API 추상화 + 시세 데이터 + 주문 실행. ccxt 통합 사용.

## 파일별 역할
- `base.py` — 추상 인터페이스 (`ExchangeClient` 프로토콜)
- `ccxt_client.py` — ccxt 기반 통합 어댑터 (Bybit / OKX / Binance)
- `data.py` — OHLCV 페치, 멀티 타임프레임 캐싱, WebSocket 구독
- `execution.py` — 주문 placement, 포지션 추적, 레버리지/마진 모드 설정

## 주요 스펙
- **거래소**: **보류** — 장수가 수수료율 높은 거래소와 별도 컨택 중
  - 정책: ccxt 추상화로 거래소 무관 구조. 어떤 거래소든 어댑터 한 줄 추가로 연결.
  - `base.py` ExchangeClient 인터페이스만 따르면 OK
  - 후보: Bybit / OKX / Binance / 컨택 중인 거래소
- **페어**: BTCUSDT, ETHUSDT (둘 다 — 1순위)
- **레버리지**: 10x ~ 50x 사용자 설정
- **타임프레임**: 1m, 3m, 5m, 15m, 1H, 2H, 4H, 1D, 1W
- **마진 모드**: Isolated 권장

## 외부 의존
- `ccxt`, `httpx`, `websockets`
- 출력: pandas DataFrame (OHLCV 표준 컬럼)
- 주문 결과: dataclass (Order, Position)

## 주의사항
- API 호출 실패 시 재시도 + 백오프
- 실계좌 모드(`run_mode=live`)에서는 주문 직전 한 번 더 검증
- 펀딩비/수수료/슬리피지 정보는 Position에 기록해서 backtest 모듈에서 참조 가능하게

## 테스트
- 모든 어댑터는 paper 모드(주문 안 보냄)에서 테스트 가능해야 함
- ccxt sandbox/testnet 활용
