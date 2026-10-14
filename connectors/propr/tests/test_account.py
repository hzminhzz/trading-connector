import asyncio
from decimal import Decimal
import httpx
import pytest

from connectors.propr.account import ReadOnlyAccount, parse_account
from connectors.propr.config import ProprConfig
from connectors.propr.rest import RemoteAPIError


def _account_payload(account_id="selected"):
    return {
        "accountId": account_id, "balance": "1000", "availableBalance": "480",
        "totalUnrealizedPnl": "-20", "isolatedPositionMargin": "100",
        "crossPositionMargin": "200", "crossOrderMargin": "30",
        "isolatedOrderMargin": "40", "totalInitialMargin": "250",
        "totalMaintenanceMargin": "100", "highWaterMark": "1100",
    }


def test_read_only_account_binding_and_no_mutations():
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path))
        assert request.headers["X-API-Key"] == "pk_live_dummy"
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"status": "OK"})
        if request.url.path.endswith("/health/services"):
            return httpx.Response(200, json={"core": "OK"})
        if request.url.path.endswith("/book-account-issuances"):
            return httpx.Response(200, json={"data": []})
        if request.url.path.endswith("/challenge-attempts"):
            return httpx.Response(200, json={"data": [
                {"accountId": "selected", "attemptId": "a1", "totalPnl": "100"}]})
        if request.url.path.endswith("/accounts/selected"):
            return httpx.Response(200, json=_account_payload())
        raise AssertionError("Unexpected endpoint")
    async def run():
        reader = ReadOnlyAccount(
            ProprConfig("production", account_id="selected", api_key="pk_live_dummy"),
            httpx.MockTransport(handler),
        )
        try:
            result = await reader.inspect()
            assert result["account_bound"]
            assert result["account_source"] == "challenge"
            assert result["financial_fields_verified"]
            assert result["snapshot"].equity == Decimal("1080")
            assert result["snapshot"].margin_used == Decimal("370")
            assert result["snapshot"].buying_power == Decimal("480")
        finally:
            await reader.close()
    asyncio.run(run())
    assert all(method == "GET" for method, _ in calls)
    assert len(calls) == 5


def test_unique_funded_account_can_be_discovered():
    def handler(request):
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"status": "OK"})
        if request.url.path.endswith("/health/services"):
            return httpx.Response(200, json={"core": "OK"})
        if request.url.path.endswith("/book-account-issuances"):
            return httpx.Response(200, json={"data": [{"accountId": "funded"}]})
        if request.url.path.endswith("/challenge-attempts"):
            return httpx.Response(200, json={"data": []})
        if request.url.path.endswith("/accounts/funded"):
            return httpx.Response(200, json=_account_payload("funded"))
        raise AssertionError("Unexpected endpoint")

    async def run():
        reader = ReadOnlyAccount(
            ProprConfig("production", api_key="pk_live_dummy"),
            httpx.MockTransport(handler),
        )
        try:
            result = await reader.inspect()
            assert result["snapshot"].account_id == "funded"
            assert result["snapshot"].attempt_id is None
            assert result["account_source"] == "funded"
        finally:
            await reader.close()
    asyncio.run(run())


def test_account_not_auto_selected_when_discovery_is_ambiguous():
    def handler(request):
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"status": "OK"})
        if request.url.path.endswith("/health/services"):
            return httpx.Response(200, json={"core": "OK"})
        if request.url.path.endswith("/book-account-issuances"):
            return httpx.Response(200, json={"data": [{"accountId": "funded"}]})
        if request.url.path.endswith("/challenge-attempts"):
            return httpx.Response(200, json={"data": [{"accountId": "challenge"}]})
        raise AssertionError("No account may be selected")

    async def run():
        reader = ReadOnlyAccount(
            ProprConfig("production", api_key="pk_live_dummy"),
            httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(RemoteAPIError, match="absent or ambiguous"):
                await reader.inspect()
        finally:
            await reader.close()
    asyncio.run(run())


def test_incomplete_fields_never_guessed():
    snapshot = parse_account("selected", "a1", {"accountId": "selected", "balance": "1000"})
    assert snapshot.equity is None
    assert snapshot.buying_power is None
    assert not snapshot.complete


def test_account_mismatch_is_error():
    with pytest.raises(RemoteAPIError):
        parse_account("selected", "a1", _account_payload("different"))
