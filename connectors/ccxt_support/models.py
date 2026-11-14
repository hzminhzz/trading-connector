"""Shared CCXT domain dataclasses and sanitized exception translation."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import ccxt


class CCXTError(Exception):
    """Base exception for CCXT-backed connector failures."""


@dataclass(frozen=True)
class CCXTErrorSpec:
    """Target exception classes and provider label for CCXT error translation."""

    provider_label: str
    base_error: type[Exception]
    auth_error: type[Exception]
    rate_limit_error: type[Exception]
    insufficient_funds_error: type[Exception]
    order_not_found_error: type[Exception]
    invalid_order_error: type[Exception]
    network_error: type[Exception]


def wrap_ccxt_exception(exc: Exception, spec: CCXTErrorSpec) -> Exception:
    """Translate raw CCXT exceptions into sanitized provider-specific errors."""
    if isinstance(exc, spec.base_error):
        return exc
    if isinstance(exc, ccxt.AuthenticationError):
        return spec.auth_error(f"{spec.provider_label} authentication failed: {type(exc).__name__}")
    if isinstance(exc, ccxt.RateLimitExceeded):
        return spec.rate_limit_error(f"{spec.provider_label} rate limit exceeded: {type(exc).__name__}")
    if isinstance(exc, ccxt.InsufficientFunds):
        return spec.insufficient_funds_error(f"{spec.provider_label} insufficient funds: {type(exc).__name__}")
    if isinstance(exc, ccxt.OrderNotFound):
        return spec.order_not_found_error(f"{spec.provider_label} order not found: {type(exc).__name__}")
    if isinstance(exc, ccxt.InvalidOrder):
        return spec.invalid_order_error(f"{spec.provider_label} invalid order: {type(exc).__name__}")
    if isinstance(exc, ccxt.NetworkError):
        return spec.network_error(f"{spec.provider_label} network error: {type(exc).__name__}")
    return spec.base_error(f"{spec.provider_label} error: {type(exc).__name__}")


@dataclass
class CCXTBalance:
    currency: str
    total: Decimal
    free: Decimal
    used: Decimal
    raw: dict[str, Any]


@dataclass
class CCXTPositionData:
    symbol: str
    side: str
    size: Decimal
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal
    leverage: Decimal
    liquidation_price: Decimal | None
    raw: dict[str, Any]


@dataclass
class CCXTOrderData:
    order_id: str
    client_order_id: str | None
    symbol: str
    side: str
    order_type: str
    price: Decimal | None
    quantity: Decimal
    filled_quantity: Decimal
    status: str
    timestamp: int
    raw: dict[str, Any]
    reduce_only: bool = False
