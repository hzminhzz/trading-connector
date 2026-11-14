"""Typed Propr native snapshots; opaque IDs and Decimal values survive mapping."""
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class OrderStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    PARTIAL = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Order:
    id: str
    intent_id: str | None
    asset: str
    side: str
    position_side: str
    status: OrderStatus
    quantity: Decimal
    filled: Decimal
    average_price: Decimal | None = None
    account_id: str | None = None
    position_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    reduce_only: bool | None = None
    order_type: str | None = None
    fee_paid: Decimal | None = None

    def __post_init__(self):
        if not self.id or not self.asset:
            raise ValueError("Missing order identity")
        if not self.quantity.is_finite() or not self.filled.is_finite():
            raise ValueError("Invalid order numeric state")
        if self.quantity <= 0 or self.filled < 0 or self.filled > self.quantity:
            raise ValueError("Invalid order quantity/fill")
        if self.side not in ("buy", "sell") or self.position_side not in ("long", "short"):
            raise ValueError("Invalid order side")


@dataclass(frozen=True)
class Position:
    id: str
    asset: str
    side: str
    quantity: Decimal
    leverage: Decimal | None = None
    margin_mode: str | None = None
    account_id: str | None = None
    status: str | None = None
    entry_price: Decimal | None = None
    mark_price: Decimal | None = None
    liquidation_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    margin_used: Decimal | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def __post_init__(self):
        if not self.id or not self.asset or self.side not in ("long", "short"):
            raise ValueError("Invalid position identity")
        if not self.quantity.is_finite() or self.quantity < 0:
            raise ValueError("Invalid position quantity")
        if self.margin_mode is not None and self.margin_mode not in ("cross", "isolated"):
            raise ValueError("Invalid margin mode")


def is_valid_account_id(account_id: object) -> bool:
    """Return True when account_id is a non-empty string without path separators."""
    return isinstance(account_id, str) and bool(account_id.strip()) and "/" not in account_id


def validate_account_id(
    account_id: object,
    *,
    allow_none: bool = False,
    message: str = "Invalid account ID",
) -> str | None:
    """Validate an account ID boundary value and fail closed with ValueError."""
    if account_id is None and allow_none:
        return None
    if not is_valid_account_id(account_id):
        raise ValueError(message)
    assert isinstance(account_id, str)
    return account_id


def one_way_positions(positions):
    """Refuse hedge mode instead of silently merging separate native positions."""
    result = {}
    for position in positions:
        if position.quantity == 0 or (position.status and position.status != "open"):
            continue
        if position.asset in result:
            raise ValueError("Multiple native positions cannot be represented as one-way")
        result[position.asset] = position
    return result
