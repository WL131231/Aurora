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
  MultiTfAggregator.step(bar_1m)  ← PR-1
        │
        ▼ 반환 dict[tf, AggregatedBar | None] — 비-None=닫힘 이벤트
  닫힌 TF 판별 → 닫힌 TF만 get_df(tf) 호출 (look-ahead 방지)
        │
        ▼ df_by_tf = {tf: aggregator.get_df(tf) for tf in closed_tfs}
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

#### 3.1.1 시그니처 점검 (2026-05-03 코드 truth 확인)

**실제 시그니처** (`src/aurora/backtest/replay.py:172`):

```python
def step(self, minute_bar: pd.Series) -> dict[str, AggregatedBar | None]:
    ...
```

**인터페이스 truth**:
- **입력**: `pd.Series` 1 분봉 — `.name` 이 open_time (Timestamp), 컬럼은 OHLCV 5 필드. `df_1m.iterrows()` 자연 호출 형태.
- **반환**: `{tf: AggregatedBar | None}` — `None`=해당 TF 미마감, 비-`None`=닫힘 이벤트 (방금 닫힌 봉).
- **닫힘 이벤트 노출**: 별도 콜백/이벤트 큐 X. `step()` 반환 dict에서 비-`None` 항목 추출만으로 닫힘 판별 — engine.step() 내부 1 줄 처리 가능.
- **DataFrame 접근**: `aggregator.get_df(tf)` 별도 호출. **진행 중인 미마감 봉은 의도적으로 제외** (look-ahead 방지) — `get_df(tf)` 결과로 지표 계산해도 미완성 봉이 섞이지 않음.

**차용 코드 (replay_engine) 비교** (drafts/PR-3-borrowed-analysis.md M-9, M-20):
- 차용: bucket-floor + `deque(maxlen)` + 닫힘 시점 bool 반환 + **TF 집계와 매매 로직 monolithic 결합**.
- PR-1: 동일 bucket-floor + `deque(maxlen)` + **TF별 닫힘 객체 dict 반환** + TF 집계만 분리(SRP).
- → **PR-1 인터페이스가 동등 + 우월** (TF별 닫힘 객체 직접 노출, 매매 로직 분리). §3.1 "차용 X" 결정 유지 + 강화.

→ engine.step() 활용 패턴 + 10단계 흐름은 §6 (신규) 참조.

### 3.2 수수료/슬리피지 모델 — replay_engine 수치 그대로 차용

```python
TAKER_FEE_PCT      = 0.0004   # 0.04% (Binance 선물 taker, replay_engine L38 단독 출처.
                              #         거래소 결정 시 #B-3 (Issue #40) 후속에서 override)
SLIP_NORMAL_PCT    = 0.0002   # 0.02%
SLIP_VOLATILE_PCT  = 0.0005   # 0.05%
VOLATILE_THRESHOLD = 0.005    # (high-low)/close > 0.5%면 변동성 봉
```

**이유**: 백테스트 정합성의 핵심. replay_engine이 검증된 실거래 환경 수치 (Aurora 거래소 미정 상태에서도 **Binance 기준**은 적정 출발점). PR-3 모듈에 dataclass 또는 module-level 상수로 박음.

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

#### 3.3.1 옵션 a 검증 + 호출 패턴 + 신호↔리스크 독립 (2026-05-03 추가 검증)

##### 단락 1 — replay_engine 정독 결과 (R 기반 차용 X 강화)

차용 코드 `archive/borrowed-code/replay_engine.py` 정독 (2026-05-03) — `risk_pct=0.01` 선언 1 건 / 사용 0 건의 dead parameter. 실제 sizing은 `size_max_pct=0.50` 고정. R 활용은 TP placement 1 건뿐 (TP1=2R / TP2=4.5R). 차용 코드의 "R 기반 리스크 관리"는 docstring 표현이고 실체는 단일 패턴이므로, Aurora `calc_position_size` 옵션 a 통합 모드 (3 모드 + min_seed floor) 채택은 정당. **§4 차용 분류에서 "R 기반 리스크 관리: 🟡 비교 후 결정 → ❌ 차용 X" 확정**.

##### 단락 2 — min_seed_pct=0.40 트레이드오프 명시

`min_seed_pct=0.40` 디폴트 적용 시, 사용자가 호출한 `risk_pct=0.01` (1R=1%) 이 **floor 발동 시 실제 손실 노출 최대 16%까지 증폭**될 수 있음.

검증 예시 (equity=$1000, leverage=10x, sl_distance=4%, risk_pct=1%):

```
risk_amount  = $1000 × 0.01           = $10
notional     = $10 / 0.04             = $250
margin       = $250 / 10              = $25
min_margin   = $1000 × 0.40           = $400   ← floor 발동
notional_재  = $400 × 10              = $4000
실제 1R      = $4000 × 0.04           = $160   = equity의 16%
```

**의도된 trade-off** — 수수료 비율 보호 (margin 너무 작으면 fee 비중 ↑, 손익비 망가짐) vs `risk_pct` R 약속 (1R=1%). 백테스트는 이 정책 위에서 시뮬 (실거래와 동일 모델 → 정합성).

##### 단락 3 — engine.py 호출 패턴 (직접 calc_position_size 호출 X)

`engine.py` 는 `calc_position_size` 직접 호출 X. 진입 시점에 `build_risk_plan(...)` 1 회 호출로 SL/TP 가격 + 포지션 사이즈 한 번에 산출:

```python
plan = build_risk_plan(
    entry_price, direction, leverage, equity_usd, config,
    atr=atr_value,           # ATR 모드 시
    risk_pct=config.risk_pct,
    full_seed=False,
    min_seed_pct=0.40,
)
# RiskPlan(entry_price, direction, leverage, position, tp_prices[4], sl_price, trailing_mode)
```

매 봉은 `update_trailing_sl(current_sl, plan, config, tp_hits, highest_since_entry, lowest_since_entry)` 호출로 SL 갱신만 (5 가지 트레일링 모드 + OFF, 단방향 보장).

##### 단락 4 — 신호↔리스크 독립 정책 정식화 (장수 답변 2026-05-03)

**Aurora 디자인 의도**: "**신호 강도 = entry/skip decision만 결정**, **position size = 마진 정책 (lev + min_seed floor 보호) 이 결정**". 신호와 리스크는 **완전 독립**.

**검증 (signal.py + risk.py 정독 2026-05-03)**:

- `core/signal.py` `compose_entry(signals, threshold=DEFAULT_ENTRY_THRESHOLD=1.0)`:
  - `weighted_score = strength × TF_WEIGHTS[tf]` 방향별 합산 (long_score / short_score)
  - 점수 큰 방향이 threshold 이상이면 `CompositeDecision(enter=True, direction=..., score=..., triggered_by=[...])` 반환
  - **TF_WEIGHTS 박힘 (8 개)**: `15m=1, 1H=2, 2H=3, 4H=5, 6H=7, 12H=10, 1D=15, 1W=25`
- `core/risk.py` `build_risk_plan(entry_price, direction, leverage, equity_usd, config, atr=None, *, risk_pct=0.01, full_seed=False, min_seed_pct=0.40)`:
  - **signal/strength/score 인자 0 개** — 완전 독립 ✅

**신호 강도 → PnL 변동성 기여 메커니즘 = 진입 횟수**. 강한 신호 (high score) → threshold 통과 빈도 ↑ → 진입 빈도 ↑ → 누적 PnL 변동성 ↑. size 자체는 신호와 무관 (마진 정책만으로 결정).

**engine.step() 흐름에서의 활용** (§6 상세):

- 신호 평가: `signals = strategy.evaluate(df_by_tf, config)` → `decision = compose_entry(signals, threshold=1.0)`
- 진입 결정: `if decision.enter: plan = build_risk_plan(... direction=decision.direction ...)` — `decision.score` / `triggered_by` 는 `build_risk_plan` 입력 X (로그·통계용으로만 보존).

### 3.4 timeframe normalizer — `aurora.backtest.tf` 모듈 신설 (장수 권고 #C-3)

별도 섹션 §7에서 상세 설계 (다음 사전 작업 항목).

---

## 4. 차용 코드 발췌 분류 (정독 확정 — 2026-05-03 갱신)

### 4.1 차용 발췌 분류 표 (정독 후 확정)

| 차용 코드 부분 | 결정 | 이유 / 처리 위치 |
|---|---|---|
| adaptive_backtest 시뮬 루프 골격 | ✅ 차용 | `aurora.backtest.engine` 의 `BacktestEngine.run()` |
| adaptive_backtest 통계 집계 / equity curve | ✅ 차용 | `aurora.backtest.stats` 의 `compute_session_stats()`, `BacktestStats` 채움 |
| adaptive_backtest AI 복기 / 파라미터 자동 수정 | ❌ 제거 | Aurora Rule #2 (LLM 금지) + `review_and_adjust` 자동 튜닝 정책 제외 |
| adaptive_backtest 텔레그램 알림 | 🟡 참고만 | 정용우 영역 (`interfaces/telegram.py`) 과 중복, 별도 통합은 후속 |
| replay_engine OHLCV 집계 (TF) | 🚫 차용 X | MultiTfAggregator 단독 사용 (§3.1 결정) |
| replay_engine 수수료/슬리피지 모델 | ✅ 차용 | `aurora.backtest.cost` 모듈 신설 (출처 정정: Bybit → **Binance** 선물, replay_engine L38 단독 출처) |
| replay_engine SL 동적/정적 한계 | ❌ 차용 X | Aurora `sl_pct_for_leverage` 그래디언트가 우월 (§3.3 Aurora 우월 표) |
| replay_engine R 기반 리스크 관리 | ❌ 차용 X | replay_engine `risk_pct` dead parameter 발견 (§3.3.1 단락 1). Aurora `calc_position_size` 통합 모드 채택 |
| replay_engine 연속 손절 방어 | ✅ 차용 | `engine.py` 시뮬 루프 + `_check_max_dd` / `_tick_pause` |
| replay_engine SignalLight·TechnicalAnalyzer 의존 | ❌ 차용 X | Aurora `core.strategy` / `core.signal` 사용 |
| replay_engine 추매 로직 | 🟡 부분 차용 / 보류 | M-19 후속 PR (PR-3 범위 외) |

### 4.2 LOC 압축 비율 (ROI 17.3%)

| 출처 | 전체 LOC | 차용 (raw) | 차용 (압축) | 압축 비율 |
|---|---:|---:|---:|---:|
| `archive/borrowed-code/adaptive_backtest.py` | 668 | ~145 | ~95 | 14.2% |
| `archive/borrowed-code/replay_engine.py` | 1086 | ~387 | ~209 | 19.2% |
| **합계** | **1754** | **~532** | **~304** | **17.3%** |

### 4.3 LOC 분류별 분포 (drafts/PR-3-borrowed-analysis.md §3 이식)

| 분류 | LOC (압축) | 처리 |
|---|---:|---|
| ① 제거 (Rule #2 + Aurora 정책 충돌) | ~85 | `review_and_adjust` 자동 튜닝 / AI 복기 / SignalLight 의존 |
| ② 차용 → `cost.py` | ~29 (raw 45) | 수수료·슬리피지 상수 + apply 함수 |
| ② 차용 → `engine.py` | ~150 (raw ~270) | 시뮬 루프 골격 + 진입·청산·연속 손절 방어 |
| ② 차용 → `stats.py` | ~80 + 신규 ~45 (raw ~125) | trade 통계 + R-multiple + equity curve |
| ③ 참고만 (정용우 영역) | ~70 | 텔레그램 알림 (`interfaces/` 별도 통합) |
| ④ 차용 X (장수 영역) | ~990 (전체의 57.1%) | TF 집계, R 기반 size, SL 그래디언트, 트레일링 5 모드 등 |
| 🟡 부분 차용 / 보류 | ~43 | 추매 로직 (M-19 후속 PR) |

### 4.4 핵심 정정 사항 (1차 검토 → 정독 확정)

1. **replay_engine R 기반 리스크 관리**: 🟡 비교 후 결정 → **❌ 차용 X 확정**
   - 근거: §3.3.1 단락 1 — `risk_pct=0.01` dead parameter, 실 사용 0 건. Aurora `calc_position_size` 통합 모드가 우월.
2. **수수료/슬리피지 출처**: Bybit perpetual taker → **Binance 선물** (replay_engine L38 단독 출처)
   - 거래소 미정 상태이지만 **Binance 기준이 출발점** (재검토는 거래소 확정 후, `#B-3` (Issue #40) 후속).
3. **replay_engine SL 동적/정적 한계**: 🟡 비교 후 결정 → **❌ 차용 X 확정**
   - 근거: Aurora `sl_pct_for_leverage` 구간 분기 + 그래디언트 (10x→2%, 50x→4%) 가 우월 (§3.3 Aurora 우월 표).

→ **본격 발췌는 PR-3 본 작업 시점**. 사전 작업 단계에선 위 분류 + 인터페이스 spec까지만.

---

## 5. 모듈 구조 (확정 — 2026-05-03 갱신)

### 5.1 디렉토리 트리

```
src/aurora/backtest/
├── __init__.py        (이미 있음)
├── replay.py          (PR-1 머지 — MultiTfAggregator)
├── tf.py              (PR-3 Stage 1A 신설 — timeframe normalizer, §7 #C-3 참조)
├── cost.py            (PR-3 Stage 1B 신설 — 수수료·슬리피지 모델)
├── stats.py           (PR-3 Stage 1B 신설 — trade 통계 + R-multiple + equity curve)
├── engine.py          (PR-3 Stage 1C 신설 — BacktestEngine.run + 시뮬 루프)
└── DESIGN.md          (이 문서)

src/aurora/core/
└── indicators.py      (PR-3 Stage 1B/1C — ChoYoon 영역 위임으로 atr_wilder 추가, 장수 명시 2026-05-03)
```

### 5.2 모듈별 spec

#### cost.py (Stage 1B)

**모듈 상수** (Binance 선물 출처, replay_engine L38):

```python
TAKER_FEE_PCT      = 0.0004    # 0.04%
SLIP_NORMAL_PCT    = 0.0002    # 0.02%
SLIP_VOLATILE_PCT  = 0.0005    # 0.05%
VOLATILE_THRESHOLD = 0.005     # (high - low) / close > 0.5% 면 변동성 봉
```

**함수**:

- `slip_pct(candle_high, candle_low, candle_close) -> float` — 봉 변동성 따라 normal/volatile 슬립 선택
- `apply_slippage(price, direction, side, slip) -> float` — entry/exit 시 unfavorable 방향으로 가격 조정
- `apply_costs(raw_pnl_pct, size_pct, leverage, fee_pct=TAKER_FEE_PCT) -> tuple[float, float]` — `(lev_pnl, fee_loss)` 반환

#### stats.py (Stage 1B)

**dataclass**:

- `TradeRecord(entry_price, entry_ts, exit_price, exit_ts, direction, leverage, pnl, r_multiple, duration_minutes, regime: str | None = None)`
- `BacktestStats(total_trades, win_rate, mdd, sharpe, expectancy, equity_curve, total_pnl, fee_paid)`

**함수**:

- `compute_session_stats(trades) -> BacktestStats` — trade 리스트 → 통계 일괄 산출
- `compute_r_multiples(trades, plans) -> list[float]` — 신규: `(exit - entry) / sl_distance × 방향` (R-multiple 단위로 trade 평가)
- `compute_drawdown(equity_curve) -> float` — MDD (peak-to-trough max %)

#### engine.py (Stage 1C)

`BacktestEngine.run(df_1m, config) -> BacktestResult` — 1 분봉 DataFrame 통째 받아 시뮬 루프 + 통계 산출.

**의존**:

- `aurora.backtest.replay.MultiTfAggregator` — 1m → 멀티 TF 집계
- `aurora.backtest.tf` — timeframe 변환 (필요 시)
- `aurora.backtest.cost` — 수수료·슬리피지 적용
- `aurora.backtest.stats` — trade 기록 + 통계
- `aurora.core.strategy.evaluate` — 신호 평가 (장수 영역)
- `aurora.core.signal.compose_entry` — 가중치 합산 + threshold 통과 판별
- `aurora.core.risk.build_risk_plan` / `update_trailing_sl` — 리스크 산출 + 트레일링
- `aurora.core.indicators.atr_wilder` — ATR 모드 시 (위임 받음, §5.3)

### 5.3 core/indicators.py 위임 (장수 영역, 장수 명시 2026-05-03)

ATR 계산은 표준 지표라 ChoYoon 영역으로 위임 받음:

```python
# src/aurora/core/indicators.py (PR-3 Stage 1B/1C 추가)
def atr_wilder(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ATR — alpha=1/period 지수 이동평균 기반 True Range."""
```

**테스트**: `tests/core/test_indicators.py` (또는 동등) — 정상 / 경계 / 알려진 ATR 값 회귀 보호.

**PR-3 description Decisions D-4 명시**: "`core/indicators.py` 영역 위임 받음 (장수 명시 2026-05-03)".

---

## 6. engine.step() 10 단계 흐름 + 모듈 책임 표 (신규 — 2026-05-03)

### 6.1 모듈 책임 표

| 모듈 | 핵심 책임 | 비책임 |
|---|---|---|
| `replay.py` (`MultiTfAggregator`) | 1 분봉 → 멀티 TF 집계 + 닫힘 이벤트 노출 | 신호 / 리스크 / 시뮬 |
| `tf.py` | timeframe 표기 변환 + 검증 (Aurora ↔ ccxt) | 데이터 처리 |
| `cost.py` | 수수료·슬리피지 상수 + apply 함수 | 거래소별 분기 (`#B-3` 후속) |
| `stats.py` | trade 단위 통계 + R-multiple + equity curve | 시뮬 진행 |
| `engine.py` | 시뮬 루프 + 레버리지별 정책 적용 + risk·strategy 결합 | 신호 검출 / 사이즈 본체 |
| `core.risk` | `TpSlConfig` 받아 `RiskPlan` 산출 (정책-agnostic) | 레버리지 정책 강제 |
| `core.strategy` | DataFrame → 신호 산출 | 시뮬 |
| `core.signal` | TF_WEIGHTS 가중치 합산, threshold 통과 시 진입 발동 | 리스크 결정 |
| `core.indicators` | 표준 지표 (Wilder ATR 등) | 시뮬·신호 결정 |

### 6.2 engine.step() 10 단계 흐름

`BacktestEngine.run(df_1m, config)` 내부에서 매 1 분봉 도착 시 `engine.step(bar_1m)` 호출:

```
[매 1 분봉 도착 시]
1. closed = aggregator.step(bar_1m)
        → dict[tf, AggregatedBar | None]  (비-None=닫힘 이벤트)

2. (포지션 보유 중) current_sl = update_trailing_sl(
        current_sl, plan, config, tp_hits,
        highest_since_entry, lowest_since_entry,
   )                                       ← SL 갱신만 (5 모드 + OFF, 단방향)

3. (포지션 보유 중) _check_exits(bar_1m)   ← SL/TP 도달 체크 + gap-fill 처리

4. (포지션 보유 중) highest_since_entry / lowest_since_entry 갱신

5. closed_tfs = [tf for tf, b in closed.items() if b is not None]
   if not closed_tfs: return              ← early return (어떤 TF도 닫힘 X → 신호 평가 skip)

6. _check_max_dd()                         ← MDD 영구정지 체크 (글로벌 가드)

7. _tick_pause()                           ← consec_sl pause_bars 카운터 감소

8. df_by_tf = {tf: aggregator.get_df(tf) for tf in closed_tfs}
   signals = (
       detect_ema_touch(df_by_tf, strategy_config)
       + (detect_rsi_divergence(df_by_tf['1H'], strategy_config)
          if '1H' in df_by_tf else [])
       + evaluate_selectable(df_by_tf, strategy_config, symbol=config.symbol)
   )                                       ← list[EntrySignal]
                                           (core.strategy 통합 evaluate() 부재 →
                                            3 함수 직접 호출 + concat. 통합 함수
                                            추가는 장수 영역 후속 PR)

9. decision = compose_entry(signals, threshold=DEFAULT_ENTRY_THRESHOLD)
                                           → CompositeDecision(enter, direction, score, ...)
   - weighted_score = strength × TF_WEIGHTS[tf] 방향별 합산
   - threshold(=1.0) 통과 방향이 진입 발동 (강도는 entry/skip 만 결정)

10. (decision.enter == True 시):
    a. atr_value = atr_wilder(df_by_tf['4H'], period=14).iloc[-1]    # ATR 모드 시
    b. sl_pct = sl_pct_for_leverage(config.leverage)
    c. tp_min, tp_max = tp_pct_range_for_leverage(config.leverage)
    d. fixed_tp_pcts = [
           tp_min,
           tp_min + (tp_max - tp_min) / 3,
           tp_min + 2 * (tp_max - tp_min) / 3,
           tp_max,
       ]                                   ← D-3 등간격 분할
    e. TpSlConfig 채움 (mode + fixed_tp_pcts + sl_pct + trailing 등)
    f. plan = build_risk_plan(
           entry_price=bar_1m['close'],
           direction=decision.direction,
           leverage=config.leverage,
           equity_usd=current_equity,
           config=tp_sl_config,
           atr=atr_value,
           risk_pct=config.risk_pct,
           full_seed=False,
           min_seed_pct=0.40,
       )                                   ← signal 강도 입력 X (§3.3.1 단락 4 독립 정책)
    g. _open(plan)                         ← 포지션 진입 + entry_ts 기록
```

### 6.3 핵심 정책 명시

- **신호↔리스크 독립** (§3.3.1 단락 4): 9 단계 `decision.score` / `triggered_by` 는 10 단계 `build_risk_plan` 입력 X. 로그·통계용으로만 보존.
- **D-3 등간격 분할** (10-d): TP 4 단계는 `[tp_min, tp_min + 1/3·range, tp_min + 2/3·range, tp_max]` 등간격. `tp_allocations` 는 `TpSlConfig` 디폴트 (25/25/25/25) 그대로.
- **early return** (5 단계): 어떤 TF도 닫힘 없는 1 분봉은 신호 평가 skip. 성능 + 동일 시점 중복 평가 방지.
- **포지션 보유 시 갱신 우선** (2-4 단계): 닫힌 TF 봉 도착 전이라도 매 1 분봉 SL 갱신 + 청산 체크 (gap-fill 가격 누락 방지).

---

## 7. `#C-3` Timeframe Normalizer 설계 (확정 2026-05-02)

장수 권고: "산발적 `.lower()/.upper()` 호출 금지, 한 곳에 책임 집중".

### 7.1 모듈 위치
**신설**: `src/aurora/backtest/tf.py`

PR-1 [replay.py](src/aurora/backtest/replay.py) `TF_MINUTES`가 source of truth — 본 모듈은 그 9개 TF를 양방향 변환·검증만 책임.

### 7.2 함수 시그니처 (옵션 A — 함수 3개 분리)

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

### 7.3 매핑 테이블

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

### 7.4 검증 정책 — Strict

| 입력 | 처리 |
|---|---|
| Aurora 포맷 → `normalize_to_ccxt` | ✅ 변환 |
| ccxt 포맷 → `normalize_to_ccxt` | ❌ ValueError (Aurora 포맷 아님) |
| ccxt 포맷 → `normalize_to_aurora` | ✅ 변환 |
| Aurora 포맷 → `normalize_to_aurora` | ❌ ValueError (ccxt 포맷 아님) |
| 분 단위 (`"1m"` 등) | ✅ 양쪽 함수 모두 idempotent |
| 알려지지 않은 TF (`"30m"`, `"1Y"`) | ❌ ValueError |
| 빈 문자열 `""` | ❌ ValueError ("빈 timeframe 입력") |
| 공백 포함 (`" 1H "`, `"1 m"`, `"\t1H"`, `"1H\n"`) | ❌ ValueError (strip / escape 모두 호출자 책임) |
| `None` / `60` / `1.5` / `list` / `tuple` / `set` 등 비-str | ❌ TypeError (Python 표준) |

### 7.5 에러 메시지 (한국어, Aurora 정책)

```python
# normalize_to_ccxt 실패
raise ValueError(
    f"지원하지 않는 timeframe: {tf!r}. "
    f"Aurora 포맷만 허용: {list(_AURORA_TO_CCXT)}"
)
# 출력 예: "지원하지 않는 timeframe: '30m'. Aurora 포맷만 허용:
#         ['1m', '3m', '5m', '15m', '1H', '2H', '4H', '1D', '1W']" (insertion order = 시간 순)

# normalize_to_aurora 실패
raise ValueError(
    f"지원하지 않는 timeframe: {tf!r}. "
    f"ccxt 포맷만 허용: {list(_CCXT_TO_AURORA)}"
)
```

### 7.6 왜 Strict?
- "한 곳에 책임 집중" 정신 (장수 권고)
- Permissive(idempotent 양방향)면 잘못된 방향 호출이 silent로 통과 → 디버깅 어려움
- 호출자가 입력 포맷 명확히 알아야 함 — 강제로 명시 시킴

### 7.7 테스트 케이스 spec (14개, mock 0)

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

### 7.8 변경 파일 (예정)

| 파일 | 종류 | 비고 |
|---|---|---|
| `src/aurora/backtest/tf.py` | 신규 (~80~100줄) | 본 설계 그대로 |
| `tests/test_tf.py` | 신규 (~150~200줄) | 14개 테스트 |

→ 본 작업 시점에 PR-3의 일부로 commit (BacktestEngine과 함께 또는 첫 commit으로 분리 가능).

### 7.9 본격 작업 시 주의

- PR-1 `MultiTfAggregator.TF_MINUTES`와 **본 모듈의 매핑이 정확히 일치하는지 회귀 보호** — 새 TF 추가 시 양쪽 동기화 필수 (테스트로 검증 가능)
- `is_valid_timeframe`에서 `tf` 인자 type guard — `isinstance(tf, str)` 체크 후 진행 (그 외 타입엔 False 반환, raise X)

---

## 8. 테스트 케이스 spec — `tests/test_engine.py` (Group 3, 2026-05-03)

PR-1 13 개 / PR-2 18 개 / Stage 1A 14 개 / Stage 1B 32 개 (cost 16 + stats 16) 패턴 정합. mock 0 / 합성 OHLCV / 결정론적 / 외부 네트워크 X.

**총 22 함수 / 23 collected** (E2 parametrize 2 분기). pytest baseline 385 → 408.

### 8.1 케이스 그룹 (5)

| 그룹 | 케이스 수 | 책임 |
|---|---:|---|
| A. dataclass + `__init__` | 3 | `BacktestConfig` 디폴트 / `risk_config` 자동 산출 / 명시 입력 불변성 (Q2) |
| B. 헬퍼 단위 | 8 | direction 격리 / `_open` 가드 / `_close` reason 분기 / `_partial_close` 분기 / `_check_exits` gap-fill |
| C. 가드 + `step()` | 6 | early return / pause / stopped / ATR 4H 가드 / MDD 영구 정지 / `_tick_pause` 단방향 |
| D. `run()` 통합 | 2 | 0 봉 / FORCE_END |
| E. edge / regression | 3 (4 collected) | sl_distance=0 / clamp parametrize / `strategy_config` 명시 |
| **합계** | **22 (23 collected)** | — |

### 8.2 결정 (D-N) ↔ 케이스 매핑

| 결정 | 케이스 | 비고 |
|---|---|---|
| D-1 (min_seed_pct=0.40) | 간접 (B 그룹 — _open / _close margin/equity 16% 증폭 시나리오) + **H1 (통합 검증, Group H 신설)** | 통합 검증 본 PR 해소 (Issue #111, `test_min_seed_amplification_floor_scenario`) — canonical 16.40배 증폭 정합 |
| D-2 (consec_sl 카운트 분기) | B4 (TP4 reset) / B5 (SL pause 발동) / B6 (REVERSE 유지) / D2 (FORCE_END 유지) | 7 reason 매핑 표 핵심 4 분기 검증 |
| D-3 (등간격 분할) | A2 (auto risk_config tps=[2.8, 3.13.., 3.46.., 3.8]) | leverage=10 산식 정합 |
| D-4 (ATR 4H 디폴트 + 가드) | C4 (ATR 모드 + 4H 미닫힘 → 진입 skip) | `build_risk_plan(atr=None)` ValueError 회피 가드 |
| D-5 (regime breakdown) | Group regime D5-1~D5-6 (단위, `tests/test_strategy.py`) + Group I I-1/I-2 (통합) | 본 PR 해소 (Issue #110) — `core.strategy.classify_regime` 신설 + `Position.regime` + `TradeRecord.regime` 전파 (4 regime: TREND_UP/DOWN/RANGE/VOLATILE, 임계 trend 0.5% / vol mult 2.0 / lookback 20) |
| D-8 (risk_pct config 노출) | A1 (default 0.01) / E3 (custom strategy_config 보존) | — |
| D-19 (direction lowercase 통일) | — (헬퍼 자연 제거 → 단위 테스트 X) | `cost.Direction` Literal lowercase + `core.strategy.Direction` StrEnum value 자연 통과 (후속 PR ✅) |
| D-20 (페어당 1 포지션) | B3 (double _open RuntimeError) | — |
| D-21 (tp_hits counter) | B7 (idx >= 3 reject) | TP4 = `_close` 책임 분리 |
| D-22 (aggregator 신규 생성) | C1 (run 별 독립, 간접) | 멀티 호출 검증은 후속 통합 PR |
| D-23 (equity_curve 사후 재계산) | 영역 외 (test_stats 커버) | — |
| D-24 (REVERSE compose_exit) | B6 (단위) | step() 통합은 Stage 1D ETHUSDT sanity 또는 후속 PR (Q1) |
| D-25 (reason 매핑 7 개) | B4 / B5 / B6 / D2 / E1 | TP4 / SL / REVERSE / FORCE_END / sl_distance=0 fallback |
| D-26 (ts_ms 변환) | C1 / D2 (run loop 자연 검증) | `bar.name.value // 10**6` |

### 8.3 sanity → pytest 변환 매핑 (단계 1·2·3 inline 22 → 정식 22)

| sanity # | 정식 케이스 | 비고 |
|---|---|---|
| [1]-[11] (단계 1·2 _check_exits / _close 분기) | B4 / B5 / B7 / B8 통합 | 분기별 합본 |
| [12] [13] clamp | E2 (parametrize 2) | SHORT 사용 (LONG 은 raw<-1 시 fill 음수 무효) |
| [14] step early return | C1 | 동일 |
| [15] _open size_pct | A2 (간접) + B3 (double 가드) | 분할 |
| [16] REVERSE | B6 + signal/test_signal compose_exit | step() 통합은 후속 |
| [17] [18] pause / stopped 가드 | C2 / C3 | 동일 |
| [19] risk_config 자동 | A2 + A3 | 분할 (자동 산출 truth + 불변성 검증) |
| [20] FORCE_END | D2 | 동일 |
| [21] [22] ATR 모드 분기 | C4 | 합본 |

### 8.4 커버리지 갭 (인지된 후속)

- ✅ **해소 (Stage 1D 단계 2, 219d7a0)** — **REVERSE step() 통합** (Q1): synthetic 1m 300 봉 + `ema_periods=(2,3)` + `_close` self-spy 패턴 (mock 외부 의존 X — `engine._close` wrapper 로 호출 인자 캡처) → `test_step_reverse_branch_synthetic_ohlcv` 통합 line coverage. `compose_exit(LONG, [SHORT signals])` True 분기 (engine.py L351-354) 검증.
- ✅ **해소 (Stage 1D 단계 3, c1086a4)** — **멀티 trade end-to-end** (1m 300 봉, 8 단계 라이프사이클): LONG entry → TP1 partial → TP2 partial → BE close → SHORT entry → REVERSE close → LONG entry → FORCE_END close → `test_run_multi_trade_end_to_end_scenario`. self-spy 확장 (`_close` + `_partial_close` 둘 다, D-21 partial idx 추적) + assertion 8 가지 (D-2 reason 매핑 / direction 분포 LONG 4 + SHORT 1 / consec_sl 흐름).
- ✅ **해소 (2026-05-05, Issue #110)** — **regime breakdown** (D-5): `core.strategy.classify_regime(df_4h)` 신설 — 4 regime 분류 (TREND_UP / TREND_DOWN / RANGE / VOLATILE, 임계 잠정 trend_threshold=0.005 / volatility_multiplier=2.0 / volatility_lookback=20). `engine.step()` 8 단계 4H 닫힘 시 호출 + `self._last_regime` 박음 + `_open()` 시점 `Position.regime` 인계. `_close` / `_partial_close` 청산 시 `TradeRecord.regime` 로 전파 (`_force_close_at_end` → `_close` 자연 위임). self-spy on `classify_regime` (module attribute monkeypatch) + `_close` + `_partial_close` (Stage 1D 단계 2/3 패턴 정합) → Group regime 6 단위 + Group I 2 통합 케이스. **PR-3 마일스톤 잔존 2건 전부 해소 (D-1 + D-5) ↔ Phase 2 입구 진입 완료**. VOLATILE 시 신호 평가 skip / regime UI 통계는 별도 후속 (보충 의견 트래커 F1 / F2).
- ✅ **해소 (2026-05-05, Issue #111)** — **min_seed_pct=0.40 16배 증폭 시나리오** (D-1): canonical 시나리오 (balance=1_000 / lev=10 / risk_pct=0.01 / FIXED_PCT sl=40 ROI = 가격 4%) → margin floor 400 USD 발동 → SL 도달 lev_pnl ≈ -0.163968 (= -16.40% balance) → 증폭 ratio ≈ 16.40배 (R 약속 1% 대비). clamp 한도 -0.40 미발동 (순수 증폭, Stage 1B 영역 분리). self-spy on `_open` + `_close` (Stage 1D 단계 2/3 패턴 정합) → `test_min_seed_amplification_floor_scenario` (Group H 신설). §11 D-1 본문 통합 검증 결과 본문화 정합.

---

## 9. 범위 외 (필요 시 후속 PR)

- 차용 코드의 텔레그램 알림 통합 (정용우 영역과 협의)
- adaptive_backtest의 적응형 파라미터 조정 (AI 부분 제외하고도 룰 기반 적응 가능 여부 검토)
- 추매 로직의 Aurora `core.strategy` 통합
- 백테스트 결과 저장 형식 (parquet vs HTML 리포트 등)
- 멀티 페어·멀티 기간 동시 백테스트 (현재는 단일 가정)
- **BotInstance ↔ BacktestEngine 통합 점검 (step 재활용 가능성)** — `BotInstance._run_loop` (PR #57, 라이브 매매 루프) 와 `BacktestEngine.run()` (Stage 1C 단계 3, 백테스트 시뮬 루프) 가 별도 outer 루프. `step(bar_1m, aggregator)` 2-인자 시그니처는 라이브 어댑터 polling 결과 주입에도 자연 호환 (D-22 상태 격리 정합). 단계 3 시점엔 **BacktestEngine = 백테스트 전용 / BotInstance = 라이브 전용** 명확 분리 — step() 재활용은 후속 PR 자연 진화 여지로 보존. 장수 페이스 조율 답변 (2026-05-03) "step() 인터페이스가 자연스럽게 라이브 호환이면 충분" 정합.

---

## 10. 장수 review 요청 사항 (장수 가이드 2026-05-02 반영)

PR-2 description §8 패턴 따라 첫 commit 전 design doc review. 장수가 명시 요구한 3가지 + 우리 추가:

### 핵심 결정점 (장수 명시)

1. **§3.3 R 기반 리스크 vs `core/risk.py` 정합** — 옵션 a(통계만 차용) vs 옵션 b(size 계산까지 통합)
   - 우리 잠정 추천: 옵션 a (PR-3 범위 좁게, core/risk.py 변경 X)
   - 장수 의견 + 추천 검증 부탁

2. **§7 timeframe normalizer spec 적정성**
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
7. 테스트 케이스 spec 범위 (§8) — 추가/제거 의견

→ design doc 골격 완성 후 PR-2 Stage 1A v2 패턴으로 review 요청. 장수 답변 받고 보강 (v2 → v3 같은 형태로 반영 후 첫 commit).

---

## 11. PR-3 description Decisions 미리보기 (D-1 ~ D-8)

PR-3 description 본문에 박을 Decisions 항목 미리보기. 본 design doc 결정사항을 PR description 형식으로 정리 — 장수 review 시 결정 추적 + 머지 후 의사결정 archive 역할.

### D-1 ✅ min_seed_pct=0.40 디폴트 (트레이드오프 명시 + 통합 검증 ✅)

**결정**: `build_risk_plan()` / `calc_position_size()` 호출 시 `min_seed_pct=0.40` 디폴트 유지.

**이유**: floor 발동 시 `risk_pct=0.01` (1R=1%) R 약속이 깨지고 실제 손실 노출이 최대 16% (10x lev, sl=4% 기준) 까지 증폭되는 트레이드오프 명시. 수수료 비율 보호 (margin floor) > R 약속 (risk_pct) 의 의도된 우선순위.

**통합 검증 (2026-05-05, Issue #111)**: PR-3 Stage 1A→1D 단위 검증으로 미진했던 통합 시나리오 본 PR 해소 — `tests/test_engine.py::test_min_seed_amplification_floor_scenario` (Group H 신설). canonical 시나리오 (balance=1_000 / leverage=10 / risk_pct=0.01 / FIXED_PCT sl=40 ROI = 가격 4%):

  - 자연 margin = 1_000 × 0.01 / 0.04 / 10 = 25 USD < floor 임계 400 USD → floor 발동 → margin=400 / notional=4_000 박힘
  - SL 도달 → `cost.apply_costs` 후 `lev_pnl ≈ -0.163968` (= -16.40% balance, slip NORMAL 0.0002 + fee 0.0008 조정)
  - 증폭 ratio `abs(lev_pnl) / risk_pct ≈ 16.40배` (R 약속 1% → 실 손실 16.40% balance)
  - clamp 한도 `-size_pct = -0.40` 미발동 (순수 증폭 검증, Stage 1B clamp 영역 분리)
  - balance: 1_000 → 836.03 USD (단일 SL trade)

  → 의도된 우선순위 정합 verify (margin floor > R 약속). self-spy on `_open` (plan 캡처) + `_close` (TradeRecord 캡처) — Stage 1D 단계 2/3 패턴 정합 (CLAUDE.md self-spy 정책 + mock 0).

**Reference**: §3.3.1 단락 2 (검증 예시 + 의도된 trade-off 본문), §8.4 ✅ 해소 항목, `tests/test_engine.py::test_min_seed_amplification_floor_scenario`.

### D-2 ✅ consec_sl 카운트 정책 (SL 만 ++, TP reset, BE / REVERSE / FORCE_END 유지)

**결정**: 연속 손절 (`consec_sl`) 카운트 분기 — `SL` 만 ++, `TP1`~`TP4` reset, `BE` / `REVERSE` / `FORCE_END` 유지.

**reason 매핑 표** (D-25 reason 7 개 정정 — `engine.py` `_close` / `_partial_close` 본 구현 동기, 2026-05-03 Stage 1C Group 2 단계 2):

| Reason | consec_sl 카운트 | 분류 |
|---|---|---|
| TP1 ~ TP3 | reset (`_partial_close`) | 분할 익절 |
| TP4 | reset (`_close`) | 마지막 익절 |
| SL | ++ (임계 도달 시 `pause_bars` 발동) | 시장 강제 (가격 도달, 봇 수동 판단 X) |
| BE | 유지 | 봇 능동 청산 (trailing 보호 발동) |
| REVERSE | 유지 (잠정) | 봇 능동 청산 (`compose_exit` 트리거) |
| FORCE_END | 유지 | 백테스트 강제 종료 (트레이딩 결과 X) |

**이유**: SL 만 시장 강제, 그 외는 봇 능동 판단 — 본질 다름. SL 만 카운트 ↑. REVERSE 카운트 별개 추적은 후속 옵션 (손실 REVERSE 빈발 시 디버깅 사안).

**차용 출처**: replay_engine L994-1002 카운트 분기 그대로 차용 (`if SL: consec_sl++; elif TP1/TP2: reset, BE/CLOUD_EXIT 는 카운트 유지`). Aurora reason 매핑은 7 개로 확장 (D-25): replay `CLOUD_EXIT` → `REVERSE` (`compose_exit` 트리거 활용), `FORCE_END` 신규 (백테스트 강제 종료, adaptive L376-391 차용).

**1차 안 정정 (2026-05-03)**: 1차 안 "BE / Cloud 도 SL 과 동일 취급 (카운트 ++)" 은 `engine.py` 본 작업 시점 정정 — SL 만 시장 강제이고 BE / REVERSE 는 봇 능동 청산이라 본질 다름. trailing 보호 발동 후 강제 청산이라도 봇이 능동 결정 (trailing 모드 선택 자체가 사용자 정책). 1 차 안 우려 ("trailing 발동 → 작은 손실 누적이 가드 silent 우회") 는 별도 디버깅 옵션 (REVERSE 카운트 별개 추적) 으로 처리.

**Reference**: §6.2 7 단계 `_tick_pause` + `engine.py` `_close` / `_partial_close` 본 구현 (Stage 1C Group 2 단계 2).

### D-3 ✅ fixed_tp_pcts 등간격 분할

**결정**: ATR 모드 / FIXED_PCT 모드 모두 TP 4 단계는 `[tp_min, tp_min + 1/3·range, tp_min + 2/3·range, tp_max]` **등간격** 산출. `tp_allocations` 는 `TpSlConfig` 디폴트 (25/25/25/25) 그대로.

**이유**: 단순 + 예측 가능. 비등간격 (예: 기하급수) 은 사용자 직관 어긋나고 백테스트 비교 어려움. 등간격으로 출발 → 백테스트 결과 따라 조정 가능.

**Reference**: §6.2 10-d, §6.3 두 번째 항목.

### D-4 ✅ ATR 위치 (core/indicators.py 위임) + timeframe (4H 디폴트)

**결정**:
- **위치**: `atr_wilder()` 는 `src/aurora/core/indicators.py` 위임 (장수 명시 2026-05-03).
- **timeframe**: 호출 시 **4H 디폴트** (`df_by_tf['4H']`). 향후 다른 TF 활용 필요 시 `config.atr_timeframe` 매개변수화 검토 (PR-3 후속).

**이유**:
- 위치: ATR 은 표준 지표 → core/indicators 가 자연 위치 (core 영역). engine.py 가 직접 ATR 계산 박으면 SRP 위반.
- timeframe: (추정 사유 — 4H 가 멀티 TF entry signal 평가 주축, ATR 변동성 적정 추정. 실제 검증은 PR-3 후속 작업에서, M-22 config 매개변수화 포함).

**Reference**: §5.3 (core/indicators.py 위임 박스), §6.2 10-a (호출 패턴).

### D-5 ✅ regime breakdown — 본 PR 해소 (2026-05-05, Issue #110)

**결정**: 시장 국면 (regime) 분류 함수 + `Position.regime` 박음 + `TradeRecord.regime` 전파 본 PR 통합 해소. 정책 spec `drafts/D-5-regime-policy-spec.md` 5/5 LGTM 합치 (장수 + WooJae + 정용우 + ChoYoon + Aurora Claude). 장수 ChoYoon 전결 위임 (2026-05-05) — 후속 review 분리 가치 ↓.

**분류 정책** (정책 spec 잠정안):
- **VOLATILE 우선**: `atr_now / atr_avg ≥ 2.0` (TREND 동시 발동 시에도 VOLATILE 채택)
- **TREND_UP**: `(ema50 - ema200) / ema200 ≥ 0.005` (4H, EMA 50/200)
- **TREND_DOWN**: `(ema50 - ema200) / ema200 ≤ -0.005`
- **RANGE**: fallback (격차 미만)
- 가드: `len(df_4h) < 20` / NaN / `atr_avg=0` / `ema200_now=0` → RANGE fallback

**구현**:
- `core/strategy.py` — `Regime` StrEnum (TREND_UP / TREND_DOWN / RANGE / VOLATILE / UNKNOWN) + 임계 모듈 상수 (`TREND_THRESHOLD` / `VOLATILITY_MULTIPLIER` / `VOLATILITY_LOOKBACK`) + `classify_regime(df_4h)` 함수 (~50 LOC). 신설 함수 0건 — 기존 `ema` + `atr_wilder` 활용.
- `engine.py` — `Position.regime: Regime = Regime.UNKNOWN` 디폴트 + `self._last_regime` 박음 + `step()` 8 단계 (`df_by_tf["4H"]` 존재 시) `classify_regime` 호출 + `_open()` 시점 `regime=self._last_regime` 인계. `_close` / `_partial_close` `regime=str(p.regime)` 전파.
- `tests/test_strategy.py` — Group regime 6 단위 (D5-1~D5-6: 4 분류 + sample 부족 + atr=0 가드).
- `tests/test_engine.py` — Group I 2 통합 (I-1: step() 4H 닫힘 시 classify_regime 호출 + Position.regime 박힘 / I-2: multi-trade 5 trade 모두 `_close` + `_partial_close` + `_force_close_at_end` 전파 verify).

**이유**:
- 정책 spec 5/5 LGTM 도달 + ChoYoon 전결 위임 → 분리 머지 비용 < 통합 비용.
- 4 regime + 임계 합의 + `engine.step()` 8 단계 호출 위치 합의 → 본질 ~135-175 LOC 한 묶음 PR 자연.
- VOLATILE 시 신호 평가 skip / regime UI 통계는 별도 정책 결정 비용 ↑ → 본 PR 범위 외 (보충 의견 트래커 F1 / F2).

**Reference**: §8.2 D-5 행, §8.4 ✅ 해소 항목, `drafts/D-5-regime-policy-spec.md`, `core/strategy.py::classify_regime`, `tests/test_strategy.py::test_regime_*`, `tests/test_engine.py::test_step_classifies_regime_on_4h_close` + `test_run_propagates_regime_to_trade_records`.

### D-6 ✅ Stage 1A draft PR 패턴

**결정**: PR-3 는 Stage 1A → 1B → 1C 분할 진행. Stage 1A 시점부터 `feat/backtest-engine` 브랜치 main 위에 **draft PR 즉시 push**. Stage 1A → 1B → 1C 통합 + 회귀 통과 후 Ready for review 전환. **단일 PR 로 통째 머지** (예외: 1A 가 의외로 작으면 별도 짧은 PR 옵션 OK — ChoYoon 본인 판단).

**이유**:
- transparency: 정용우 / 장수가 진행상황 실시간 추적 가능 (별도 알림 X).
- 회고 가치: stage 별 commit history 가 PR 안에 archive — 머지 후 "왜 이렇게 결정" 추적 명확.
- Stage 1A 단독 머지 X 원칙: `tf.py` 만으로는 BacktestEngine 미완성. 통째 PR 으로 머지 (Stage 1C 완료 시점 ready for review 전환).

**Reference**: PR-2 description §8 패턴 차용 + 확장.

### D-7 🚫 추매 (add-on) 별도 후속 PR (M-19)

**결정**: replay_engine 추매 로직 (~43 LOC) 은 PR-3 범위 외. **M-19 후속 PR** 로 처리.

**이유**:
- 추매 로직은 Aurora `core.strategy` 에 유사 기능 있는지 확인 + 신호 정책 결정 필요 (장수 영역) — PR-3 범위 ↑.
- 우선 baseline (단일 진입 + 4 단계 분할 익절) 확정 후 추매는 보강 PR 자연.

**Reference**: §4.1 차용 분류 표 ("🟡 부분 차용 / 보류"), §4.3 LOC 분포.

### D-8 🟡 risk_pct 디폴트 — config 사용자 노출

**결정**: `risk_pct=0.01` (1R=1%) 디폴트 유지하되 **config 매개변수로 사용자 노출**. `BacktestConfig.risk_pct` (사용자 설정) → `build_risk_plan(risk_pct=config.risk_pct)` 전달.

**이유**:
- D-1 트레이드오프 (16% 증폭) 위에서도 `risk_pct` 는 사용자가 조절 가능해야 함 — risk-tolerance 다양성 (보수 0.005 ~ 공격 0.02).
- `min_seed_pct=0.40` floor 는 정책 (config 노출 X) vs `risk_pct` 는 사용자 변수 (config 노출 O).

**Reference**: §3.3.1 단락 3 (호출 패턴), §6.2 10-f.

### D-19 ✅ direction lowercase 통일 (후속 PR)

**결정**: `cost.Direction` Literal `["LONG","SHORT"]` → `["long","short"]` 통일. `core.strategy.Direction` StrEnum value (이미 `"long"`/`"short"`) 자연 통과. `engine._to_record_direction` 헬퍼 + `RecordDir` alias 자연 제거.

**이유**:
- 이중 표준 (core lowercase ↔ backtest UPPERCASE) 격리 비용 > 통일 비용. 헬퍼 1 곳 수정으로는 영구 격리, lowercase 통일은 1 회 변경 후 자연 정합.
- `core.strategy.Direction` 이 source of truth (StrEnum, signal 영역 모두 사용) — backtest 가 따라가는 게 자연.
- 헬퍼 제거 + assertion 단순화 → engine.py / test_engine.py LOC 감소 (-49 LOC).
- 함께 묶음: `_check_exits` `close` 매개변수 제거 (장수+WooJae 합의 2026-05-04) — `del close` 자연 제거 + 시그니처 단순화.

**구현 위치**: `cost.py` L37 (Literal) + L99/L102-103 (assert + 4 분기) / `stats.py` L209 (sign 분기) / `engine.py` (`_to_record_direction` 제거 + 4 호출 정정) / 테스트 lowercase 정합.

**Reference**: §6.3 (구 `_check_exits` `del close` 항목 제거), §8.2 D-19 라인 갱신.

### D-24 ✅ REVERSE 분기 — `compose_exit` 활용 (signal.is_reverse_signal 부재)

**결정**: `step()` 보유 중 분기에서 반대 방향 신호 감지 시 `_close(reason="REVERSE")` 호출. 판정은 `core.signal.compose_exit(current_direction, signals) -> bool` 활용.

**이유**:
- `core.signal` 에 `is_reverse_signal` 같은 별도 함수 부재 — `compose_exit` 가 동등 역할 (`compose_entry` 내부 호출 후 반대 방향 비교, `True` = 청산 신호).
- `step()` 8 단계 (signals 평가) 결과를 1 회 더 활용 — 별도 신호 재평가 X (성능 + 일관성).
- 동일 1m 에 close + open 금지 (D-20 단일 포지션 정책) — REVERSE 청산 후 즉시 `return`. 다음 1m 부터 신규 진입 가능.
- consec_sl 카운트 분기 (D-2 매핑 표): REVERSE 는 봇 능동 청산 → 카운트 유지 (시장 강제 SL 과 본질 다름).

**구현 위치**: `engine.py` `step()` 9 단계 (`compose_entry` 호출 직전, 보유 중 분기에서 early return).

**Reference**: §6.2 step 8 (REVERSE 분기), §11 D-2 매핑 표 (REVERSE = 봇 능동 청산), `core.signal.compose_exit` (signal.py L133-144).

---

## 변경 이력
- 2026-05-02 (저녁): 신설. PR-2 머지 직후 + archive branch 1차 검토 완료 시점. 차용 분류 + 핵심 결정 3건 + 모듈 구조 골격까지.
- 2026-05-02 (저녁 갱신): 장수 design doc 가이드 반영 — §2 데이터 흐름 다이어그램 + 모듈 매핑 표, §3.3 R 기반 옵션 a/b 장단점 + 우리 추천(a), §9 review 요청 항목 정리.
- 2026-05-02 (밤 갱신): 장수 §3.3 옵션 a 동의 + 양 모델 차이 명시 반영 — Aurora 레버리지 기반 size vs Tako Bot R 기반 % per trade. 옵션 a 결정 확정. 정독은 검증 + 추가 발견 차원.
- 2026-05-02 (밤 갱신 #2): §6 timeframe normalizer 상세 설계 확정 — 옵션 A(함수 3개 분리), Strict 검증 정책, 14개 테스트 케이스 spec. design doc 골격 거의 완성.
- 2026-05-02 (밤 갱신 #3): Aurora `core/risk.py` 정독 완료. §3.3 옵션 a 결정 검증 — 더 강해짐. **발견**: Aurora `calc_position_size`가 이미 R 기반 + 풀시드 + 최소 시드 강제(40%) 통합. 장수 명시한 두 모델 차이는 한 함수의 두 모드. Aurora 모델이 차용 코드보다 정교 → 차용 X 부분 추가 (SL/TP 그래디언트, 트레일링 5모드 등). 차용은 통계·시뮬 루프·수수료 모델만.
- 2026-05-03 (오후): §3.1.1 시그니처 truth / §3.3.1 옵션 a 검증 + 신호↔리스크 독립 / §4 정독 확정 + ROI 17.3% / §5 모듈 spec + atr_wilder 위임 / §6 신설 (engine.step() 10단계) / 절번호 시프트 / §3.2 Bybit→Binance 정정.
- 2026-05-03 (저녁, Stage 1C 단계 3): §6.2 8 단계 정정 (`strategy.evaluate` 통합 함수 부재 → `detect_ema_touch + detect_rsi_divergence + evaluate_selectable` 3 함수 합본 명시) / §9 BotInstance ↔ BacktestEngine 책임 경계 환기 추가 / §11 D-24 본문화 (REVERSE 분기 = `compose_exit` 활용).
- 2026-05-03 (밤, Stage 1C Group 3): §8 본문화 — `tests/test_engine.py` 22 함수 / 23 collected (5 그룹 A~E + D-N 매핑 + sanity → pytest 변환 + 커버리지 갭). pytest baseline 385 → 408.
- 2026-05-04 (Stage 1D 마무리, 단계 4): §8.4 커버리지 갭 갱신 — REVERSE step() 통합 (단계 2 219d7a0) + 멀티 trade end-to-end (단계 3 c1086a4) 2 건 ✅ 해소, regime breakdown (D-5) + min_seed_pct 16% 증폭 (D-1) 2 건 🟡 잔존 (별도 Issue 예정). §6.3 `_check_exits` `del close` 환기 추가 (D-19 direction lowercase 통일 후속 PR 함께 검토 — 장수+WooJae 합의). pytest baseline 408 → 472 (+64, main 머지 신규 테스트 흡수 + 단계 2/3 회귀 +2).
- 2026-05-04+ (D-19 direction lowercase 통일 후속 PR): §6.3 `_check_exits` `del close` 항목 제거 (close 매개변수 자체 제거 — 장수+WooJae 합의 (a) 채택). §8.2 D-19 라인 갱신 (RecordDir Literal → cost.Direction Literal lowercase + StrEnum value 자연 통과). §11 D-19 신규 본문화 (Stage 1C·1D 패턴 정합). pytest baseline 504 → 502 (-2, `_to_record_direction` 헬퍼 테스트 자연 제거). PR-3 BacktestEngine Stage 1A→1D 전체 완료 + lowercase 통일 마일스톤.
- 2026-05-05 (D-1 min_seed_pct 16배 증폭 통합 검증, Issue #111): §11 D-1 본문 통합 검증 결과 본문화 (✅ 해소) — canonical 시나리오 (balance=1_000 / lev=10 / sl 가격 4%) margin floor 400 USD 발동 후 lev_pnl ≈ -0.163968 (16.40배 증폭) 정합. §8.4 잔존 항목 D-1 ✅ 해소 처리 (D-5 regime breakdown 별도 진행). §8.2 D-1 매핑 표 갱신 (Group H 신설 → H1 통합 검증 본 PR 해소). `tests/test_engine.py::test_min_seed_amplification_floor_scenario` self-spy on `_open` + `_close` (Stage 1D 단계 2/3 정합). pytest baseline 525 → 526 (+1, main HEAD d69722e 위 PR #117~#121 11 commits 자연 흡수). PR-3 마일스톤 잔존 2건 중 D-1 ✅ 해소 ↔ Phase 2 입구 진입.
- 2026-05-05 (D-5 regime breakdown 본 구현, Issue #110, 장수 ChoYoon 전결 위임): §11 D-5 본문 재작성 (✅ 해소) — `core.strategy.classify_regime(df_4h)` 신설 + `Regime` StrEnum + 임계 모듈 상수 (trend_threshold=0.005 / volatility_multiplier=2.0 / volatility_lookback=20) + `Position.regime` 필드 + `TradeRecord.regime` 전파 chain. 분류 우선순위 VOLATILE > TREND > RANGE (정책 spec drafts/D-5-regime-policy-spec.md 5/5 LGTM 합치). §8.2 D-5 행 신설 (Group regime 6 단위 + Group I 2 통합 매핑). §8.4 잔존 항목 D-5 ✅ 해소 처리 — **PR-3 마일스톤 잔존 2건 전부 해소 (D-1 + D-5) ↔ Phase 2 입구 진입 완료**. self-spy on `classify_regime` (module attribute monkeypatch) + `_close` + `_partial_close` (Stage 1D 단계 2/3 패턴 정합). 신설 함수 0건 (기존 `ema` + `atr_wilder` 활용). pytest baseline 526 → 534 (+8, 단위 6 + 통합 2). VOLATILE 시 신호 평가 skip / regime UI 통계는 별도 후속 (보충 의견 트래커 F1 / F2).
- 다음 보강 예정: §10 review 발송 준비 (시프트 후), §11 PR description Decisions 미리보기 (D-9~D-18 / D-20~D-23 누락 보충 별도 후속 트래커), 동기화 매트릭스 별도 파일 신설, VOLATILE 신호 평가 skip + regime UI 통계 후속 트래커.
