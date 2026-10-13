"""Provider-native typed snapshots. Never conflate long and short positions."""
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

    def __post_init__(self):
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

    def __post_init__(self):
        if self.side not in ("long", "short") or self.quantity < 0:
            raise ValueError("Invalid position")

def one_way_positions(positions):
    result = {}
    for position in positions:
        if position.asset in result:
            raise ValueError("Multiple native positions cannot be represented as one-way")
        result[position.asset] = position
    return result
