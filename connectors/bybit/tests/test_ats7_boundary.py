"""ATS-7 provider-boundary regressions. No credentials or network orders."""
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from connectors.bybit.broker import BybitLiveBroker
from connectors.bybit.config import BybitConfig, Environment, Mode
from connectors.bybit.journal import BybitIntentJournal, UnknownOutcome
from connectors.bybit.models import BybitOrderData


def fixture(tmp_path):
    client = MagicMock()
    client.config = BybitConfig(Environment.TESTNET, Mode.EXECUTION, api_key="fixture", api_secret="fixture")
    for name in ("connect", "close", "fetch_balance", "fetch_positions", "fetch_open_orders",
                 "create_order", "cancel_order"):
        setattr(client, name, AsyncMock())
    client.fetch_balance.return_value = {}
    client.fetch_positions.return_value = []
    client.fetch_open_orders.return_value = []
    client.create_order.return_value = BybitOrderData(
        order_id="observed-provider-id", client_order_id=None, symbol="BTC/USDT:USDT",
        side="buy", order_type="market", price=Decimal("80000"), quantity=Decimal("0.001"),
        filled_quantity=Decimal("0"), status="open", timestamp=1700000000000, raw={})
    journal = BybitIntentJournal(tmp_path / "intents.sqlite")
    return BybitLiveBroker(client=client, journal=journal), client


def test_client_identity_survives_ack_and_duplicate_is_not_resent(tmp_path):
    broker, client = fixture(tmp_path)
    async def run():
        await broker.connect()
        first = await broker.submit_order_async("BTC/USDT:USDT", 0.001,
                                                 intent_idempotency_key="stable-execution-001")
        assert first.intent_idempotency_key == "stable-execution-001"
        assert client.create_order.call_args.kwargs["params"]["orderLinkId"] == "stable-execution-001"
        with pytest.raises(UnknownOutcome):
            await broker.submit_order_async("BTC/USDT:USDT", 0.001,
                                             intent_idempotency_key="stable-execution-001")
        assert client.create_order.await_count == 1
    try:
        asyncio.run(run())
    finally:
        broker.journal.close()


@pytest.mark.parametrize("failure", [TimeoutError("invalid response, outcome unknown"), asyncio.CancelledError()])
def test_ambiguous_failure_never_becomes_rejection(tmp_path, failure):
    broker, client = fixture(tmp_path)
    client.create_order.side_effect = failure
    async def run():
        await broker.connect()
        with pytest.raises(type(failure)):
            await broker.submit_order_async("BTC/USDT:USDT", 0.001, intent_idempotency_key="interrupted")
        assert broker.journal.has_unknown()
        with pytest.raises(UnknownOutcome):
            await broker.submit_order_async("BTC/USDT:USDT", 0.001, intent_idempotency_key="second")
        assert client.create_order.await_count == 1
    try:
        asyncio.run(run())
    finally:
        broker.journal.close()


def test_native_reducing_risk_is_provider_reduce_only(tmp_path):
    broker, client = fixture(tmp_path)
    async def run():
        await broker.connect()
        await broker.submit_order_async("BTC/USDT:USDT", -0.001, reducing_risk=True,
                                         intent_idempotency_key="native-reduction")
        args = client.create_order.call_args.kwargs
        assert args["reduce_only"] is True
        assert args["params"]["orderLinkId"] == "native-reduction"
    try:
        asyncio.run(run())
    finally:
        broker.journal.close()


def test_cancel_ack_keeps_pending_obligation(tmp_path):
    broker, client = fixture(tmp_path)
    client.fetch_open_orders.return_value = [client.create_order.return_value]
    client.cancel_order.return_value = True
    async def run():
        await broker.connect()
        assert len(broker.pending_orders) == 1
        assert await broker.cancel_order_async("observed-provider-id")
        # Provider still reports the open order. ACK cannot erase that fact.
        assert len(broker.pending_orders) == 1
        assert len(await broker.get_pending_orders_async()) == 1
    try:
        asyncio.run(run())
    finally:
        broker.journal.close()


def test_after_acceptance_snapshot_failure_is_unknown(tmp_path):
    broker, client = fixture(tmp_path)
    async def run():
        await broker.connect()
        client.fetch_positions.side_effect = RuntimeError("snapshot unavailable")
        with pytest.raises(RuntimeError):
            await broker.submit_order_async("BTC/USDT:USDT", 0.001, intent_idempotency_key="accepted")
        row = broker.journal.connection.execute(
            "SELECT state,exchange_order_id FROM intents WHERE id='accepted'").fetchone()
        assert row == ("unknown", "observed-provider-id")
    try:
        asyncio.run(run())
    finally:
        broker.journal.close()


@pytest.mark.parametrize("params", [{"orderLinkId": "override"}, {"clientOrderId": "override"}, {"reduceOnly": True}])
def test_raw_parameter_overrides_cannot_change_bound_identity_or_risk(tmp_path, params):
    broker, client = fixture(tmp_path)
    async def run():
        await broker.connect()
        with pytest.raises(ValueError):
            await broker.submit_order_async("BTC/USDT:USDT", 0.001, params=params)
        assert client.create_order.await_count == 0
    try:
        asyncio.run(run())
    finally:
        broker.journal.close()
