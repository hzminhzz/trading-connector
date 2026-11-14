"""Fail-closed boundary validation tests for shared validation and CCXT connectors."""
from __future__ import annotations

import asyncio
from datetime import UTC
from decimal import Decimal
import json
from unittest.mock import AsyncMock

import pytest

from connectors.ccxt_support.tests.conftest import PROVIDER_SPECS, make_mock_ccxt_exchange
from connectors.common.validation import (
    exact_decimal_sum,
    finite_json_loads,
    parse_utc_iso,
    validate_relative_api_path,
)


def test_exact_decimal_sum_preserves_long_precision():
    a = Decimal("1." + "0" * 30 + "1")
    b = Decimal("2." + "0" * 30 + "2")
    assert exact_decimal_sum(a, b) == Decimal("3." + "0" * 30 + "3")


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_finite_json_loads_rejects_nonfinite(token: str):
    with pytest.raises(ValueError, match="Non-finite JSON numeric token"):
        finite_json_loads(f'{{"x": {token}}}', parse_float=Decimal)


def test_parse_float_preservation_matches_propr_json_decoder():
    raw = b'{"price": 0.12345678901234567890}'
    parsed = finite_json_loads(raw, parse_float=Decimal)
    assert parsed["price"] == Decimal("0.12345678901234567890")
    assert json.loads(raw)["price"] != parsed["price"]


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=[s.name for s in PROVIDER_SPECS])
@pytest.mark.parametrize(
    "payload",
    [
        {"total": {"USDC": "10"}, "used": {"USDC": "0"}},
        {"total": {"USDC": "10"}, "free": {"USDC": "10"}},
        {"free": {"USDC": "10"}, "used": {"USDC": "0"}},
        {"total": {"USDC": "10"}, "free": {"USDC": None}, "used": {"USDC": "0"}},
        {"total": {"USDC": True}, "free": {"USDC": "10"}, "used": {"USDC": "0"}},
        {"total": {"USDC": "NaN"}, "free": {"USDC": "10"}, "used": {"USDC": "0"}},
        {"total": {"USDC": "10"}, "free": {"USDC": "10", "BTC": "1"}, "used": {"USDC": "0"}},
    ],
)
def test_incomplete_balance_rejected(spec, payload):
    mock = make_mock_ccxt_exchange(spec)
    mock.fetch_balance = AsyncMock(return_value=payload)
    client = spec.client_cls(spec.make_config(), exchange=mock)
    with pytest.raises(spec.base_err):
        asyncio.run(client.fetch_balance())


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=[s.name for s in PROVIDER_SPECS])
def test_long_decimal_and_explicit_zero_preserved(spec):
    long_value = "12345.123456789012345678901234567890123456"
    mock = make_mock_ccxt_exchange(spec)
    mock.fetch_balance = AsyncMock(
        return_value={
            "total": {"USDC": long_value, "BTC": "0"},
            "free": {"USDC": long_value, "BTC": "0"},
            "used": {"USDC": "0", "BTC": "0"},
        }
    )
    client = spec.client_cls(spec.make_config(), exchange=mock)
    balances = asyncio.run(client.fetch_balance())
    assert balances["USDC"].total == Decimal(long_value)
    assert balances["BTC"].total == Decimal(0)
    assert set(balances) == {"USDC", "BTC"}


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=[s.name for s in PROVIDER_SPECS])
@pytest.mark.parametrize(
    "changes",
    [
        {"contracts": None, "size": None},
        {"contracts": "NaN"},
        {"contracts": True},
        {"contracts": "-1"},
        {"side": "hedge"},
        {"side": None},
        {"symbol": None},
        {"entryPrice": "Infinity"},
    ],
)
def test_active_position_incomplete_rejected(spec, changes):
    data = {"symbol": "BTC/USDC:USDC", "side": "long", "contracts": "1", "entryPrice": "10", "markPrice": "11"}
    data.update(changes)
    mock = make_mock_ccxt_exchange(spec)
    mock.fetch_positions = AsyncMock(return_value=[data])
    client = spec.client_cls(spec.make_config(), exchange=mock)
    with pytest.raises(spec.base_err):
        asyncio.run(client.fetch_positions())


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=[s.name for s in PROVIDER_SPECS])
def test_explicit_inactive_zero_position_is_not_active(spec):
    mock = make_mock_ccxt_exchange(spec)
    mock.fetch_positions = AsyncMock(return_value=[{"symbol": "BTC/USDC:USDC", "side": "short", "contracts": "0"}])
    client = spec.client_cls(spec.make_config(), exchange=mock)
    assert asyncio.run(client.fetch_positions()) == []


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=[s.name for s in PROVIDER_SPECS])
@pytest.mark.parametrize(
    "changes",
    [
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
    ],
)
def test_open_order_incomplete_rejected(spec, changes):
    data = {
        "id": "order-1",
        "symbol": "BTC/USDC:USDC",
        "side": "buy",
        "type": "limit",
        "status": "open",
        "amount": "1",
        "filled": "0",
        "price": "10",
    }
    data.update(changes)
    mock = make_mock_ccxt_exchange(spec)
    mock.fetch_open_orders = AsyncMock(return_value=[data])
    client = spec.client_cls(spec.make_config(), exchange=mock)
    with pytest.raises(spec.base_err):
        asyncio.run(client.fetch_open_orders())


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=[s.name for s in PROVIDER_SPECS])
def test_duplicate_order_id_rejected(spec):
    order = {
        "id": "same",
        "symbol": "BTC/USDC:USDC",
        "side": "buy",
        "type": "limit",
        "status": "open",
        "amount": "1",
        "filled": "0",
        "price": "10",
    }
    mock = make_mock_ccxt_exchange(spec)
    mock.fetch_open_orders = AsyncMock(return_value=[order, order])
    client = spec.client_cls(spec.make_config(), exchange=mock)
    with pytest.raises(spec.base_err):
        asyncio.run(client.fetch_open_orders())


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=[s.name for s in PROVIDER_SPECS])
def test_valid_open_order_preserves_native_symbol_and_decimal(spec):
    order = {
        "id": "Order-ABC123",
        "symbol": "xyz:BTC/USDC:USDC",
        "side": "buy",
        "type": "limit",
        "status": "partially_filled",
        "amount": "1.0000000000000000001",
        "filled": "0.0000000000000000001",
        "price": "10.01",
    }
    mock = make_mock_ccxt_exchange(spec)
    mock.fetch_open_orders = AsyncMock(return_value=[order])
    client = spec.client_cls(spec.make_config(), exchange=mock)
    observed = asyncio.run(client.fetch_open_orders())
    assert observed[0].symbol == order["symbol"]
    assert observed[0].quantity == Decimal(order["amount"])
    assert observed[0].order_id == "Order-ABC123"
    assert observed[0].filled_quantity == Decimal(order["filled"])


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=[s.name for s in PROVIDER_SPECS])
def test_create_order_rejects_missing_order_id(spec):
    mock = make_mock_ccxt_exchange(spec)
    mock.create_order = AsyncMock(
        return_value={
            "id": "",
            "symbol": spec.symbol,
            "side": "buy",
            "type": "market",
            "amount": "0.01",
            "filled": "0",
            "status": "open",
        }
    )
    client = spec.client_cls(spec.make_config(mode="execution"), exchange=mock)
    with pytest.raises(spec.base_err):
        asyncio.run(client.create_order(spec.symbol, "buy", "market", 0.01))


def test_parse_utc_iso_normalization_and_validation():
    assert parse_utc_iso("2026-11-14T10:00:00Z").tzinfo == UTC
    converted = parse_utc_iso("2026-11-14T12:00:00+02:00")
    assert converted.tzinfo == UTC
    assert converted.hour == 10

    class CustomParseError(RuntimeError):
        pass

    for bad in ("2026-11-14T10:00:00", "", "   ", "not-a-timestamp", None, 123):
        with pytest.raises(CustomParseError):
            parse_utc_iso(bad, "ts", error_cls=CustomParseError)


def test_validate_relative_api_path_rejects_traversal_and_absolute_urls():
    assert validate_relative_api_path("/accounts/123") == "/accounts/123"
    assert validate_relative_api_path("/accounts/123?status=open") == "/accounts/123?status=open"

    class CustomPathError(RuntimeError):
        pass

    for bad in ("accounts/123", "https://evil.com/x", "//evil.com/x", "/accounts/../etc/passwd", ""):
        with pytest.raises(CustomPathError, match="Relative API path required"):
            validate_relative_api_path(bad, error_cls=CustomPathError)
