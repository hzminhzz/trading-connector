# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportMissingImports=false
# type: ignore
"""Hyperliquid connector data models and structured error hierarchies."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


class HyperliquidError(Exception):
    """Base exception for all Hyperliquid connector failures."""


class HyperliquidAuthenticationError(HyperliquidError):
    """Raised when wallet address or private key are rejected."""


class HyperliquidRateLimitError(HyperliquidError):
    """Raised when Hyperliquid rate limits are exceeded."""


class HyperliquidInsufficientFundsError(HyperliquidError):
    """Raised when account has insufficient margin to place order."""


class HyperliquidOrderNotFoundError(HyperliquidError):
    """Raised when an order ID is not found for cancel or replace."""


class HyperliquidInvalidOrderError(HyperliquidError):
    """Raised when order parameters violate exchange limits, tick size, or step size."""


class HyperliquidNetworkError(HyperliquidError):
    """Raised on connection drops, timeouts, or RPC failures."""


@dataclass(frozen=True)
class HyperliquidBalance:
    currency: str
    total: Decimal
    free: Decimal
    used: Decimal
    raw: dict[str, Any]


@dataclass(frozen=True)
class HyperliquidPositionData:
    symbol: str
    side: str  # "buy" / "long" or "sell" / "short"
    size: Decimal
    entry_price: Decimal | None
    mark_price: Decimal | None
    unrealized_pnl: Decimal | None
    leverage: Decimal | None
    liquidation_price: Decimal | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class HyperliquidOrderData:
    order_id: str
    client_order_id: str | None
    symbol: str
    side: str
    order_type: str
    price: Decimal | None
    quantity: Decimal
    filled_quantity: Decimal
    status: str
    timestamp: int | None
    raw: dict[str, Any]
