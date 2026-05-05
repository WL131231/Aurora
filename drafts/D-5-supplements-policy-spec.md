# D-5 보충 의견 F1 + F3 — 정책 명시 + 미리보기 spec

> **작업 영역**: `feat/D-5-supplements-F1-F3` 브랜치 (main HEAD `07e8921` 분기)
> **선행 PR**: PR #124 (D-5 regime breakdown ✅ 해소) — squash `72bec3f` (2026-05-05)
> **본 PR 범위**: F1 (VOLATILE 진입 skip) + F3 (임계 매개변수화) 묶음 — `RegimeConfig` dataclass 통합
> **베이스라인**: pytest 539 / ruff clean
> **담당자**: ChoYoon (장수 전결 위임 정합)

---

## 0. Q 6건 추천 채택 정합 (사용자 결정 — 2026-05-05)

| Q | 결정 | 근거 |
|---|------|------|
| Q1 | **F1 옵션 C** (진입 가드) | `step()` 9 단계 `decision.enter` 산출 후 `_open` 직전 VOLATILE skip — 신호 평가/risk_plan 자연 진행, Aurora 가드 패턴 정합 |
| Q2 | **F3 옵션 B** (BacktestConfig 노출) | D-1 `risk_config` 패턴 정합 — 사용자 조정 가능, 디폴트는 모듈 상수 보존 |
| Q3 | **묶음 PR** (RegimeConfig dataclass 통합) | F1 skip flag + F3 임계 3 개 한 dataclass — review 효율 ↑, 정책 source of truth 단일화 |
| Q4 | **즉시 진입** | 사용자 결정 (2026-05-05) |
| Q5 | **`core/strategy.py` 위치** | `classify_regime` 함수와 같은 모듈 — 정책 source of truth, 외부 import path 단일 |
| Q6 | **F1 디폴트 `skip_on_volatile=False`** | 현 동작 보존 — VOLATILE 시 진입 허용 (기존 #124 유지), 옵트인 정합 |

---

## [A] 체크 포인트 — truth grep (2026-05-05 fetch)

### A-1. `src/aurora/core/strategy.py` (D-5 본구현 영역)

| 위치 | 현재 본문 | 변경 잠정 |
|------|-----------|-----------|
| L978-994 | `# D-5 Regime breakdown` 헤더 + `Regime` StrEnum 5 멤버 | 유지 |
| **L997-1000** | 모듈 상수 `TREND_THRESHOLD = 0.005` / `VOLATILITY_MULTIPLIER = 2.0` / `VOLATILITY_LOOKBACK = 20` | **유지** (RegimeConfig 디폴트 값 source of truth) — 단계 1 위에 `RegimeConfig` dataclass 신설 위치 후보 |
| **L1003** | `def classify_regime(df_4h: pd.DataFrame) -> Regime:` | **변경**: `def classify_regime(df_4h: pd.DataFrame, regime_config: RegimeConfig \| None = None) -> Regime:` |
| L1004-1025 | docstring (분류 우선순위 VOLATILE > TREND > RANGE) | docstring `Args` 항목 추가 (regime_config) |
| L1026-1028 | `if len(df_4h) < VOLATILITY_LOOKBACK: return Regime.RANGE` | **변경**: `cfg = regime_config or RegimeConfig()` 로 분기 + `cfg.volatility_lookback` 참조 |
| L1043-1047 | `VOLATILITY_MULTIPLIER` 직접 참조 | **변경**: `cfg.volatility_multiplier` 참조 |
| L1053-1057 | `TREND_THRESHOLD` 직접 참조 | **변경**: `cfg.trend_threshold` 참조 |

**RegimeConfig 신설 위치** — L1001 (모듈 상수 직후, `def classify_regime` 직전). 4 필드 (trend_threshold / volatility_multiplier / volatility_lookback / **skip_on_volatile**). `slots=True` (Aurora 패턴 정합).

### A-2. `src/aurora/backtest/engine.py`

| 위치 | 현재 본문 | 변경 잠정 |
|------|-----------|-----------|
| L40-50 | `from aurora.core.strategy import` (classify_regime 등) | **변경**: `RegimeConfig` 추가 import |
| L60-105 | `@dataclass(slots=True) class BacktestConfig` (12 필드) | **변경**: L105 마지막에 `regime_config: RegimeConfig \| None = None` 필드 추가 (`strategy_config` 직후, D-1 `risk_config` 패턴 정합) — docstring `Attributes` 항목 추가 |
| L177-230 | `__init__` (`_risk_config` / `_strategy_config` / `_last_regime` 산출) | **변경**: L206 `_strategy_config` 산출 직후 `self._regime_config: RegimeConfig = config.regime_config or RegimeConfig()` 추가 (D-1 패턴 정합, config mutate X) |
| **L355-356** | `if "4H" in df_by_tf: self._last_regime = classify_regime(df_by_tf["4H"])` | **변경**: `classify_regime(df_by_tf["4H"], regime_config=self._regime_config)` |
| **L370-398** | 9 단계 진입 가드 (`stopped` / `pause_bars` / `decision.enter`) → 10 단계 ATR 가드 → 11 단계 `_open` | **추가**: L374-375 `if not decision.enter` 가드 직후, L377 ATR 가드 직전에 **VOLATILE skip 가드 신설**:<br>`# 9b. F1 보충 — VOLATILE 시 진입 skip (옵트인, RegimeConfig.skip_on_volatile)`<br>`if self._regime_config.skip_on_volatile and self._last_regime == Regime.VOLATILE:`<br>`    return` |

### A-3. `tests/test_strategy.py`

| 위치 | 현재 Group | 추가 |
|------|-----------|------|
| L1284 | `D-5 classify_regime` Group regime D5-1~D5-6 (4 분류 + sample 부족 + atr=0 가드) | **신설 Group regime D5-7~D5-9** (F3 매개변수화 검증) — L1359 직후 추가 |

### A-4. `tests/test_engine.py`

| 위치 | 현재 Group | 추가 |
|------|-----------|------|
| L741 | Group I — D-5 통합 (I-1: classify_regime 호출 / I-2: TradeRecord.regime 전파) | **신설 Group J — F1 VOLATILE-skip 통합** (J-1~J-3) — Group I 직후 추가 (Group I 끝 라인 번호 본구현 시 재확인) |

### A-5. `src/aurora/backtest/DESIGN.md`

| 위치 | 현재 본문 | 갱신 |
|------|-----------|------|
| L708 | §8.2 D-5 행 (Group I I-1/I-2) | **변경**: Group J 추가 매핑 ("F1 VOLATILE-skip 통합 J-1~J-3") |
| L848-870 | §11 D-5 ✅ 해소 본문 | **추가**: §11 D-5 보충 항 신설 (F1 + F3 묶음 본구현) — 별도 sub-항목 (`### D-5 보충 ✅ F1 + F3 — 본 PR 해소`) |
| L946 | §12 변경 이력 | **신설 entry**: 2026-05-XX D-5 보충 의견 F1 + F3 묶음 본 PR 해소 entry |

---

## [B] 변경 잠정 표 — 파일별 LOC 추정

| 파일 | 추정 LOC | 본질 |
|------|---------|------|
| `src/aurora/core/strategy.py` | +35 / -8 | RegimeConfig dataclass 신설 (~25 LOC) + classify_regime 시그니처 + cfg 참조 변경 (~10 LOC) + docstring Args |
| `src/aurora/backtest/engine.py` | +15 / -2 | import 추가 + BacktestConfig 필드 + __init__ 산출 + step() 8 단계 인자 + 9b VOLATILE skip 가드 |
| `tests/test_strategy.py` | +50 / 0 | Group D5-7~D5-9 신설 (3 케이스 × ~17 LOC, df 합성 + RegimeConfig 인자) |
| `tests/test_engine.py` | +60 / 0 | Group J J-1~J-3 신설 (3 케이스 × ~20 LOC, self-spy on classify_regime + skip flag toggle) |
| `src/aurora/backtest/DESIGN.md` | +25 / -1 | §11 D-5 보충 항 신설 + §8.2 매핑 갱신 + §12 변경 이력 entry |
| **합계** | **~+185 / -11** | drafts spec 정합 (~100-155 본질) — 테스트 더 빈번 추가로 LOC 약간 ↑ |

---

## [C] 단계 분할 + commit 패턴 — 옵션 A 4 단계 (D-5 #124 패턴 정합)

### 단계 1: `core/strategy.py` RegimeConfig + classify_regime 시그니처 변경
```
feat(strategy): D-5 보충 RegimeConfig dataclass + classify_regime 매개변수화 (#XXX)
```
- L1001 위치에 `@dataclass(slots=True) class RegimeConfig:` 신설 (4 필드, 디폴트 = 모듈 상수)
- `def classify_regime(df_4h, regime_config: RegimeConfig | None = None)` 시그니처
- 본문 4 곳 (`VOLATILITY_LOOKBACK` / `VOLATILITY_MULTIPLIER` / `TREND_THRESHOLD` ×2) → `cfg.*` 참조 변경
- 모듈 상수 4 개 보존 (배포 호환성, RegimeConfig 디폴트 값 source)
- `tests/test_strategy.py` Group D5-7~D5-9 (F3 매개변수화 sanity 3 건) 동시 추가

### 단계 2: `backtest/engine.py` BacktestConfig.regime_config + 인자 전달
```
feat(engine): D-5 보충 BacktestConfig.regime_config + step() 8 단계 인자 전달 (#XXX)
```
- import 갱신 (`RegimeConfig` 추가)
- `BacktestConfig` 필드 `regime_config: RegimeConfig | None = None` 추가 + docstring Attributes
- `__init__` 산출 `self._regime_config = config.regime_config or RegimeConfig()` (D-1 risk_config 패턴 정합)
- `step()` 8 단계 (L356) `classify_regime(df_4h, regime_config=self._regime_config)` 인자 전달

### 단계 3: `engine.py` 9b VOLATILE skip 가드 + Group J 통합 테스트
```
feat(engine): D-5 보충 F1 VOLATILE 진입 skip 가드 + Group J 통합 (#XXX)
```
- `step()` 9 단계 (`decision.enter` 분기 직후) **9b 가드 신설** — `if self._regime_config.skip_on_volatile and self._last_regime == Regime.VOLATILE: return`
- `tests/test_engine.py` Group J J-1~J-3 신설:
  - **J-1** `test_step_skips_entry_on_volatile_when_enabled` — `skip_on_volatile=True` + spy 강제 VOLATILE → `_open` 미호출 + position None
  - **J-2** `test_step_enters_on_volatile_when_disabled` — `skip_on_volatile=False` (디폴트) + spy 강제 VOLATILE → `_open` 호출 정상
  - **J-3** `test_step_enters_on_trend_up_with_skip_enabled` — `skip_on_volatile=True` + spy 강제 TREND_UP → `_open` 호출 정상 (skip 가드는 VOLATILE 한정)

### 단계 4: `DESIGN.md` 보충 본문화 + 변경 이력
```
docs(backtest): DESIGN §11 D-5 보충 ✅ F1 + F3 + §8.2/§12 정합 (#XXX)
```
- §11 D-5 보충 항 신설 (`### D-5 보충 ✅ F1 + F3 — 본 PR 해소`) — RegimeConfig 도입 + skip_on_volatile 옵트인 + F1 가드 흐름 (9b 위치) + F3 임계 매개변수화 + 디폴트 보존 (현 동작 호환)
- §8.2 D-5 행 갱신 — Group J 추가 매핑
- §12 변경 이력 신규 entry — 2026-05-XX D-5 보충 묶음 PR 해소

---

## [D] sanity 케이스 spec — 합계 6 신규 (pytest 539 → ~545)

### F3 매개변수화 (Group D5-7~D5-9, `tests/test_strategy.py`)

| 케이스 | 검증 |
|--------|------|
| **D5-7** `test_classify_regime_custom_threshold` | `RegimeConfig(trend_threshold=0.01)` 인자 → 디폴트 0.005 로는 TREND_UP 분류되는 df 가 RANGE 로 분류 (임계 ↑ 효과 검증) |
| **D5-8** `test_classify_regime_custom_volatility_multiplier` | `RegimeConfig(volatility_multiplier=5.0)` 인자 → 디폴트 2.0 으로는 VOLATILE 분류되는 df 가 TREND/RANGE 로 분류 (변동성 임계 ↑ 효과) |
| **D5-9** `test_classify_regime_default_module_constants` | `regime_config=None` 호출 시 모듈 상수 디폴트 (TREND_THRESHOLD=0.005 등) 그대로 사용 — Group D5-1~D5-6 정합 verify (현 동작 보존) |

### F1 VOLATILE skip 가드 (Group J-1~J-3, `tests/test_engine.py`)

| 케이스 | 검증 |
|--------|------|
| **J-1** `test_step_skips_entry_on_volatile_when_enabled` | `BacktestConfig(regime_config=RegimeConfig(skip_on_volatile=True))` + self-spy on `classify_regime` 강제 VOLATILE 반환 + 진입 신호 강제 산출 → `_open` 미호출 + `engine.position is None` + `engine._last_regime == Regime.VOLATILE` |
| **J-2** `test_step_enters_on_volatile_when_disabled` | `skip_on_volatile=False` (디폴트) + 동일 spy + 진입 신호 → `_open` 호출 + position 생성 + `position.regime == Regime.VOLATILE` (기존 D-5 #124 동작 회귀 X 보장) |
| **J-3** `test_step_enters_on_trend_up_with_skip_enabled` | `skip_on_volatile=True` + spy 강제 TREND_UP + 진입 신호 → `_open` 호출 (skip 가드는 VOLATILE 한정 verify) |

**테스트 패턴**: D-5 Group I 와 동일 self-spy on `aurora.backtest.engine.classify_regime` (module attribute monkeypatch) + signal stub (compose_entry 결과 강제) + position 산출 wrapper. Aurora 정책 mock 0 / self-spy 정합.

---

## [E] DESIGN.md 갱신 항목

1. **§11 D-5 보충 항 신설** (단계 4):
   - 위치: §11 L848 D-5 ✅ 해소 본문 직후 sub-항 (`### D-5 보충 ✅ F1 + F3 — 본 PR 해소`)
   - 본문: 결정 / 변경 본질 (RegimeConfig dataclass + skip_on_volatile 옵트인) / F1 가드 흐름 (9b 위치) / F3 매개변수화 / 디폴트 보존 / sanity 매핑

2. **§8.2 매핑 표 갱신**:
   - L708 D-5 행 → Group J 매핑 추가

3. **§12 변경 이력 신규 entry**:
   - 2026-05-XX 머지 시점 entry — RegimeConfig 도입 + F1 옵트인 + F3 매개변수화 + pytest baseline 539 → ~545 정합

---

## [F] 미반영 / 후속 처리

| 항목 | 처리 |
|------|------|
| **F2** (정용우 영역 — regime UI 통계) | 본 PR 범위 외 — 별도 PR (interfaces/api.py + ui/) — Phase 2 진입 후 통계 dashboard 항목으로 |
| **BotInstance step() 재활용** | Phase 2 실거래 path 검증 후 — 본 PR 범위 외 (백테스트 vs 실거래 step() 통합 후속 트래커) |
| **VOLATILE skip 백테스트 결과 회고** | 본 PR 머지 후 별도 후속 트래커 — `skip_on_volatile=True` 시뮬 vs 디폴트 시뮬 결과 비교 (실데이터 검증, ChoYoon 백테스트 영역) |
| **모듈 상수 deprecation** | 본 PR 범위 외 — 모듈 상수 4 개 (TREND_THRESHOLD 등) 보존 (RegimeConfig 디폴트 source). 외부 import path 영향 검토 후 별도 트래커에서 결정 |

---

## [G] Q 항목 (사용자 confirm 부탁)

### G-1. RegimeConfig 디폴트 값
**잠정안**: 모듈 상수 그대로 (trend_threshold=0.005 / volatility_multiplier=2.0 / volatility_lookback=20 / skip_on_volatile=False)
**대안**: 디폴트 변경 가능성 (예: skip_on_volatile=True 가 더 안전?) — D-5 #124 합의 (현 동작 보존 = False 디폴트) 그대로 유지 추천

### G-2. F1 가드 위치 — 9b (옵션 C)
**잠정안**: `step()` 9 단계 `decision.enter` 산출 직후, 10 단계 ATR 가드 직전 (9b 신설)
**대안**:
- **A**. `classify_regime` 호출 직후 (8 단계 직후) skip — but 신호 평가 + risk_plan 산출 자체를 차단해서 비효율
- **C** (잠정안). 진입 직전 가드 — 신호 평가 / risk_plan 자연 진행, 가드 명시적 (Aurora 패턴 정합)

### G-3. 단위 테스트 Group 명명 — Group J (Group I 직후)
**잠정안**: Group J — F1 VOLATILE-skip 통합 (J-1~J-3, `tests/test_engine.py`)
**대안**: Group I-3~I-5 (Group I 확장) — but Group I 본 D-5 #124 분리 가치 ↓, F1 보충 별도 group 자연

### G-4. 시그니처 — `regime_config: RegimeConfig | None = None`
**잠정안**: 디폴트 None + 함수 내 `cfg = regime_config or RegimeConfig()` 분기 (배포 호환성, 외부 호출자 변경 X)
**대안**: 디폴트 `RegimeConfig()` 직접 박음 — but mutable default 안티패턴 + 인스턴스 매번 생성 (성능 미세 영향)

### G-5. PR description 양식
**잠정안**: D-5 #124 본 PR description 패턴 정합 (Decisions / Changes / Tests / Why / Reference 섹션). 본 PR 추가로 "F1+F3 묶음 본 PR 해소" 명시.

### G-6. 머지 전 review 발송 대상
**잠정안**: 장수 ChoYoon 전결 위임 정합 (D-5 #124 패턴 정합) — 장수 + 정용우 + WooJae + Aurora Claude 4 명 LGTM 우선, ChoYoon 직접 머지.
**대안**: 장수만 LGTM 후 즉시 머지 (D-5 #124 정합) — 보충 PR 단계라 review 비중 ↓.

---

## 진행 패턴 (D-5 #124 정합)

1. **본 turn**: 새 브랜치 생성 + 미리보기 spec 작성 ✅
2. **다음 turn**: 윈도우 검토 → Q 항목 G-1~G-6 confirm
3. **단계별 Edit + commit + push** — 4-commit chain 잠정
4. **회귀 verify**: ruff + pytest baseline 539 → ~545 유지
5. **PR description Phase A 드라프트** → Aurora Claude PR 생성 (Phase B)
6. **Phase D 알림** → review → 머지 (장수 ChoYoon 전결 위임 정합 가능성, D-5 #124 패턴 반복 추정)
