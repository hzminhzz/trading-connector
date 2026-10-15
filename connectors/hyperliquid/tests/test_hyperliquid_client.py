# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportMissingImports=false
# type: ignore
"""Unit tests for HyperliquidClient with mock and real CCXT exchange."""
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import ccxt
import ccxt.async_support as ccxta
import pytest

from connectors.hyperliquid.client import HyperliquidClient
from connectors.hyperliquid.config import Environment, HyperliquidConfig, Mode
from connectors.hyperliquid.models import (
    HyperliquidAuthenticationError,
    HyperliquidInsufficientFundsError,
    HyperliquidInvalidOrderError,
    HyperliquidNetworkError,
    HyperliquidOrderNotFoundError,
    HyperliquidRateLimitError,
)


@pytest.fixture
def mock_exchange():
    mock = MagicMock()
    mock.load_markets = AsyncMock()
    mock.close = AsyncMock()
    mock.fetch_balance = AsyncMock()
    mock.fetch_positions = AsyncMock()
    mock.fetch_open_orders = AsyncMock()
    mock.create_order = AsyncMock()
    mock.cancel_order = AsyncMock()
    mock.edit_order = AsyncMock()
    mock.fetch_ohlcv = AsyncMock()
    mock.fetch_ticker = AsyncMock(return_value={"last": "82500.0"})
    return mock


@pytest.fixture
def client(mock_exchange):
    cfg = HyperliquidConfig(Environment.TESTNET, Mode.READ_ONLY, wallet_address="0x1", private_key="0x2")
    return HyperliquidClient(cfg, exchange=mock_exchange)


@pytest.fixture
def exec_client(mock_exchange):
    cfg = HyperliquidConfig(Environment.TESTNET, Mode.EXECUTION, wallet_address="0x1", private_key="0x2")
    return HyperliquidClient(cfg, exchange=mock_exchange)


def test_client_connect_and_close(client, mock_exchange):
    async def run():
        await client.connect()
        assert client.is_connected
        mock_exchange.load_markets.assert_called_once()

        await client.close()
        assert not client.is_connected
        mock_exchange.close.assert_called_once()

    asyncio.run(run())


def test_fetch_balance(client, mock_exchange):
    mock_exchange.fetch_balance.return_value = {
        "total": {"USDC": "50000.0"},
        "free": {"USDC": "45000.0"},
        "used": {"USDC": "5000.0"},
    }

    async def run():
        balances = await client.fetch_balance()
        assert "USDC" in balances
        assert balances["USDC"].total == Decimal("50000.0")
        assert balances["USDC"].free == Decimal("45000.0")
        assert balances["USDC"].used == Decimal("5000.0")

    asyncio.run(run())


def test_fetch_positions(client, mock_exchange):
    mock_exchange.fetch_positions.return_value = [
        {
            "symbol": "BTC/USDC:USDC",
            "side": "buy",
            "contracts": "0.10",
            "entryPrice": "82000.0",
            "markPrice": "83000.0",
            "unrealizedPnl": "100.0",
            "leverage": "20",
            "liquidationPrice": "70000.0",
        },
        {
            "symbol": "ETH/USDC:USDC",
            "side": "sell",
            "contracts": "0",
            "entryPrice": "3000.0",
        },
    ]

    async def run():
        positions = await client.fetch_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "BTC/USDC:USDC"
        assert positions[0].size == Decimal("0.10")
        assert positions[0].entry_price == Decimal("82000.0")
        assert positions[0].mark_price == Decimal("83000.0")

    asyncio.run(run())


def test_create_market_order_resolves_reference_price(exec_client, mock_exchange):
    mock_exchange.create_order.return_value = {
        "id": "hl_ord_1",
        "clientOrderId": "cid_hl",
        "symbol": "BTC/USDC:USDC",
        "side": "buy",
        "type": "market",
        "price": "82500.0",
        "amount": "0.05",
        "filled": "0.05",
        "status": "filled",
        "timestamp": 1700000000000,
    }

    async def run():
        # Notice price is None: client resolves via fetch_ticker
        order = await exec_client.create_order(
            symbol="BTC/USDC:USDC",
            side="buy",
            order_type="market",
            amount=0.05,
            price=None,
        )
        assert order.order_id == "hl_ord_1"
        assert order.price == Decimal("82500.0")
        mock_exchange.fetch_ticker.assert_called_once_with("BTC/USDC:USDC")
        mock_exchange.create_order.assert_called_once_with(
            symbol="BTC/USDC:USDC",
            type="market",
            side="buy",
            amount=0.05,
            price=82500.0,
            params={},
        )

    asyncio.run(run())


def test_cancel_order_requires_symbol(exec_client, mock_exchange):
    mock_exchange.cancel_order.return_value = {"id": "hl_ord_1"}

    async def run():
        # Missing symbol must fail
        with pytest.raises(ValueError, match="Hyperliquid DEX requires a symbol to cancel an order"):
            await exec_client.cancel_order("hl_ord_1", symbol=None)

        # Providing symbol succeeds
        success = await exec_client.cancel_order("hl_ord_1", symbol="BTC/USDC:USDC")
        assert success is True
        mock_exchange.cancel_order.assert_called_once_with("hl_ord_1", "BTC/USDC:USDC")

    asyncio.run(run())


def test_real_sdk_request_builders_offline():
    """Validates installed CCXT Hyperliquid request builders without making any network calls."""
    hl = ccxta.hyperliquid({
        "walletAddress": "0x0000000000000000000000000000000000000001",
        "privateKey": "0x0000000000000000000000000000000000000000000000000000000000000001",
    })
    market = {
        "id": "BTC",
        "symbol": "BTC/USDC:USDC",
        "baseId": "0",
        "spot": False,
        "precision": {"price": "1.0", "amount": "0.001"},
    }
    hl.markets = {"BTC/USDC:USDC": market}
    hl.markets_by_id = {"0": market}

    # 1. Market order requires a reference price
    with pytest.raises(ccxt.ArgumentsRequired, match="market orders require price"):
        hl.create_orders_request([
            {"symbol": "BTC/USDC:USDC", "type": "market", "side": "buy", "amount": 0.01, "price": None, "params": {}}
        ])

    # 2. Market order with reference price succeeds and computes bounded slippage (5% default)
    order_req = hl.create_orders_request([
        {"symbol": "BTC/USDC:USDC", "type": "market", "side": "buy", "amount": 0.01, "price": 80000.0, "params": {}}
    ])
    assert "action" in order_req
    orders_list = order_req["action"]["orders"]
    assert len(orders_list) == 1
    # 80000 * 1.05 = 84000
    assert orders_list[0]["p"] == "84000"
    assert orders_list[0]["t"]["limit"]["tif"] == "Ioc"

    # 3. Cancel order requires symbol
    with pytest.raises(Exception):
        hl.cancel_orders_request(["12345"], symbol=None)

    # 4. Cancel order with symbol succeeds
    cancel_req = hl.cancel_orders_request(["12345"], symbol="BTC/USDC:USDC")
    assert cancel_req["action"]["type"] == "cancel"
    assert cancel_req["action"]["cancels"][0]["o"] == 12345


@pytest.mark.parametrize("mode", [Mode.READ_ONLY, Mode.SHADOW])
def test_mutations_blocked_in_non_execution_modes(mock_exchange, mode):
    cfg = HyperliquidConfig(Environment.TESTNET, mode, wallet_address="0x1", private_key="0x2")
    c = HyperliquidClient(cfg, exchange=mock_exchange)

    async def run():
        with pytest.raises(PermissionError, match="Remote order mutation"):
            await c.create_order("BTC/USDC:USDC", "buy", "market", 0.01)

        with pytest.raises(PermissionError, match="Remote order mutation"):
            await c.cancel_order("hl_1", symbol="BTC/USDC:USDC")

        with pytest.raises(PermissionError, match="Remote order mutation"):
            await c.edit_order("hl_1", "BTC/USDC:USDC", "limit", "buy", amount=0.01)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("ccxt_exc", "expected_exc"),
    [
        (ccxt.AuthenticationError("bad key"), HyperliquidAuthenticationError),
        (ccxt.RateLimitExceeded("too fast"), HyperliquidRateLimitError),
        (ccxt.InsufficientFunds("no margin"), HyperliquidInsufficientFundsError),
        (ccxt.OrderNotFound("not found"), HyperliquidOrderNotFoundError),
        (ccxt.InvalidOrder("step error"), HyperliquidInvalidOrderError),
        (ccxt.NetworkError("timeout"), HyperliquidNetworkError),
    ],
)
def test_exception_wrapping(client, mock_exchange, ccxt_exc, expected_exc):
    mock_exchange.fetch_balance.side_effect = ccxt_exc

    async def run():
        with pytest.raises(expected_exc):
            await client.fetch_balance()

    asyncio.run(run())
