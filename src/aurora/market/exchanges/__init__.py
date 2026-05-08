"""거래소별 raw 시장 데이터 fetcher (v0.1.87+).

Coinalyze (aggregated) 와 별도 — 사용자 인지 정합 목적으로 거래소별 OI / Funding /
Long-Short Ratio / Top Trader Ratio 분리 표시. Phase 3 dashboard view 측 본질
(거래소 5개: Binance / Bybit / OKX / Bitget / Hyperliquid).

각 거래소 측 무료 public endpoint 직접 호출 — API 키 X. rate limit 본질 verify
박음 (5 거래소 × ~5 엔드포인트 = 매 dashboard 폴링당 ~25 요청, 60초 주기 박음).

박힘 순서:
- v0.1.87: Binance
- v0.1.88: Bybit, OKX
- v0.1.89: Bitget, Hyperliquid (5/5 완성)
- v0.1.90: Whale Notional (별도, 거래 stream 본질)
"""

from aurora.market.exchanges.base import ExchangeMarketData, ExchangeSnapshot
from aurora.market.exchanges.binance import BinanceMarketData
from aurora.market.exchanges.bitget import BitgetMarketData
from aurora.market.exchanges.bybit import BybitMarketData
from aurora.market.exchanges.hyperliquid import HyperliquidMarketData
from aurora.market.exchanges.okx import OkxMarketData

__all__ = [
    "BinanceMarketData",
    "BitgetMarketData",
    "BybitMarketData",
    "ExchangeMarketData",
    "ExchangeSnapshot",
    "HyperliquidMarketData",
    "OkxMarketData",
]
