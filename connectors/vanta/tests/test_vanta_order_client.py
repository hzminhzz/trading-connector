"""Tests for explicitly armed VantaOrderClient."""
from __future__ import annotations

import asyncio
import hashlib
from decimal import Decimal
import json

import httpx
import pytest

from connectors.vanta import (
    VantaConfig,
    VantaOrderClient,
    VantaTransportError,
    VantaValidationError,
)


def _cfg() -> VantaConfig:
    return VantaConfig(
        account_id="6f1c2e34-9a4b-4c1d-8e2f-1a2b3c4d5e6f",
        api_key="vk_test_key_id",
        api_secret="vks_test_secret",
    )


def test_order_client_requires_explicit_arming_and_account_match() -> None:
    cfg = _cfg()
    with pytest.raises(PermissionError, match="VANTA_EXECUTION_NOT_ENABLED"):
        VantaOrderClient(cfg, confirm_account_id=cfg.account_id, allow_execution=False)
    with pytest.raises(PermissionError, match="VANTA_ACCOUNT_CONFIRMATION_MISMATCH"):
        VantaOrderClient(cfg, confirm_account_id="wrong-account", allow_execution=True)


def test_order_client_signs_and_posts_exact_body_once() -> None:
    cfg = _cfg()
    seen: list[tuple[str, str, bytes, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.content, dict(request.headers)))
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "success": True,
                    "order_uuid": "ord-vanta-1",
                    "order": {
                        "orderUuid": "ord-vanta-1",
                        "tradePair": "BTCUSDC",
                        "orderType": "LONG",
                        "executionType": "MARKET",
                        "filled": True,
                        "quantity": 0.015,
                    },
                },
            },
        )

    async def run() -> dict:
        async with VantaOrderClient(
            cfg,
            confirm_account_id=cfg.account_id,
            allow_execution=True,
            transport=httpx.MockTransport(handler),
        ) as client:
            return await client.submit_order(
                {
                    "execution_type": "MARKET",
                    "trade_pair": "BTCUSDC",
                    "order_type": "LONG",
                    "quantity": Decimal("0.015"),
                }
            )

    res = asyncio.run(run())
    assert res["order"]["orderUuid"] == "ord-vanta-1"
    assert len(seen) == 1
    method, path, raw_body, headers = seen[0]
    assert method == "POST"
    assert path == "/api/v1/trading/orders"
    assert json.loads(raw_body.decode()) == {
        "accountId": cfg.account_id,
        "trade": {
            "execution_type": "MARKET",
            "trade_pair": "BTCUSDC",
            "order_type": "LONG",
            "quantity": 0.015,
        },
    }
    assert headers["x-vanta-key-id"] == cfg.api_key
    assert headers["x-vanta-signature"].startswith("v1=")
    assert hashlib.sha256(raw_body).hexdigest()


def test_order_client_never_retries_timeout_and_surfaces_http_400() -> None:
    cfg = _cfg()
    calls = 0

    def timeout_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout")

    async def run_timeout() -> None:
        async with VantaOrderClient(
            cfg,
            confirm_account_id=cfg.account_id,
            allow_execution=True,
            transport=httpx.MockTransport(timeout_handler),
        ) as client:
            with pytest.raises(VantaTransportError, match="not retried"):
                await client.submit_order(
                    {
                        "execution_type": "MARKET",
                        "trade_pair": "BTCUSDC",
                        "order_type": "SHORT",
                        "quantity": Decimal("0.01"),
                    }
                )

    asyncio.run(run_timeout())
    assert calls == 1

    def err_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "INVALID_ORDER"})

    async def run_err() -> None:
        async with VantaOrderClient(
            cfg,
            confirm_account_id=cfg.account_id,
            allow_execution=True,
            transport=httpx.MockTransport(err_handler),
        ) as client:
            with pytest.raises(VantaValidationError, match="INVALID_ORDER"):
                await client.submit_order({"execution_type": "LIMIT_CANCEL", "order_uuid": "ord-1"})

    asyncio.run(run_err())
