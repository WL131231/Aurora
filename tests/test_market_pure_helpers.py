"""market 순수 헬퍼 단위 테스트 — ratios_aggregator + coinalyze + exchange symbol_for.

_ratio_to_long_pct / _interval_to_seconds / _coin_from_symbol / symbol_for (거래소별)
외부 네트워크/API 호출 없음 — 합성 입력만 사용.

담당: 정용우
"""

from __future__ import annotations

import pytest

from aurora.market.coinalyze import _coin_from_symbol, _interval_to_seconds
from aurora.market.ratios_aggregator import _ratio_to_long_pct

# ── _ratio_to_long_pct ────────────────────────────────────────────────


def test_ratio_to_long_pct_none_returns_none():
    """None 입력 → None."""
    assert _ratio_to_long_pct(None) is None


def test_ratio_to_long_pct_zero_returns_none():
    """ratio=0 → None (≤0 분기)."""
    assert _ratio_to_long_pct(0.0) is None


def test_ratio_to_long_pct_negative_returns_none():
    """ratio<0 → None (≤0 분기)."""
    assert _ratio_to_long_pct(-1.5) is None


def test_ratio_to_long_pct_one_equals_half():
    """ratio=1 (1:1) → long_pct=0.5."""
    assert _ratio_to_long_pct(1.0) == pytest.approx(0.5)


def test_ratio_to_long_pct_formula():
    """ratio=3 → 3/(1+3)=0.75."""
    assert _ratio_to_long_pct(3.0) == pytest.approx(0.75)


def test_ratio_to_long_pct_small_ratio():
    """ratio=0.5 → 0.5/1.5 ≈ 0.3333."""
    assert _ratio_to_long_pct(0.5) == pytest.approx(1 / 3)


def test_ratio_to_long_pct_large_ratio_approaches_one():
    """ratio=9999 → long_pct ≈ 1.0 (숏 거의 없음)."""
    result = _ratio_to_long_pct(9999.0)
    assert result is not None
    assert result == pytest.approx(9999 / 10000, rel=1e-4)
    assert result < 1.0


def test_ratio_to_long_pct_tiny_positive_ratio():
    """ratio=0.0001 → long_pct ≈ 0 (롱 거의 없음)."""
    result = _ratio_to_long_pct(0.0001)
    assert result is not None
    assert result == pytest.approx(0.0001 / 1.0001, rel=1e-4)


# ── _interval_to_seconds ─────────────────────────────────────────────


@pytest.mark.parametrize("interval,expected", [
    ("1min", 60),
    ("5min", 300),
    ("15min", 900),
    ("30min", 1800),
    ("1hour", 3600),
    ("2hour", 7200),
    ("4hour", 14400),
    ("6hour", 21600),
    ("12hour", 43200),
    ("daily", 86400),
])
def test_interval_to_seconds_known_values(interval, expected):
    """지원 interval 모두 정확한 초 값 반환."""
    assert _interval_to_seconds(interval) == expected


def test_interval_to_seconds_unknown_falls_back_to_3600():
    """알 수 없는 interval → 3600 fallback."""
    assert _interval_to_seconds("weekly") == 3600
    assert _interval_to_seconds("") == 3600
    assert _interval_to_seconds("3hour") == 3600


# ── _coin_from_symbol ─────────────────────────────────────────────────


def test_coin_from_symbol_btc_perp():
    """BTC/USDT:USDT → 'BTC'."""
    assert _coin_from_symbol("BTC/USDT:USDT") == "BTC"


def test_coin_from_symbol_eth_perp():
    """ETH/USDT:USDT → 'ETH'."""
    assert _coin_from_symbol("ETH/USDT:USDT") == "ETH"


def test_coin_from_symbol_lowercase_btc():
    """소문자 입력도 대문자 변환 후 매칭."""
    assert _coin_from_symbol("btc/usdt:usdt") == "BTC"


def test_coin_from_symbol_unsupported_coin_returns_none():
    """지원 안 하는 코인 → None."""
    assert _coin_from_symbol("SOL/USDT:USDT") is None


def test_coin_from_symbol_empty_string_returns_none():
    """빈 문자열 → split 후 '' → FUTURES_AGG 키 없음 → None."""
    assert _coin_from_symbol("") is None


def test_coin_from_symbol_spot_pair_unsupported():
    """spot 페어 형식도 BTC 이면 매칭 (base만 추출)."""
    assert _coin_from_symbol("BTC/USDT") == "BTC"


def test_coin_from_symbol_eth_spot_pair_unsupported():
    """ETH spot 페어."""
    assert _coin_from_symbol("ETH/USDT") == "ETH"


def test_coin_from_symbol_mixed_case_eth():
    """대소문자 혼합 — eth → ETH 매칭."""
    assert _coin_from_symbol("Eth/USDT:USDT") == "ETH"


# ── symbol_for (거래소별 override) ───────────────────────────────────────


def test_binance_symbol_for_default_usdt_suffix():
    """BinanceMarketData (base default) → '{coin}USDT' 형식."""
    from aurora.market.exchanges.binance import BinanceMarketData
    inst = BinanceMarketData()
    assert inst.symbol_for("BTC") == "BTCUSDT"
    assert inst.symbol_for("ETH") == "ETHUSDT"


def test_hyperliquid_market_data_symbol_for_returns_coin():
    """HyperliquidMarketData → coin 그대로 반환 (HL 측 표기 본질)."""
    from aurora.market.exchanges.hyperliquid import HyperliquidMarketData
    inst = HyperliquidMarketData()
    assert inst.symbol_for("BTC") == "BTC"
    assert inst.symbol_for("ETH") == "ETH"


def test_okx_market_data_symbol_for_returns_swap_format():
    """OkxMarketData → '{coin}-USDT-SWAP' 형식 (OKX perp 표기 본질)."""
    from aurora.market.exchanges.okx import OkxMarketData
    inst = OkxMarketData()
    assert inst.symbol_for("BTC") == "BTC-USDT-SWAP"
    assert inst.symbol_for("ETH") == "ETH-USDT-SWAP"


def test_hyperliquid_series_provider_symbol_for_returns_coin():
    """HyperliquidSeriesProvider → coin 그대로 반환."""
    from aurora.market.exchanges.hyperliquid_series import HyperliquidSeriesProvider
    inst = HyperliquidSeriesProvider()
    assert inst.symbol_for("BTC") == "BTC"
    assert inst.symbol_for("ETH") == "ETH"


def test_okx_series_provider_symbol_for_returns_swap_format():
    """OkxSeriesProvider → '{coin}-USDT-SWAP' 형식."""
    from aurora.market.exchanges.okx_series import OkxSeriesProvider
    inst = OkxSeriesProvider()
    assert inst.symbol_for("BTC") == "BTC-USDT-SWAP"
    assert inst.symbol_for("ETH") == "ETH-USDT-SWAP"
