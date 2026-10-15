# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportMissingImports=false
"""Bybit connector data models and structured error hierarchies."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


class BybitError(Exception):
    """Base exception for all Bybit connector failures."""


class BybitAuthenticationError(BybitError):
    """Raised when API credentials are rejected or permissions are insufficient."""


class BybitRateLimitError(BybitError):
    """Raised when exchange rate limits are exceeded."""


class BybitInsufficientFundsError(BybitError):
    """Raised when account has insufficient margin or balance to place order."""


class BybitOrderNotFoundError(BybitError):
    """Raised when an order ID is not found for cancel or replace."""


class BybitInvalidOrderError(BybitError):
    """Raised when order parameters violate exchange limits, tick size, or step size."""


class BybitNetworkError(BybitError):
    """Raised on connection drops, timeouts, or DNS failures."""


class BybitSnapshotError(BybitError):
    """A required authenticated Bybit snapshot is missing, ambiguous or unsupported."""


@dataclass(frozen=True)
class BybitAccountSummary:
    """Bybit V5 UNIFIED account-level USD totals (not individual coin balances)."""

    equity_usd: Decimal
    available_usd: Decimal


@dataclass(frozen=True)
class BybitBalance:
    currency: str
    total: Decimal
    free: Decimal
    used: Decimal
    raw: dict[str, Any]


@dataclass(frozen=True)
class BybitPositionData:
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
class BybitOrderData:
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
