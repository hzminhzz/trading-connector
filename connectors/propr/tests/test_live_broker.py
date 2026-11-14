# pyright: reportMissingImports=false
# type: ignore
"""Integration tests for ProprLiveBroker ML4T adapter."""
import asyncio
import pytest

pytest.importorskip("ml4t.live", reason="Optional ML4T integration not installed")

from ml4t.live.protocols import AsyncBrokerProtocol, ExecutionCapability, OrderSide, OrderType
from ml4t.backtest.types import OrderStatus as ML4TOrderStatus
from connectors.propr.ml4t_bridge import ProprLiveBroker
from connectors.propr.state import UnknownOutcome
from connectors.propr.tests.conftest import MockProprClient


def _make_broker(tmp_path, name: str = "live.db", client: MockProprClient | None = None):
    return ProprLiveBroker(client=client or MockProprClient(), journal_path=tmp_path / name)


def test_propr_live_broker_protocol_conformance(tmp_path):
    broker = _make_broker(tmp_path, "conformance.db")
    try:
        assert isinstance(broker, AsyncBrokerProtocol)
        assert ExecutionCapability.LIMIT in broker.execution_capabilities
    finally:
        broker.journal.close()


def test_propr_live_broker_lifecycle_and_queries(tmp_path):
    broker = _make_broker(tmp_path, "lifecycle.db")

    async def run():
        assert not broker.is_connected
        assert not await broker.is_connected_async()

        with pytest.raises(PermissionError):
            broker.assert_live_trading()

        with pytest.raises(PermissionError):
            broker.assert_paper_trading()

        await broker.connect()
        assert broker.is_connected
        assert await broker.is_connected_async()
        with pytest.raises(PermissionError, match="disabled"):
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

    try:
        asyncio.run(run())
    finally:
        broker.journal.close()


def test_propr_live_broker_order_submission_and_cancellation(tmp_path):
    client = MockProprClient()
    broker = _make_broker(tmp_path, "orders.db", client)

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

    try:
        asyncio.run(run())
    finally:
        broker.journal.close()


def test_propr_live_broker_replace_order(tmp_path):
    client = MockProprClient()
    broker = _make_broker(tmp_path, "replace.db", client)

    async def run():
        await broker.connect()
        # Replace existing ord_1
        with pytest.raises(UnknownOutcome, match="cutoff unverified"):
            await broker.replace_order_async(
                "ord_1", quantity=0.03, limit_price=87500.0,
            )
        assert not any(call[0] == "create_order" for call in client.calls)

    try:
        asyncio.run(run())
    finally:
        broker.journal.close()


def test_propr_live_broker_sync_rejects_hedge_conflict_and_missing_timestamps(tmp_path):
    client = MockProprClient()
    broker = _make_broker(tmp_path, "sync_errors.db", client)

    client._positions_data = [
        {
            "asset": "BTC/USDC", "base": "BTC", "quote": "USDC",
            "positionSide": "long", "quantity": "0.05",
            "entryPrice": "90000.0", "markPrice": "92000.0",
            "createdAt": "2026-10-14T00:00:00Z",
        },
        {
            "asset": "BTC/USDC", "base": "BTC", "quote": "USDC",
            "positionSide": "short", "quantity": "0.02",
            "entryPrice": "91000.0", "markPrice": "92000.0",
            "createdAt": "2026-10-14T00:01:00Z",
        },
    ]
    try:
        with pytest.raises(UnknownOutcome, match="Conflicting/hedge positions"):
            asyncio.run(broker.get_positions_async())

        client._positions_data = [
            {
                "asset": "BTC/USDC", "base": "BTC", "quote": "USDC",
                "positionSide": "long", "quantity": "0.05",
                "entryPrice": "90000.0", "markPrice": "92000.0",
                "createdAt": "2026-10-14T00:00:00",
            },
        ]
        with pytest.raises(UnknownOutcome, match="Timezone-aware"):
            asyncio.run(broker.get_positions_async())
    finally:
        broker.journal.close()


def test_propr_live_broker_no_fabricated_position_id_on_reduce_only(tmp_path):
    client = MockProprClient()
    broker = _make_broker(tmp_path, "propr_no_fake_pos.db", client)

    async def run():
        await broker.connect()
        await broker.submit_order_async(
            asset="BTC/USDC", quantity=0.01, side=OrderSide.SELL,
            order_type=OrderType.MARKET, reduce_only=True,
        )
        assert all(i.position_id is None for i in broker.journal.all_intents())

    try:
        asyncio.run(run())
    finally:
        broker.journal.close()


def test_propr_live_broker_hardened_submit_account_sync_and_replace_paths(tmp_path):
    client = MockProprClient()
    broker = _make_broker(tmp_path, "hardened_paths.db", client)

    async def run():
        await broker.connect()

        # Explicit reduce_only=True on a flat asset still forwards reduce_only=True.
        await broker.submit_order_async(
            asset="SOL/USDC", quantity=1.0, side=OrderSide.SELL,
            order_type=OrderType.MARKET, reduce_only=True,
        )
        create_calls = [c for c in client.calls if c[0] == "create_order"]
        assert create_calls[-1][7] is True

        with pytest.raises(ValueError, match="reduce_only must be a bool"):
            await broker.submit_order_async(
                asset="SOL/USDC", quantity=1.0, side=OrderSide.BUY, reduce_only="yes"
            )
        with pytest.raises(ValueError, match="Unsupported order arguments"):
            await broker.submit_order_async(
                asset="SOL/USDC", quantity=1.0, side=OrderSide.BUY, mystery=1
            )

        # Flat close_position returns None.
        assert await broker.close_position_async("DOGE/USDC") is None

        # Missing order and False cancel in replace_order_async fail closed.
        with pytest.raises(ValueError, match="not found in pending orders"):
            await broker.replace_order_async("missing-ord", quantity=0.1)
        client.cancel_order = lambda order_id: None
        with pytest.raises(UnknownOutcome, match="Cancellation outcome unknown"):
            await broker.replace_order_async("ord_1", quantity=0.03)

        # Account value sum fallback & cash validation.
        client._account_data = {
            "balance": "1000.0",
            "totalUnrealizedPnl": "50.5",
            "isolatedPositionMargin": "25.0",
        }
        assert await broker.get_account_value_async() == pytest.approx(1075.5)
        assert await broker.get_cash_async() == pytest.approx(1000.0)
        client._account_data = {"balance": "NaN", "availableBalance": None}
        with pytest.raises(UnknownOutcome):
            await broker.get_cash_async()
        client._account_data = {"balance": "1000.0", "availableBalance": "900.0"}

        # Short position sign + zero-quantity skipping + missing mark/entry price rejection.
        client._positions_data = [
            {
                "asset": "ETH/USDC", "base": "ETH", "quote": "USDC",
                "positionSide": "short", "quantity": "0.25",
                "entryPrice": "3000.0", "markPrice": "2950.0",
                "createdAt": "2026-10-14T00:00:00Z",
            },
            {
                "asset": "BTC/USDC", "base": "BTC", "quote": "USDC",
                "positionSide": "long", "quantity": "0",
                "entryPrice": "90000.0", "markPrice": "90000.0",
                "createdAt": "2026-10-14T00:00:00Z",
            },
        ]
        positions = await broker.get_positions_async()
        assert list(positions) == ["ETH/USDC"]
        assert positions["ETH/USDC"].quantity == -0.25

        client._positions_data = [{
            "asset": "ETH/USDC", "base": "ETH", "quote": "USDC",
            "positionSide": "long", "quantity": "0.1",
            "markPrice": "3000.0", "createdAt": "2026-10-14T00:00:00Z",
        }]
        with pytest.raises(UnknownOutcome, match="entryPrice"):
            await broker.get_positions_async()

        client._positions_data = [{
            "asset": "ETH/USDC", "base": "ETH", "quote": "USDC",
            "positionSide": "long", "quantity": "0.1",
            "entryPrice": "3000.0", "markPrice": "NaN", "createdAt": "2026-10-14T00:00:00Z",
        }]
        with pytest.raises(UnknownOutcome, match="markPrice"):
            await broker.get_positions_async()

        # _parse_order stop_market / stop_limit / statuses / missing orderId / empty response.
        stop_order = broker._parse_order({
            "orderId": "ord-stop", "asset": "BTC", "side": "sell", "type": "stop_market",
            "quantity": "0.1", "status": "filled", "createdAt": "2026-10-14T01:00:00Z",
        })
        assert stop_order.order_type == OrderType.STOP
        assert stop_order.status == ML4TOrderStatus.FILLED
        stop_limit = broker._parse_order({
            "orderId": "ord-sl", "asset": "BTC", "side": "buy", "type": "stop_limit",
            "quantity": "0.1", "price": "85000", "status": "canceled", "createdAt": "2026-10-14T01:00:00Z",
        })
        assert stop_limit.order_type == OrderType.STOP_LIMIT
        assert stop_limit.status == ML4TOrderStatus.CANCELLED
        rejected = broker._parse_order({
            "orderId": "ord-rej", "asset": "BTC", "side": "buy", "type": "market",
            "quantity": "0.1", "status": "rejected", "createdAt": "2026-10-14T01:00:00Z",
        })
        assert rejected.status == ML4TOrderStatus.REJECTED

        with pytest.raises(UnknownOutcome, match="Unsupported order type"):
            broker._parse_order({
                "orderId": "ord-bad", "asset": "BTC", "side": "buy", "type": "twap",
                "quantity": "0.1", "status": "open", "createdAt": "2026-10-14T01:00:00Z",
            })
        with pytest.raises(UnknownOutcome, match="orderId"):
            broker._parse_order({
                "orderId": "", "asset": "BTC", "side": "buy", "type": "market",
                "quantity": "0.1", "status": "open", "createdAt": "2026-10-14T01:00:00Z",
            })

        client._positions_data = []
        client.create_order = lambda **_kwargs: []
        with pytest.raises(UnknownOutcome, match="Empty order acknowledgement"):
            await broker.submit_order_async("ETH/USDC", 0.1, side=OrderSide.BUY)
        assert broker.journal.has_unknown() is True

    try:
        asyncio.run(run())
    finally:
        broker.journal.close()
