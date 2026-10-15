# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportMissingImports=false, reportOptionalMemberAccess=false, reportOptionalCall=false, reportInvalidTypeForm=false
"""Bybit Live Broker adapter implementing ML4T AsyncBrokerProtocol."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from connectors.bybit.client import BybitClient
from connectors.bybit.config import BybitConfig, Environment, Mode
from connectors.bybit.journal import BybitIntentJournal
from connectors.bybit.models import BybitOrderData
try:
    from ml4t.backtest.types import Order, OrderSide, OrderStatus, OrderType, Position
    from ml4t.live.protocols import AsyncBrokerProtocol, ExecutionCapability
except ImportError:
    Order = OrderSide = OrderStatus = OrderType = Position = None  # type: ignore
    AsyncBrokerProtocol = ExecutionCapability = None  # type: ignore

def _require_ml4t() -> None:
    if AsyncBrokerProtocol is None:
        raise ImportError("Optional dependency ml4t-live is required for BybitLiveBroker")


class BybitLiveBroker:
    """AsyncBrokerProtocol adapter for Bybit V5 Unified Trading Account via CCXT."""

    def __init__(
        self,
        client: BybitClient | None = None,
        config: BybitConfig | None = None,
        journal: BybitIntentJournal | None = None,
    ) -> None:
        _require_ml4t()
        if client is not None:
            self.client = client
        elif config is not None:
            self.client = BybitClient(config)
        else:
            self.client = BybitClient(BybitConfig.from_env())

        self.journal = journal or BybitIntentJournal()
        self._connected = False
        self._positions: dict[str, Position] = {}
        self._pending_orders: list[Order] = []
        self._account_value: float = 0.0
        self._cash: float = 0.0

    @property
    def execution_capabilities(self) -> frozenset[ExecutionCapability]:
        return frozenset({
            ExecutionCapability.LIMIT,
            ExecutionCapability.STOP,
            ExecutionCapability.STOP_LIMIT,
            ExecutionCapability.PARTIAL_FILL,
        })

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def is_connected_async(self) -> bool:
        return self._connected

    @property
    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    @property
    def pending_orders(self) -> list[Order]:
        return list(self._pending_orders)

    async def connect(self) -> None:
        """Connects client; never report connected after an incomplete snapshot."""
        self._connected = False
        await self.client.connect()
        try:
            await self._sync_state()
        except BaseException:
            await self.client.close()
            raise
        self._connected = True

    async def disconnect(self) -> None:
        """Closes client connection."""
        self._connected = False
        await self.client.close()

    def assert_paper_trading(self) -> None:
        """Verify paper environment; Demo requires authenticated UID/endpoint attestation."""
        if self.client.config.environment not in (Environment.TESTNET, Environment.DEMO):
            raise PermissionError("Paper trading requires Bybit TESTNET or DEMO environment")
        if self.client.config.environment is Environment.DEMO and not (
            self._connected and self.client.demo_identity_verified
        ):
            raise PermissionError("Bybit Demo requires an authenticated, selected Demo account")

    def assert_live_trading(self) -> None:
        """Verifies environment is mainnet and active."""
        if not self._connected:
            raise RuntimeError("Live trading requires an active connected broker")
        if self.client.config.environment != Environment.MAINNET:
            raise PermissionError("Live production trading requires Bybit MAINNET environment")

    async def get_account_value_async(self) -> float:
        await self._sync_balance()
        return self._account_value

    async def get_cash_async(self) -> float:
        await self._sync_balance()
        return self._cash

    async def get_positions_async(self) -> dict[str, Position]:
        await self._sync_positions()
        return dict(self._positions)

    async def get_position_async(self, asset: str) -> Position | None:
        await self._sync_positions()
        target = self._normalize_symbol(asset)
        return self._positions.get(asset) or next(
            (position for symbol, position in self._positions.items()
             if self._normalize_symbol(symbol) == target), None
        )

    async def get_pending_orders_async(self, asset: str | None = None) -> list[Order]:
        await self._sync_orders(asset)
        if asset is None:
            return list(self._pending_orders)
        target = self._normalize_symbol(asset)
        return [o for o in self._pending_orders if o.asset == asset or o.asset == target]

    async def submit_order_async(
        self,
        asset: str,
        quantity: float,
        side: OrderSide | None = None,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        **kwargs: Any,
    ) -> Order:
        """Submits an order to Bybit."""
        if not self._connected:
            raise RuntimeError("Broker is not connected. Call connect() first.")
        self._assert_execution_allowed("submit_order_async")

        symbol = self._to_exchange_symbol(asset)
        resolved_side = side
        if resolved_side is None:
            resolved_side = OrderSide.BUY if quantity > 0 else OrderSide.SELL

        abs_qty = abs(quantity)
        side_str = "buy" if resolved_side == OrderSide.BUY else "sell"
        type_str = "market" if order_type == OrderType.MARKET else "limit"

        # Check existing position for reduce_only logic if applicable
        reduce_only = kwargs.get("reduce_only", False)
        params = dict(kwargs.get("params") or {})
        if stop_price is not None:
            params["stopPrice"] = stop_price

        # Stage intent before submission
        intent = self.journal.stage(
            asset=asset,
            side=side_str,
            quantity=abs_qty,
            order_type=type_str,
            limit_price=limit_price,
            reduce_only=reduce_only,
        )

        try:
            order_data = await self.client.create_order(
                symbol=symbol,
                side=side_str,
                order_type=type_str,
                amount=abs_qty,
                price=limit_price,
                reduce_only=reduce_only,
                params=params,
            )
            self.journal.mark_submitted(intent.id, order_data.order_id)
        except Exception as exc:
            self.journal.mark_unknown(intent.id, str(exc))
            raise

        ml4t_order = self._parse_order(order_data, default_asset=asset)
        await self._sync_state()
        return ml4t_order

    def _assert_execution_allowed(self, operation: str) -> None:
        if self.client.config.mode != Mode.EXECUTION:
            raise PermissionError(
                f"Broker order mutation ({operation}) prohibited in {self.client.config.mode.value} mode"
            )
    async def cancel_order_async(self, order_id: str) -> bool:
        """Cancels an open order."""
        if not self._connected:
            raise RuntimeError("Broker is not connected. Call connect() first.")
        self._assert_execution_allowed("cancel_order_async")

        symbol = None
        target = next((o for o in self._pending_orders if o.order_id == order_id), None)
        if target is not None:
            symbol = self._to_exchange_symbol(target.asset)
        else:
            await self._sync_orders()
            target = next((o for o in self._pending_orders if o.order_id == order_id), None)
            if target is not None:
                symbol = self._to_exchange_symbol(target.asset)
            else:
                cur = self.journal.connection.execute(
                    "SELECT asset FROM intents WHERE exchange_order_id = ? LIMIT 1", (order_id,)
                )
                row = cur.fetchone()
                if row:
                    symbol = self._to_exchange_symbol(row[0])

        if symbol is None:
            raise ValueError(f"Cannot resolve exchange symbol for order {order_id} on Bybit")

        success = await self.client.cancel_order(order_id, symbol=symbol)
        if success:
            self._pending_orders = [o for o in self._pending_orders if o.order_id != order_id]
        return success

    async def replace_order_async(
        self,
        order_id: str,
        *,
        quantity: float | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> Order:
        """Atomically replaces/amends an open order on Bybit."""
        if not self._connected:
            raise RuntimeError("Broker is not connected. Call connect() first.")
        self._assert_execution_allowed("replace_order_async")
        target_order = next((o for o in self._pending_orders if o.order_id == order_id), None)
        if target_order is None:
            await self._sync_orders()
            target_order = next((o for o in self._pending_orders if o.order_id == order_id), None)
            if target_order is None:
                raise ValueError(f"Cannot find order {order_id} to replace")

        symbol = self._to_exchange_symbol(target_order.asset)
        side_str = "buy" if target_order.side == OrderSide.BUY else "sell"
        type_str = "market" if target_order.order_type == OrderType.MARKET else "limit"
        params: dict[str, Any] = {}
        if stop_price is not None:
            params["stopPrice"] = stop_price

        amended = await self.client.edit_order(
            order_id=order_id,
            symbol=symbol,
            order_type=type_str,
            side=side_str,
            amount=quantity,
            price=limit_price,
            params=params,
        )

        ml4t_order = self._parse_order(amended, default_asset=target_order.asset)
        await self._sync_state()
        return ml4t_order

    async def close_position_async(self, asset: str) -> Order | None:
        """Flattens an open position using a reduce-only market order."""
        self._assert_execution_allowed("close_position_async")
        pos = await self.get_position_async(asset)
        if pos is None or abs(pos.quantity) == 0:
            return None

        # Opposite side to close
        close_side = OrderSide.SELL if pos.quantity > 0 else OrderSide.BUY
        return await self.submit_order_async(
            asset=asset,
            quantity=abs(pos.quantity),
            side=close_side,
            order_type=OrderType.MARKET,
            reduce_only=True,
        )

    async def _sync_state(self) -> None:
        """Synchronizes balance, positions, and open orders concurrently."""
        if not self.client.config.api_key or not self.client.config.api_secret:
            return
        if self.client.config.environment is Environment.DEMO:
            # Fail on the first unsupported authenticated snapshot; do not leave
            # concurrent background inventory reads after a failed connect.
            await self._sync_balance()
            await self._sync_positions()
            await self._sync_orders()
        else:
            await asyncio.gather(
                self._sync_balance(),
                self._sync_positions(),
                self._sync_orders(),
            )

    async def _sync_balance(self) -> None:
        if not self.client.config.api_key or not self.client.config.api_secret:
            return
        if isinstance(self.client, BybitClient) and self.client.config.environment is Environment.DEMO:
            account = await self.client.fetch_demo_account_summary()
            self._account_value = float(account.equity_usd)
            self._cash = float(account.available_usd)
            return
        balances = await self.client.fetch_balance()
        usdt_bal = balances.get("USDT") or balances.get("USD")
        if usdt_bal:
            self._account_value = float(usdt_bal.total)
            self._cash = float(usdt_bal.free)
        else:
            # Fallback to total sum across all balances
            total_val = sum(float(b.total) for b in balances.values())
            free_val = sum(float(b.free) for b in balances.values())
            self._account_value = total_val
            self._cash = free_val

    async def _sync_positions(self) -> None:
        if not self.client.config.api_key or not self.client.config.api_secret:
            return
        pos_list = await self.client.fetch_positions()
        new_positions: dict[str, Position] = {}
        for p in pos_list:
            qty = float(p.size)
            if p.side == "sell" or p.side == "short":
                qty = -abs(qty)
            entry_px = float(p.entry_price) if p.entry_price is not None else 0.0
            cur_px = float(p.mark_price) if p.mark_price is not None else entry_px

            if self.client.config.environment is Environment.DEMO:
                # V5 createdTime (ms) is required by the Demo snapshot parser.
                entry_time = datetime.fromtimestamp(int(p.raw["createdTime"]) / 1000, UTC)
            else:
                entry_time = datetime.now(UTC)
            pos = Position(
                asset=p.symbol,
                quantity=qty,
                entry_price=entry_px,
                current_price=cur_px,
                entry_time=entry_time,
            )
            # Native SafeBroker requires exactly one matching key per position.
            new_positions[p.symbol] = pos

        self._positions = new_positions

    async def _sync_orders(self, asset: str | None = None) -> None:
        if not self.client.config.api_key or not self.client.config.api_secret:
            return
        symbol = self._to_exchange_symbol(asset) if asset else None
        order_list = await self.client.fetch_open_orders(symbol)
        self._pending_orders = [self._parse_order(o) for o in order_list]

    def _parse_order(self, o: BybitOrderData, default_asset: str | None = None) -> Order:
        """Translates BybitOrderData to ML4T Order."""
        asset = default_asset or o.symbol
        side = OrderSide.BUY if o.side in ("buy", "long") else OrderSide.SELL
        order_type = OrderType.LIMIT if o.order_type == "limit" else OrderType.MARKET

        status_map = {
            "open": OrderStatus.PENDING,
            "new": OrderStatus.PENDING,
            "partially_filled": OrderStatus.PENDING,
            "filled": OrderStatus.FILLED,
            "closed": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "cancelled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
        }
        order_status = status_map.get(o.status.lower(), OrderStatus.PENDING)
        if self.client.config.environment is Environment.DEMO and o.timestamp is None:
            raise ValueError("Bybit Demo pending order missing creation timestamp")

        return Order(
            asset=asset,
            side=side,
            quantity=float(o.quantity),
            order_type=order_type,
            limit_price=float(o.price) if o.price is not None else None,
            order_id=o.order_id,
            status=order_status,
            created_at=(datetime.fromtimestamp(o.timestamp / 1000, UTC)
                        if o.timestamp is not None else datetime.now(UTC)),
            filled_quantity=float(o.filled_quantity),
            filled_price=float(o.price) if o.price is not None else None,
        )

    def _to_exchange_symbol(self, asset: str) -> str:
        """Converts ML4T asset notation to Bybit CCXT symbol."""
        # e.g., 'BTC/USDT' -> 'BTC/USDT:USDT' for linear perpetuals
        if ":" in asset:
            return asset
        if "/" in asset:
            base, quote = asset.split("/")
            return f"{base}/{quote}:{quote}"
        return f"{asset}/USDT:USDT"

    def _normalize_symbol(self, symbol: str) -> str:
        """Converts CCXT exchange symbol to simplified asset name."""
        # e.g., 'BTC/USDT:USDT' -> 'BTC/USDT'
        if ":" in symbol:
            return symbol.split(":")[0]
        return symbol
