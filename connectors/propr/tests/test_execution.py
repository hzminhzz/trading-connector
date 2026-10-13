import asyncio
from decimal import Decimal as D
import pytest
from connectors.propr.execution import FakeVenue, FakeExecutionGateway, OrderRequest
from connectors.propr.instruments import Instrument, InstrumentRegistry
from connectors.propr.state import IntentJournal, UnknownOutcome

def setup(tmp_path):
    journal = IntentJournal(tmp_path / "orders.db")
    venue = FakeVenue()
    registry = InstrumentRegistry([Instrument("BTC", "BTC", D(".01"), D(".01"), D(".01"))])
    return journal, venue, FakeExecutionGateway(journal, venue, registry)

def test_fake_acceptance_and_validation(tmp_path):
    journal, venue, gateway = setup(tmp_path)
    async def run():
        intent, status = await gateway.submit(OrderRequest("a", "BTC", "buy", "long", D(".02")))
        assert status == "accepted"
        assert venue.calls == [("submit", intent.id)]
        with pytest.raises(ValueError):
            await gateway.submit(OrderRequest("a", "BTC", "buy", "long", D(".001")))
        with pytest.raises(ValueError):
            await gateway.submit(OrderRequest("a", "BTC", "buy", "long", D(".02"), order_type="stop"))
    asyncio.run(run())
    journal.close()

def test_timeout_freezes_openings_and_reconciles(tmp_path):
    journal, venue, gateway = setup(tmp_path)
    venue.outcome = "timeout"
    async def run():
        req = OrderRequest("a", "BTC", "sell", "short", D(".02"))
        with pytest.raises(UnknownOutcome):
            await gateway.submit(req)
        assert journal.has_unknown()
        assert len(venue.calls) == 1
        with pytest.raises(UnknownOutcome):
            await gateway.submit(req)
        assert len(venue.calls) == 1
        intent_id = venue.calls[0][1]
        venue.orders[intent_id] = "accepted"
        assert await gateway.reconcile(intent_id) == "accepted"
        assert not journal.has_unknown()
    asyncio.run(run())
    journal.close()

def test_real_transport_rejected(tmp_path):
    journal, _, _ = setup(tmp_path)
    with pytest.raises(PermissionError):
        FakeExecutionGateway(journal, object(), {})
    journal.close()
