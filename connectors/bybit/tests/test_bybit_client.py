"""Bybit-specific client unit tests (sparse ACK hydration, demo createdTime, USD fallback)."""
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from connectors.bybit.broker import BybitLiveBroker
from connectors.bybit.client import BybitClient
from connectors.bybit.config import BybitConfig, Environment, Mode
from connectors.bybit.journal import BybitIntentJournal
from connectors.bybit.models import BybitBalance, BybitSnapshotError
from connectors.ccxt_support.tests.conftest import (
    BYBIT_SPEC,
    make_mock_ccxt_exchange,
    make_mock_provider_client,
)


@pytest.fixture
def mock_exchange():
    mock = make_mock_ccxt_exchange(BYBIT_SPEC)
    mock.create_order = AsyncMock()
    mock.edit_order = AsyncMock()
    mock.fetch_order = AsyncMock()
    return mock


@pytest.fixture
def exec_client(mock_exchange):
    cfg = BybitConfig(Environment.TESTNET, Mode.EXECUTION, api_key="k", api_secret="s")
    return BybitClient(cfg, exchange=mock_exchange)


def test_create_order_sparse_ack_hydrates_authoritative_fields(exec_client, mock_exchange):
    mock_exchange.create_order.return_value = {
        "id": "sparse_123",
        "clientOrderId": "client_ref",
        "info": {"orderId": "sparse_123", "orderLinkId": "client_ref"},
    }
    mock_exchange.fetch_order = AsyncMock(
        return_value={
            "id": "sparse_123",
            "clientOrderId": "client_ref",
            "symbol": "BTC/USDT:USDT",
            "side": "buy",
            "type": "limit",
            "price": 50000.0,
            "amount": 0.001,
            "filled": 0.0,
            "status": "open",
            "timestamp": 1730000000000,
        }
    )

    async def run():
        res = await exec_client.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=50000.0)
        assert res.order_id == "sparse_123"
        assert res.timestamp == 1730000000000
        assert str(res.quantity) == "0.001"
        assert str(res.price) == "50000.0"
        assert res.status == "open"
        mock_exchange.fetch_order.assert_called_once_with(
            "sparse_123", "BTC/USDT:USDT", params={"acknowledged": True}
        )

    asyncio.run(run())


def test_create_order_sparse_ack_hydration_failure_falls_back_to_submitted_fields(
    exec_client, mock_exchange
):
    mock_exchange.create_order.return_value = {"id": "sparse_fail"}
    mock_exchange.fetch_order.side_effect = RuntimeError("transient fetch failure")

    async def run():
        res = await exec_client.create_order("BTC/USDT:USDT", "buy", "limit", 0.002, price=51000.0)
        assert res.order_id == "sparse_fail"
        assert res.quantity == Decimal("0.002")
        assert res.price == Decimal("51000.0")
        assert res.status == "open"

    asyncio.run(run())


@pytest.mark.parametrize(
    "bad_row",
    [
        {},
        {"createdTime": "0"},
        {"createdTime": "-10"},
        {"createdTime": "not-an-int"},
    ],
)
def test_demo_created_ms_rejects_missing_or_nonpositive(bad_row):
    with pytest.raises(BybitSnapshotError, match="creation time"):
        BybitClient._demo_created_ms(bad_row, "demo_orders")


def test_bybit_broker_accepts_usd_balance_fallback():
    client = make_mock_provider_client(BYBIT_SPEC)
    client.fetch_balance.return_value = {
        "USD": BybitBalance("USD", Decimal("2500.0"), Decimal("2000.0"), Decimal("500.0"), {})
    }
    journal = BybitIntentJournal(":memory:")
    broker = BybitLiveBroker(client=client, journal=journal)
    try:
        asyncio.run(broker.connect())
        assert asyncio.run(broker.get_account_value_async()) == 2500.0
        assert asyncio.run(broker.get_cash_async()) == 2000.0
    finally:
        journal.close()
