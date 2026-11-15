import asyncio
import base64
import hashlib
import hmac
from decimal import Decimal

import httpx
import pytest

from connectors.vanta import VantaClient, VantaConfig
from connectors.vanta.client import (
    MutationDenied,
    VantaAccountScopeError,
    VantaAuthenticationError,
    VantaConflictError,
    VantaPermissionError,
    VantaRateLimitError,
    VantaResponseError,
    VantaNotFoundError,
    VantaServerError,
    VantaTransportError,
    VantaValidationError,
    canonical_string,
)
from connectors.vanta.tests.fixtures import (
    ACCOUNT,
    KEY_ID,
    OTHER_ACCOUNT,
    SECRET,
    account_payload,
    envelope,
    key_payload,
    order_payload,
    payout_payload,
    payouts_payload,
    position_payload,
    trade_pairs_payload,
    trade_payload,
)


def run(coro):
    return asyncio.run(coro)


def client_with(handler):
    return VantaClient(
        VantaConfig(ACCOUNT, KEY_ID, SECRET), transport=httpx.MockTransport(handler)
    )


def ok(data):
    return httpx.Response(200, json=envelope(data))


def assert_signed(request):
    headers = request.headers
    assert headers["X-Vanta-Key-Id"] == KEY_ID
    assert headers["X-Vanta-Timestamp"].isdigit()
    assert headers["X-Vanta-Nonce"]
    canonical = canonical_string(
        request.method,
        request.url.raw_path.decode("ascii"),
        headers["X-Vanta-Timestamp"],
        headers["X-Vanta-Nonce"],
        request.content,
    )
    expected = base64.b64encode(
        hmac.new(
            SECRET.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
        ).digest()
    ).decode("ascii")
    assert headers["X-Vanta-Signature"] == f"v1={expected}"


def test_account_read_verifies_key_identity_then_parses_decimal():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/v1/keys/self":
            return ok(key_payload())
        if request.url.path == "/api/v1/trading/account":
            return ok(account_payload(futureField={"kept": True}))
        raise AssertionError(request.url.path)

    async def scenario():
        client = client_with(handler)
        try:
            account = await client.get_account()
        finally:
            await client.close()
        return account

    account = run(scenario())
    assert [request.url.path for request in calls] == [
        "/api/v1/keys/self",
        "/api/v1/trading/account",
    ]
    for request in calls:
        assert_signed(request)
    assert account["accountId"] == ACCOUNT
    assert account["account"]["currentEquity"] == Decimal("25180.25")
    assert isinstance(account["account"]["currentEquity"], Decimal)
    assert account["accountSize"] == 25000
    assert account["futureField"] == {"kept": True}
    assert account["proTrack"] is None


def test_key_account_mismatch_blocks_account_read():
    calls = []

    def handler(request):
        calls.append(request)
        return ok(key_payload(OTHER_ACCOUNT))

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(VantaAccountScopeError):
                await client.get_account()
        finally:
            await client.close()

    run(scenario())
    assert [request.url.path for request in calls] == ["/api/v1/keys/self"]


def test_payload_account_mismatch_is_rejected():
    def handler(request):
        if request.url.path == "/api/v1/keys/self":
            return ok(key_payload())
        return ok(account_payload(OTHER_ACCOUNT))

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(VantaAccountScopeError):
                await client.get_account()
        finally:
            await client.close()

    run(scenario())


def test_missing_account_id_in_account_payload_is_rejected():
    payload = account_payload()
    payload.pop("accountId")

    def handler(request):
        if request.url.path == "/api/v1/keys/self":
            return ok(key_payload())
        return ok(payload)

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(VantaResponseError):
                await client.get_account()
        finally:
            await client.close()

    run(scenario())


def test_identity_is_cached_across_reads():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/v1/keys/self":
            return ok(key_payload())
        if request.url.path == "/api/v1/trading/positions":
            return ok([position_payload()])
        if request.url.path == "/api/v1/trading/orders":
            return ok([order_payload()])
        raise AssertionError(request.url.path)

    async def scenario():
        client = client_with(handler)
        try:
            await client.get_positions()
            await client.get_orders()
        finally:
            await client.close()

    run(scenario())
    assert calls == [
        "/api/v1/keys/self",
        "/api/v1/trading/positions",
        "/api/v1/trading/orders",
    ]


def test_get_key_always_refetches_and_verifies():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return ok(key_payload())

    async def scenario():
        client = client_with(handler)
        try:
            first = await client.get_key()
            second = await client.get_key()
        finally:
            await client.close()
        return first, second

    first, second = run(scenario())
    assert calls == ["/api/v1/keys/self", "/api/v1/keys/self"]
    assert first["accountId"] == ACCOUNT
    assert second["scopes"] == ["trade:place", "trade:close", "trade:read"]


def test_get_key_rejects_mismatched_account():
    def handler(request):
        return ok(key_payload(OTHER_ACCOUNT))

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(VantaAccountScopeError):
                await client.get_key()
        finally:
            await client.close()

    run(scenario())


def test_positions_orders_and_trades_preserve_null_and_decimal():
    def handler(request):
        if request.url.path == "/api/v1/keys/self":
            return ok(key_payload())
        if request.url.path == "/api/v1/trading/positions":
            return ok(
                [
                    position_payload(
                        netQuantity=None,
                        positionType="FUTURE_UNKNOWN",
                        extra="kept",
                    )
                ]
            )
        if request.url.path == "/api/v1/trading/orders":
            return ok([order_payload()])
        if request.url.path == "/api/v1/trading/trades":
            return ok([trade_payload()])
        raise AssertionError(request.url.path)

    async def scenario():
        client = client_with(handler)
        try:
            positions = await client.get_positions()
            orders = await client.get_orders()
            trades = await client.get_trades()
        finally:
            await client.close()
        return positions, orders, trades

    positions, orders, trades = run(scenario())
    assert positions[0]["netQuantity"] is None
    assert positions[0]["positionType"] == "FUTURE_UNKNOWN"
    assert positions[0]["extra"] == "kept"
    assert positions[0]["averageEntryPrice"] == Decimal("62450.12")
    assert orders[0]["quantity"] is None
    assert orders[0]["limitPrice"] == Decimal("3200")
    assert trades[0]["quantity"] == Decimal("0.01663")
    assert trades[0]["realizedPnl"] == Decimal("23.62")


def test_positions_row_with_another_account_is_rejected():
    def handler(request):
        if request.url.path == "/api/v1/keys/self":
            return ok(key_payload())
        return ok([position_payload(accountId=OTHER_ACCOUNT)])

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(VantaAccountScopeError):
                await client.get_positions()
        finally:
            await client.close()

    run(scenario())


def test_failed_identity_is_not_cached():
    calls = []
    state = {"key": key_payload(OTHER_ACCOUNT)}

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/v1/keys/self":
            return ok(state["key"])
        return ok(account_payload())

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(VantaAccountScopeError):
                await client.get_account()
            state["key"] = key_payload()
            account = await client.get_account()
        finally:
            await client.close()
        return account

    account = run(scenario())
    assert calls == [
        "/api/v1/keys/self",
        "/api/v1/keys/self",
        "/api/v1/trading/account",
    ]
    assert account["accountId"] == ACCOUNT


def test_account_read_does_not_send_an_account_query():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/api/v1/keys/self":
            return ok(key_payload())
        return ok(account_payload())

    async def scenario():
        client = client_with(handler)
        try:
            await client.get_account()
        finally:
            await client.close()

    run(scenario())
    account_request = requests[-1]
    assert account_request.url.query == b""
    assert account_request.url.path == "/api/v1/trading/account"


def test_key_and_trade_pairs_require_account_id():
    def missing_key(request):
        payload = key_payload()
        payload.pop("accountId")
        return ok(payload)

    def missing_pairs(request):
        if request.url.path == "/api/v1/keys/self":
            return ok(key_payload())
        payload = trade_pairs_payload()
        payload.pop("accountId")
        return ok(payload)

    async def scenario(handler, method):
        client = client_with(handler)
        try:
            with pytest.raises(VantaResponseError):
                await method(client)
        finally:
            await client.close()

    run(scenario(missing_key, VantaClient.get_key))
    run(scenario(missing_pairs, VantaClient.get_trade_pairs))


def test_trade_pairs_rejects_another_account():
    def handler(request):
        if request.url.path == "/api/v1/keys/self":
            return ok(key_payload())
        return ok(trade_pairs_payload(accountId=OTHER_ACCOUNT))

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(VantaAccountScopeError):
                await client.get_trade_pairs()
        finally:
            await client.close()

    run(scenario())


def test_trade_pairs_returns_allowed_and_disabled():
    def handler(request):
        if request.url.path == "/api/v1/keys/self":
            return ok(key_payload())
        return ok(trade_pairs_payload())

    async def scenario():
        client = client_with(handler)
        try:
            return await client.get_trade_pairs()
        finally:
            await client.close()

    pairs = run(scenario())
    assert pairs["accountId"] == ACCOUNT
    assert pairs["allowed"][0]["tradePairId"] == "BTCUSDC"
    assert pairs["disabled"][0]["tradePairId"] == "XAUUSD"


def test_payouts_auto_paginates_and_signs_encoded_query():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/v1/keys/self":
            return ok(key_payload())
        cursor = request.url.params.get("cursor")
        if cursor is None:
            return ok(payouts_payload(next_cursor="cur/1+="))
        assert cursor == "cur/1+="
        return ok(
            payouts_payload(
                items=[payout_payload(id="second")],
                next_cursor=None,
                accrualStatus="omitted",
                accrual=None,
            )
        )

    async def scenario():
        client = client_with(handler)
        try:
            return await client.get_payouts(limit=2)
        finally:
            await client.close()

    result = run(scenario())
    assert [request.url.path for request in calls] == [
        "/api/v1/keys/self",
        "/api/v1/trading/payouts",
        "/api/v1/trading/payouts",
    ]
    assert b"limit=2" in calls[1].url.raw_path
    assert b"cursor=cur%2F1%2B%3D" in calls[2].url.raw_path
    for request in calls:
        assert_signed(request)
    assert calls[1].headers["X-Vanta-Nonce"] != calls[2].headers["X-Vanta-Nonce"]
    assert [row["id"] for row in result["items"]] == [
        payout_payload()["id"],
        "second",
    ]
    assert result["accrualStatus"] == "ok"
    assert result["accrual"]["estimatedAmount"] == Decimal("88.25")


def test_payouts_rejects_repeated_cursor():
    def handler(request):
        if request.url.path == "/api/v1/keys/self":
            return ok(key_payload())
        return ok(payouts_payload(next_cursor="same"))

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(VantaResponseError):
                await client.get_payouts()
        finally:
            await client.close()

    run(scenario())


def test_payouts_rejects_malformed_cursor():
    def handler(request):
        if request.url.path == "/api/v1/keys/self":
            return ok(key_payload())
        return ok(payouts_payload(next_cursor=123))

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(VantaResponseError):
                await client.get_payouts()
        finally:
            await client.close()

    run(scenario())


@pytest.mark.parametrize("limit", [0, -1, 201, "50", 1.5])
def test_payouts_rejects_invalid_limit_before_io(limit):
    calls = []

    def handler(request):
        calls.append(request)
        return ok(payouts_payload())

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(ValueError):
                await client.get_payouts(limit=limit)
        finally:
            await client.close()

    run(scenario())
    assert calls == []


def test_error_statuses_map_to_typed_errors():
    def handler(request):
        return httpx.Response(401, json={"error": "Bad signature"})

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(VantaAuthenticationError) as err:
                await client.get_key()
        finally:
            await client.close()
        return err.value

    error = run(scenario())
    assert error.status_code == 401
    assert error.code == "Bad signature"
    assert SECRET not in str(error)


def test_insufficient_scope_carries_required_scope():
    def handler(request):
        return httpx.Response(
            403,
            json={"error": "insufficient_scope", "required_scope": "trade:read"},
        )

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(VantaPermissionError) as err:
                await client.get_key()
        finally:
            await client.close()
        return err.value

    error = run(scenario())
    assert error.required_scope == "trade:read"


def test_rate_limit_exposes_retry_after_and_headers():
    def handler(request):
        return httpx.Response(
            429,
            json={"error": "rate_limited"},
            headers={
                "Retry-After": "7",
                "X-RateLimit-Limit": "120",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "1717718460",
            },
        )

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(VantaRateLimitError) as err:
                await client.get_key()
        finally:
            await client.close()
        return err.value

    error = run(scenario())
    assert str(error.retry_after) == "7"
    assert error.rate_limit


def test_validation_and_not_found_errors_are_distinct():
    def bad_request(request):
        return httpx.Response(400, json={"error": "limit must be an integer between 1 and 200"})

    def not_found(request):
        return httpx.Response(404, json={"error": "Account not found"})

    async def scenario(handler, error_type):
        client = client_with(handler)
        try:
            with pytest.raises(error_type):
                await client.get_key()
        finally:
            await client.close()

    run(scenario(bad_request, VantaValidationError))
    run(scenario(not_found, VantaNotFoundError))


def test_conflict_and_server_errors_are_distinct():
    def conflict(request):
        return httpx.Response(409, json={"error": "Nonce"})

    def unavailable(request):
        return httpx.Response(503, json={"error": "Account snapshot temporarily unavailable"})

    async def scenario(handler, error_type):
        client = client_with(handler)
        try:
            with pytest.raises(error_type):
                await client.get_key()
        finally:
            await client.close()

    run(scenario(conflict, VantaConflictError))
    run(scenario(unavailable, VantaServerError))


def test_malformed_envelope_is_a_response_error():
    def handler(request):
        return httpx.Response(200, json={"success": True})

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(VantaResponseError):
                await client.get_key()
        finally:
            await client.close()

    run(scenario())


def test_transport_failure_is_not_an_inferred_outcome():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(VantaTransportError):
                await client.get_key()
        finally:
            await client.close()

    run(scenario())


def test_raw_http_post_is_denied_before_the_injected_transport():
    calls = []

    def handler(request):
        calls.append(request)
        return ok({})

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(MutationDenied):
                await client._http.post("/api/v1/trading/orders", content=b"{}")
            with pytest.raises(MutationDenied):
                await client._http.request("DELETE", "/api/v1/trading/orders")
        finally:
            await client.close()

    run(scenario())
    assert calls == []


def test_guard_rejects_a_foreign_host_before_transport():
    calls = []

    def handler(request):
        calls.append(request)
        return ok({})

    async def scenario():
        client = client_with(handler)
        try:
            with pytest.raises(PermissionError):
                await client._http.get("https://evil.example/api/v1/trading/account")
        finally:
            await client.close()

    run(scenario())
    assert calls == []


def test_public_client_interface_is_complete():
    import inspect

    for name in (
        "get_account",
        "get_positions",
        "get_orders",
        "get_trades",
        "get_payouts",
        "get_trade_pairs",
        "get_key",
        "place_order",
        "close",
    ):
        assert inspect.iscoroutinefunction(getattr(VantaClient, name))
    assert callable(VantaClient.prepare_order)
    assert not inspect.iscoroutinefunction(VantaClient.prepare_order)


def test_async_context_manager_closes():
    async def scenario():
        async with client_with(lambda request: ok({})) as client:
            assert isinstance(client, VantaClient)

    run(scenario())


def test_close_is_idempotent():
    async def scenario():
        client = client_with(lambda request: ok({}))
        await client.close()
        await client.close()

    run(scenario())
