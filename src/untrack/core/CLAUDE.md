# Core 모듈 — 장수 담당 영역

## 책임 범위
이 폴더는 봇의 **두뇌**다. 외부 의존(거래소·DB·UI) 없이 순수 함수로 구성.
입력: OHLCV DataFrame + 사용자 설정 → 출력: 신호 / 포지션 사이즈 / SL·TP 가격.

## 파일별 역할
- `indicators.py` — EMA, RSI, RSI Divergence (4종), BB, MA Cross, Harmonic, Ichimoku 계산 함수들 + 피벗 헬퍼(`pivot_low`, `pivot_high`)
- `strategy.py` — 진입 룰 (EMA 터치, Divergence 단독, Select 지표 OR 조합)
- `signal.py` — Fixed + Selectable 지표 결과를 합쳐서 최종 진입/청산 신호 산출
- `risk.py` — 포지션 사이즈, SL/TP 거리 계산, 트레일링 로직, 레버리지별 SL 캡

## 주요 스펙
- **Fixed 지표**: EMA 200/480 (1H~1W), RSI Divergence (1H)
- **Selectable**: Bollinger / MA Cross / Harmonic / Ichimoku (사용자 on/off)
- **단일 신호 진입 가능** (OR 방식)
- **Tako TP/SL**: ATR/Fixed%/Manual% 3모드 + 4단계 분할 + 5가지 트레일링

## RSI 사용 정책
- **RSI 수치 자체는 진입 신호 X** (과매수/과매도 룰 사용 안 함)
- **RSI Divergence 만 진입 신호** (TradingView 표준, 피벗 기반)
- 4 가지 종류 검출:
  - `regular_bull` — 가격 LL + RSI HL → 강세 반전 (롱 진입)
  - `hidden_bull` — 가격 HL + RSI LL → 상승 추세 지속 (롱 추가)
  - `regular_bear` — 가격 HH + RSI LH → 약세 반전 (숏 진입)
  - `hidden_bear` — 가격 LH + RSI HH → 하락 추세 지속 (숏 추가)
- 파라미터: `lb_left=5`, `lb_right=5`, `range_lower=5`, `range_upper=60`

## SL/TP 룰 (레버리지 기반, 구간 분기 + 그래디언트)

```
10x ~ 37x (보수):
    SL(L)  = 2 + (L - 10) / 27          # 10x→2%, 37x→3%
    TP min = SL + 0.8                    # 10x→2.8%, 37x→3.8%
    TP max = SL + 1.8                    # 10x→3.8%, 37x→4.8%

38x ~ 50x (공격):
    SL(L)  = 0.08 × L                    # 50x→4%
    TP min = SL + 2.0                    # 50x→6%
    TP max = SL + 3.0                    # 50x→7%
```

근거:
- 저배율(10~37x): 작은 가격 변동에도 의미 있는 거래 → SL/TP 모두 작게 (보수, 빈도 누적)
- 고배율(38~50x): 풀시드 수수료가 시드 많이 갉아먹음 (50x = 5.5%) → SL/TP 크게 (공격)
- 37x→38x: 보수→공격 전환 경계 (의도된 점프)

주요 매핑:
- 10x → SL 2.00% / TP 2.8~3.8%
- 37x → SL 3.00% / TP 3.8~4.8%
- 38x → SL 3.04% / TP 5.04~6.04%
- 50x → SL 4.00% / TP 6~7%

이 값들은 출발점. 테스트하면서 조정 가능.

## 외부 의존
- 입력: pandas DataFrame (OHLCV 표준)
- 출력: dataclass 또는 dict (Signal, RiskPlan 등)
- **금지**: 거래소 API, AI API, 파일 IO, 네트워크 호출

## 테스트
- `tests/test_indicators.py`, `test_strategy.py`, `test_signal.py`, `test_risk.py`
- 모든 함수에 단위 테스트 + 합성 데이터 회귀 테스트
