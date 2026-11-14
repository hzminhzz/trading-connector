"""Shared lifecycle contract tests for CCXT AsyncBrokerProtocol brokers."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from ml4t.backtest.types import OrderSide, OrderStatus, OrderType
from ml4t.live.protocols import AsyncBrokerProtocol

from connectors.ccxt_support.tests.conftest import make_mock_provider_client
from connectors.common.intents import UnknownOutcome


def _open_limit_order(spec, order_id: str, qty: str = "0.05", price: str = "80000.0"):
    return spec.order_cls(
        order_id=order_id,
        client_order_id=None,
        symbol=spec.symbol,
        side="buy",
        order_type="limit",
        price=Decimal(price),
        quantity=Decimal(qty),
        filled_quantity=Decimal("0.0"),
        status="open",
        timestamp=1700000000000,
        raw={},
    )


def test_broker_conforms_to_async_broker_protocol(provider_spec):
    broker = provider_spec.broker_cls(
        client=make_mock_provider_client(provider_spec),
        journal=provider_spec.journal_cls(":memory:"),
    )
    assert isinstance(broker, AsyncBrokerProtocol)


def test_broker_accepts_config_without_client(provider_spec):
    broker = provider_spec.broker_cls(config=provider_spec.make_config(mode="shadow"))
    assert isinstance(broker, AsyncBrokerProtocol)
    assert broker.client is not None


def test_broker_connect_syncs_state(provider_spec):
    client = make_mock_provider_client(provider_spec)
    client.fetch_positions.return_value = [
        provider_spec.position_cls(
            symbol=provider_spec.symbol,
            side="long",
            size=Decimal("0.1"),
            entry_price=Decimal("82000.0"),
            mark_price=Decimal("83000.0"),
            unrealized_pnl=Decimal("100.0"),
            leverage=Decimal("10"),
            liquidation_price=Decimal("70000.0"),
            raw={},
        )
    ]
    client.fetch_open_orders.return_value = [_open_limit_order(provider_spec, "ord_1")]
    broker = provider_spec.broker_cls(client=client, journal=provider_spec.journal_cls(":memory:"))

    async def run():
        await broker.connect()
        assert await broker.is_connected_async()
        assert await broker.get_account_value_async() == 5000.0
        assert await broker.get_cash_async() == 4500.0
        positions = await broker.get_positions_async()
        assert positions[provider_spec.symbol].quantity == 0.1
        pending = await broker.get_pending_orders_async()
        assert len(pending) == 1
        assert pending[0].order_id == "ord_1"

    asyncio.run(run())


def test_submit_order_tracks_journal_intent(provider_spec):
    client = make_mock_provider_client(provider_spec)
    client.create_order.return_value = provider_spec.order_cls(
        order_id="ord_101",
        client_order_id="cid_101",
        symbol=provider_spec.symbol,
        side="buy",
        order_type="limit",
        price=Decimal("82000.0"),
        quantity=Decimal("0.01"),
        filled_quantity=Decimal("0.0"),
        status="open",
        timestamp=1700000000000,
        raw={},
    )
    broker = provider_spec.broker_cls(client=client, journal=provider_spec.journal_cls(":memory:"))

    async def run():
        await broker.connect()
        order = await broker.submit_order_async(
            asset=provider_spec.asset,
            quantity=0.01,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=82000.0,
        )
        assert order.order_id == "ord_101"
        assert order.limit_price == 82000.0
        assert order.status == OrderStatus.PENDING
        records = broker.journal.all_intents()
        assert len(records) == 1
        assert records[0].state == "submitted"
        assert records[0].exchange_order_id == "ord_101"
        assert records[0].limit_price == "82000.0"

    asyncio.run(run())


def test_replace_order(provider_spec):
    client = make_mock_provider_client(provider_spec)
    client.fetch_open_orders.return_value = [_open_limit_order(provider_spec, "ord_202")]
    client.edit_order.return_value = _open_limit_order(provider_spec, "ord_202", qty="0.06", price="81000.0")
    broker = provider_spec.broker_cls(client=client, journal=provider_spec.journal_cls(":memory:"))

    async def run():
        await broker.connect()
        amended = await broker.replace_order_async(order_id="ord_202", quantity=0.06, limit_price=81000.0)
        assert amended.order_id == "ord_202"
        assert amended.quantity == 0.06
        assert amended.limit_price == 81000.0

    asyncio.run(run())


def test_cancel_order(provider_spec):
    client = make_mock_provider_client(provider_spec)
    client.fetch_open_orders.return_value = [_open_limit_order(provider_spec, "ord_303")]
    client.cancel_order.return_value = True
    broker = provider_spec.broker_cls(client=client, journal=provider_spec.journal_cls(":memory:"))

    async def run():
        await broker.connect()
        assert await broker.cancel_order_async("ord_303") is True
        client.cancel_order.assert_called_once_with("ord_303", symbol=provider_spec.symbol)
        # Cancel ACK keeps order pending until fresh exchange snapshot removes it.
        assert len(broker.pending_orders) == 1

    asyncio.run(run())


def test_close_position(provider_spec):
    client = make_mock_provider_client(provider_spec)
    client.fetch_positions.return_value = [
        provider_spec.position_cls(
            symbol=provider_spec.symbol,
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
    client.create_order.return_value = provider_spec.order_cls(
        order_id="close_1",
        client_order_id=None,
        symbol=provider_spec.symbol,
        side="sell",
        order_type="market",
        price=None,
        quantity=Decimal("0.10"),
        filled_quantity=Decimal("0.10"),
        status="filled",
        timestamp=1700000000000,
        raw={},
    )
    broker = provider_spec.broker_cls(client=client, journal=provider_spec.journal_cls(":memory:"))

    async def run():
        await broker.connect()
        close_order = await broker.close_position_async(provider_spec.asset)
        assert close_order is not None
        assert close_order.side == OrderSide.SELL
        assert close_order.quantity == 0.10

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["read_only", "shadow"])
def test_broker_mutations_blocked_in_non_execution_modes(provider_spec, mode):
    client = make_mock_provider_client(provider_spec, mode=mode)
    broker = provider_spec.broker_cls(client=client, journal=provider_spec.journal_cls(":memory:"))

    async def run():
        await broker.connect()
        with pytest.raises(PermissionError, match="Broker order mutation"):
            await broker.submit_order_async(provider_spec.asset, 0.01)
        with pytest.raises(PermissionError, match="Broker order mutation"):
            await broker.cancel_order_async("ord_1")
        with pytest.raises(PermissionError, match="Broker order mutation"):
            await broker.replace_order_async("ord_1", quantity=0.02)
        with pytest.raises(PermissionError, match="Broker order mutation"):
            await broker.close_position_async(provider_spec.asset)

    asyncio.run(run())


def test_fail_closed_gate_on_unreconciled_intent(provider_spec):
    client = make_mock_provider_client(provider_spec)
    journal = provider_spec.journal_cls(":memory:")
    staged = journal.stage(provider_spec.asset, "buy", "0.01", "market")
    journal.mark_unknown(staged.id, "timeout")
    assert journal.has_unknown() is True

    broker = provider_spec.broker_cls(client=client, journal=journal)

    async def run():
        await broker.connect()
        with pytest.raises(UnknownOutcome, match="Unreconciled order intent exists: position opening halted"):
            await broker.submit_order_async(provider_spec.asset, 0.01, side=OrderSide.BUY)

        client.create_order.return_value = provider_spec.order_cls(
            order_id="exit_1",
            client_order_id=None,
            symbol=provider_spec.symbol,
            side="sell",
            order_type="market",
            price=None,
            quantity=Decimal("0.01"),
            filled_quantity=Decimal("0.01"),
            status="filled",
            timestamp=1700000000000,
            raw={},
        )
        exit_order = await broker.submit_order_async(
            provider_spec.asset, 0.01, side=OrderSide.SELL, reduce_only=True
        )
        assert exit_order.order_id == "exit_1"

    asyncio.run(run())


def test_paper_and_live_assertions(provider_spec):
    broker_testnet = provider_spec.broker_cls(
        client=make_mock_provider_client(provider_spec, mode="read_only"),
        journal=provider_spec.journal_cls(":memory:"),
    )
    broker_testnet.assert_paper_trading()
    with pytest.raises(RuntimeError, match="Live trading requires an active connected broker"):
        broker_testnet.assert_live_trading()

    main_client = make_mock_provider_client(provider_spec, mode="shadow")
    main_client.config = provider_spec.make_config(mainnet=True, mode="shadow")
    broker_mainnet = provider_spec.broker_cls(
        client=main_client, journal=provider_spec.journal_cls(":memory:")
    )
    with pytest.raises(PermissionError, match=provider_spec.paper_error_match):
        broker_mainnet.assert_paper_trading()


def test_replace_order_preserves_reduce_only_and_handles_terminal_rejection(provider_spec):
    client = make_mock_provider_client(provider_spec)
    journal = provider_spec.journal_cls(":memory:")
    open_ro = _open_limit_order(provider_spec, "ord_ro")
    open_ro.side = "sell"
    client.create_order.return_value = open_ro
    client.fetch_open_orders.return_value = [open_ro]
    client.edit_order.return_value = _open_limit_order(provider_spec, "ord_ro", qty="0.04", price="81500.0")
    broker = provider_spec.broker_cls(client=client, journal=journal)

    async def run():
        await broker.connect()
        await broker.submit_order_async(
            provider_spec.asset, 0.05, side=OrderSide.SELL, order_type=OrderType.LIMIT,
            limit_price=80000.0, reduce_only=True,
        )
        blocker = journal.stage(provider_spec.asset, "buy", "0.01", "market")
        journal.mark_unknown(blocker.id, "timeout")

        amended = await broker.replace_order_async("ord_ro", quantity=0.04, limit_price=81500.0)
        assert amended.quantity == 0.04

        client.edit_order.side_effect = provider_spec.invalid_order_err("bad step")
        with pytest.raises(provider_spec.invalid_order_err):
            await broker.replace_order_async("ord_ro", quantity=0.03, limit_price=81501.0)
        assert journal.all_intents()[-1].state == "rejected"

    asyncio.run(run())


def test_sync_balance_and_positions_fail_closed(provider_spec):
    client = make_mock_provider_client(provider_spec)
    client.fetch_balance.return_value = {}
    broker = provider_spec.broker_cls(client=client, journal=provider_spec.journal_cls(":memory:"))

    async def run():
        with pytest.raises(ValueError, match="account balance unverified"):
            await broker.connect()

        ccy = provider_spec.currency
        client.fetch_balance.return_value = {
            ccy: provider_spec.balance_cls(ccy, Decimal("1000"), Decimal("800"), Decimal("200"), {})
        }
        client.fetch_positions.return_value = [
            provider_spec.position_cls(
                symbol=provider_spec.symbol, side="buy", size=Decimal("0.2"),
                entry_price=Decimal("80000"), mark_price=Decimal("81000"),
                unrealized_pnl=Decimal("200"), leverage=Decimal("5"),
                liquidation_price=None, raw={"createdTime": "1730000000000"},
            )
        ]
        await broker.connect()
        assert broker.positions[provider_spec.symbol].quantity == 0.2

        client.fetch_positions.return_value = [
            provider_spec.position_cls(
                symbol=provider_spec.symbol, side="invalid_side", size=Decimal("0.2"),
                entry_price=Decimal("80000"), mark_price=Decimal("81000"),
                unrealized_pnl=Decimal("0"), leverage=Decimal("5"),
                liquidation_price=None, raw={"createdTime": "1730000000000"},
            )
        ]
        with pytest.raises(ValueError, match="Unsupported .* position side"):
            await broker.get_positions_async()

        client.fetch_positions.return_value = [
            provider_spec.position_cls(
                symbol=provider_spec.symbol, side="long", size=Decimal("0.2"),
                entry_price=None, mark_price=Decimal("81000"),
                unrealized_pnl=Decimal("0"), leverage=Decimal("5"),
                liquidation_price=None, raw={"createdTime": "1730000000000"},
            )
        ]
        with pytest.raises(ValueError, match="Missing .* active position price"):
            await broker.get_positions_async()

    asyncio.run(run())
