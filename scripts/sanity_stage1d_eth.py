"""ETHUSDT_1m_sample.parquet 통합 회귀 sanity — Stage 1D 단계 1.

PR-2 산출 ETHUSDT 1m 1 주치 (10080 봉) 입력으로 ``BacktestEngine.run()`` 결정론
end-to-end 시뮬 + ``stats.compute_session_stats()`` 결과표 출력. 같은 parquet +
같은 config → 같은 결과 보장 (Q6 결정론 검증).

본 스크립트 실행 결과는 PR description body 박을 markdown 표 형식 stdout 출력 +
engine 내부 상태 (balance / peak / consec_sl / stopped) trace.

장수 권고 (2026-05-04) 정합: pytest 정식 회귀 X — 매 pytest 실행 시 ETHUSDT
10080 봉 처리 비용 ↑ → 별도 ``scripts/`` 분리. tests/test_engine.py 단위 케이스
+ 본 스크립트 통합 sanity 의 두 갈래 회귀 보호.

담당: ChoYoon
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from aurora.backtest.engine import BacktestConfig, BacktestEngine
from aurora.backtest.stats import compute_session_stats

# 입력 parquet — workspace root 기준 data/ 위치
PARQUET_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "ETHUSDT_1m_sample.parquet"
)


def load_eth_1m() -> pd.DataFrame:
    """ETHUSDT 1m parquet 로드 + RangeIndex → DatetimeIndex 변환 (D-26 정합).

    PR-2 산출 parquet 는 ``timestamp`` int64 ms column + RangeIndex 형태 →
    ``BacktestEngine.run()`` 입력 정합 위해 DatetimeIndex 변환 필요. 진입점
    1 줄. ``bar.name.value // 10**6`` ms epoch 자연 호출 가능 형태.

    Returns:
        DatetimeIndex (ms epoch 변환) + OHLCV 컬럼 DataFrame.
    """
    df = pd.read_parquet(PARQUET_PATH)
    df.index = pd.to_datetime(df["timestamp"], unit="ms")
    return df.drop(columns=["timestamp"])


def run_sanity() -> None:
    """Stage 1D 단계 1 sanity — 결과표 stdout 출력."""
    df = load_eth_1m()

    config = BacktestConfig(
        symbol="ETHUSDT",                          # 1 순위 페어 (CLAUDE.md)
        initial_capital=10_000.0,
        leverage=10,
        risk_pct=0.01,
    )
    engine = BacktestEngine(config)
    trades = engine.run(df)
    stats = compute_session_stats(trades)

    final_balance = config.initial_capital * (1.0 + stats.total_pnl)

    print(f"# ETHUSDT 1m sanity — {len(df):,} 봉 ({len(df) / 1440:.1f}일치)")
    print(f"# 기간: {df.index[0]} ~ {df.index[-1]} (KST 변환 X — UTC 기준)")
    print(f"# 진입: {config.symbol} / lev={config.leverage}x / "
          f"risk={config.risk_pct * 100:.1f}% / seed=${config.initial_capital:,.0f}")
    print()

    print("## sanity 결과 (Stage 1D 단계 1, 결정론적)")
    print()
    print("| metric | value |")
    print("|---|---|")
    print(f"| total_trades | {stats.total_trades} |")
    print(f"| win_rate | {stats.win_rate * 100:.2f}% |")
    print(f"| mdd | {stats.mdd * 100:.2f}% |")
    print(f"| sharpe | {stats.sharpe:.4f} |")
    print(f"| expectancy (R) | {stats.expectancy:.4f} |")
    print(f"| avg_r_multiple | {stats.expectancy:.4f} |")
    print(f"| fee_paid | {stats.fee_paid:.4f} (Stage 1B placeholder) |")
    print(f"| final_balance | ${final_balance:,.2f} (initial ${config.initial_capital:,.0f}) |")
    print()

    print("## engine 내부 상태 trace")
    print(f"# balance       = ${engine.balance:,.2f}")
    print(f"# peak_balance  = ${engine.peak_balance:,.2f}")
    print(f"# consec_sl     = {engine.consec_sl}")
    print(f"# pause_bars    = {engine.pause_bars}")
    print(f"# stopped (MDD) = {engine.stopped}")
    print(f"# trades 누적   = {len(engine.trades)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    run_sanity()
