"""Hyperliquid-specific client unit tests (DEX market reference price resolution)."""
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from connectors.ccxt_support.tests.conftest import HYPERLIQUID_SPEC, make_mock_ccxt_exchange
from connectors.hyperliquid.client import HyperliquidClient
from connectors.hyperliquid.config import Environment, HyperliquidConfig, Mode
from connectors.hyperliquid.models import HyperliquidError


@pytest.fixture
def mock_exchange():
    mock = make_mock_ccxt_exchange(HYPERLIQUID_SPEC)
    mock.create_order = AsyncMock()
    mock.fetch_ticker = AsyncMock(return_value={"last": "82500.0"})
    return mock


@pytest.fixture
def exec_client(mock_exchange):
    cfg = HyperliquidConfig(Environment.TESTNET, Mode.EXECUTION, wallet_address="0x1", private_key="0x2")
    return HyperliquidClient(cfg, exchange=mock_exchange)


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


def test_create_market_order_uses_explicit_price_without_ticker_lookup(exec_client, mock_exchange):
    mock_exchange.create_order.return_value = {
        "id": "hl_ord_2",
        "symbol": "BTC/USDC:USDC",
        "side": "sell",
        "type": "market",
        "price": "83000.0",
        "amount": "0.02",
        "filled": "0.02",
        "status": "filled",
        "timestamp": 1700000000000,
    }

    async def run():
        order = await exec_client.create_order(
            symbol="BTC/USDC:USDC",
            side="sell",
            order_type="market",
            amount=0.02,
            price=83000.0,
        )
        assert order.order_id == "hl_ord_2"
        mock_exchange.fetch_ticker.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("bad_ticker", [{}, {"last": None}, {"last": "NaN"}, {"last": ""}])
def test_create_market_order_rejects_missing_or_nonfinite_ticker_last(
    exec_client, mock_exchange, bad_ticker
):
    mock_exchange.fetch_ticker.return_value = bad_ticker

    async def run():
        with pytest.raises(HyperliquidError, match="ticker.last"):
            await exec_client.create_order("BTC/USDC:USDC", "buy", "market", 0.05)

    asyncio.run(run())
