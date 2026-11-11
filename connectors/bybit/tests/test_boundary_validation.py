"""Provider boundary adversarial normalization fixtures (synthetic; no network I/O)."""
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from connectors.bybit.client import BybitClient
from connectors.bybit.config import BybitConfig, Environment, Mode
from connectors.bybit.models import BybitError


@pytest.fixture
def client():
    exchange = MagicMock()
    exchange.fetch_balance = AsyncMock()
    exchange.fetch_positions = AsyncMock()
    exchange.fetch_open_orders = AsyncMock()
    return BybitClient(BybitConfig(Environment.TESTNET, Mode.READ_ONLY, api_key="synthetic-key", api_secret="synthetic-secret"), exchange=exchange)


@pytest.mark.parametrize("field,payload", [
    ("free", {"total": {"USDC": "10"}, "used": {"USDC": "0"}}),
    ("used", {"total": {"USDC": "10"}, "free": {"USDC": "10"}}),
    ("total", {"free": {"USDC": "10"}, "used": {"USDC": "0"}}),
    ("null", {"total": {"USDC": "10"}, "free": {"USDC": None}, "used": {"USDC": "0"}}),
    ("bool", {"total": {"USDC": True}, "free": {"USDC": "10"}, "used": {"USDC": "0"}}),
    ("nan", {"total": {"USDC": "NaN"}, "free": {"USDC": "10"}, "used": {"USDC": "0"}}),
    ("duplicate_scope", {"total": {"USDC": "10"}, "free": {"USDC": "10", "BTC": "1"}, "used": {"USDC": "0"}}),
])
def test_incomplete_balance_rejected(client, field, payload):
    client.exchange.fetch_balance = AsyncMock(return_value=payload)
    with pytest.raises(BybitError):
        asyncio.run(client.fetch_balance())


def test_long_decimal_and_explicit_zero_preserved(client):
    long_value = "12345.123456789012345678901234567890123456"
    client.exchange.fetch_balance = AsyncMock(return_value={
        "total": {"USDC": long_value, "BTC": "0"},
        "free": {"USDC": long_value, "BTC": "0"},
        "used": {"USDC": "0", "BTC": "0"},
    })
    balances = asyncio.run(client.fetch_balance())
    assert balances["USDC"].total == Decimal(long_value)
    assert balances["BTC"].total == Decimal(0)
    assert set(balances) == {"USDC", "BTC"}


@pytest.mark.parametrize("changes", [
    {"contracts": None, "size": None},
    {"contracts": "NaN"},
    {"contracts": True},
    {"contracts": "-1"},
    {"side": "hedge"},
    {"side": None},
    {"symbol": None},
    {"entryPrice": "Infinity"},
])
def test_active_position_incomplete_rejected(client, changes):
    data = {"symbol": "BTC/USDC:USDC", "side": "long",
            "contracts": "1", "entryPrice": "10", "markPrice": "11"}
    data.update(changes)
    client.exchange.fetch_positions = AsyncMock(return_value=[data])
    with pytest.raises(BybitError):
        asyncio.run(client.fetch_positions())


def test_explicit_inactive_zero_position_is_not_active(client):
    client.exchange.fetch_positions = AsyncMock(return_value=[
        {"symbol": "BTC/USDC:USDC", "side": "short", "contracts": "0"}
    ])
    assert asyncio.run(client.fetch_positions()) == []


@pytest.mark.parametrize("changes", [
    {"id": ""},
    {"symbol": ""},
    {"side": "hedge"},
    {"type": "future_order_type"},
    {"status": "future_order_state"},
    {"amount": None},
    {"amount": "NaN"},
    {"filled": None},
    {"filled": "-1"},
    {"filled": "2"},
    {"price": None},
])
def test_open_order_incomplete_rejected(client, changes):
    data = {"id": "order-1", "symbol": "BTC/USDC:USDC",
            "side": "buy", "type": "limit", "status": "open",
            "amount": "1", "filled": "0", "price": "10"}
    data.update(changes)
    client.exchange.fetch_open_orders = AsyncMock(return_value=[data])
    with pytest.raises(BybitError):
        asyncio.run(client.fetch_open_orders())


def test_duplicate_order_id_rejected(client):
    order = {"id": "same", "symbol": "BTC/USDC:USDC",
             "side": "buy", "type": "limit", "status": "open",
             "amount": "1", "filled": "0", "price": "10"}
    client.exchange.fetch_open_orders = AsyncMock(return_value=[order, order])
    with pytest.raises(BybitError):
        asyncio.run(client.fetch_open_orders())


def test_valid_open_order_preserves_native_symbol_and_decimal(client):
    order = {"id": "Order-ABC123", "symbol": "xyz:BTC/USDC:USDC",
             "side": "buy", "type": "limit", "status": "partially_filled",
             "amount": "1.0000000000000000001",
             "filled": "0.0000000000000000001", "price": "10.01"}
    client.exchange.fetch_open_orders = AsyncMock(return_value=[order])
    observed = asyncio.run(client.fetch_open_orders())
    assert observed[0].symbol == order["symbol"]
    assert observed[0].quantity == Decimal(order["amount"])
    assert observed[0].order_id == "Order-ABC123"
    assert observed[0].filled_quantity == Decimal(order["filled"])
