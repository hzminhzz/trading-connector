import asyncio
from decimal import Decimal as D
import pytest
from connectors.propr.lifecycle import FakeLifecycleGateway, FakeLifecycleVenue, NativePosition
from connectors.propr.execution import FakeOrderResult, OrderRequest
from connectors.propr.instruments import Instrument, InstrumentRegistry
from connectors.propr.state import IntentJournal, UnknownOutcome

def make(tmp_path):
    journal = IntentJournal(tmp_path / "journal.db")
    venue = FakeLifecycleVenue()
    registry = InstrumentRegistry([Instrument("BTC", "BTC", D(".01"), D(".01"), D(".01"))])
    return journal, venue, FakeLifecycleGateway(journal, venue, registry)

def test_cancel_400_does_not_mean_cancelled(tmp_path):
    journal, venue, gateway = make(tmp_path)
    async def run():
        intent, _ = await gateway.submit(OrderRequest("a", "BTC", "buy", "long", D("1")))
        venue.cancel_outcome = "400"
        with pytest.raises(UnknownOutcome):
            await gateway.cancel(intent.id)
        assert journal.has_unknown()
    asyncio.run(run())
    journal.close()

def test_cancel_400_after_fill_and_replace_refusal(tmp_path):
    journal, venue, gateway = make(tmp_path)
    async def run():
        request = OrderRequest("a", "BTC", "buy", "long", D("1"))
        intent, _ = await gateway.submit(request)
        venue.orders[intent.id] = "filled"
        venue.cancel_outcome = "400"
        assert await gateway.cancel(intent.id) == "filled"
        with pytest.raises(UnknownOutcome):
            await gateway.replace(intent.id, request)
        assert len([c for c in venue.calls if c[0] == "submit"]) == 1
    asyncio.run(run())
    journal.close()

def test_replace_requires_no_partial_fill(tmp_path):
    journal, venue, gateway = make(tmp_path)
    async def run():
        request = OrderRequest("a", "BTC", "buy", "long", D("1"))
        intent, _ = await gateway.submit(request)
        venue.filled_quantities[intent.id] = D(".5")
        with pytest.raises(UnknownOutcome):
            await gateway.replace(intent.id, request)
        assert len([c for c in venue.calls if c[0] == "submit"]) == 1
    asyncio.run(run())
    journal.close()


def test_replace_rejects_partial_quantity_from_order_result(tmp_path):
    journal, venue, gateway = make(tmp_path)

    async def run():
        request = OrderRequest("a", "BTC", "buy", "long", D("1"))
        intent, _ = await gateway.submit(request)
        venue.orders[intent.id] = FakeOrderResult("partial", D(".5"))
        with pytest.raises(UnknownOutcome, match="partial fill"):
            await gateway.replace(intent.id, request)
        assert venue.orders[intent.id] == FakeOrderResult("cancelled", D(".5"))
        assert len([call for call in venue.calls if call[0] == "submit"]) == 1

    asyncio.run(run())
    journal.close()

def test_reduce_native_position_side(tmp_path):
    journal, venue, gateway = make(tmp_path)
    venue.positions["short-1"] = NativePosition("short-1", "a", "BTC", "short", D("2"))
    async def run():
        intent, status = await gateway.reduce("short-1", D("1"))
        assert status == "open"
        assert intent.side == "buy" and intent.position_side == "short" and intent.reduce_only
        assert intent.position_id == "short-1"
        with pytest.raises(ValueError):
            await gateway.reduce("short-1", D("3"))
        with pytest.raises(UnknownOutcome):
            await gateway.reduce("missing", D("1"))
    asyncio.run(run())
    journal.close()
