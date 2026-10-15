# pyright: reportMissingImports=false
# type: ignore
"""Integration tests for ProprLiveBroker ML4T adapter."""
import asyncio
from datetime import UTC, datetime
from typing import Any
import pytest

pytest.importorskip("ml4t.live", reason="Optional ML4T integration not installed")

from ml4t.live.protocols import AsyncBrokerProtocol, ExecutionCapability, OrderSide, OrderType
from ml4t.backtest.types import OrderStatus as ML4TOrderStatus
from connectors.propr.ml4t_bridge import ProprLiveBroker


class MockProprClient:
    """Mock client simulating Propr REST API endpoints."""

    def __init__(self, account_id: str = "urn:prp-acc:test1234"):
        self.account_id: str = account_id
        self._positions_data: list[dict[str, Any]] = [
            {
                "asset": "BTC/USDC",
                "base": "BTC",
                "quote": "USDC",
                "positionSide": "long",
                "quantity": "0.05",
                "entryPrice": "90000.0",
                "markPrice": "92000.0",
                "createdAt": "2026-10-14T00:00:00Z",
            }
        ]
        self._orders_data: list[dict[str, Any]] = [
            {
                "orderId": "ord_1",
                "asset": "BTC/USDC",
                "base": "BTC",
                "quote": "USDC",
                "side": "buy",
                "type": "limit",
                "price": "88000.0",
                "quantity": "0.02",
                "cumulativeQuantity": "0.0",
                "status": "open",
                "createdAt": "2026-10-14T01:00:00Z",
            }
        ]
        self._account_data: dict[str, Any] = {
            "accountId": self.account_id,
            "balance": "5000.0",
            "availableBalance": "4500.0",
            "totalUnrealizedPnl": "100.0",
            "marginBalance": "5100.0",
        }
        self.calls: list[Any] = []

    def setup(self, account_id: str | None = None) -> str:
        self.calls.append(("setup", account_id))
        if account_id:
            self.account_id = account_id
        return self.account_id

    def get_open_positions(self, base: str | None = None) -> list[dict[str, Any]]:
        self.calls.append(("get_open_positions", base))
        if base:
            return [p for p in self._positions_data if p.get("base") == base]
        return list(self._positions_data)

    def get_orders(self, status: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("get_orders", status))
        if status:
            return [o for o in self._orders_data if o.get("status") == status]
        return list(self._orders_data)

    def get_account(self) -> dict[str, Any]:
        self.calls.append(("get_account",))
        return dict(self._account_data)

    def create_order(
        self,
        side: str,
        position_side: str,
        order_type: str,
        asset: str,
        base: str,
        quote: str,
        quantity: str,
        price: str | None = None,
        reduce_only: bool = False,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        self.calls.append((
            "create_order", side, position_side, order_type, asset, quantity, price, reduce_only
        ))
        new_order: dict[str, Any] = {
            "orderId": f"ord_{len(self._orders_data) + 1}",
            "asset": asset,
            "base": base,
            "quote": quote,
            "side": side,
            "positionSide": position_side,
            "type": order_type,
            "price": price,
            "quantity": quantity,
            "cumulativeQuantity": "0.0",
            "status": "open",
            "createdAt": datetime.now(UTC).isoformat(),
        }
        self._orders_data.append(new_order)
        return [new_order]

    def cancel_order(self, order_id: str) -> dict[str, Any] | None:
        self.calls.append(("cancel_order", order_id))
        for o in self._orders_data:
            if o["orderId"] == order_id:
                o["status"] = "cancelled"
                return o
        return None

    def close_position(self, base: str, quote: str = "USDC") -> list[dict[str, Any]]:
        self.calls.append(("close_position", base, quote))
        pos = next((p for p in self._positions_data if p["base"] == base), None)
        if not pos:
            return []
        side = "sell" if pos["positionSide"] == "long" else "buy"
        return self.create_order(
            side=side,
            position_side=pos["positionSide"],
            order_type="market",
            asset=f"{base}/{quote}",
            base=base,
            quote=quote,
            quantity=pos["quantity"],
            reduce_only=True,
        )


def test_propr_live_broker_protocol_conformance():
    client = MockProprClient()
    broker = ProprLiveBroker(client=client)
    assert isinstance(broker, AsyncBrokerProtocol)
    assert ExecutionCapability.LIMIT in broker.execution_capabilities


def test_propr_live_broker_lifecycle_and_queries():
    client = MockProprClient()
    broker = ProprLiveBroker(client=client)

    async def run():
        assert not broker.is_connected
        assert not await broker.is_connected_async()

        with pytest.raises(RuntimeError):
            broker.assert_live_trading()

        with pytest.raises(PermissionError):
            broker.assert_paper_trading()

        await broker.connect()
        assert broker.is_connected
        assert await broker.is_connected_async()
        broker.assert_live_trading()

        # Account balances
        val = await broker.get_account_value_async()
        cash = await broker.get_cash_async()
        assert val == 5100.0
        assert cash == 4500.0

        # Positions
        positions = await broker.get_positions_async()
        assert "BTC/USDC" in positions
        btc_pos = await broker.get_position_async("BTC/USDC")
        assert btc_pos is not None
        assert btc_pos.quantity == 0.05
        assert btc_pos.entry_price == 90000.0
        assert btc_pos.current_price == 92000.0

        # Pending orders
        orders = await broker.get_pending_orders_async()
        assert len(orders) == 1
        assert orders[0].order_id == "ord_1"
        assert orders[0].side == OrderSide.BUY
        assert orders[0].limit_price == 88000.0

        # Disconnect
        await broker.disconnect()
        assert not broker.is_connected

    asyncio.run(run())


def test_propr_live_broker_order_submission_and_cancellation():
    client = MockProprClient()
    broker = ProprLiveBroker(client=client)

    async def run():
        await broker.connect()

        # Submit limit order (buy)
        order = await broker.submit_order_async(
            asset="ETH/USDC",
            quantity=0.5,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=3000.0,
        )
        assert order.asset == "ETH/USDC"
        assert order.side == OrderSide.BUY
        assert order.quantity == 0.5
        assert order.limit_price == 3000.0
        assert order.status == ML4TOrderStatus.PENDING

        # Check call evidence
        order_call = [c for c in client.calls if c[0] == "create_order"][-1]
        assert order_call[1] == "buy"
        assert order_call[2] == "long"
        assert order_call[3] == "limit"
        assert order_call[4] == "ETH"
        assert order_call[5] == "0.5"
        assert order_call[6] == "3000.0"
        assert order_call[7] is False  # reduce_only

        # Cancel order
        cancelled = await broker.cancel_order_async(order.order_id)
        assert cancelled is True

        # Close position
        close_order = await broker.close_position_async("BTC/USDC")
        assert close_order is not None
        assert close_order.side == OrderSide.SELL

    asyncio.run(run())


def test_propr_live_broker_replace_order():
    client = MockProprClient()
    broker = ProprLiveBroker(client=client)

    async def run():
        await broker.connect()
        # Replace existing ord_1
        replaced = await broker.replace_order_async(
            "ord_1",
            quantity=0.03,
            limit_price=87500.0,
        )
        assert replaced.quantity == 0.03
        assert replaced.limit_price == 87500.0

    asyncio.run(run())
