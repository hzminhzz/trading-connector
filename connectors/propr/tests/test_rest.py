import asyncio
import httpx
import pytest
from connectors.propr.rest import ProprRest, RemoteAPIError, TransportPolicy


def test_read_only_transport_rejects_mutations_and_credentials_before_network():
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={})

    async def run():
        rest = ProprRest("https://api.propr.xyz/v1", "pk_live_dummy", transport=httpx.MockTransport(handler))
        try:
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                with pytest.raises(PermissionError):
                    await rest.request(method, "/accounts/a/orders", json={})
            with pytest.raises(PermissionError):
                await rest.request("GET", "/health", headers={"X-API-Key": "override"})
            with pytest.raises(PermissionError):
                await rest.client.post("/accounts/a/orders", json={})
            assert calls == []
        finally:
            await rest.close()

    asyncio.run(run())


def test_nonoverrideable_transport_policy():
    with pytest.raises(TypeError):
        TransportPolicy(allow_mutations=True)

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
