import asyncio
import json
from decimal import Decimal as D
import pytest
from connectors.propr.shadow import ShadowGateway
from connectors.propr.execution import OrderRequest
from connectors.propr.instruments import Instrument, InstrumentRegistry
from connectors.propr.state import IntentJournal, UnknownOutcome

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


def test_shadow_journal_preserves_native_reduce_position(tmp_path):
    journal = IntentJournal(tmp_path / "shadow.db")
    gateway = ShadowGateway(
        InstrumentRegistry([Instrument("BTC", "BTC", D(".01"), D(".01"), D(".01"))]),
        Ready(), journal=journal,
    )
    request = OrderRequest(
        "account", "BTC", "sell", "long", D(".1"),
        reduce_only=True, position_id="native-position-7",
    )

    result = gateway.evaluate(request)
    payload = json.loads(journal.connection.execute(
        "SELECT payload FROM intents"
    ).fetchone()[0])
    assert result.allowed
    assert payload["position_id"] == "native-position-7"
    journal.close()
