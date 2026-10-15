# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportMissingImports=false
# type: ignore
"""Contract and lifecycle tests for BybitLiveBroker implementing AsyncBrokerProtocol."""
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from ml4t.backtest.types import OrderSide, OrderStatus, OrderType
from ml4t.live.protocols import AsyncBrokerProtocol

from connectors.bybit.broker import BybitLiveBroker
from connectors.bybit.config import BybitConfig, Environment, Mode
from connectors.bybit.journal import BybitIntentJournal, UnknownOutcome
from connectors.bybit.models import BybitBalance, BybitOrderData, BybitPositionData


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.config = BybitConfig(Environment.TESTNET, Mode.EXECUTION, api_key="k", api_secret="s")
    client.connect = AsyncMock()
    client.close = AsyncMock()
    client.fetch_balance = AsyncMock()
    client.fetch_positions = AsyncMock()
    client.fetch_open_orders = AsyncMock()
    client.create_order = AsyncMock()
    client.cancel_order = AsyncMock()
    client.edit_order = AsyncMock()
    return client


@pytest.fixture
def broker(mock_client):
    journal = BybitIntentJournal(":memory:")
    return BybitLiveBroker(client=mock_client, journal=journal)


def test_broker_conforms_to_async_broker_protocol(broker):
    assert isinstance(broker, AsyncBrokerProtocol)


def test_broker_connect_syncs_state(broker, mock_client):
    mock_client.fetch_balance.return_value = {
        "USDT": BybitBalance("USDT", Decimal("5000.0"), Decimal("4500.0"), Decimal("500.0"), {}),
    }
    mock_client.fetch_positions.return_value = [
        BybitPositionData(
            symbol="BTC/USDT:USDT",
            side="long",
            size=Decimal("0.05"),
            entry_price=Decimal("82000.0"),
            mark_price=Decimal("83000.0"),
            unrealized_pnl=Decimal("50.0"),
            leverage=Decimal("10"),
            liquidation_price=Decimal("75000.0"),
            raw={},
        )
    ]
    mock_client.fetch_open_orders.return_value = []

    async def run():
        await broker.connect()
        assert broker.is_connected
        assert await broker.is_connected_async()

        # Cash and account value
        cash = await broker.get_cash_async()
        equity = await broker.get_account_value_async()
        assert cash == 4500.0
        assert equity == 5000.0

        # Positions
        positions = await broker.get_positions_async()
        assert "BTC/USDT:USDT" in positions
        assert "BTC/USDT" not in positions  # native SafeBroker forbids duplicate aliases
        pos = await broker.get_position_async("BTC/USDT")
        assert pos is not None
        assert pos.quantity == 0.05
        assert pos.entry_price == 82000.0

        await broker.disconnect()
        assert not broker.is_connected

    asyncio.run(run())


def test_submit_order_market(broker, mock_client):
    mock_client.fetch_balance.return_value = {}
    mock_client.fetch_positions.return_value = []
    mock_client.fetch_open_orders.return_value = []

    mock_client.create_order.return_value = BybitOrderData(
        order_id="ord_101",
        client_order_id="cid_101",
        symbol="BTC/USDT:USDT",
        side="buy",
        order_type="market",
        price=None,
        quantity=Decimal("0.01"),
        filled_quantity=Decimal("0.01"),
        status="filled",
        timestamp=1700000000000,
        raw={},
    )

    async def run():
        await broker.connect()
        order = await broker.submit_order_async(
            asset="BTC/USDT",
            quantity=0.01,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
        )
        assert order.order_id == "ord_101"
        assert order.side == OrderSide.BUY
        assert order.order_type == OrderType.MARKET
        assert order.quantity == 0.01
        assert order.status == OrderStatus.FILLED

        # Verify journal tracked intent
        cur = broker.journal.connection.execute("SELECT state, exchange_order_id FROM intents")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "submitted"
        assert rows[0][1] == "ord_101"

    asyncio.run(run())


def test_replace_order(broker, mock_client):
    mock_client.fetch_balance.return_value = {}
    mock_client.fetch_positions.return_value = []
    mock_client.fetch_open_orders.return_value = [
        BybitOrderData(
            order_id="ord_202",
            client_order_id=None,
            symbol="BTC/USDT:USDT",
            side="buy",
            order_type="limit",
            price=Decimal("80000.0"),
            quantity=Decimal("0.05"),
            filled_quantity=Decimal("0.0"),
            status="open",
            timestamp=1700000000000,
            raw={},
        )
    ]

    mock_client.edit_order.return_value = BybitOrderData(
        order_id="ord_202",
        client_order_id=None,
        symbol="BTC/USDT:USDT",
        side="buy",
        order_type="limit",
        price=Decimal("81000.0"),
        quantity=Decimal("0.06"),
        filled_quantity=Decimal("0.0"),
        status="open",
        timestamp=1700000000000,
        raw={},
    )

    async def run():
        await broker.connect()
        amended = await broker.replace_order_async(
            order_id="ord_202",
            quantity=0.06,
            limit_price=81000.0,
        )
        assert amended.order_id == "ord_202"
        assert amended.quantity == 0.06
        assert amended.limit_price == 81000.0

    asyncio.run(run())


def test_cancel_order(broker, mock_client):
    mock_client.fetch_balance.return_value = {}
    mock_client.fetch_positions.return_value = []
    mock_client.fetch_open_orders.return_value = [
        BybitOrderData(
            order_id="ord_303",
            client_order_id=None,
            symbol="BTC/USDT:USDT",
            side="buy",
            order_type="limit",
            price=Decimal("80000.0"),
            quantity=Decimal("0.05"),
            filled_quantity=Decimal("0.0"),
            status="open",
            timestamp=1700000000000,
            raw={},
        )
    ]
    mock_client.cancel_order.return_value = True

    async def run():
        await broker.connect()
        success = await broker.cancel_order_async("ord_303")
        assert success is True
        mock_client.cancel_order.assert_called_once_with("ord_303", symbol="BTC/USDT:USDT")

    asyncio.run(run())


def test_close_position(broker, mock_client):
    mock_client.fetch_balance.return_value = {}
    mock_client.fetch_positions.return_value = [
        BybitPositionData(
            symbol="BTC/USDT:USDT",
            side="long",
            size=Decimal("0.10"),
            entry_price=Decimal("82000.0"),
            mark_price=Decimal("83000.0"),
            unrealized_pnl=Decimal("100.0"),
            leverage=Decimal("10"),
            liquidation_price=Decimal("75000.0"),
            raw={},
        )
    ]
    mock_client.fetch_open_orders.return_value = []

    mock_client.create_order.return_value = BybitOrderData(
        order_id="close_1",
        client_order_id=None,
        symbol="BTC/USDT:USDT",
        side="sell",
        order_type="market",
        price=None,
        quantity=Decimal("0.10"),
        filled_quantity=Decimal("0.10"),
        status="filled",
        timestamp=1700000000000,
        raw={},
    )

    async def run():
        await broker.connect()
        close_order = await broker.close_position_async("BTC/USDT")
        assert close_order is not None
        assert close_order.side == OrderSide.SELL
        assert close_order.quantity == 0.10

    asyncio.run(run())


@pytest.mark.parametrize("mode", [Mode.READ_ONLY, Mode.SHADOW])
def test_broker_mutations_blocked_in_non_execution_modes(mock_client, mode):
    mock_client.config = BybitConfig(Environment.TESTNET, mode, api_key="k", api_secret="s")
    b = BybitLiveBroker(client=mock_client)

    async def run():
        b._connected = True
        with pytest.raises(PermissionError, match="Broker order mutation"):
            await b.submit_order_async("BTC/USDT", 0.01)

        with pytest.raises(PermissionError, match="Broker order mutation"):
            await b.cancel_order_async("ord_1")

        with pytest.raises(PermissionError, match="Broker order mutation"):
            await b.replace_order_async("ord_1", quantity=0.02)

        with pytest.raises(PermissionError, match="Broker order mutation"):
            await b.close_position_async("BTC/USDT")

    asyncio.run(run())


def test_fail_closed_gate_on_unreconciled_intent(broker, mock_client):
    # Manually inject an ambiguous unknown intent into the journal
    broker.journal.connection.execute(
        "INSERT INTO intents VALUES ('ambig_1', 'BTC/USDT', 'buy', '0.01', 'market', NULL, 0, 'unknown', NULL, 1000.0, 'timeout')"
    )
    broker.journal.connection.commit()
    assert broker.journal.has_unknown() is True

    async def run():
        broker._connected = True
        # Opening new position must fail closed
        with pytest.raises(UnknownOutcome, match="Unreconciled order intent exists: position opening halted"):
            await broker.submit_order_async("BTC/USDT", 0.01, side=OrderSide.BUY)

        # But reduce-only (risk-reducing position exit) is allowed through
        mock_client.create_order.return_value = BybitOrderData(
            order_id="exit_1",
            client_order_id=None,
            symbol="BTC/USDT:USDT",
            side="sell",
            order_type="market",
            price=None,
            quantity=Decimal("0.01"),
            filled_quantity=Decimal("0.01"),
            status="filled",
            timestamp=1700000000000,
            raw={},
        )
        mock_client.fetch_balance.return_value = {}
        mock_client.fetch_positions.return_value = []
        mock_client.fetch_open_orders.return_value = []

        exit_order = await broker.submit_order_async(
            "BTC/USDT", 0.01, side=OrderSide.SELL, reduce_only=True
        )
        assert exit_order.order_id == "exit_1"

    asyncio.run(run())


def test_paper_and_live_assertions(mock_client):
    # Testnet configuration
    testnet_cfg = BybitConfig(Environment.TESTNET, Mode.READ_ONLY)
    mock_client.config = testnet_cfg
    b = BybitLiveBroker(client=mock_client)
    b.assert_paper_trading()  # Should not raise

    with pytest.raises(RuntimeError, match="Live trading requires an active connected broker"):
        b.assert_live_trading()

    # Mainnet configuration
    mainnet_cfg = BybitConfig(Environment.MAINNET, Mode.SHADOW)
    mock_client.config = mainnet_cfg
    b_main = BybitLiveBroker(client=mock_client)
    with pytest.raises(PermissionError, match="Paper trading requires Bybit TESTNET or DEMO environment"):
        b_main.assert_paper_trading()
