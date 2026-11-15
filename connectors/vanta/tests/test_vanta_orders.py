import asyncio
import base64
import hashlib
import hmac
import json
from decimal import Decimal

import httpx
import pytest

from connectors.vanta import VantaClient, VantaConfig
from connectors.vanta.client import (
    MutationDenied,
    PreparedOrder,
    canonical_string,
    signed_headers,
)
from connectors.vanta.tests.fixtures import ACCOUNT, KEY_ID, ORDER_ID, SECRET


def run(coro):
    return asyncio.run(coro)


def client_with(handler):
    return VantaClient(
        VantaConfig(ACCOUNT, KEY_ID, SECRET), transport=httpx.MockTransport(handler)
    )


VARIANTS = {
    "MARKET": {
        "execution_type": "MARKET",
        "trade_pair": "BTCUSDC",
        "order_type": "LONG",
        "value": Decimal("1000.5"),
    },
    "LIMIT": {
        "execution_type": "LIMIT",
        "trade_pair": "ETHUSDC",
        "order_type": "LONG",
        "value": Decimal("1500"),
        "limit_price": Decimal("3200.25"),
    },
    "BRACKET": {
        "execution_type": "BRACKET",
        "trade_pair": "BTCUSDC",
        "order_type": "LONG",
        "bracket_orders": [
            {"take_profit": 72000, "quantity": Decimal("0.115")},
            {"stop_loss": 61000, "quantity": Decimal("0.115")},
        ],
    },
    "LIMIT_EDIT": {
        "execution_type": "LIMIT_EDIT",
        "trade_pair": "BTCUSDC",
        "order_type": "LONG",
        "quantity": Decimal("0.115"),
        "bracket_orders": [{"take_profit": 72000, "quantity": Decimal("0.115")}],
    },
    "LIMIT_CANCEL": {"execution_type": "LIMIT_CANCEL", "order_uuid": ORDER_ID},
    "FLAT_ALL": {"execution_type": "FLAT_ALL", "order_uuid": "ALL"},
}


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_prepare_order_builds_exact_envelope(name):
    payload = VARIANTS[name]
    async def scenario():
        client = client_with(lambda request: httpx.Response(500))
        try:
            return client.prepare_order(payload)
        finally:
            await client.close()

    prepared = run(scenario())
    assert isinstance(prepared, PreparedOrder)
    assert prepared.method == "POST"
    assert prepared.path == "/api/v1/trading/orders"
    assert prepared.body["accountId"] == ACCOUNT
    assert prepared.body["trade"] == payload
    assert isinstance(prepared.body_json, str)
    assert " " not in prepared.body_json
    assert prepared.body_json.startswith('{"accountId":"' + ACCOUNT + '","trade":')
    decoded = json.loads(prepared.body_json, parse_float=Decimal)
    assert decoded == {"accountId": ACCOUNT, "trade": payload}


def test_decimal_wire_values_are_lossless_and_not_floats():
    payload = {
        "execution_type": "MARKET",
        "trade_pair": "BTCUSDC",
        "order_type": "SHORT",
        "quantity": Decimal("0.1") + Decimal("0.2"),
        "take_profit": Decimal("62450.12"),
        "stop_loss": Decimal("61000.00"),
    }
    async def scenario():
        client = client_with(lambda request: httpx.Response(500))
        try:
            return client.prepare_order(payload)
        finally:
            await client.close()

    prepared = run(scenario())
    assert prepared.body["trade"]["quantity"] == Decimal("0.3")
    assert '"quantity":0.3' in prepared.body_json
    assert '"take_profit":62450.12' in prepared.body_json
    assert '"stop_loss":61000.00' in prepared.body_json
    assert "e-" not in prepared.body_json.lower()


def test_prepared_body_json_is_the_signed_body():
    payload = VARIANTS["MARKET"]
    async def scenario():
        client = client_with(lambda request: httpx.Response(500))
        try:
            return client.prepare_order(payload)
        finally:
            await client.close()

    prepared = run(scenario())
    body = prepared.body_json.encode("utf-8")
    headers = signed_headers(
        KEY_ID,
        SECRET,
        prepared.method,
        prepared.path,
        body,
        timestamp="1717718400000",
        nonce="3c1d5e7f90a2b4c6d8e0f2a4b6c8d0e2",
    )
    canonical = canonical_string(
        prepared.method,
        prepared.path,
        "1717718400000",
        "3c1d5e7f90a2b4c6d8e0f2a4b6c8d0e2",
        body,
    )
    expected = base64.b64encode(
        hmac.new(SECRET.encode(), canonical.encode(), hashlib.sha256).digest()
    ).decode()
    assert headers["X-Vanta-Signature"] == f"v1={expected}"
    assert hashlib.sha256(body).hexdigest() in canonical


@pytest.mark.parametrize(
    "payload",
    [
        {**VARIANTS["MARKET"], "typo_value": 1000},
        {**VARIANTS["MARKET"], "accountId": ACCOUNT},
        {**VARIANTS["MARKET"], "value": 1000.5},
        {**VARIANTS["MARKET"], "value": "1000"},
        {**VARIANTS["MARKET"], "value": True},
        {"execution_type": "MARKET", "trade_pair": "BTCUSDC", "order_type": "LONG"},
        {
            "execution_type": "MARKET",
            "trade_pair": "BTCUSDC",
            "order_type": "LONG",
            "value": 1000,
            "quantity": 1,
        },
        {
            "execution_type": "LIMIT",
            "trade_pair": "BTCUSDC",
            "order_type": "LONG",
            "value": 1000,
        },
        {
            "execution_type": "BRACKET",
            "trade_pair": "BTCUSDC",
            "order_type": "LONG",
            "bracket_orders": [],
        },
        {"execution_type": "LIMIT_CANCEL"},
        {"execution_type": "FLAT_ALL"},
        {"execution_type": "STOP_LIMIT", "trade_pair": "BTCUSDC", "order_type": "LONG"},
        {**VARIANTS["MARKET"], "order_type": "SIDEWAYS"},
        {**VARIANTS["MARKET"], "trade_pair": ""},
    ],
)
def test_prepare_order_rejects_unsafe_or_unknown_payloads(payload):
    async def scenario():
        client = client_with(lambda request: httpx.Response(500))
        try:
            with pytest.raises(ValueError):
                client.prepare_order(payload)
        finally:
            await client.close()

    run(scenario())


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_place_order_is_denied_before_the_transport(name):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"success": True, "data": {}})

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(MutationDenied) as err:
                await client.place_order(VARIANTS[name])
        finally:
            await client.close()
        return err.value

    error = run(scenario())
    assert calls == []
    assert error.prepared is not None
    assert error.prepared.path == "/api/v1/trading/orders"
    assert error.prepared.body["trade"] == VARIANTS[name]


def test_place_order_rejects_bad_payload_without_touching_transport():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200)

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(ValueError):
                await client.place_order({"execution_type": "MARKET"})
        finally:
            await client.close()

    run(scenario())
    assert calls == []


@pytest.mark.parametrize("payload", [None, [], "MARKET", {}, {"execution_type": "MARKET"}])
def test_prepare_order_rejects_non_mapping_or_incomplete_payloads(payload):
    async def scenario():
        client = client_with(lambda request: httpx.Response(500))
        try:
            with pytest.raises(ValueError):
                client.prepare_order(payload)
        finally:
            await client.close()

    run(scenario())


def test_prepare_order_does_not_mutate_the_caller_payload():
    payload = {
        "execution_type": "MARKET",
        "trade_pair": "BTCUSDC",
        "order_type": "LONG",
        "value": Decimal("1000"),
    }
    original = dict(payload)

    async def scenario():
        client = client_with(lambda request: httpx.Response(500))
        try:
            client.prepare_order(payload)
        finally:
            await client.close()

    run(scenario())
    assert payload == original
