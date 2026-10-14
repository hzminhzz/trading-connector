import asyncio
from decimal import Decimal as D
import pytest
from connectors.propr.execution import (
    FakeExecutionGateway, FakeOrderResult, FakeVenue, OrderRequest,
)
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
        assert status == "open"
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
        venue.orders[intent_id] = FakeOrderResult("open", D("0"))
        assert await gateway.reconcile(intent_id) == "open"
        assert not journal.has_unknown()
    asyncio.run(run())
    journal.close()

def test_real_transport_rejected(tmp_path):
    journal, _, _ = setup(tmp_path)
    with pytest.raises(PermissionError):
        FakeExecutionGateway(journal, object(), {})
    journal.close()


@pytest.mark.parametrize("result", [
    FakeOrderResult("pending", D("0")),
    FakeOrderResult("open", D("0")),
    FakeOrderResult("partial", D(".01")),
    FakeOrderResult("filled", D(".02")),
    FakeOrderResult("rejected", D("0")),
    FakeOrderResult("expired", D(".01")),
])
def test_fake_lifecycle_preserves_cumulative_quantity(tmp_path, result):
    journal, venue, gateway = setup(tmp_path)
    venue.outcome = result

    async def run():
        intent, status = await gateway.submit(
            OrderRequest("a", "BTC", "buy", "long", D(".02"))
        )
        assert status == result.status
        assert journal.get(intent.id) == intent
        assert journal.connection.execute(
            "SELECT state FROM intents WHERE id=?", (intent.id,)
        ).fetchone()[0] == result.status
        assert venue.orders[intent.id].cumulative_quantity == result.cumulative_quantity

    asyncio.run(run())
    journal.close()


def test_unknown_fake_outcome_stays_unresolved(tmp_path):
    journal, venue, gateway = setup(tmp_path)
    venue.outcome = FakeOrderResult("unknown", D("0"))

    async def run():
        with pytest.raises(UnknownOutcome):
            await gateway.submit(OrderRequest("a", "BTC", "buy", "long", D(".02")))
        assert journal.has_unknown()

    asyncio.run(run())
    journal.close()


def test_retry_after_restart_reuses_durable_id(tmp_path):
    path = tmp_path / "orders.db"
    journal = IntentJournal(path)
    venue = FakeVenue()
    venue.outcome = "timeout"
    registry = InstrumentRegistry([Instrument("BTC", "BTC", D(".01"), D(".01"), D(".01"))])
    gateway = FakeExecutionGateway(journal, venue, registry)
    request = OrderRequest("a", "BTC", "buy", "long", D(".02"))

    async def first_attempt():
        with pytest.raises(UnknownOutcome):
            await gateway.submit(request)

    asyncio.run(first_attempt())
    intent_id = journal.unresolved()[0][0]
    venue.orders[intent_id] = FakeOrderResult("partial", D(".01"))
    journal.close()

    journal = IntentJournal(path)
    gateway = FakeExecutionGateway(journal, venue, registry)

    async def retry():
        intent, status = await gateway.retry(intent_id, request)
        assert intent.id == intent_id
        assert status == "partial"
        assert [call[1] for call in venue.calls] == [intent_id, intent_id]
        assert not journal.has_unknown()

    asyncio.run(retry())
    journal.close()


def test_split_batch_outcomes_are_recorded_per_intent(tmp_path):
    journal, venue, gateway = setup(tmp_path)
    venue.batch_outcomes = [
        FakeOrderResult("partial", D(".01")),
        FakeOrderResult("rejected", D("0")),
    ]
    requests = [
        OrderRequest("a", "BTC", "buy", "long", D(".02")),
        OrderRequest("a", "BTC", "sell", "short", D(".02")),
    ]

    async def run():
        outcomes = await gateway.submit_batch(requests)
        assert [status for _, status in outcomes] == ["partial", "rejected"]
        assert len({intent.id for intent, _ in outcomes}) == 2
        assert [state for _, _, state in journal.unresolved()] == []
        assert venue.calls == [("submit_batch", tuple(i.id for i, _ in outcomes))]

    asyncio.run(run())
    journal.close()


def test_unknown_split_batch_member_blocks_openings(tmp_path):
    journal, venue, gateway = setup(tmp_path)
    venue.batch_outcomes = [
        FakeOrderResult("open", D("0")),
        FakeOrderResult("unknown", D("0")),
    ]
    requests = [
        OrderRequest("a", "BTC", "buy", "long", D(".02")),
        OrderRequest("a", "BTC", "sell", "short", D(".02")),
    ]

    async def run():
        with pytest.raises(UnknownOutcome, match="Batch contains unknown"):
            await gateway.submit_batch(requests)
        states = journal.connection.execute(
            "SELECT state FROM intents ORDER BY rowid"
        ).fetchall()
        assert states == [("open",), ("unknown",)]
        with pytest.raises(UnknownOutcome):
            await gateway.submit(requests[0])

    asyncio.run(run())
    journal.close()
