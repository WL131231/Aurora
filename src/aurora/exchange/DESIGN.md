# exchange/ 어댑터 설계 — 데모 트레이딩 (Bybit Demo Trading) 우선

> **상태**: Draft — ChoYoon 협의 (PR-3 머지 후 어댑터 PR 위임 받음, 2026-05-03)
> **작성**: 지휘자 (Orchestrator) + 장수 supervision
> **범위**: `src/aurora/exchange/{base, ccxt_client, data, execution}.py` 본 구현

ChoYoon 의 backtest/DESIGN.md 패턴 차용 — §1 개요 → §11 Decisions 까지 분기 정리.

---

## §1. 목표 / 범위

**목표**: Bybit Demo Trading 환경에서 Aurora 봇이 실시간 매매 사이클 (데이터 → 신호 → 진입 → SL/TP 적용 → 청산) 을 자동 수행.

**Phase 분리**:
- **Phase 1 (본 PR)**: Bybit Demo Trading 우선 구현 + paper 모드 (실제 호출 X)
- **Phase 2 (별도 PR)**: 실거래 거래소 (장수 협상 결과) 어댑터 추가
- **Phase 3 (별도 PR)**: WebSocket 실시간 스트림 (현재는 REST 폴링)

**비범위**:
- 멀티 거래소 동시 (지금은 단일 거래소)
- 거래소 간 중계 (Aurora 가 단일 거래소에서만 매매)

---

## §2. 모듈 책임

| 파일 | 책임 |
|---|---|
| `base.py` | Protocol 정의 (`ExchangeClient`) + dataclass (`Order`, `Position`, `Balance`) |
| `ccxt_client.py` | ccxt 통합 어댑터 (Bybit 우선) — REST 호출 wrapper |
| `data.py` | `MultiTfCache` — 멀티 TF 캔들 캐싱 + warmup |
| `execution.py` | `Executor` — open/close/SL update (트레일링 5모드 적용) |

`bot_instance.py._run_loop` 는 본 PR 범위 외 (정용우 영역) — 어댑터가 noop 일 때도 안 깨지게 인터페이스 안정성만 보장.

---

## §3. ccxt 통합 — Bybit Demo Trading

### §3.1 인스턴스 생성 (검증 완료 2026-05-03)

```python
import ccxt
from aurora.config import settings

ex = ccxt.bybit({
    'apiKey': settings.bybit_api_key,
    'secret': settings.bybit_api_secret,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap',                  # USDT-margined linear perpetual
        'recvWindow': 60000,                    # Windows clock skew 허용 (60초)
        'adjustForTimeDifference': True,        # 서버 시각 자동 보정
    },
})
if settings.bybit_demo:
    ex.enableDemoTrading(True)                  # bybit.com Demo Trading 활성화
ex.load_time_difference()                       # 시각 차이 명시 동기 (6초 skew 검증됨)
```

**핵심 결정**:
- **`enableDemoTrading(True)`** — Bybit Demo URL `https://api-demo.{hostname}` 사용 (≠ testnet.bybit.com)
- **`recvWindow=60000`** — Windows 환경에서 시각 차이 6초 발생 검증됨. 5초 디폴트로는 InvalidNonce 빈발
- **`adjustForTimeDifference=True` + `load_time_difference()`** — ccxt 가 서버 시각으로 자동 timestamp 보정. 봇 기동 시 1회 호출 권장

### §3.2 거래 모드 (`run_mode` 매핑)

| `run_mode` | 동작 | API 호출 |
|---|---|---|
| `paper` | 신호만 발생, 가짜 Order 반환 | fetch_* OK, place_order/set_leverage 차단 (가짜 응답) |
| `demo` | Bybit Demo Trading 호출 | 모든 API 호출, 가상 자금 |
| `live` | 실거래 (Phase 3) | 모든 API 호출, 실제 자금 |

**`paper` 모드 분기 패턴** (place_order 예):
```python
async def place_order(self, ...) -> Order:
    if settings.run_mode == "paper":
        return self._fake_order(symbol, side, qty, price)  # 가짜 Order, 거래소 호출 X
    # demo / live 는 실제 ccxt 호출
    ...
```

---

## §4. base.py 확장

ChoYoon DESIGN.md §3.3.1 "어댑터 PR (옵션 a)" 명시 항목:

```python
class ExchangeClient(Protocol):
    # 기존
    name: str
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame: ...
    async def fetch_position(self, symbol: str) -> Position | None: ...
    async def place_order(self, symbol: str, side: ..., qty: float, ...) -> Order: ...
    async def set_leverage(self, symbol: str, leverage: int) -> None: ...
    async def cancel_all(self, symbol: str) -> None: ...

    # ✨ 신설 (옵션 a 어댑터 PR)
    async def get_positions(self) -> list[Position]: ...     # 모든 페어 포지션
    async def get_equity(self) -> Balance: ...               # 총 자본금 + 사용 가능
```

`Balance` dataclass 신설:
```python
@dataclass(slots=True)
class Balance:
    total_usd: float       # 전체 자본금
    free_usd: float        # 사용 가능
    used_usd: float        # 묶여있는 마진
```

### §4.1 검증 결과 적용 (2026-05-03)

`fetch_balance()` 응답 매핑:
```python
balance = await ex.fetch_balance()
usdt = balance.get('USDT', {})
return Balance(
    total_usd=float(usdt.get('total', 0)),
    free_usd=float(usdt.get('free', 0)),
    used_usd=float(usdt.get('used', 0)),
)
```

검증된 응답 (장수 Demo 키, 2026-05-03):
- USDT total: 8663.43 / free: 8663.43 / used: 0.0

---

## §5. data.py — MultiTfCache

### §5.1 책임

여러 TF (15m / 1H / 4H / 1D 등) 캔들을 메모리 캐시 + 새 봉마다 갱신. `strategy.evaluate` 가 매번 fetch 호출하면 rate limit 위험.

### §5.2 인터페이스

```python
class MultiTfCache:
    def __init__(self, client: ExchangeClient, symbol: str, timeframes: list[str]):
        self._client = client
        self._symbol = symbol
        self._tfs = timeframes
        self._cache: dict[str, pd.DataFrame] = {}

    async def warmup(self, lookback_per_tf: dict[str, int]) -> None:
        """봇 시작 시 각 TF 별 충분한 history 가져옴 (예: 4H × 500 봉)."""
        ...

    async def step(self, now_ts: int) -> dict[str, pd.DataFrame]:
        """현재 시점에서 각 TF 의 최신 DataFrame 반환. 새 봉 발생 시 fetch + append."""
        ...

    def get(self, tf: str) -> pd.DataFrame:
        """캐시된 TF DataFrame (warmup 후)."""
        ...
```

### §5.3 봉 새로고침 정책

| TF | 갱신 주기 |
|---|---|
| 15m | 매 15분 (분 == 0/15/30/45) |
| 1H | 매 시 (분 == 0) |
| 4H | 4시간마다 (시 == 0/4/8/12/16/20) |
| 1D | UTC 0시 |

호출자(`_run_loop`)가 매 1초 step() 하면 캐시는 새 봉 발생 시점에만 fetch.

---

## §6. execution.py — Executor

### §6.1 책임

`RiskPlan` (core/risk.py) 받아서 거래소에 진입 / SL · TP 모니터링 / 트레일링 SL 갱신 / 청산.

### §6.2 인터페이스

```python
class Executor:
    def __init__(self, client: ExchangeClient, config: TpSlConfig):
        ...

    async def open_position(self, plan: RiskPlan) -> Order:
        """진입 — leverage 설정 + 시장가 주문 + SL/TP 자동 trigger 설정."""
        ...

    async def update_trailing_sl(self, plan: RiskPlan, current_market: float) -> None:
        """트레일링 5모드 적용 — 새 SL 가격이 유리한 방향이면 거래소 SL 변경."""
        ...

    async def close_position(self, plan: RiskPlan, reason: str) -> Order:
        """청산 — 시장가 reduce_only 주문."""
        ...
```

### §6.3 SL/TP 적용 정책

- **진입 시**: `place_order` + Bybit 의 conditional SL/TP attach (한 번에 등록)
- **트레일링**: `update_trailing_sl` 가 `risk.update_trailing_sl()` 호출 → 새 SL 이 현재 SL 보다 유리하면 거래소 측 SL trigger 변경
- **하모닉 예외**: 패턴별 자체 SL (X 방향 연장) 적용. 레버리지 SL 캡 미적용 (project_indicator_spec.md 명시)

---

## §7. interfaces/bot_instance.py 와의 결합

본 PR 범위 외지만 인터페이스 안정성 약속:

```python
# bot_instance._run_loop 가 어댑터 호출하는 가상 코드 (정용우 영역)
async def _run_loop(self):
    client = CcxtClient(...)
    cache = MultiTfCache(client, "BTC/USDT:USDT", ["15m", "1H", "4H"])
    await cache.warmup(...)
    executor = Executor(client, tpsl_config)

    while self._running:
        df_by_tf = await cache.step(now_ts)
        signals = strategy.evaluate(df_by_tf, ...)
        decision = signal.compose_entry(signals)
        if decision.enter and not has_position:
            plan = build_risk_plan(...)
            await executor.open_position(plan)
        elif has_position:
            await executor.update_trailing_sl(plan, current_market)
            # 청산 신호 또는 SL/TP 도달 시 close
        await asyncio.sleep(1)
```

→ 어댑터가 안정적이면 정용우는 위 패턴으로 `_run_loop` 본 구현 가능.

---

## §8. 테스트 전략

### §8.1 단위 테스트 (mock)

- `tests/test_ccxt_client.py` — ccxt 인스턴스 mock + 각 메서드 호출 / 응답 변환 검증
- `tests/test_executor.py` — Mock client + open/close/trailing 시나리오
- `tests/test_multitf_cache.py` — 캐시 히트/미스 / warmup / step 새 봉 발생 분기

### §8.2 통합 검증 (Demo 환경, manual)

`scripts/verify_demo_connection.py` — 본 PR 직전 검증 스크립트 (커밋 안 함, throwaway):
- ccxt instance + Demo mode
- fetch_balance / fetch_ohlcv / fetch_positions 동작 확인
- 검증 결과 (2026-05-03): USDT 8663 / clock skew 6초 / Demo URL 정상

### §8.3 회귀 보호

- core / backtest 모듈 불변 (변경 X)
- 기존 269 tests 그대로 PASS 보장

---

## §9. 차용 코드 / 참고 자료

- **ccxt 4.5+ Bybit support** — `enableDemoTrading()` 메서드 (4.4 부터)
- **Bybit V5 API doc** — Linear perpetual (USDT-M) 스펙
- **ChoYoon backtest/DESIGN.md** — 옵션 a 통합 모드 (build_risk_plan 측 sizing) 정합 필요
- **ChoYoon cost.py** — `apply_costs(fee_pct=...)` Bybit override 시점

---

## §10. 일정 (예상)

| Stage | 작업 | 예상 |
|---|---|---|
| 2A | base.py 확장 (Balance dataclass + get_positions/get_equity) | 30분 |
| 2B | ccxt_client.py 본 구현 + 검증 | 4~6시간 |
| 2C | data.py MultiTfCache | 2~3시간 |
| 2D | execution.py Executor | 3~4시간 |
| 2E | tests (mock) + 통합 검증 | 2~3시간 |

총 12~16시간 예상. 1~2일 분량 (ChoYoon 의 1~2일 평가 일치).

---

## §11. Decisions

| # | 결정 | 핵심 |
|---|---|---|
| E-1 | Bybit Demo Trading vs Testnet | **Demo Trading** 선택 — 실 시장 데이터, $1M 가상 자금. config: `bybit_demo: bool = True` |
| E-2 | clock skew 처리 | `recvWindow=60000` + `adjustForTimeDifference=True` + `load_time_difference()` 봇 기동 1회 |
| E-3 | paper 모드 분기 | `place_order` / `set_leverage` 만 차단, fetch_* 는 실제 호출 (시세 검증 자유롭게) |
| E-4 | `Balance` dataclass 신설 | total/free/used USDT 만 우선. 다중 자산은 Phase 3 |
| E-5 | MultiTfCache 갱신 정책 | 봉 경계 시점에만 fetch (TF 별 분 단위 정렬) |
| E-6 | 트레일링 SL 거래소 측 적용 | Bybit conditional order 의 trailing 옵션 사용 vs 봇 측 polling 후 trigger 변경 — Phase 1 = 봇 측 polling (단순) |
| E-7 | 하모닉 패턴 SL/TP 캡 예외 | core/strategy 의 결정 그대로 — 어댑터는 plan.sl_price 받은 그대로 등록 |
| E-8 | TAKER_FEE_PCT (Bybit Demo) | ChoYoon `cost.py TAKER_FEE_PCT = 0.0004` (Binance 출발값) → Bybit perpetual taker = **0.06%** (확인 필요). `apply_costs(fee_pct=0.0006)` override |
| E-9 | 레버리지 모드 | Bybit 의 isolated/cross 중 **isolated** (같은 페어 다른 포지션 안전). config 노출 검토 |
| E-10 | symbol 표기 | ccxt 표준 `BTC/USDT:USDT` (linear perpetual) — 내부 계약 통일 |
