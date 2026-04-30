# Core 모듈 — 장수 담당 영역

## 책임 범위
이 폴더는 봇의 **두뇌**다. 외부 의존(거래소·DB·UI) 없이 순수 함수로 구성.
입력: OHLCV DataFrame + 사용자 설정 → 출력: 신호 / 포지션 사이즈 / SL·TP 가격.

## 파일별 역할
- `indicators.py` — EMA, RSI, RSI Divergence, BB, MA Cross, Harmonic, Ichimoku 계산 함수들
- `strategy.py` — 진입 룰 (EMA 터치, Divergence 단독, Select 지표 OR 조합)
- `signal.py` — Fixed + Selectable 지표 결과를 합쳐서 최종 진입/청산 신호 산출
- `risk.py` — 포지션 사이즈, SL/TP 거리 계산, 트레일링 로직, 레버리지별 SL 캡

## 주요 스펙
- **Fixed 지표**: EMA 200/480 (1H~1W), RSI Divergence (1H)
- **Selectable**: Bollinger / MA Cross / Harmonic / Ichimoku (사용자 on/off)
- **단일 신호 진입 가능** (OR 방식)
- **Tako TP/SL**: ATR/Fixed%/Manual% 3모드 + 4단계 분할 + 5가지 트레일링

## SL/TP 룰 (레버리지 기반)

```
SL(L) = max(3.0, 0.08 × L)        # 0.08×L = 수수료 기반, 3% = 최소 하한
TP(L) = SL(L) + 2.0 ~ 3.0          # SL 위 +2~3% 가 진짜 순수익 영역
```

근거: 레버리지 높을수록 풀시드 수수료가 시드를 더 갉아먹음 (10x = 1.1% / 50x = 5.5%).
50x에서 4% 가격 변동이 break-even, 그 위 +2~3%가 TP. 작은 레버리지에선 3% 하한 (whipsaw 방지).

매핑:
- 10x ~ 37x: SL 3.0% / TP 5~6% (하한 활성화)
- 38x ~ 50x: 공식 활성화, 점진 증가
- 50x: SL 4.0% / TP 6~7%

이 값들은 출발점. 테스트하면서 조정 가능.

## 외부 의존
- 입력: pandas DataFrame (OHLCV 표준)
- 출력: dataclass 또는 dict (Signal, RiskPlan 등)
- **금지**: 거래소 API, AI API, 파일 IO, 네트워크 호출

## 테스트
- `tests/test_indicators.py`, `test_strategy.py`, `test_signal.py`, `test_risk.py`
- 모든 함수에 단위 테스트 + 합성 데이터 회귀 테스트
