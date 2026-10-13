import asyncio
import httpx
import pytest
from connectors.propr.account import ReadOnlyAccount
from connectors.propr.config import ProprConfig
from connectors.propr.rest import RemoteAPIError

def test_read_only_account_binding_and_no_mutations():
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path, request.url.params.get("offset")))
        assert request.headers["X-API-Key"] == "pk_beta_dummy"
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"status": "OK"})
        if request.url.path.endswith("/challenge-attempts"):
            return httpx.Response(200, json={"data": [{"accountId": "selected", "attemptId": "a1", "totalPnl": "100"}]})
        raise AssertionError("Unexpected endpoint")
    async def run():
        reader = ReadOnlyAccount(ProprConfig("beta", account_id="selected", api_key="pk_beta_dummy"), httpx.MockTransport(handler))
        try:
            result = await reader.inspect()
            assert result["account_bound"]
            assert not result["financial_fields_verified"]
            assert result["snapshot"].equity is None
        finally:
            await reader.close()
    asyncio.run(run())
    assert all(method == "GET" for method, _, _ in calls)

def test_account_not_auto_selected():
    def handler(request):
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"status": "OK"})
        return httpx.Response(200, json={"data": [{"accountId": "other"}]})
    async def run():
        reader = ReadOnlyAccount(ProprConfig("beta", account_id="selected", api_key="pk_beta_dummy"), httpx.MockTransport(handler))
        try:
            with pytest.raises(RemoteAPIError, match="not uniquely"):
                await reader.inspect()
        finally:
            await reader.close()
    asyncio.run(run())
