"""Bybit WebSocket completed-bar kline feed implementing ML4T DataFeedProtocol."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from connectors.ccxt_support.feed import CCXTKlineFeed, make_ccxtpro_factory


class BybitKlineFeed(CCXTKlineFeed):
    """Async Bybit CCXT Pro OHLCV feed emitting completed MarketEvents."""

    def __init__(
        self,
        asset: str = "BTC/USDT",
        symbol: str = "BTC/USDT:USDT",
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
            default_symbol="BTC/USDT:USDT",
            exchange_factory=make_ccxtpro_factory("bybit"),
            source="bybit_kline",
            source_market="linear_perpetual",
        )
