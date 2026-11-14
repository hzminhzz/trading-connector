"""Hyperliquid WebSocket completed-bar kline feed implementing ML4T DataFeedProtocol."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from connectors.ccxt_support.feed import CCXTKlineFeed, make_ccxtpro_factory


class HyperliquidKlineFeed(CCXTKlineFeed):
    """Async Hyperliquid CCXT Pro OHLCV feed emitting completed MarketEvents."""

    def __init__(
        self,
        asset: str = "BTC/USDC",
        symbol: str = "BTC/USDC:USDC",
        timeframe: str = "1m",
        exchange: Any | None = None,
        max_age_seconds: float = 120.0,
        silence_seconds: float = 120.0,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(
            asset=asset,
            symbol=symbol,
            timeframe=timeframe,
            exchange=exchange,
            max_age_seconds=max_age_seconds,
            silence_seconds=silence_seconds,
            now_fn=now_fn,
            default_symbol="BTC/USDC:USDC",
            exchange_factory=make_ccxtpro_factory("hyperliquid"),
            source="hyperliquid_kline",
            source_market="hyperliquid_perp",
        )
