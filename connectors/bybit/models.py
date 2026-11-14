"""Normalized domain models and sanitized exception hierarchy for Bybit V5."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from connectors.ccxt_support.models import (
    CCXTBalance,
    CCXTError,
    CCXTOrderData,
    CCXTPositionData,
)


class BybitError(CCXTError):
    """Base exception for Bybit connector failures."""


class BybitAuthenticationError(BybitError):
    """Raised on authentication / credential failures."""


class BybitRateLimitError(BybitError):
    """Raised when Bybit API rate limits are exceeded."""


class BybitInsufficientFundsError(BybitError):
    """Raised on margin/balance insufficiency."""


class BybitOrderNotFoundError(BybitError):
    """Raised when an order cannot be located on the venue."""


class BybitInvalidOrderError(BybitError):
    """Raised when order parameters fail venue validation."""


class BybitNetworkError(BybitError):
    """Raised on connectivity or transport errors."""


class BybitSnapshotError(BybitError):
    """Raised when Bybit Demo snapshot or identity attestation fails."""


@dataclass
class BybitAccountSummary:
    equity_usd: Decimal
    available_usd: Decimal
    raw: dict[str, Any] = field(default_factory=dict)


BybitBalance = CCXTBalance
BybitPositionData = CCXTPositionData
BybitOrderData = CCXTOrderData
