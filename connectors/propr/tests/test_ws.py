import asyncio
import json
import pytest

from connectors.propr.ws import AccountWebSocket
from connectors.propr.reconcile import AccountReconciler
from connectors.propr.state import IntentJournal, UnknownOutcome


class FakeReader:
    def __init__(self):
        self.count = 0
    async def snapshot(self):
        self.count += 1
        return {"orders": [], "positions": [], "trades": []}


class FakeSocket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.closed = False
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        self.closed = True
    async def recv(self):
        return next(self.messages)


def fake_connector(messages, observed):
    def connect(url, **kwargs):
        observed.append((url, kwargs))
        return FakeSocket(messages)
    return connect


def test_authenticated_snapshot_event_and_reconcile(tmp_path):
    journal = IntentJournal(tmp_path / "j.db")
    reader = FakeReader()
    reconciler = AccountReconciler(reader, journal)
    messages = [
        json.dumps({"type": "connected", "data": {"userId": "fake-user"}}),
        json.dumps({"type": "order.partially_filled", "userId": "fake-user",
                    "timestamp": 1234567, "data": {"orderId": "abc", "cumulativeQuantity": "0.1"}}),
    ]
    observed = []
    ws = AccountWebSocket(
        ws_url="wss://api.propr.xyz/ws", api_key="pk_live_dummy",
        reconciler=reconciler, connector=fake_connector(messages, observed),
    )
    asyncio.run(ws.run_once(max_events=1))
    assert len(observed) == 1
    assert observed[0][1]["additional_headers"]["X-API-Key"] == "pk_live_dummy"
    assert reader.count == 4
    assert not reconciler.ready
    assert not ws.health.authenticated
    assert any(event.kind == "event" for event in reconciler.audit)
    journal.close()


@pytest.mark.parametrize("messages", [
    [json.dumps({"type": "connected", "data": {}})],
    [json.dumps({"type": "order.filled", "data": {}})],
    [json.dumps({"type": "connected", "data": {"userId": "u"}}),
     json.dumps({"type": "order.filled", "data": {}})],
    [json.dumps({"type": "connected", "data": {"userId": "u"}}),
     json.dumps({"type": "order.filled", "timestamp": 123,
                 "userId": "attacker", "data": {}})],
])
def test_ws_invalid_identity_or_event_fails_closed(tmp_path, messages):
    journal = IntentJournal(tmp_path / "j.db")
    reconciler = AccountReconciler(FakeReader(), journal)
    ws = AccountWebSocket(
        ws_url="wss://api.propr.xyz/ws", api_key="pk_live_dummy",
        reconciler=reconciler, connector=fake_connector(messages, []),
    )
    with pytest.raises(UnknownOutcome):
        asyncio.run(ws.run_once(max_events=1))
    assert not reconciler.ready and not reconciler.connected
    journal.close()


def test_unknown_intent_blocks_ws_ready(tmp_path):
    journal = IntentJournal(tmp_path / "j.db")
    intent = journal.stage("fake", "BTC", "buy", "long", "1")
    journal.mark(intent.id, "unknown")
    reconciler = AccountReconciler(FakeReader(), journal)
    ws = AccountWebSocket(
        ws_url="wss://api.propr.xyz/ws", api_key="pk_live_dummy",
        reconciler=reconciler,
        connector=fake_connector([json.dumps({"type": "connected", "data": {"userId": "u"}})], []),
    )
    with pytest.raises(UnknownOutcome):
        asyncio.run(ws.run_once(max_events=0))
    assert not reconciler.ready
    journal.close()
