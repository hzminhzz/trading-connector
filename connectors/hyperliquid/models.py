"""Normalized domain models and sanitized exception hierarchy for Hyperliquid."""
from __future__ import annotations

from connectors.ccxt_support.models import (
    CCXTBalance,
    CCXTError,
    CCXTOrderData,
    CCXTPositionData,
)


class HyperliquidError(CCXTError):
    """Base exception for Hyperliquid connector failures."""


class HyperliquidAuthenticationError(HyperliquidError):
    """Raised on authentication / credential failures."""


class HyperliquidRateLimitError(HyperliquidError):
    """Raised when Hyperliquid API rate limits are exceeded."""


class HyperliquidInsufficientFundsError(HyperliquidError):
    """Raised on margin/collateral insufficiency."""


class HyperliquidOrderNotFoundError(HyperliquidError):
    """Raised when an order cannot be located on the venue."""


class HyperliquidInvalidOrderError(HyperliquidError):
    """Raised when order parameters fail venue validation."""


class HyperliquidNetworkError(HyperliquidError):
    """Raised on connectivity or transport errors."""


HyperliquidBalance = CCXTBalance
HyperliquidPositionData = CCXTPositionData
HyperliquidOrderData = CCXTOrderData
