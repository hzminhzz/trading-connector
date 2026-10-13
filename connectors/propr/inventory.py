"""Read-only paginated Propr inventory using documented native field names.

Snapshots remain provisional if the provider changes during offset pagination.
No opaque IDs or balances are printed by summary().
"""
from dataclasses import dataclass
from decimal import Decimal

from .models import Order, OrderStatus, Position, finite_decimal, one_way_positions
from .rest import RemoteAPIError


@dataclass(frozen=True)
class Trade:
    id: str
    order_id: str
    asset: str
    quantity: Decimal
    price: Decimal
    timestamp: str
    position_id: str | None = None
    fee: Decimal | None = None
    account_id: str | None = None
    side: str | None = None


@dataclass(frozen=True)
class Inventory:
    orders: tuple[Order, ...]
    positions: tuple[Position, ...]
    trades: tuple[Trade, ...]

    def summary(self) -> dict:
        try:
            one_way_positions(self.positions)
            hedged = False
        except ValueError:
            hedged = True
        return {
            "orders": len(self.orders),
            "positions": len(self.positions),
            "trades": len(self.trades),
            "has_hedges": hedged,
        }


def _required(row, *keys):
    if not isinstance(row, dict) or any(k not in row or row[k] is None for k in keys):
        raise RemoteAPIError("Missing required provider inventory field")
    return [row[k] for k in keys]


def _decimal(value):
    try:
        return finite_decimal(value)
    except ValueError as exc:
        raise RemoteAPIError("Invalid provider decimal") from exc


def _optional_decimal(row, name):
    return _decimal(row[name]) if row.get(name) is not None else None


def parse_order(row):
    ident, asset, side, position_side, status, quantity, filled = _required(
        row, "orderId", "asset", "side", "positionSide", "status", "quantity", "cumulativeQuantity"
    )
    try:
        return Order(
            id=str(ident), intent_id=row.get("intentId"), asset=str(asset),
            side=str(side), position_side=str(position_side),
            status=OrderStatus(status), quantity=_decimal(quantity),
            filled=_decimal(filled), average_price=_optional_decimal(row, "averageFillPrice"),
            account_id=row.get("accountId"), position_id=row.get("positionId"),
            created_at=row.get("createdAt"), updated_at=row.get("updatedAt"),
            reduce_only=row.get("reduceOnly"), order_type=row.get("type"),
            fee_paid=_optional_decimal(row, "cumulativeTradingFees"),
        )
    except (ValueError, TypeError) as exc:
        raise RemoteAPIError("Invalid provider order") from exc


def parse_position(row):
    ident, asset, side, quantity = _required(row, "positionId", "asset", "positionSide", "quantity")
    try:
        return Position(
            id=str(ident), asset=str(asset), side=str(side), quantity=_decimal(quantity),
            leverage=_optional_decimal(row, "leverage"), margin_mode=row.get("marginMode"),
            account_id=row.get("accountId"), status=row.get("status"),
            entry_price=_optional_decimal(row, "entryPrice"),
            mark_price=_optional_decimal(row, "markPrice"),
            liquidation_price=_optional_decimal(row, "liquidationPrice"),
            unrealized_pnl=_optional_decimal(row, "unrealizedPnl"),
            margin_used=_optional_decimal(row, "marginUsed"), created_at=row.get("createdAt"),
            updated_at=row.get("updatedAt"),
        )
    except (ValueError, TypeError) as exc:
        raise RemoteAPIError("Invalid provider position") from exc


def parse_trade(row):
    ident, order_id, asset, quantity, price, timestamp = _required(
        row, "tradeId", "orderId", "asset", "quantity", "price", "executedAt"
    )
    qty, px = _decimal(quantity), _decimal(price)
    if qty <= 0 or px <= 0 or not str(timestamp).strip():
        raise RemoteAPIError("Invalid provider trade")
    return Trade(
        id=str(ident), order_id=str(order_id), asset=str(asset), quantity=qty,
        price=px, timestamp=str(timestamp), position_id=row.get("positionId"),
        fee=_optional_decimal(row, "fee"), account_id=row.get("accountId"),
        side=row.get("side"),
    )


class InventoryReader:
    def __init__(self, rest, account_id):
        if not isinstance(account_id, str) or not account_id.strip() or "/" in account_id:
            raise ValueError("Explicit valid account ID required")
        self.rest = rest
        self.account_id = account_id

    async def snapshot(self):
        base = f"/accounts/{self.account_id}"
        orders = tuple(parse_order(x) for x in await self.rest.pages(f"{base}/orders"))
        positions = tuple(parse_position(x) for x in await self.rest.pages(f"{base}/positions"))
        trades = tuple(parse_trade(x) for x in await self.rest.pages(f"{base}/trades"))
        for collection in (orders, positions, trades):
            ids = [item.id for item in collection]
            if len(ids) != len(set(ids)):
                raise RemoteAPIError("Duplicate inventory identity; pagination may have shifted")
            if any(item.account_id is not None and item.account_id != self.account_id
                   for item in collection):
                raise RemoteAPIError("Inventory row account identity mismatch")
        return Inventory(orders, positions, trades)
