import asyncio
import pytest
from connectors.propr.reconcile import AccountReconciler
from connectors.propr.state import IntentJournal, UnknownOutcome

class Reader:
    def __init__(self):
        self.calls = 0
    async def snapshot(self):
        self.calls += 1
        return {"orders": [], "positions": [], "trades": []}

def test_reconnect_duplicate_event_and_stale(tmp_path):
    journal = IntentJournal(tmp_path / "j.db")
    clock = [0.0]
    reader = Reader()
    r = AccountReconciler(reader, journal, stale_after=5, clock=lambda: clock[0])
    async def run():
        with pytest.raises(UnknownOutcome):
            r.assert_opening_safe()
        r.connected_unverified()
        with pytest.raises(UnknownOutcome):
            r.assert_opening_safe()
        await r.refresh()
        r.assert_opening_safe()
        assert r.event("e1", "order.filled", {"quantity": "1"})
        assert not r.event("e1", "order.filled", {"quantity": "1"})
        with pytest.raises(UnknownOutcome):
            r.assert_opening_safe()
        await r.refresh()
        r.assert_opening_safe()
        clock[0] = 6
        with pytest.raises(UnknownOutcome):
            r.assert_opening_safe()
        r.disconnected()
        with pytest.raises(UnknownOutcome):
            await r.refresh()
        r.connected_unverified()
        await r.refresh()
        r.assert_opening_safe()
        assert reader.calls == 3
    asyncio.run(run())
    journal.close()

def test_unknown_intent_blocks_snapshot(tmp_path):
    journal = IntentJournal(tmp_path / "j.db")
    reader = Reader()
    r = AccountReconciler(reader, journal)
    r.connected_unverified()
    intent = journal.stage("a", "BTC", "buy", "long", "1")
    journal.mark(intent.id, "unknown")
    async def run():
        with pytest.raises(UnknownOutcome):
            await r.refresh()
        with pytest.raises(UnknownOutcome):
            r.assert_opening_safe()
    asyncio.run(run())
    journal.close()
