"""거래소별 raw 시장 데이터 fetcher (v0.1.87+).

Coinalyze (aggregated) 와 별도 — 사용자 인지 정합 목적으로 거래소별 OI / Funding /
Long-Short Ratio / Top Trader Ratio 분리 표시. Phase 3 dashboard view 측 본질
(거래소 5개: Binance / Bybit / OKX / Bitget / Hyperliquid).

각 거래소 측 무료 public endpoint 직접 호출 — API 키 X. rate limit 본질 verify
박음 (5 거래소 × ~5 엔드포인트 = 매 dashboard 폴링당 ~25 요청, 60초 주기 박음).
"""

from aurora.market.exchanges.base import ExchangeMarketData, ExchangeSnapshot
from aurora.market.exchanges.binance import BinanceMarketData

__all__ = ["BinanceMarketData", "ExchangeMarketData", "ExchangeSnapshot"]
