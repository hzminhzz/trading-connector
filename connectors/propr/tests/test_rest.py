import asyncio
import httpx
import pytest
from connectors.propr.rest import ProprRest, RemoteAPIError

def test_read_only_transport_rejects_post_before_network():
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={})
    async def run():
        rest = ProprRest("https://api.propr.xyz/v1", "pk_live_dummy", transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(PermissionError):
                await rest.request("POST", "/accounts/a/orders", json={})
            assert calls == []
        finally:
            await rest.close()
    asyncio.run(run())

def test_pagination_works_past_twenty():
    def handler(request):
        offset = int(request.url.params["offset"])
        rows = list(range(offset, min(offset+20, 43)))
        return httpx.Response(200, json={"data": rows})
    async def run():
        rest = ProprRest("https://api.propr.xyz/v1", "pk_live_dummy", transport=httpx.MockTransport(handler))
        try:
            assert await rest.pages("/accounts/a/orders") == list(range(43))
        finally:
            await rest.close()
    asyncio.run(run())

def test_http_errors_redacted():
    def handler(request):
        return httpx.Response(429, json={"private": "never expose"})
    async def run():
        rest = ProprRest("https://api.propr.xyz/v1", "pk_live_dummy", transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(RemoteAPIError, match="429") as err:
                await rest.request("GET", "/health")
            assert "private" not in str(err.value)
        finally:
            await rest.close()
    asyncio.run(run())
