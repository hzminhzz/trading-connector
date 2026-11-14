"""Shared client contract tests for CCXT-backed connectors (Bybit & Hyperliquid)."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import ccxt
import pytest

from connectors.ccxt_support.tests.conftest import make_mock_ccxt_exchange


def test_client_connect_and_close(provider_spec):
    mock_exchange = make_mock_ccxt_exchange(provider_spec)
    client = provider_spec.client_cls(provider_spec.make_config(), exchange=mock_exchange)

    async def run():
        await client.connect()
        assert client.is_connected
        mock_exchange.load_markets.assert_called_once()
        await client.close()
        assert not client.is_connected
        mock_exchange.close.assert_called_once()

    asyncio.run(run())


def test_fetch_balance(provider_spec):
    mock_exchange = make_mock_ccxt_exchange(provider_spec)
    ccy = provider_spec.currency
    mock_exchange.fetch_balance.return_value = {
        "total": {ccy: "10000.50"},
        "free": {ccy: "8000.25"},
        "used": {ccy: "2000.25"},
    }
    client = provider_spec.client_cls(provider_spec.make_config(), exchange=mock_exchange)

    async def run():
        balances = await client.fetch_balance()
        assert balances[ccy].total == Decimal("10000.50")
        assert balances[ccy].free == Decimal("8000.25")
        assert balances[ccy].used == Decimal("2000.25")

    asyncio.run(run())


def test_fetch_positions_filters_zero_size(provider_spec):
    mock_exchange = make_mock_ccxt_exchange(provider_spec)
    ccy = provider_spec.currency
    mock_exchange.fetch_positions.return_value = [
        {
            "symbol": provider_spec.symbol,
            "side": "buy",
            "contracts": "0.05",
            "entryPrice": "82000.0",
            "markPrice": "83000.0",
            "unrealizedPnl": "50.0",
            "leverage": "10",
            "liquidationPrice": "75000.0",
        },
        {
            "symbol": f"ETH/{ccy}:{ccy}",
            "side": "sell",
            "contracts": "0",
            "entryPrice": "3000.0",
        },
    ]
    client = provider_spec.client_cls(provider_spec.make_config(), exchange=mock_exchange)

    async def run():
        positions = await client.fetch_positions()
        assert len(positions) == 1
        assert positions[0].symbol == provider_spec.symbol
        assert positions[0].size == Decimal("0.05")
        assert positions[0].entry_price == Decimal("82000.0")
        assert positions[0].mark_price == Decimal("83000.0")

    asyncio.run(run())


def test_cancel_order_requires_symbol_and_handles_missing_order(provider_spec):
    mock_exchange = make_mock_ccxt_exchange(provider_spec)
    client = provider_spec.client_cls(
        provider_spec.make_config(mode="execution"), exchange=mock_exchange
    )

    async def run():
        with pytest.raises(ValueError, match="requires a symbol to cancel"):
            await client.cancel_order("ord_1", symbol=None)

        assert await client.cancel_order("ord_1", symbol=provider_spec.symbol) is True
        mock_exchange.cancel_order.assert_awaited_once_with("ord_1", provider_spec.symbol)

        mock_exchange.cancel_order.side_effect = ccxt.OrderNotFound("gone")
        assert await client.cancel_order("ord_2", symbol=provider_spec.symbol) is False

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["read_only", "shadow"])
def test_mutations_blocked_in_non_execution_modes(provider_spec, mode):
    mock_exchange = make_mock_ccxt_exchange(provider_spec)
    client = provider_spec.client_cls(provider_spec.make_config(mode=mode), exchange=mock_exchange)

    async def run():
        with pytest.raises(PermissionError, match="Remote order mutation"):
            await client.create_order(provider_spec.symbol, "buy", "market", 0.01)
        with pytest.raises(PermissionError, match="Remote order mutation"):
            await client.cancel_order("ord_1", symbol=provider_spec.symbol)
        with pytest.raises(PermissionError, match="Remote order mutation"):
            await client.edit_order("ord_1", provider_spec.symbol, "limit", "buy", amount=0.01)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("exc_factory", "attr"),
    [
        (lambda s: ccxt.AuthenticationError("bad key"), "auth_err"),
        (lambda s: ccxt.RateLimitExceeded("too fast"), "rate_err"),
        (lambda s: ccxt.InsufficientFunds("no margin"), "funds_err"),
        (lambda s: ccxt.OrderNotFound("not found"), "not_found_err"),
        (lambda s: ccxt.InvalidOrder("step error"), "invalid_order_err"),
        (lambda s: ccxt.NetworkError("timeout"), "net_err"),
    ],
)
def test_exception_wrapping(provider_spec, exc_factory, attr):
    mock_exchange = make_mock_ccxt_exchange(provider_spec)
    mock_exchange.fetch_balance.side_effect = exc_factory(provider_spec)
    client = provider_spec.client_cls(provider_spec.make_config(), exchange=mock_exchange)

    async def run():
        with pytest.raises(getattr(provider_spec, attr)):
            await client.fetch_balance()

    asyncio.run(run())


def test_create_order_forwards_limit_reduce_only_and_params(provider_spec):
    mock_exchange = make_mock_ccxt_exchange(provider_spec)
    mock_exchange.create_order.return_value = {
        "id": "ord_lim_1",
        "clientOrderId": "cid_1",
        "symbol": provider_spec.symbol,
        "side": "sell",
        "type": "limit",
        "price": "81000.0",
        "amount": "0.02",
        "filled": "0",
        "status": "open",
        "timestamp": 1700000000000,
    }
    client = provider_spec.client_cls(
        provider_spec.make_config(mode="execution"), exchange=mock_exchange
    )

    async def run():
        order = await client.create_order(
            symbol=provider_spec.symbol,
            side="sell",
            order_type="limit",
            amount=0.02,
            price=81000.0,
            reduce_only=True,
            params={"customFlag": "v"},
        )
        assert order.order_id == "ord_lim_1"
        assert order.price == Decimal("81000.0")
        assert order.quantity == Decimal("0.02")
        assert mock_exchange.create_order.call_args.kwargs["params"] == {
            "customFlag": "v",
            "reduceOnly": True,
        }

    asyncio.run(run())


def test_edit_order_forwards_fields_and_parses_result(provider_spec):
    mock_exchange = make_mock_ccxt_exchange(provider_spec)
    mock_exchange.edit_order.return_value = {
        "id": "ord_edit_1",
        "symbol": provider_spec.symbol,
        "side": "buy",
        "type": "limit",
        "price": "81000.0",
        "amount": "0.03",
        "filled": "0",
        "status": "open",
        "timestamp": 1700000000000,
    }
    client = provider_spec.client_cls(
        provider_spec.make_config(mode="execution"), exchange=mock_exchange
    )

    async def run():
        amended = await client.edit_order(
            order_id="ord_edit_1",
            symbol=provider_spec.symbol,
            order_type="limit",
            side="buy",
            amount=0.03,
            price=81000.0,
        )
        assert amended.order_id == "ord_edit_1"
        assert amended.price == Decimal("81000.0")
        assert amended.quantity == Decimal("0.03")
        mock_exchange.edit_order.assert_called_once_with(
            id="ord_edit_1",
            symbol=provider_spec.symbol,
            type="limit",
            side="buy",
            amount=0.03,
            price=81000.0,
            params={},
        )

    asyncio.run(run())
