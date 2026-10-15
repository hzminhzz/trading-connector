# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportMissingImports=false
# type: ignore
"""Unit tests for BybitClient with mock CCXT exchange."""
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import ccxt
import pytest

from connectors.bybit.client import BybitClient
from connectors.bybit.config import BybitConfig, Environment, Mode
from connectors.bybit.models import (
    BybitAuthenticationError,
    BybitInsufficientFundsError,
    BybitInvalidOrderError,
    BybitNetworkError,
    BybitOrderNotFoundError,
    BybitRateLimitError,
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
    return mock


@pytest.fixture
def client(mock_exchange):
    cfg = BybitConfig(Environment.TESTNET, Mode.READ_ONLY, api_key="k", api_secret="s")
    return BybitClient(cfg, exchange=mock_exchange)


@pytest.fixture
def exec_client(mock_exchange):
    cfg = BybitConfig(Environment.TESTNET, Mode.EXECUTION, api_key="k", api_secret="s")
    return BybitClient(cfg, exchange=mock_exchange)


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
        "total": {"USDT": "10000.50", "BTC": "0.5"},
        "free": {"USDT": "8000.25", "BTC": "0.1"},
        "used": {"USDT": "2000.25", "BTC": "0.4"},
    }

    async def run():
        balances = await client.fetch_balance()
        assert "USDT" in balances
        assert balances["USDT"].total == Decimal("10000.50")
        assert balances["USDT"].free == Decimal("8000.25")
        assert balances["USDT"].used == Decimal("2000.25")

    asyncio.run(run())


def test_fetch_positions(client, mock_exchange):
    mock_exchange.fetch_positions.return_value = [
        {
            "symbol": "BTC/USDT:USDT",
            "side": "buy",
            "contracts": "0.05",
            "entryPrice": "82000.0",
            "markPrice": "83000.0",
            "unrealizedPnl": "50.0",
            "leverage": "10",
            "liquidationPrice": "75000.0",
        },
        {
            "symbol": "ETH/USDT:USDT",
            "side": "sell",
            "contracts": "0",  # zero size should be filtered out
            "entryPrice": "3000.0",
        },
    ]

    async def run():
        positions = await client.fetch_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "BTC/USDT:USDT"
        assert positions[0].size == Decimal("0.05")
        assert positions[0].entry_price == Decimal("82000.0")
        assert positions[0].mark_price == Decimal("83000.0")

    asyncio.run(run())


def test_create_and_cancel_order(exec_client, mock_exchange):
    mock_exchange.create_order.return_value = {
        "id": "12345",
        "clientOrderId": "cid_1",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "type": "limit",
        "price": "80000.0",
        "amount": "0.01",
        "filled": "0",
        "status": "open",
        "timestamp": 1700000000000,
    }
    mock_exchange.cancel_order.return_value = {"id": "12345"}

    async def run():
        order = await exec_client.create_order(
            symbol="BTC/USDT:USDT",
            side="buy",
            order_type="limit",
            amount=0.01,
            price=80000.0,
        )
        assert order.order_id == "12345"
        assert order.price == Decimal("80000.0")
        assert order.quantity == Decimal("0.01")

        with pytest.raises(ValueError, match="Bybit V5 requires a symbol to cancel an order"):
            await exec_client.cancel_order("12345", symbol=None)

        success = await exec_client.cancel_order("12345", symbol="BTC/USDT:USDT")
        assert success is True
        mock_exchange.cancel_order.assert_called_once_with("12345", "BTC/USDT:USDT")

    asyncio.run(run())


def test_edit_order(exec_client, mock_exchange):
    mock_exchange.edit_order.return_value = {
        "id": "12345",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "type": "limit",
        "price": "81000.0",
        "amount": "0.02",
        "filled": "0",
        "status": "open",
    }

    async def run():
        amended = await exec_client.edit_order(
            order_id="12345",
            symbol="BTC/USDT:USDT",
            order_type="limit",
            side="buy",
            amount=0.02,
            price=81000.0,
        )
        assert amended.order_id == "12345"
        assert amended.price == Decimal("81000.0")
        assert amended.quantity == Decimal("0.02")
        mock_exchange.edit_order.assert_called_once_with(
            id="12345",
            symbol="BTC/USDT:USDT",
            type="limit",
            side="buy",
            amount=0.02,
            price=81000.0,
            params={},
        )
    asyncio.run(run())


@pytest.mark.parametrize("mode", [Mode.READ_ONLY, Mode.SHADOW])
def test_mutations_blocked_in_non_execution_modes(mock_exchange, mode):
    cfg = BybitConfig(Environment.TESTNET, mode, api_key="k", api_secret="s")
    c = BybitClient(cfg, exchange=mock_exchange)

    async def run():
        with pytest.raises(PermissionError, match="Remote order mutation"):
            await c.create_order("BTC/USDT:USDT", "buy", "market", 0.01)

        with pytest.raises(PermissionError, match="Remote order mutation"):
            await c.cancel_order("12345")

        with pytest.raises(PermissionError, match="Remote order mutation"):
            await c.edit_order("12345", "BTC/USDT:USDT", "limit", "buy", amount=0.01)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("ccxt_exc", "expected_exc"),
    [
        (ccxt.AuthenticationError("bad key"), BybitAuthenticationError),
        (ccxt.RateLimitExceeded("too fast"), BybitRateLimitError),
        (ccxt.InsufficientFunds("no margin"), BybitInsufficientFundsError),
        (ccxt.OrderNotFound("not found"), BybitOrderNotFoundError),
        (ccxt.InvalidOrder("step error"), BybitInvalidOrderError),
        (ccxt.NetworkError("timeout"), BybitNetworkError),
    ],
)
def test_exception_wrapping(client, mock_exchange, ccxt_exc, expected_exc):
    mock_exchange.fetch_balance.side_effect = ccxt_exc

    async def run():
        with pytest.raises(expected_exc):
            await client.fetch_balance()

    asyncio.run(run())
