"""Fail-closed Propr parser and exact JSON-token regression fixtures."""
import asyncio
from decimal import Decimal

import httpx
import pytest

from connectors.propr.account import parse_account
from connectors.propr.inventory import parse_order, parse_position
from connectors.propr.models import finite_decimal
from connectors.propr.rest import ProprRest, RemoteAPIError


ORDER = {
    "orderId": "test-order", "asset": "BTC", "side": "buy",
    "positionSide": "long", "status": "partially_filled",
    "quantity": "2", "cumulativeQuantity": "1",
}
POSITION = {
    "positionId": "test-position", "asset": "BTC",
    "positionSide": "long", "quantity": "1", "status": "open",
}


@pytest.mark.parametrize("bad", ["false", "true", 0, 1, [], {}, [False]])
def test_reduce_only_malformed_must_not_become_bool(bad):
    with pytest.raises(RemoteAPIError, match="reduceOnly"):
        parse_order({**ORDER, "reduceOnly": bad})


@pytest.mark.parametrize("value", [None, True, False])
def test_reduce_only_nullable_bool(value):
    assert parse_order({**ORDER, "reduceOnly": value}).reduce_only is value


@pytest.mark.parametrize("bad", [
    {"status": "future_pending"},
    {"status": "filled", "cumulativeQuantity": "1"},
    {"status": "partially_filled", "cumulativeQuantity": "0"},
    {"status": "partially_filled", "cumulativeQuantity": "2"},
    {"positionSide": "hedge"},
])
def test_unsupported_or_inconsistent_order_rejected(bad):
    with pytest.raises(RemoteAPIError):
        parse_order({**ORDER, **bad})


@pytest.mark.parametrize("bad_status", [None, "", "future_state", 0])
def test_unknown_position_state_never_vanishes_as_flat(bad_status):
    with pytest.raises(RemoteAPIError, match="position status"):
        parse_position({**POSITION, "status": bad_status})


@pytest.mark.parametrize("value", [True, False, "NaN", "Infinity", "1e10001", "1e-10001", "0." + "1" * 300])
def test_invalid_financial_input_rejected(value):
    with pytest.raises(ValueError):
        finite_decimal(value)


def test_exact_numeric_json_tokens_survive_rest_and_derived_finance():
    long_balance = "1000.123456789012345678901234567890123456789"
    tiny = "0.000000000000000000000000000000000000011"
    payload = (
        '{"accountId":"selected","balance":' + long_balance +
        ',"totalUnrealizedPnl":' + tiny +
        ',"isolatedPositionMargin":1e-37,'
        '"crossPositionMargin":0,"crossOrderMargin":0,"isolatedOrderMargin":0,'
        '"availableBalance":"0.00000000000000000000000001"}'
    )
    def handler(request):
        return httpx.Response(200, text=payload)

    async def run():
        rest = ProprRest("https://api.propr.xyz/v1", "synthetic-key",
                         transport=httpx.MockTransport(handler))
        try:
            raw = await rest.request("GET", "/accounts/selected")
            assert isinstance(raw["balance"], Decimal)
            assert raw["balance"] == Decimal(long_balance)
            assert raw["totalUnrealizedPnl"] == Decimal(tiny)
            snap = parse_account("selected", None, raw)
            assert snap.balance == Decimal(long_balance)
            assert snap.equity == Decimal("1000.123456789012345678901234567890123456900")
            assert snap.margin_used == Decimal("1e-37")
            assert snap.buying_power == Decimal("0.00000000000000000000000001")
        finally:
            await rest.close()

    # Construct the expected full-precision sum outside default Decimal context.
    # The numeric fields above intentionally exceed 28 significant digits.
    asyncio.run(run())


@pytest.mark.parametrize("body", [
    b'{"balance":NaN}',
    b'{"balance":Infinity}',
    b'{"balance":-Infinity}',
    b'{"balance":01}',
    b'{"balance":',
])
def test_invalid_json_tokens_redacted(body):
    async def run():
        rest = ProprRest("https://api.propr.xyz/v1", "secret-not-for-errors",
                         transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)))
        try:
            with pytest.raises(RemoteAPIError) as error:
                await rest.request("GET", "/accounts/test")
            assert "secret-not-for-errors" not in str(error.value)
            assert "balance" not in str(error.value)
        finally:
            await rest.close()
    asyncio.run(run())


def test_missing_provider_account_echo_not_proof():
    snap = parse_account("selected", None, {"balance": "10"})
    assert snap.account_id == "selected"
    assert snap.complete is False
