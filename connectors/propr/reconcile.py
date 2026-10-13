"""Fail-closed account event reconciliation; transport-independent, no order mutations.

No provider replay or atomic REST/WS snapshot guarantees are assumed.
"""
from dataclasses import dataclass
from collections import deque
from time import monotonic

from .state import UnknownOutcome

@dataclass(frozen=True)
class AuditEvent:
    kind: str
    detail: str

class AccountReconciler:
    def __init__(self, inventory_reader, journal, *, stale_after=30.0, clock=monotonic):
        if stale_after <= 0:
            raise ValueError("stale_after must be positive")
        self.reader = inventory_reader
        self.journal = journal
        self.clock = clock
        self.stale_after = stale_after
        self.connected = False
        self.ready = False
        self.last_heartbeat = None
        self.snapshot = None
        self.seen = set()
        self.audit = deque(maxlen=1000)

    def disconnected(self):
        self.connected = False
        self.ready = False
        self.audit.append(AuditEvent("connection", "disconnected; openings halted"))

    def connected_unverified(self):
        self.connected = True
        self.ready = False
        self.last_heartbeat = self.clock()
        self.audit.append(AuditEvent("connection", "connected; snapshot required"))

    def heartbeat(self):
        if not self.connected:
            raise UnknownOutcome("Heartbeat on disconnected stream")
        self.last_heartbeat = self.clock()

    def event(self, event_id, event_type, payload):
        if not self.connected:
            raise UnknownOutcome("Event received while disconnected")
        if not event_id or not event_type or not isinstance(payload, dict):
            self.ready = False
            raise UnknownOutcome("Invalid event identity")
        if event_id in self.seen:
            return False
        self.seen.add(event_id)
        # Without provider ordering/replay guarantees, any state mutation requires
        # a new authoritative snapshot before opening exposure.
        self.ready = False
        self.audit.append(AuditEvent("event", f"{event_type}: snapshot required"))
        return True

    async def refresh(self):
        self.ready = False
        if not self.connected:
            raise UnknownOutcome("Cannot reconcile disconnected stream")
        candidate = await self.reader.snapshot()
        if self.journal.has_unknown():
            self.audit.append(AuditEvent("reconciliation", "unknown intents remain"))
            raise UnknownOutcome("Unknown order intent requires independent confirmation")
        self.snapshot = candidate
        self.last_heartbeat = self.clock()
        self.ready = True
        self.audit.append(AuditEvent("reconciliation", "REST inventory refreshed"))
        return candidate

    def assert_opening_safe(self):
        if (not self.connected or not self.ready or self.last_heartbeat is None
                or self.clock() - self.last_heartbeat > self.stale_after
                or self.journal.has_unknown()):
            self.ready = False
            raise UnknownOutcome("Account stream not reconciled and fresh")
