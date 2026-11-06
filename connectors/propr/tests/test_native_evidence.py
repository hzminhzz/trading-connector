"""Native Propr evidence contract: credential-free, deliberately not opening-ready."""
import asyncio
from datetime import datetime
from decimal import Decimal

import httpx
import pytest

from connectors.propr.rest import ProprRest
from connectors.propr.native_evidence import inspect_native_evidence, SCHEMA_VERSION


ACCOUNT = "fixture-account"
ENDPOINT = "https://api.propr.xyz/v1"
PROVIDER_TIME = "2026-11-06T03:00:00Z"
NOW = int(datetime.fromisoformat(PROVIDER_TIME.replace("Z", "+00:00")).timestamp() * 1000) + 500


def account(**changes):
    values = {
        "accountId": ACCOUNT, "updatedAt": PROVIDER_TIME,
        "balance": "1000.0000000000001", "availableBalance": "480",
        "totalUnrealizedPnl": "-20", "isolatedPositionMargin": "100",
        "crossPositionMargin": "200", "crossOrderMargin": "30",
        "isolatedOrderMargin": "40", "totalInitialMargin": "250",
        "totalMaintenanceMargin": "100",
    }
    values.update(changes)
    return values


def order(i, **changes):
    values = {
        "orderId": f"ord-{i}", "intentId": "durable-intent-1", "accountId": ACCOUNT,
        "asset": "xyz:AAPL", "side": "buy", "positionSide": "long",
        "status": "partially_filled", "quantity": "1.25",
        "cumulativeQuantity": "0.25", "averageFillPrice": "123.123456789123",
        "cumulativeTradingFees": "0.02", "reduceOnly": False,
        "createdAt": PROVIDER_TIME,
    }
    values.update(changes)
    return values


def position(side):
    return {
        "positionId": f"pos-{side}", "accountId": ACCOUNT, "asset": "xyz:AAPL",
        "positionSide": side, "quantity": "2.000000000000001",
        "entryPrice": "120", "markPrice": "123", "status": "open",
    }


def trade(i, **changes):
    values = {
        "tradeId": f"trade-{i}", "orderId": "ord-1", "accountId": ACCOUNT,
        "asset": "xyz:AAPL", "quantity": "0.25", "price": "123.123456789123",
        "executedAt": PROVIDER_TIME, "side": "buy",
    }
    values.update(changes)
    return values


def invoke(*, account_payload=None, inventory=None, discovery=None, endpoint=ENDPOINT,
           stream_connected=False, known_at_ms=NOW):
    inv = inventory or {}
    calls = []

    def handler(req):
        calls.append((req.method, req.url.path, str(req.url.query)))
        path = req.url.path
        if path.endswith("/book-account-issuances"):
            return httpx.Response(200, json={"data": []})
        if path.endswith("/challenge-attempts"):
            rows = [{"accountId": ACCOUNT, "attemptId": "trial-1"}] if discovery is None else discovery
            return httpx.Response(200, json={"data": rows})
        if path.endswith("/accounts/" + ACCOUNT):
            return httpx.Response(200, json=account() if account_payload is None else account_payload)
        for suffix in ("orders", "positions", "trades"):
            if path.endswith("/" + suffix):
                rows = inv.get(suffix, [])
                offset = int(req.url.params["offset"])
                limit = int(req.url.params["limit"])
                return httpx.Response(200, json={"data": rows[offset:offset + limit]})
        raise AssertionError("Unexpected read path")

    async def run():
        rest = ProprRest(ENDPOINT, "fixture-secret", transport=httpx.MockTransport(handler))
        try:
            return await inspect_native_evidence(
                rest, account_id=ACCOUNT, endpoint=endpoint,
                known_at_ms=known_at_ms, stream_connected=stream_connected,
            )
        finally:
            await rest.close()

    return asyncio.run(run()), calls


def test_contract_preserves_provider_semantics_without_fake_cash_or_cutoff():
    evidence, calls = invoke(inventory={
        "orders": [order(1)], "positions": [position("long")],
        "trades": [trade(1)],
    }, stream_connected=True)
    assert evidence.schema == SCHEMA_VERSION
    assert evidence.account_bound and evidence.attempt_id == "trial-1"
    assert evidence.account_source == "challenge"
    assert evidence.endpoint == ENDPOINT
    assert evidence.provider_time == PROVIDER_TIME
    assert evidence.local_known_at_ms == NOW
    assert evidence.financial.balance.value == Decimal("1000.0000000000001")
    assert evidence.financial.balance.provider_field == "balance"
    assert evidence.financial.equity.value == Decimal("1080.0000000000001")
    assert evidence.financial.equity.provenance == "DERIVED_SDK_FORMULA"
    assert evidence.financial.buying_power.provider_field == "availableBalance"
    assert evidence.financial.cash.value is None
    assert evidence.financial.cash.provenance == "UNKNOWN"
    assert evidence.financial.initial_margin.value == Decimal("250")
    assert evidence.financial.maintenance_margin.value == Decimal("100")
    assert evidence.inventory_read_complete
    assert evidence.inventory.orders[0].intent_id == "durable-intent-1"
    assert evidence.inventory.orders[0].fee_paid == Decimal("0.02")
    assert evidence.inventory.trades[0].fee is None
    assert evidence.inventory.trades[0].asset == "xyz:AAPL"
    assert evidence.inventory.positions[0].quantity == Decimal("2.000000000000001")
    assert evidence.stream_connected
    assert evidence.cutoff is None
    assert evidence.history_complete is False
    assert evidence.opening_ready is False
    assert "PROVIDER_CUTOFF_UNPROVEN" in evidence.reasons
    assert set(method for method, *_ in calls) == {"GET"}


@pytest.mark.parametrize(("changes", "expected"), [
    ({"totalMaintenanceMargin": None}, "MISSING_MAINTENANCE_MARGIN"),
    ({"totalInitialMargin": "-2"}, "INVALID_MARGIN"),
    ({"balance": "NaN"}, "ACCOUNT_FINANCIAL_INVALID"),
    ({"updatedAt": None}, "PROVIDER_TIME_UNVERIFIED"),
    ({"updatedAt": "2020-01-01T00:00:00Z"}, "PROVIDER_TIME_STALE"),
])
def test_invalid_or_stale_financial_fields_fail_closed(changes, expected):
    evidence, _ = invoke(account_payload=account(**changes))
    assert evidence.opening_ready is False
    assert expected in evidence.reasons


def test_wrong_endpoint_is_rejected_before_any_network_read():
    evidence, calls = invoke(endpoint="https://different.invalid/v1")
    assert not calls
    assert evidence.inventory is None
    assert not evidence.account_bound
    assert "ENDPOINT_BINDING_MISMATCH" in evidence.reasons


def test_ambiguous_discovery_never_selects_an_account():
    evidence, calls = invoke(discovery=[
        {"accountId": ACCOUNT}, {"accountId": ACCOUNT},
    ])
    assert evidence.account_bound is False
    assert evidence.inventory is None
    assert "ACCOUNT_UNVERIFIED" in evidence.reasons
    assert not any(p.endswith("/accounts/" + ACCOUNT) for _, p, _ in calls)


def test_cross_account_and_shifted_pages_never_become_empty_inventory():
    other, _ = invoke(inventory={"orders": [order(1, accountId="other")]})
    assert "INVENTORY_UNVERIFIED" in other.reasons
    assert other.inventory is None
    assert not other.inventory_read_complete

    shifted = [order(i) for i in range(20)] + [order(19)]
    duplicate, _ = invoke(inventory={"orders": shifted})
    assert "INVENTORY_UNVERIFIED" in duplicate.reasons
    assert duplicate.inventory is None
    assert not duplicate.inventory_read_complete


def test_hedge_positions_are_preserved_natively_but_not_flattened_for_guardian():
    evidence, _ = invoke(inventory={
        "positions": [position("long"), position("short")],
    })
    assert len(evidence.inventory.positions) == 2
    assert "HEDGE_MODE_UNSUPPORTED_FOR_GUARDIAN" in evidence.reasons
    assert not evidence.opening_ready


def test_repeated_rest_reads_and_stream_connectivity_do_not_prove_cutoff():
    first, _ = invoke(inventory={"orders": [order(1)]}, stream_connected=True)
    second, _ = invoke(inventory={"orders": [order(1)]}, stream_connected=True)
    assert first == second
    assert not first.history_complete and not second.history_complete
    assert "PROVIDER_CUTOFF_UNPROVEN" in second.reasons
    disconnected, _ = invoke(stream_connected=False)
    assert "STREAM_NOT_CONNECTED" in disconnected.reasons
    assert disconnected.opening_ready is False


def test_invalid_trades_do_not_create_partial_verified_inventory():
    evidence, _ = invoke(inventory={"trades": [trade(1, quantity="NaN")]})
    assert evidence.inventory is None
    assert "INVENTORY_UNVERIFIED" in evidence.reasons
    assert evidence.opening_ready is False
