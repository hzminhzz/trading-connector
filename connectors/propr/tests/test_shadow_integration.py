from decimal import Decimal as D
import asyncio

import pytest

from connectors.propr.execution import OrderRequest
from connectors.propr.instruments import Instrument, InstrumentRegistry
from connectors.propr.shadow import ShadowGateway
from connectors.propr.state import IntentJournal
from connectors.propr.rest import ProprRest, TransportPolicy
import httpx


class Ready:
    def assert_opening_safe(self):
        pass


def test_shadow_journaled_decisions_never_have_transport(tmp_path):
    journal = IntentJournal(tmp_path / "shadow.db")
    reg = InstrumentRegistry([Instrument("BTC", "BTC", D(".01"), D(".01"), D(".01"))])
    shadow = ShadowGateway(reg, Ready(), journal=journal)
    yes = shadow.evaluate(OrderRequest("fake", "BTC", "buy", "long", D(".1")))
    no = shadow.evaluate(OrderRequest("fake", "BTC", "buy", "long", D(".005")))
    assert yes.allowed and yes.intent_id
    assert not no.allowed
    assert not journal.has_unknown()
    assert len(journal.audit_history()) == 2
    assert not hasattr(shadow, "client") and not hasattr(shadow, "rest")
    journal.close()


def test_real_http_mutations_stay_blocked():
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={})
    async def run():
        rest = ProprRest(
            "https://api.propr.xyz/v1", "pk_live_dummy",
            transport=httpx.MockTransport(handler),
        )
        try:
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                with pytest.raises(PermissionError):
                    await rest.request(method, "/accounts/fake/orders")
            with pytest.raises(PermissionError):
                await rest.request("GET", "/health", headers={"X-API-Key": "override"})
            # Direct access to underlying HTTPX client is also guarded.
            with pytest.raises(PermissionError):
                await rest.client.post("/accounts/fake/orders", json={})
        finally:
            await rest.close()
    asyncio.run(run())
    assert calls == []


def test_nonoverrideable_real_transport_policy():
    with pytest.raises(TypeError):
        TransportPolicy(allow_mutations=True)
