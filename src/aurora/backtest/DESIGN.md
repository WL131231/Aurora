# PR-3 BacktestEngine — Design Doc

장수 review용 (PR-2 Stage 1A 패턴 따라). 첫 commit 전 인터페이스 spec을 정리해 사전 합의 받은 후 본 작업 진입.

**작성 시점**: 2026-05-02 (PR-2 머지 직후, archive/borrowed-code 차용 코드 1차 검토 완료 시점)
**상태**: 🟡 In Progress — 골격만 박힘, 사전 작업 진행하며 보강

---

## 1. 목표

PR-2의 fetch_ohlcv 산출물(parquet)을 입력으로 받아 PR-1의 MultiTfAggregator + core 모듈(strategy / signal / risk)을 조합해 룰 기반 백테스트를 실행하는 엔진. 차용 코드(adaptive_backtest.py, replay_engine.py)의 비-AI 부분 발췌 + Aurora 자료구조에 맞게 재작성.

---

## 2. 입력·출력 인터페이스

### 데이터 흐름 (장수 명시 2026-05-02)

```
data/{SYMBOL}_{TIMEFRAME}.parquet (PR-2 산출물)
        │
        ▼ pd.read_parquet
  pd.DataFrame (timestamp=int64 ms, OHLCV=float64)
        │
        ▼ 1m 단위 순차 공급 (BacktestEngine.run 내부)
  MultiTfAggregator.step()  ← PR-1
        │
        ▼ 닫힌 TF 봉 (5m/1h/4h/1d/1w)
  df_by_tf = {tf: aggregator.get_df(tf)}
        │
        ▼
  core.strategy.evaluate(df_by_tf, config)  ← 장수 영역 (PR-19+)
        │
        ▼ list[EntrySignal]
  core.signal.compose_entry(signals, threshold)  ← 장수 영역
        │
        ▼ CompositeDecision (enter / direction / strength)
  core.risk.build_risk_plan(decision, ...)  ← 장수 영역 (PR-19)
        │
        ▼ RiskPlan (size, sl, tp, trailing 등)
  ┌──────────────────────────────┐
  │ BacktestEngine 시뮬 (이번 PR) │
  │ - 진입·청산 로직             │
  │ - 수수료/슬리피지 적용 (※)   │
  │ - SL/TP 도달 / 반대 신호 청산│
  │ - trades 기록                │
  └──────────────────────────────┘
        │
        ▼
  BacktestStats.compute()  ← 우리 stats.py 채움
        │
        ▼ TradeRecord[], BacktestStats (승률·MDD·Sharpe·equity curve)
```

(※) 수수료/슬리피지 모델은 replay_engine 차용 (§3.2)

### 모듈 재사용 / 차용 발췌 매핑

| 흐름 단계 | 출처 | 새 작업 vs 재사용 vs 차용 |
|---|---|---|
| parquet → DataFrame | PR-2 (`scripts/fetch_ohlcv.py`의 산출물) | 재사용 (그냥 읽기) |
| 1m 순차 공급 | 새 작업 — `BacktestEngine.run()` 루프 | 새 |
| TF 집계 | PR-1 `MultiTfAggregator` | 재사용 |
| timeframe 변환 (필요 시) | **새 모듈 `aurora.backtest.tf`** (#C-3) | 새 |
| 신호 평가 | `core.strategy.evaluate` (PR-19+) | 재사용 (장수 영역) |
| 신호 합성 | `core.signal.compose_entry` (장수 영역) | 재사용 |
| 리스크 계산 | `core.risk` (PR-19) | 재사용 (옵션 a) / 보강 (옵션 b) |
| 진입·청산 시뮬 루프 | adaptive_backtest 골격 | **차용 발췌** |
| 수수료/슬리피지 | replay_engine 모델 | **차용 발췌** |
| 통계 집계·equity curve | adaptive_backtest 통계 + replay_engine R-multiple | **차용 발췌** + 새 |
| trades 기록 | PR-1 `TradeRecord` dataclass (빈 정의 채움) | 재사용 + 채움 |
| 통계 dataclass | PR-1 `BacktestStats` dataclass (빈 정의 채움) | 재사용 + 채움 |

→ **새 작업 비중**: 시뮬 루프 (BacktestEngine.run) + tf 모듈 + 통계 채움.
→ **차용 발췌 비중**: 시뮬 루프 골격 + 수수료/슬리피지 + R-multiple 통계 (옵션 a 기준).
→ **재사용 비중**: 데이터 로드 + TF 집계 + 신호·리스크 (장수 영역, 변경 X).

---

## 3. 핵심 결정 사항 (현재까지)

### 3.1 TF 집계 — `MultiTfAggregator` 단독 사용

장수 README 명시: "replay_engine과 MultiTfAggregator 인터페이스 차이 가능성 — 통합 spec 결정 필요"

**결정**: PR-1의 `MultiTfAggregator`만 사용, replay_engine TF 집계 부분 차용 X.

**이유**:
- PR-1의 MultiTfAggregator는 13개 회귀 테스트로 보호됨 (Bybit since 함정까지 잡힌 검증된 구현)
- replay_engine은 TF 집계 + 매매 로직이 monolithic하게 묶인 구조 → 차용 시 분리 필요해 복잡도 ↑
- Aurora의 모듈 분리 원칙(SRP)과도 정합

### 3.2 수수료/슬리피지 모델 — replay_engine 수치 그대로 차용

```python
TAKER_FEE_PCT      = 0.0004   # 0.04% (Bybit perpetual taker)
SLIP_NORMAL_PCT    = 0.0002   # 0.02%
SLIP_VOLATILE_PCT  = 0.0005   # 0.05%
VOLATILE_THRESHOLD = 0.005    # (high-low)/close > 0.5%면 변동성 봉
```

**이유**: 백테스트 정합성의 핵심. replay_engine이 검증된 실거래 환경 수치 (Aurora 거래소 미정 상태에서도 Bybit 기준은 적정 출발점). PR-3 모듈에 dataclass 또는 module-level 상수로 박음.

**차용 위치 (예정)**: `aurora/backtest/cost.py` (신설) 또는 `aurora/backtest/engine.py` 내부 상수.

### 3.3 R 기반 리스크 관리 vs Aurora `core/risk.py` (PR-19) — **design doc의 핵심 결정점** (장수 명시 2026-05-02)

**현황**:
- replay_engine: R 기반 (`risk_pct=0.01`, `rr_tp1=2.0`, `rr_tp2=4.5`, `sl_min/max_pct` 동적)
- Aurora `core/risk.py` (PR-19): `RiskPlan`, `sl_pct_for_leverage`, `tp_pct_range_for_leverage` 등 — 본 구현됨, 아직 정독 안 함

**장수 제시 두 옵션**:

#### 옵션 a — 안전 (통계만 차용)
- replay_engine의 **R-multiple / expectancy 통계만** 차용
- Position size 계산은 `core/risk.py` 그대로 사용 (변경 0)
- **장점**:
  - core/risk.py(장수 영역) 변경 0 → 안전, 빠름
  - PR-3 범위 좁게 유지 → 머지 빠름
  - 통계 보고에 R-multiple 표시 가능 (전략 평가 풍부)
- **단점**:
  - replay_engine의 정교한 R 기반 size 계산은 활용 안 함
  - 향후 R 기반으로 전환하려면 별도 PR 필요

#### 옵션 b — 통합 (size 계산까지)
- R 기반 size 계산도 차용 → `core/risk.py` **보강** (리스크 % per trade 도입)
- **장점**:
  - 더 정교한 리스크 관리 (1R = 자본 1% 같은 표준 패턴)
  - 백테스트 결과의 의미 명확 (R-multiple 단위로 모든 trade 평가)
- **단점**:
  - core/risk.py 변경 → 장수 영역 (별도 합의 필요)
  - PR-3 범위 ↑ → 머지 시간 ↑
  - 기존 `sl_pct_for_leverage` 등과의 정합 검토 필요

**TBD — 결정 시점**: PR-3 사전 작업 중 Aurora `core/risk.py` 정독 직후. 두 방식 호환성 확인이 결정 기반.

**우리 잠정 추천**: **옵션 a 우선**. 이유:
- PR-3 범위 좁게 유지 (입문자 + 첫 큰 모듈 작업)
- core/risk.py 변경은 장수 영역이라 사전 합의 비용 큼
- R-multiple 통계만으로도 백테스트 가치 충분
- 옵션 b는 PR-3 안정화 후 별도 PR로 자연 진화 가능

### 장수 의견 (2026-05-02 명시) — 옵션 a 동의

장수가 양 모델의 정확한 차이를 풀어 옵션 a를 잠정 추천:

#### Aurora `core/risk.py` (PR-19) — **레버리지 기반 size**
```
- 풀시드 옵션 (10x → 1.1% 수수료 / 50x → 5.5% 가정)
- 최소 진입 마진 40% 강제
- SL/TP 그래디언트 (sl_pct_for_leverage, tp_pct_range_for_leverage)
= 레버리지 기반 size 결정
```

#### Tako Bot 차용 코드 — **리스크 % per trade 기반 size**
```
- (entry - SL) = 1R
- size = (account × risk_per_trade%) / (1R × point_value)
= 리스크 % per trade 기반 size 결정
```

#### 충돌 양상
- **옵션 a (통계만)**: R-multiple / expectancy / Sharpe는 **size 계산과 무관** → Aurora 모델 손대지 않고 통계만 풍부 → **자연스럽게 차용 가능**
- **옵션 b (size 까지)**: Aurora 풀시드·마진 강제 정책과 R 기반 모델 충돌 → 양쪽 모두 손봐야 함 (작업량 ↑ + 검증 ↑)

→ **옵션 a 자연스러움 확정** (장수 + 우리 추천 일치).

### 결정: **옵션 a 채택** + 정독 결과 명시

#### 정독 발견 (2026-05-02 코드 검증)

장수 풀어준 두 모델이 **이미 `calc_position_size` 한 함수에 통합**되어 있음을 발견. 결정 뒤집을 발견은 아니지만 정확화 가치 큼:

```python
# Aurora core/risk.py 의 calc_position_size — 두 모드 통합
def calc_position_size(
    equity_usd, leverage, sl_distance_pct, entry_price,
    *,
    risk_pct=None,         # ← R 기반 모드용 (Tako Bot 모델)
    full_seed=False,       # ← 풀시드 모드 플래그
    min_seed_pct=0.40,     # ← 최소 시드 강제 (Aurora 정책)
):
    if full_seed:
        # 풀시드: notional = equity × leverage
    else:
        # R 기반: notional = (equity × risk_pct) / sl_distance_pct
    # 양 모드 공통: margin < equity × 0.40 이면 강제 끌어올림
```

#### 모델 정합 진실
- "Aurora = 레버리지 기반" → `full_seed=True` 또는 `min_seed_pct=40%` 강제 발동 케이스
- "Tako Bot = R 기반" → `full_seed=False` + `risk_pct=0.01`
- **둘은 충돌 X. 한 함수의 두 모드.** Aurora가 더 정교 (R 기반 + 최소 시드 보호 결합)

#### 옵션 a 추천 — 정독 후 더 강해짐
- ✅ `calc_position_size`가 이미 R 기반 지원 → size 계산 차용 불필요
- ✅ R-multiple / expectancy 통계는 `risk_pct` 알면 자연 계산 가능
- ✅ 차용 코드의 R 기반 size 모델은 단순함 (min_seed_pct 강제 없음) — Aurora 모델이 우월
- ✅ 백테스트 시 실거래와 동일 모델 사용 → 정합성 ↑

#### PR-3 BacktestEngine 호출 시 결정사항
백테스트가 `calc_position_size` 호출 시 인자:
```python
calc_position_size(
    equity_usd=current_equity,
    leverage=config.leverage,
    sl_distance_pct=plan.sl_distance_pct,
    entry_price=signal.entry_price,
    risk_pct=config.risk_pct,    # 백테스트 설정 (예: 0.01)
    full_seed=False,              # R 기반이 자연 (실거래도 동일 가정)
    min_seed_pct=0.40,            # Aurora 정책 그대로
)
```
→ 새 코드 작성 X. Aurora 함수 그대로 호출.

#### Aurora가 이미 우월해서 차용 안 할 부분 (정독 발견)
| Aurora 기존 (PR-19) | 차용 코드 |
|---|---|
| `sl_pct_for_leverage` (10x→2%, 50x→4% 등) | replay_engine `sl_min/max_pct` |
| `tp_pct_range_for_leverage` (그래디언트) | replay_engine `rr_tp1/rr_tp2` |
| `calc_position_size` (R 기반 + min_seed) | replay_engine R 기반 size |
| `update_trailing_sl` 5가지 모드 + OFF | replay_engine 단순 트레일링 |
| `TpSlMode` (ATR/FIXED_PCT/MANUAL) + 4단계 분할 익절 | replay_engine FIXED 모드만 |

→ Aurora 모델이 **차용 코드보다 정교**. 차용은 통계·시뮬 루프·수수료 모델만.

### 3.4 timeframe normalizer — `aurora.backtest.tf` 모듈 신설 (장수 권고 #C-3)

별도 섹션 §6에서 상세 설계 (다음 사전 작업 항목).

---

## 4. 차용 코드 발췌 분류 (1차 검토 결과)

| 차용 코드 부분 | 결정 | 이유 / 처리 위치 |
|---|---|---|
| adaptive_backtest 시뮬 루프 골격 | ✅ 차용 | `aurora.backtest.engine`의 `BacktestEngine.run()` |
| adaptive_backtest 통계 집계 / equity curve | ✅ 차용 | `aurora.backtest.stats`의 `compute_stats()`, `BacktestStats` 채움 |
| adaptive_backtest AI 복기 / 파라미터 자동 수정 | ❌ 제거 | Aurora Rule #2 (LLM 금지) |
| adaptive_backtest 텔레그램 알림 | 🟡 참고만 | 정용우 영역 (interfaces/telegram.py)과 중복, 별도 통합은 후속 |
| replay_engine OHLCV 집계 (TF) | 🚫 차용 X | MultiTfAggregator 단독 사용 (§3.1 결정) |
| replay_engine 수수료/슬리피지 모델 | ✅ 즉시 차용 | `aurora.backtest.cost` (신설) 또는 engine 내부 상수 |
| replay_engine SL 동적/정적 한계 | 🟡 비교 후 결정 | Aurora `core/risk.py` 정독 후 정합 결정 |
| replay_engine R 기반 리스크 관리 | 🟡 비교 후 결정 | 동상 |
| replay_engine 연속 손절 방어 | ✅ 차용 가치 | engine 또는 risk 모듈에 추가 |
| replay_engine SignalLight·TechnicalAnalyzer 의존 | ❌ 차용 X | Aurora `core.strategy` 사용 |
| replay_engine 추매 로직 | 🟡 비교 후 결정 | Aurora `core.strategy`에 유사 기능 있는지 확인 후 |

→ **본격 발췌는 PR-3 본 작업 시점**. 사전 작업 단계에선 위 분류 + 인터페이스 spec까지만.

---

## 5. 모듈 구조 (예정)

```
src/aurora/backtest/
├── __init__.py        (이미 있음)
├── replay.py          (PR-1: MultiTfAggregator — 그대로 활용)
├── tf.py              (신설: timeframe normalizer — §6 #C-3)
├── engine.py          (신설 또는 보강: BacktestEngine.run)
├── stats.py           (PR-1: BacktestStats dataclass 빈 정의 — 채움)
└── cost.py            (신설 검토: 수수료/슬리피지 모델 — engine 내부 상수도 OK)
```

---

## 6. `#C-3` Timeframe Normalizer 설계 (확정 2026-05-02)

장수 권고: "산발적 `.lower()/.upper()` 호출 금지, 한 곳에 책임 집중".

### 6.1 모듈 위치
**신설**: `src/aurora/backtest/tf.py`

PR-1 [replay.py](src/aurora/backtest/replay.py) `TF_MINUTES`가 source of truth — 본 모듈은 그 9개 TF를 양방향 변환·검증만 책임.

### 6.2 함수 시그니처 (옵션 A — 함수 3개 분리)

```python
from typing import Literal

def normalize_to_ccxt(tf: str) -> str:
    """Aurora 포맷 → ccxt 포맷.
    
    예: "1H" → "1h", "1m" → "1m", "1W" → "1w"
    
    Raises:
        TypeError: tf가 str이 아닐 때
        ValueError: 빈 문자열 / 공백 포함 / Aurora 포맷 아닌 입력 / 지원 안 하는 TF
    """

def normalize_to_aurora(tf: str) -> str:
    """ccxt 포맷 → Aurora 포맷.
    
    예: "1h" → "1H", "1m" → "1m", "1w" → "1W"
    
    Raises:
        동일.
    """

def is_valid_timeframe(
    tf: str,
    format: Literal["aurora", "ccxt", "either"] = "either",
) -> bool:
    """포맷 검증 (raise 안 함, bool 반환).
    
    잘못된 타입 / 빈 / 공백 / unknown → False (raise X).
    """
```

### 6.3 매핑 테이블

```python
_AURORA_TO_CCXT: dict[str, str] = {
    "1m":  "1m",   # 분 단위는 양쪽 동일 (자연 idempotent)
    "3m":  "3m",
    "5m":  "5m",
    "15m": "15m",
    "1H":  "1h",   # 시간/일/주는 대소문자 변환
    "2H":  "2h",
    "4H":  "4h",
    "1D":  "1d",
    "1W":  "1w",
}
_CCXT_TO_AURORA: dict[str, str] = {v: k for k, v in _AURORA_TO_CCXT.items()}
```

### 6.4 검증 정책 — Strict

| 입력 | 처리 |
|---|---|
| Aurora 포맷 → `normalize_to_ccxt` | ✅ 변환 |
| ccxt 포맷 → `normalize_to_ccxt` | ❌ ValueError (Aurora 포맷 아님) |
| ccxt 포맷 → `normalize_to_aurora` | ✅ 변환 |
| Aurora 포맷 → `normalize_to_aurora` | ❌ ValueError (ccxt 포맷 아님) |
| 분 단위 (`"1m"` 등) | ✅ 양쪽 함수 모두 idempotent |
| 알려지지 않은 TF (`"30m"`, `"1Y"`) | ❌ ValueError |
| 빈 문자열 `""` | ❌ ValueError ("빈 timeframe 입력") |
| 공백 포함 (`" 1H "`, `"1 m"`) | ❌ ValueError (strip은 호출자 책임) |
| `None` / 잘못된 타입 | ❌ TypeError (Python 표준) |

### 6.5 에러 메시지 (한국어, Aurora 정책)

```python
# normalize_to_ccxt 실패
raise ValueError(
    f"지원하지 않는 timeframe: {tf!r}. "
    f"Aurora 포맷만 허용: {sorted(_AURORA_TO_CCXT)}"
)

# normalize_to_aurora 실패
raise ValueError(
    f"지원하지 않는 timeframe: {tf!r}. "
    f"ccxt 포맷만 허용: {sorted(_CCXT_TO_AURORA)}"
)
```

### 6.6 왜 Strict?
- "한 곳에 책임 집중" 정신 (장수 권고)
- Permissive(idempotent 양방향)면 잘못된 방향 호출이 silent로 통과 → 디버깅 어려움
- 호출자가 입력 포맷 명확히 알아야 함 — 강제로 명시 시킴

### 6.7 테스트 케이스 spec (14개, mock 0)

**정상 변환 (4)**
1. `test_normalize_to_ccxt_aurora_uppercase` — `1H/2H/4H/1D/1W` → 각 ccxt 변환
2. `test_normalize_to_ccxt_minutes_idempotent` — `1m/3m/5m/15m` 양쪽 동일
3. `test_normalize_to_aurora_ccxt_lowercase` — `1h/2h/4h/1d/1w` → 각 Aurora 변환
4. `test_normalize_to_aurora_minutes_idempotent` — 분 단위 idempotent

**양방향 round-trip (장수 명시) (2)**
5. `test_round_trip_aurora_to_ccxt_to_aurora` — 모든 Aurora TF round-trip
6. `test_round_trip_ccxt_to_aurora_to_ccxt` — 모든 ccxt TF round-trip

**Strict 검증 — 잘못된 방향 reject (2)**
7. `test_normalize_to_ccxt_rejects_ccxt_format` — `normalize_to_ccxt("1h")` → ValueError
8. `test_normalize_to_aurora_rejects_aurora_format` — `normalize_to_aurora("1H")` → ValueError

**알려지지 않은 TF (2)**
9. `test_normalize_to_ccxt_unknown_tf_raises` — `"30m"`, `"1Y"` → ValueError
10. `test_normalize_to_aurora_unknown_tf_raises` — 동일

**입력 타입·공백 검증 (3)**
11. `test_normalize_empty_string_raises` — `""` → ValueError
12. `test_normalize_whitespace_raises` — `" 1H "`, `"1 m"` → ValueError
13. `test_normalize_wrong_type_raises` — `None`, `60` → TypeError

**`is_valid_timeframe` (1)**
14. `test_is_valid_timeframe_format_modes` — 8~10 케이스 묶음 (각 format mode별 검증)

→ **PR-1(13개) / PR-2(18개)와 비슷한 수준**. 새 모듈이라 자체 회귀 보호 충실히.

### 6.8 변경 파일 (예정)

| 파일 | 종류 | 비고 |
|---|---|---|
| `src/aurora/backtest/tf.py` | 신규 (~80~100줄) | 본 설계 그대로 |
| `tests/test_tf.py` | 신규 (~150~200줄) | 14개 테스트 |

→ 본 작업 시점에 PR-3의 일부로 commit (BacktestEngine과 함께 또는 첫 commit으로 분리 가능).

### 6.9 본격 작업 시 주의

- PR-1 `MultiTfAggregator.TF_MINUTES`와 **본 모듈의 매핑이 정확히 일치하는지 회귀 보호** — 새 TF 추가 시 양쪽 동기화 필수 (테스트로 검증 가능)
- `is_valid_timeframe`에서 `tf` 인자 type guard — `isinstance(tf, str)` 체크 후 진행 (그 외 타입엔 False 반환, raise X)

---

## 7. 테스트 케이스 spec (TBD)

PR-1 테스트(13개) + PR-2 테스트(18개)와 같은 mock 기반 결정론적 검증. 외부 네트워크 X.

**예상 테스트 그룹** (예정):
- BacktestEngine.run() 기본 흐름
- 수수료/슬리피지 적용 정확성
- 진입·청산 정확성 (TP/SL/추매)
- 통계 집계 정확성 (승률, MDD, Sharpe, equity curve)
- 엣지 (빈 데이터, 단일 봉, 너무 짧은 기간)
- 차용 코드 발췌 부분 회귀 보호

상세는 발췌 진행하며 보강.

---

## 8. 범위 외 (필요 시 후속 PR)

- 차용 코드의 텔레그램 알림 통합 (정용우 영역과 협의)
- adaptive_backtest의 적응형 파라미터 조정 (AI 부분 제외하고도 룰 기반 적응 가능 여부 검토)
- 추매 로직의 Aurora `core.strategy` 통합
- 백테스트 결과 저장 형식 (parquet vs HTML 리포트 등)
- 멀티 페어·멀티 기간 동시 백테스트 (현재는 단일 가정)

---

## 9. 장수 review 요청 사항 (장수 가이드 2026-05-02 반영)

PR-2 description §8 패턴 따라 첫 commit 전 design doc review. 장수가 명시 요구한 3가지 + 우리 추가:

### 핵심 결정점 (장수 명시)

1. **§3.3 R 기반 리스크 vs `core/risk.py` 정합** — 옵션 a(통계만 차용) vs 옵션 b(size 계산까지 통합)
   - 우리 잠정 추천: 옵션 a (PR-3 범위 좁게, core/risk.py 변경 X)
   - 장수 의견 + 추천 검증 부탁

2. **§6 timeframe normalizer spec 적정성**
   - 입력 ccxt 소문자(1m, 1h, 1d) ↔ 내부 Aurora 대문자(1H, 1D)
   - 양방향 round-trip + invalid input 처리
   - 함수 분리 (3개) vs 통합 (1개) — Step 3-1에서 결정

3. **§2 BacktestEngine 인터페이스 + 모듈 재사용/차용 매핑**
   - parquet → DataFrame → strategy → signal → risk → 시뮬 → 통계 흐름
   - core 모듈 재사용 vs 차용 발췌 vs 새 작업 분류 적정성

### 추가 검토 (우리 추가)

4. TF 집계 = MultiTfAggregator 단독 결정 (replay_engine TF 부분 차용 X)
5. 수수료/슬리피지 모델 수치 그대로 차용 (Bybit 거래소 미정 상태에서도 Bybit 기준 적정?)
6. 모듈 구조 — `aurora/backtest/cost.py` 신설 vs `engine.py` 내부 상수
7. 테스트 케이스 spec 범위 (§7) — 추가/제거 의견

→ design doc 골격 완성 후 PR-2 Stage 1A v2 패턴으로 review 요청. 장수 답변 받고 보강 (v2 → v3 같은 형태로 반영 후 첫 commit).

---

## 변경 이력
- 2026-05-02 (저녁): 신설. PR-2 머지 직후 + archive branch 1차 검토 완료 시점. 차용 분류 + 핵심 결정 3건 + 모듈 구조 골격까지.
- 2026-05-02 (저녁 갱신): 장수 design doc 가이드 반영 — §2 데이터 흐름 다이어그램 + 모듈 매핑 표, §3.3 R 기반 옵션 a/b 장단점 + 우리 추천(a), §9 review 요청 항목 정리.
- 2026-05-02 (밤 갱신): 장수 §3.3 옵션 a 동의 + 양 모델 차이 명시 반영 — Aurora 레버리지 기반 size vs Tako Bot R 기반 % per trade. 옵션 a 결정 확정. 정독은 검증 + 추가 발견 차원.
- 2026-05-02 (밤 갱신 #2): §6 timeframe normalizer 상세 설계 확정 — 옵션 A(함수 3개 분리), Strict 검증 정책, 14개 테스트 케이스 spec. design doc 골격 거의 완성.
- 2026-05-02 (밤 갱신 #3): Aurora `core/risk.py` 정독 완료. §3.3 옵션 a 결정 검증 — 더 강해짐. **발견**: Aurora `calc_position_size`가 이미 R 기반 + 풀시드 + 최소 시드 강제(40%) 통합. 장수 명시한 두 모델 차이는 한 함수의 두 모드. Aurora 모델이 차용 코드보다 정교 → 차용 X 부분 추가 (SL/TP 그래디언트, 트레일링 5모드 등). 차용은 통계·시뮬 루프·수수료 모델만.
- 다음 보강 예정: §7 BacktestEngine 테스트 케이스 spec, §9 review 발송 준비, 동기화 매트릭스 별도 파일 신설.
