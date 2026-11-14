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
    wallet_balance_usd: Decimal | None = None
    unrealized_pnl_usd: Decimal | None = None
    initial_margin_usd: Decimal | None = None
    maintenance_margin_usd: Decimal | None = None


BybitBalance = CCXTBalance
BybitPositionData = CCXTPositionData
BybitOrderData = CCXTOrderData


@dataclass(frozen=True)
class BybitExecutionData:
    execution_id: str
    order_id: str
    client_order_id: str | None
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    fee_currency: str | None
    timestamp_ms: int
    raw: dict[str, Any]


@dataclass(frozen=True)
class BybitDemoReconciliationSnapshot:
    endpoint: str
    observed_at_ms: int
    received_at_ms: int
    server_time_ms: int
    epoch: int
    connected: bool
    history_complete: bool
    financial_complete: bool
    cutoff: str | None
    account: BybitAccountSummary | None
    positions: tuple[BybitPositionData, ...]
    orders: tuple[BybitOrderData, ...]
    executions: tuple[BybitExecutionData, ...]
    market_marks: tuple[tuple[str, Decimal, int], ...]
    native_account_id: str | None = None
    native_account_source: str | None = None
    uncertainty_reasons: tuple[str, ...] = ()
