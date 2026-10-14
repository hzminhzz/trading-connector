"""Optional ML4T 0.1.2 shadow adapter and strategy compatibility boundary.

Every ML4T intent becomes an audited shadow decision; remote mutations remain disabled.
"""
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .execution import OrderRequest
from .models import OrderStatus as NativeStatus, one_way_positions
from .state import UnknownOutcome, new_ulid

try:
    from ml4t.backtest import Strategy
    from ml4t.live.protocols import (
        AsyncBrokerProtocol, ExecutionCapability, Order, OrderSide,
        OrderType, Position,
    )
    from ml4t.backtest.types import OrderStatus as ML4TOrderStatus
except ImportError:
    Strategy = AsyncBrokerProtocol = ExecutionCapability = Order = OrderSide = OrderType = Position = None
    ML4TOrderStatus = None

def _require_ml4t():
    if AsyncBrokerProtocol is None:
        raise ImportError("Optional dependency ml4t-live==0.1.2 not installed")


def _time(value):
    if not value:
        raise UnknownOutcome("Native position missing entry timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as exc:
        raise UnknownOutcome("Invalid native position timestamp") from exc


class _TargetOrderFacade:
    """Map legacy target-weight calls onto ML4T's incremental broker API."""

    def __init__(self, broker, data=None):
        self._broker = broker
        self._data = data

    def __getattr__(self, name):
        return getattr(self._broker, name)

    def order_target_percent(self, asset, target_percent):
        equity = self._broker.get_account_value()
        if not equity > 0:
            raise ValueError("Cannot target a portfolio weight without positive equity")
        row = self._data.get(asset, self._data) if isinstance(self._data, dict) else self._data
        price = row.get("close", row.get("price")) if isinstance(row, dict) else None
        if price is None or not Decimal(str(price)).is_finite() or Decimal(str(price)) <= 0:
            raise ValueError(f"Missing positive reference price for {asset}")
        position = self._broker.get_position(asset)
        current_quantity = Decimal(str(position.quantity)) if position else Decimal(0)
        target_quantity = Decimal(str(equity)) * Decimal(str(target_percent)) / Decimal(str(price))
        quantity = target_quantity - current_quantity
        if quantity == 0:
            return None
        return self._broker.submit_order(asset, float(quantity))

    def order_target_value(self, asset, target_value):
        equity = self._broker.get_account_value()
        if not equity > 0:
            raise ValueError("Cannot target portfolio value without positive equity")
        return self.order_target_percent(asset, float(target_value) / equity)


class ML4TStrategyAdapter(Strategy if Strategy is not None else object):
    """Run a synchronous ``ml4t.backtest.Strategy`` through LiveEngine unchanged."""

    def __init__(self, strategy):
        _require_ml4t()
        super().__init__()
        self.strategy = strategy

    def __getattr__(self, name):
        return getattr(self.strategy, name)

    def on_start(self, broker):
        return self.strategy.on_start(_TargetOrderFacade(broker))

    def on_data(self, timestamp, data, context, broker):
        return self.strategy.on_data(
            timestamp, data, context, _TargetOrderFacade(broker, data)
        )

    def on_end(self, broker):
        return self.strategy.on_end(_TargetOrderFacade(broker))


class ML4TShadowBroker:
    def __init__(self, account_reader, inventory_reader, shadow_gateway, registry, reconciler):
        _require_ml4t()
        self.account_reader = account_reader
        self.inventory_reader = inventory_reader
        self.shadow = shadow_gateway
        self.registry = registry
        self.reconciler = reconciler
        self._account = None
        self._inventory = None
        self._connected = False

    @property
    def execution_capabilities(self) -> frozenset:
        # Shadow intent validation is not a claim of native order execution.
        return frozenset()

    @property
    def is_connected(self) -> bool:
        if not self._connected:
            return False
        try:
            self.reconciler.assert_opening_safe()
            return True
        except UnknownOutcome:
            return False

    @property
    def positions(self):
        if self._inventory is None:
            return {}
        native = one_way_positions(self._inventory.positions)
        out = {}
        for instrument, position in native.items():
            if position.entry_price is None or position.created_at is None:
                raise UnknownOutcome("Position cannot be mapped without entry price/time")
            canonical = self.registry.to_canonical(instrument)
            signed = position.quantity if position.side == "long" else -position.quantity
            out[canonical] = Position(
                asset=canonical, quantity=float(signed),
                entry_price=float(position.entry_price), entry_time=_time(position.created_at),
                current_price=float(position.mark_price) if position.mark_price is not None else None,
            )
        return out

    @property
    def pending_orders(self):
        if self._inventory is None:
            return []
        output = []
        for order in self._inventory.orders:
            if order.status in (NativeStatus.FILLED, NativeStatus.CANCELLED,
                                NativeStatus.REJECTED, NativeStatus.EXPIRED):
                continue
            canonical = self.registry.to_canonical(order.asset)
            order_type = OrderType.LIMIT if order.order_type == "limit" else OrderType.MARKET
            output.append(Order(
                asset=canonical, side=OrderSide(order.side),
                quantity=float(order.quantity), order_type=order_type,
                order_id=order.id, status=ML4TOrderStatus.PENDING,
                filled_quantity=float(order.filled),
                created_at=_time(order.created_at) if order.created_at else None,
            ))
        return output

    async def connect(self) -> None:
        self._connected = False
        self.reconciler.assert_opening_safe()
        result = await self.account_reader.inspect()
        if not result["account_bound"] or not result["financial_fields_verified"]:
            raise UnknownOutcome("Read-only account identity or equity not verified")
        account_id = result["snapshot"].account_id
        selected_id = getattr(self.inventory_reader, "account_id", None)
        if selected_id is not None and selected_id != account_id:
            raise UnknownOutcome("Inventory reader is bound to a different account")
        inventory = await self.inventory_reader.snapshot(account_id=account_id)
        self._inventory = inventory
        self._account = result["snapshot"]
        self.account_source = result["account_source"]
        _ = self.positions
        self.reconciler.assert_opening_safe()
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_connected_async(self) -> bool:
        return self.is_connected

    def assert_paper_trading(self) -> None:
        raise PermissionError("Propr challenge-account paper execution is not independently verified")

    def assert_live_trading(self) -> None:
        raise PermissionError("Propr live order execution has not been approved")

    def _assert_ready(self) -> None:
        if not self.is_connected:
            raise UnknownOutcome("ML4T shadow adapter disconnected or unreconciled")

    async def get_positions_async(self) -> dict:
        self._assert_ready()
        return dict(self.positions)

    async def get_position_async(self, asset: str):
        return (await self.get_positions_async()).get(asset)

    async def get_pending_orders_async(self, asset: str | None = None) -> list:
        self._assert_ready()
        pending = self.pending_orders
        return [x for x in pending if x.asset == asset] if asset else list(pending)

    async def get_account_value_async(self) -> float:
        self._assert_ready()
        if self._account.equity is None:
            raise UnknownOutcome("Equity unavailable")
        return float(self._account.equity)

    async def get_cash_async(self) -> float:
        self._assert_ready()
        if self._account.buying_power is None:
            raise UnknownOutcome("Available funds unavailable")
        # ML4T cash proxy here means availableBalance, NOT spot cash balance.
        return float(self._account.buying_power)

    async def submit_order_async(
        self, asset: str, quantity: float, side=None, order_type=None,
        limit_price: float | None = None, stop_price: float | None = None, **kwargs: Any,
    ):
        self._assert_ready()
        if kwargs:
            raise ValueError("Unsupported execution parameters")
        if stop_price is not None:
            raise ValueError("Native contingent order semantics not qualified")
        order_type = OrderType.MARKET if order_type is None else OrderType(order_type)
        if order_type not in (OrderType.MARKET, OrderType.LIMIT):
            raise ValueError("Unsupported order type")
        magnitude = Decimal(str(quantity))
        if not magnitude.is_finite() or magnitude == 0:
            raise ValueError("Quantity must be finite and nonzero")
        if side is None:
            actual_side = OrderSide.BUY if magnitude > 0 else OrderSide.SELL
        else:
            actual_side = OrderSide(side)
            if magnitude < 0:
                raise ValueError("Negative quantity with explicit side is ambiguous")
        qty = abs(magnitude)
        current = self.positions.get(asset)
        reduce_only = bool(current and (
            (current.quantity > 0 and actual_side is OrderSide.SELL)
            or (current.quantity < 0 and actual_side is OrderSide.BUY)
        ))
        if reduce_only and qty > abs(Decimal(str(current.quantity))):
            raise ValueError("Order would overclose or flip a native position")
        position_side = (
            ("long" if current.quantity > 0 else "short") if current
            else ("long" if actual_side is OrderSide.BUY else "short")
        )
        position_id = None
        if reduce_only:
            native_asset = self.registry.to_native(asset)
            native_position = one_way_positions(self._inventory.positions).get(native_asset)
            if native_position is None:
                raise ValueError("Reduce-only intent requires one exact native position")
            position_id = native_position.id
        request = OrderRequest(
            account_id=self._account.account_id, asset=asset,
            side=actual_side.value, position_side=position_side,
            quantity=qty, reduce_only=reduce_only, order_type=order_type.value,
            limit_price=Decimal(str(limit_price)) if limit_price is not None else None,
            position_id=position_id,
        )
        decision = await self.shadow.submit(request)
        return Order(
            asset=asset, side=actual_side, quantity=float(qty),
            order_type=order_type, limit_price=limit_price,
            order_id=f"shadow-{new_ulid()}",
            status=ML4TOrderStatus.PENDING if decision.allowed else ML4TOrderStatus.REJECTED,
            created_at=datetime.now(UTC),
            rejection_reason=None if decision.allowed else decision.reason,
        )

    async def cancel_order_async(self, order_id: str) -> bool:
        raise PermissionError("Shadow adapter cannot cancel remote orders")

    async def replace_order_async(self, order_id: str, *, quantity=None,
                                  limit_price=None, stop_price=None):
        raise PermissionError("Shadow adapter cannot replace remote orders")

    async def close_position_async(self, asset: str):
        raise PermissionError("Shadow adapter cannot close remote positions")
