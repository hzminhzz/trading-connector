import asyncio
from decimal import Decimal as D
import pytest
from connectors.propr.shadow import ShadowGateway
from connectors.propr.execution import OrderRequest
from connectors.propr.instruments import Instrument, InstrumentRegistry
from connectors.propr.state import UnknownOutcome

class Ready:
    def assert_opening_safe(self):
        return None
class Stale:
    def assert_opening_safe(self):
        raise UnknownOutcome("stale")

def make(reconciler):
    return ShadowGateway(InstrumentRegistry([Instrument("BTC", "BTC", D(".01"), D(".01"), D(".01"))]), reconciler)

def test_allowed_and_blocked_no_transport():
    gateway = make(Ready())
    assert gateway.evaluate(OrderRequest("account", "BTC", "buy", "long", D(".1"))).allowed
    assert not gateway.evaluate(OrderRequest("account", "BTC", "buy", "long", D(".001"))).allowed
    assert len(gateway.decisions) == 2
    assert not hasattr(gateway, "rest")

def test_stale_and_mutations_refused():
    gateway = make(Stale())
    async def run():
        assert not (await gateway.submit(OrderRequest("account", "BTC", "buy", "long", D(".1")))).allowed
        with pytest.raises(PermissionError):
            await gateway.cancel("id")
        with pytest.raises(PermissionError):
            await gateway.close("id")
        with pytest.raises(PermissionError):
            await gateway.change_leverage("BTC", 5)
    asyncio.run(run())
