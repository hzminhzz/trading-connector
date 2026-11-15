"""Tests for explicitly armed single-order ProprOrderClient."""
import asyncio
from decimal import Decimal
import json

import httpx
import pytest

from connectors.propr.rest import ProprOrderClient, ProviderHTTPError, RemoteAPIError


def test_order_client_requires_explicit_arming_and_account_match():
    with pytest.raises(PermissionError, match="PROPR_EXECUTION_NOT_ENABLED"):
        ProprOrderClient(
            "https://api.propr.xyz/v1",
            "pk_live_dummy",
            account_id="acc-1",
            confirm_account_id="acc-1",
            allow_execution=False,
        )
    with pytest.raises(PermissionError, match="PROPR_ACCOUNT_CONFIRMATION_MISMATCH"):
        ProprOrderClient(
            "https://api.propr.xyz/v1",
            "pk_live_dummy",
            account_id="acc-1",
            confirm_account_id="acc-2",
            allow_execution=True,
        )
    with pytest.raises(ValueError, match="pinned"):
        ProprOrderClient(
            "https://api.beta.propr.xyz/v1",
            "pk_live_dummy",
            account_id="acc-1",
            confirm_account_id="acc-1",
            allow_execution=True,
        )


def test_submit_market_order_sends_single_order_with_intent_id_and_cancel():
    requests_seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests_seen.append((request.method, request.url.path, body))
        if request.url.path.endswith("/orders"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "orderId": "ord-123",
                            "intentId": "intent-001",
                            "accountId": "acc-1",
                            "asset": "BTC",
                            "status": "filled",
                            "cumulativeQuantity": "0.01",
                        }
                    ]
                },
            )
        if request.url.path.endswith("/orders/ord-123/cancel"):
            return httpx.Response(200, json={"data": {"orderId": "ord-123", "status": "cancelled"}})
        raise AssertionError(f"Unexpected path: {request.url.path}")

    async def run():
        client = ProprOrderClient(
            "https://api.propr.xyz/v1",
            "pk_live_dummy",
            account_id="acc-1",
            confirm_account_id="acc-1",
            allow_execution=True,
            transport=httpx.MockTransport(handler),
        )
        try:
            ack = await client.submit_market_order(
                intent_id="intent-001",
                asset="BTC",
                side="sell",
                position_side="long",
                quantity=Decimal("0.01"),
                reduce_only=True,
            )
            assert ack["orderId"] == "ord-123"
            cancel = await client.cancel_order("ord-123")
            assert cancel["data"]["status"] == "cancelled"
        finally:
            await client.close()

    asyncio.run(run())
    assert len(requests_seen) == 2
    method, path, body = requests_seen[0]
    assert method == "POST"
    assert path == "/v1/accounts/acc-1/orders"
    assert body == {
        "orders": [
            {
                "accountId": "acc-1",
                "intentId": "intent-001",
                "exchange": "hyperliquid",
                "type": "market",
                "side": "sell",
                "positionSide": "long",
                "productType": "perp",
                "timeInForce": "IOC",
                "asset": "BTC",
                "base": "BTC",
                "quote": "USDC",
                "quantity": "0.01",
                "reduceOnly": True,
                "closePosition": False,
            }
        ]
    }


def test_submit_timeout_is_never_retried_and_errors_are_redacted():
    calls = 0

    def timeout_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out")

    async def run_timeout():
        client = ProprOrderClient(
            "https://api.propr.xyz/v1",
            "pk_live_dummy",
            account_id="acc-1",
            confirm_account_id="acc-1",
            allow_execution=True,
            transport=httpx.MockTransport(timeout_handler),
        )
        try:
            with pytest.raises(RemoteAPIError, match="not retried"):
                await client.submit_market_order(
                    intent_id="intent-timeout",
                    asset="HYPE",
                    side="buy",
                    position_side="long",
                    quantity=Decimal("1"),
                )
        finally:
            await client.close()

    asyncio.run(run_timeout())
    assert calls == 1

    def err_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "sensitive-detail"})

    async def run_http_err():
        client = ProprOrderClient(
            "https://api.propr.xyz/v1",
            "pk_live_dummy",
            account_id="acc-1",
            confirm_account_id="acc-1",
            allow_execution=True,
            transport=httpx.MockTransport(err_handler),
        )
        try:
            with pytest.raises(ProviderHTTPError, match="400") as exc:
                await client.submit_market_order(
                    intent_id="intent-400",
                    asset="HYPE",
                    side="buy",
                    position_side="long",
                    quantity=Decimal("1"),
                )
            assert "sensitive-detail" not in str(exc.value)
        finally:
            await client.close()

    asyncio.run(run_http_err())
