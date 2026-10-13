"""Fail-closed read-only inventory and redacted reconciliation summary.

Provider field mappings remain provisional until authenticated read-only verification.
"""
from dataclasses import dataclass
from decimal import Decimal

from .models import Order, OrderStatus, Position, one_way_positions
from .rest import RemoteAPIError

@dataclass(frozen=True)
class Trade:
    id: str
    order_id: str
    asset: str
    quantity: Decimal
    price: Decimal
    timestamp: str

@dataclass(frozen=True)
class Inventory:
    orders: tuple[Order, ...]
    positions: tuple[Position, ...]
    trades: tuple[Trade, ...]

    def summary(self):
        return {"orders": len(self.orders), "positions": len(self.positions),
                "trades": len(self.trades), "has_hedges": self._has_hedges()}

    def _has_hedges(self):
        try:
            one_way_positions(self.positions)
            return False
        except ValueError:
            return True

def _require(row, *keys):
    if not isinstance(row, dict) or any(k not in row or row[k] is None for k in keys):
        raise RemoteAPIError("Missing required provider inventory field")
    return [row[k] for k in keys]

def _decimal(value):
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise RemoteAPIError("Invalid provider decimal") from exc

def parse_order(row):
    ident, asset, side, position_side, status, quantity, filled = _require(
        row, "id", "asset", "side", "positionSide", "status", "quantity", "filledQuantity")
    try:
        return Order(str(ident), row.get("intentId"), str(asset), str(side), str(position_side),
                     OrderStatus(status), _decimal(quantity), _decimal(filled),
                     _decimal(row["averageFillPrice"]) if row.get("averageFillPrice") is not None else None)
    except (ValueError, TypeError) as exc:
        raise RemoteAPIError("Invalid provider order") from exc

def parse_position(row):
    ident, asset, side, quantity = _require(row, "id", "asset", "positionSide", "quantity")
    try:
        return Position(str(ident), str(asset), str(side), _decimal(quantity),
                        _decimal(row["leverage"]) if row.get("leverage") is not None else None,
                        row.get("marginMode"))
    except (ValueError, TypeError) as exc:
        raise RemoteAPIError("Invalid provider position") from exc

def parse_trade(row):
    ident, order_id, asset, quantity, price, timestamp = _require(
        row, "id", "orderId", "asset", "quantity", "price", "timestamp")
    qty, px = _decimal(quantity), _decimal(price)
    if qty <= 0 or px <= 0:
        raise RemoteAPIError("Invalid provider trade")
    return Trade(str(ident), str(order_id), str(asset), qty, px, str(timestamp))

class InventoryReader:
    def __init__(self, rest, account_id):
        if not account_id or not account_id.strip():
            raise ValueError("Explicit account ID required")
        self.rest = rest
        self.account_id = account_id

    async def snapshot(self):
        base = f"/accounts/{self.account_id}"
        orders = tuple(parse_order(x) for x in await self.rest.pages(f"{base}/orders"))
        positions = tuple(parse_position(x) for x in await self.rest.pages(f"{base}/positions"))
        trades = tuple(parse_trade(x) for x in await self.rest.pages(f"{base}/trades"))
        return Inventory(orders, positions, trades)
