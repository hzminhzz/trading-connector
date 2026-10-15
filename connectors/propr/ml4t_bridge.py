"""Optional ML4T 0.1.2 shadow adapter and strategy compatibility boundary.

Every ML4T intent becomes an audited shadow decision; remote mutations remain disabled.
"""
import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .execution import OrderRequest
from .models import OrderStatus as NativeStatus, one_way_positions
from .state import IntentJournal, UnknownOutcome, new_ulid

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

try:
    from propr_sdk import ProprClient
except ImportError:
    try:
        from .sdk import ProprClient
    except ImportError:
        ProprClient = None

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


class ProprLiveBroker:
    """Live ML4T 0.1.2 AsyncBrokerProtocol adapter powered directly by ProprClient."""

    def __init__(
        self,
        client: Any = None,
        account_id: str | None = None,
        journal: Any = None,
        journal_path: str | Path | None = None,
    ):
        _require_ml4t()
        if client is None:
            if ProprClient is None:
                raise ImportError("propr_sdk is not available")
            client = ProprClient()
        self.client = client
        self._target_account_id = account_id
        if journal is not None:
            self.journal = journal
        else:
            resolved_acc = account_id or getattr(client, "account_id", None) or "default"
            safe_acc = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(resolved_acc))
            path = journal_path or Path(f".propr_intent_journal_{safe_acc}.db")
            self.journal = IntentJournal(str(path))
        self._connected = False
        self._positions: dict[str, Any] = {}
        self._pending_orders: list[Any] = []
        self._account_data: dict[str, Any] = {}
    @property
    def execution_capabilities(self) -> frozenset:
        caps = set()
        if ExecutionCapability is not None:
            caps.update({
                ExecutionCapability.LIMIT,
                ExecutionCapability.STOP,
                ExecutionCapability.STOP_LIMIT,
            })
        return frozenset(caps)
    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def positions(self) -> dict[str, Any]:
        return dict(self._positions)

    @property
    def pending_orders(self) -> list[Any]:
        return list(self._pending_orders)

    async def connect(self) -> None:
        await asyncio.to_thread(self.client.setup, self._target_account_id)
        await self._sync_state()
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_connected_async(self) -> bool:
        return self._connected

    def assert_paper_trading(self) -> None:
        raise PermissionError(
            "Propr challenge accounts execute on live venues and are not verified paper-only"
        )

    def assert_live_trading(self) -> None:
        if not self._connected or not getattr(self.client, "account_id", None):
            raise RuntimeError("Live trading requires a connected, explicitly bound account")
    async def _sync_state(self) -> None:
        raw_positions = await asyncio.to_thread(self.client.get_open_positions)
        new_positions = {}
        for p in raw_positions:
            asset = p.get("asset") or f"{p.get('base')}/{p.get('quote', 'USDC')}"
            qty = float(p.get("quantity", 0))
            if p.get("positionSide") == "short":
                qty = -abs(qty)
            entry_px = float(p.get("entryPrice") or 0.0)
            mark_px = float(p.get("markPrice") or entry_px)
            created_str = p.get("createdAt") or datetime.now(UTC).isoformat()
            base_sym = p.get("base") or (asset.split("/")[0] if "/" in asset else asset)
            pos = Position(
                asset=asset,
                quantity=qty,
                entry_price=entry_px,
                entry_time=_time(created_str),
                current_price=mark_px,
            )
            pos._position_id = p.get("positionId")
            new_positions[asset] = pos
            new_positions[base_sym] = pos
        self._positions = new_positions

        raw_orders = await asyncio.to_thread(self.client.get_orders, status="open")
        self._pending_orders = [self._parse_order(o) for o in raw_orders]

        if hasattr(self.client, "get_account"):
            self._account_data = await asyncio.to_thread(self.client.get_account)
        else:
            acc = await asyncio.to_thread(self.client._get, self.client._account_path(""))
            self._account_data = acc.get("data", acc) if isinstance(acc, dict) else {}

    def _parse_order(self, o: dict[str, Any]) -> Any:
        raw_asset = str(o.get("asset") or o.get("base") or "")
        quote = o.get("quote") or "USDC"
        asset = f"{raw_asset}/{quote}" if "/" not in raw_asset else raw_asset
        side = OrderSide.BUY if o.get("side", "").lower() == "buy" else OrderSide.SELL
        order_type = OrderType.LIMIT if o.get("type", "").lower() == "limit" else OrderType.MARKET
        price = float(o["price"]) if o.get("price") is not None else None
        status_str = o.get("status", "").lower()
        if status_str in ("open", "pending"):
            status = ML4TOrderStatus.PENDING
        elif status_str in ("filled",):
            status = ML4TOrderStatus.FILLED
        elif status_str in ("cancelled", "canceled"):
            status = ML4TOrderStatus.CANCELLED
        elif status_str in ("rejected",):
            status = ML4TOrderStatus.REJECTED
        else:
            status = ML4TOrderStatus.PENDING
        created_at = _time(o.get("createdAt")) if o.get("createdAt") else datetime.now(UTC)
        return Order(
            asset=asset,
            side=side,
            quantity=float(o.get("quantity", 0)),
            order_type=order_type,
            limit_price=price,
            order_id=str(o.get("orderId", "")),
            status=status,
            created_at=created_at,
            filled_quantity=float(o.get("cumulativeQuantity", 0)),
            filled_price=float(o["averageFillPrice"]) if o.get("averageFillPrice") else None,
        )

    async def get_positions_async(self) -> dict[str, Any]:
        await self._sync_state()
        return dict(self._positions)

    async def get_position_async(self, asset: str) -> Any | None:
        positions = await self.get_positions_async()
        return positions.get(asset)

    async def get_pending_orders_async(self, asset: str | None = None) -> list[Any]:
        await self._sync_state()
        if asset:
            target = asset.split("/")[0] if "/" in asset else asset
            return [
                o for o in self._pending_orders
                if o.asset == asset or o.asset.split("/")[0] == target
            ]
        return list(self._pending_orders)

    async def get_account_value_async(self) -> float:
        await self._sync_state()
        margin_balance = self._account_data.get("marginBalance")
        if margin_balance is not None:
            return float(margin_balance)
        balance = float(self._account_data.get("balance", 0))
        upnl = float(self._account_data.get("totalUnrealizedPnl", 0))
        isolated = float(self._account_data.get("isolatedPositionMargin", 0))
        return balance + upnl + isolated

    async def get_cash_async(self) -> float:
        await self._sync_state()
        available = self._account_data.get("availableBalance")
        if available is not None:
            return float(available)
        return float(self._account_data.get("balance", 0))

    async def submit_order_async(
        self,
        asset: str,
        quantity: float,
        side: Any | None = None,
        order_type: Any = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        **kwargs: Any,
    ) -> Any:
        if not self._connected:
            raise RuntimeError("Broker not connected. Call connect() first.")

        base, quote = (asset.split("/", 1) if "/" in asset else (asset, "USDC"))
        propr_asset = base

        if side is None:
            order_side = OrderSide.BUY if quantity > 0 else OrderSide.SELL
            qty = abs(quantity)
        else:
            order_side = OrderSide(side)
            qty = abs(quantity)

        await self._sync_state()
        existing = (
            self._positions.get(asset)
            or self._positions.get(propr_asset)
            or self._positions.get(base)
        )
        position_side = "long"
        reduce_only = False
        if existing and existing.quantity != 0:
            if existing.quantity > 0:
                position_side = "long"
                if order_side == OrderSide.SELL:
                    reduce_only = True
            else:
                position_side = "short"
                if order_side == OrderSide.BUY:
                    reduce_only = True
        else:
            position_side = "long" if order_side == OrderSide.BUY else "short"

        resolved_order_type = OrderType.MARKET if order_type is None else OrderType(order_type)
        if resolved_order_type == OrderType.STOP:
            api_order_type = "stop_market"
        elif resolved_order_type == OrderType.STOP_LIMIT:
            api_order_type = "stop_limit"
        elif resolved_order_type == OrderType.LIMIT or limit_price is not None:
            api_order_type = "limit"
        else:
            api_order_type = "market"
        # Stage intent into durable SQLite WAL before network transmission
        pos_id = (
            getattr(existing, "_position_id", None)
            or getattr(existing, "position_id", None)
            or f"pos-{propr_asset}"
        )

        account_id = getattr(self.client, "account_id", None) or self._target_account_id or "default"
        intent = self.journal.stage(
            account_id=account_id,
            asset=propr_asset,
            side=order_side.value.lower(),
            position_side=position_side,
            quantity=qty,
            reduce_only=reduce_only,
            position_id=pos_id,
        )

        try:
            orders = await asyncio.to_thread(
                self.client.create_order,
                side=order_side.value.lower(),
                position_side=position_side,
                order_type=api_order_type,
                asset=propr_asset,
                base=base,
                quote=quote,
                quantity=str(qty),
                price=str(limit_price) if limit_price is not None else None,
                trigger_price=str(stop_price) if stop_price is not None else None,
                reduce_only=reduce_only,
            )
            if not orders:
                self.journal.mark(intent.id, "unknown")
                raise UnknownOutcome("Empty order acknowledgement returned from Propr API")
            self.journal.mark(intent.id, "accepted")
        except Exception as exc:
            if not isinstance(exc, UnknownOutcome):
                self.journal.mark(intent.id, "unknown")
                raise UnknownOutcome(f"Order submission outcome ambiguous: {exc}") from exc
            raise
        await self._sync_state()
        return self._parse_order(orders[0])

    async def cancel_order_async(self, order_id: str) -> bool:
        res = await asyncio.to_thread(self.client.cancel_order, order_id=order_id)
        await self._sync_state()
        return res is not None

    async def replace_order_async(
        self,
        order_id: str,
        *,
        quantity: float | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> Any:
        target = None
        for o in self._pending_orders:
            if o.order_id == order_id:
                target = o
                break
        if not target:
            raise ValueError(f"Order {order_id} not found in pending orders")
        await self.cancel_order_async(order_id)
        new_qty = quantity if quantity is not None else target.quantity
        new_price = limit_price if limit_price is not None else target.limit_price
        return await self.submit_order_async(
            asset=target.asset,
            quantity=new_qty,
            side=target.side,
            order_type=target.order_type,
            limit_price=new_price,
            stop_price=stop_price,
        )

    async def close_position_async(self, asset: str) -> Any | None:
        base, quote = (asset.split("/", 1) if "/" in asset else (asset, "USDC"))
        orders = await asyncio.to_thread(self.client.close_position, base=base, quote=quote)
        await self._sync_state()
        if not orders:
            return None
        return self._parse_order(orders[0])
