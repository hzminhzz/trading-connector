# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportMissingImports=false, reportOptionalMemberAccess=false, reportOptionalCall=false, reportInvalidTypeForm=false
# type: ignore
"""Hyperliquid Live Broker adapter implementing ML4T AsyncBrokerProtocol."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from connectors.hyperliquid.client import HyperliquidClient
from connectors.hyperliquid.config import Environment, HyperliquidConfig, Mode
from connectors.hyperliquid.journal import HyperliquidIntentJournal
from connectors.hyperliquid.models import HyperliquidOrderData

try:
    from ml4t.backtest.types import Order, OrderSide, OrderStatus, OrderType, Position
    from ml4t.live.protocols import AsyncBrokerProtocol, ExecutionCapability
except ImportError:
    Order = OrderSide = OrderStatus = OrderType = Position = None
    AsyncBrokerProtocol = ExecutionCapability = None


def _require_ml4t() -> None:
    if AsyncBrokerProtocol is None:
        raise ImportError("Optional dependency ml4t-live is required for HyperliquidLiveBroker")


class HyperliquidLiveBroker:
    """AsyncBrokerProtocol adapter for Hyperliquid DEX via CCXT."""

    def __init__(
        self,
        client: HyperliquidClient | None = None,
        config: HyperliquidConfig | None = None,
        journal: HyperliquidIntentJournal | None = None,
    ) -> None:
        _require_ml4t()
        if client is not None:
            self.client = client
        elif config is not None:
            self.client = HyperliquidClient(config)
        else:
            self.client = HyperliquidClient(HyperliquidConfig.from_env())

        self.journal = journal or HyperliquidIntentJournal()
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
        """Connects client and synchronizes positions, orders, and balances."""
        await self.client.connect()
        self._connected = True
        await self._sync_state()

    async def disconnect(self) -> None:
        """Closes client connection."""
        self._connected = False
        await self.client.close()

    def assert_paper_trading(self) -> None:
        """Verifies environment is testnet."""
        if self.client.config.environment != Environment.TESTNET:
            raise PermissionError("Paper trading requires Hyperliquid TESTNET environment")

    def assert_live_trading(self) -> None:
        """Verifies environment is mainnet and active."""
        if not self._connected:
            raise RuntimeError("Live trading requires an active connected broker")
        if self.client.config.environment != Environment.MAINNET:
            raise PermissionError("Live production trading requires Hyperliquid MAINNET environment")

    def _assert_execution_allowed(self, operation: str) -> None:
        if self.client.config.mode != Mode.EXECUTION:
            raise PermissionError(
                f"Broker order mutation ({operation}) prohibited in {self.client.config.mode.value} mode"
            )

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
        return self._positions.get(asset) or self._positions.get(self._normalize_symbol(asset))

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
        """Submits an order to Hyperliquid with pre-submission intent journaling."""
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

        reduce_only = kwargs.get("reduce_only", False)
        params = dict(kwargs.get("params") or {})
        if stop_price is not None:
            params["stopPrice"] = stop_price

        price_to_submit = limit_price
        if order_type == OrderType.MARKET and price_to_submit is None:
            pos = self._positions.get(asset) or self._positions.get(symbol)
            if pos is not None and pos.current_price > 0:
                price_to_submit = float(pos.current_price)
            else:
                ticker = await self.client.exchange.fetch_ticker(symbol)
                price_to_submit = float(ticker.get("last") or ticker.get("close") or 0.0)
                if price_to_submit <= 0:
                    raise ValueError(f"Cannot resolve reference price for market order on {symbol}")

        # Stage intent before submission (fails closed if ambiguous intent exists)
        intent = self.journal.stage(
            asset=asset,
            side=side_str,
            quantity=abs_qty,
            order_type=type_str,
            limit_price=price_to_submit,
            reduce_only=reduce_only,
        )

        try:
            order_data = await self.client.create_order(
                symbol=symbol,
                side=side_str,
                order_type=type_str,
                amount=abs_qty,
                price=price_to_submit,
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
            raise ValueError(f"Cannot resolve exchange symbol for order {order_id} on Hyperliquid")

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
        """Amends an open order on Hyperliquid."""
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
        if not self.client.config.wallet_address or not self.client.config.private_key:
            return
        await asyncio.gather(
            self._sync_balance(),
            self._sync_positions(),
            self._sync_orders(),
        )

    async def _sync_balance(self) -> None:
        if not self.client.config.wallet_address or not self.client.config.private_key:
            return
        balances = await self.client.fetch_balance()
        usdc_bal = balances.get("USDC")
        if usdc_bal:
            self._account_value = float(usdc_bal.total)
            self._cash = float(usdc_bal.free)
        else:
            total_val = sum(float(b.total) for b in balances.values())
            free_val = sum(float(b.free) for b in balances.values())
            self._account_value = total_val
            self._cash = free_val

    async def _sync_positions(self) -> None:
        if not self.client.config.wallet_address or not self.client.config.private_key:
            return
        pos_list = await self.client.fetch_positions()
        new_positions: dict[str, Position] = {}
        for p in pos_list:
            qty = float(p.size)
            if p.side == "sell" or p.side == "short":
                qty = -abs(qty)
            entry_px = float(p.entry_price) if p.entry_price is not None else 0.0
            cur_px = float(p.mark_price) if p.mark_price is not None else entry_px

            pos = Position(
                asset=p.symbol,
                quantity=qty,
                entry_price=entry_px,
                current_price=cur_px,
                entry_time=datetime.now(UTC),
            )
            new_positions[p.symbol] = pos
            norm = self._normalize_symbol(p.symbol)
            new_positions[norm] = pos

        self._positions = new_positions

    async def _sync_orders(self, asset: str | None = None) -> None:
        if not self.client.config.wallet_address or not self.client.config.private_key:
            return
        symbol = self._to_exchange_symbol(asset) if asset else None
        order_list = await self.client.fetch_open_orders(symbol)
        self._pending_orders = [self._parse_order(o) for o in order_list]

    def _parse_order(self, o: HyperliquidOrderData, default_asset: str | None = None) -> Order:
        """Translates HyperliquidOrderData to ML4T Order."""
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

        return Order(
            asset=asset,
            side=side,
            quantity=float(o.quantity),
            order_type=order_type,
            limit_price=float(o.price) if o.price is not None else None,
            order_id=o.order_id,
            status=order_status,
            filled_quantity=float(o.filled_quantity),
            filled_price=float(o.price) if o.price is not None else None,
        )

    def _to_exchange_symbol(self, asset: str) -> str:
        """Converts ML4T asset notation to Hyperliquid CCXT symbol."""
        # e.g., 'BTC/USDC' -> 'BTC/USDC:USDC' for linear perpetuals
        if ":" in asset:
            return asset
        if "/" in asset:
            base, quote = asset.split("/")
            return f"{base}/{quote}:{quote}"
        return f"{asset}/USDC:USDC"

    def _normalize_symbol(self, symbol: str) -> str:
        """Converts CCXT exchange symbol to simplified asset name."""
        if ":" in symbol:
            return symbol.split(":")[0]
        return symbol
