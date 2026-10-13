"""Conservative account-event reconciliation, independent of network mutation.

No replay cursor or atomic snapshot boundary is promised by Propr. Stable
consecutive REST snapshots reduce but cannot eliminate undetected races;
remote orders remain disabled.
"""
from collections import deque
from dataclasses import dataclass
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
        self.seen_order = deque()
        self.max_seen = 10000
        self.revision = 0
        self.audit = deque(maxlen=1000)

    def disconnected(self):
        self.revision += 1
        self.connected = False
        self.ready = False
        self.audit.append(AuditEvent("connection", "disconnected; openings halted"))
        self.journal.audit("account_connection", "disconnected")

    def connected_unverified(self):
        self.revision += 1
        self.connected = True
        self.ready = False
        self.last_heartbeat = self.clock()
        self.audit.append(AuditEvent("connection", "connected; snapshot required"))
        self.journal.audit("account_connection", "connected; unverified")

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
        if len(self.seen_order) >= self.max_seen:
            oldest = self.seen_order.popleft()
            self.seen.remove(oldest)
        self.seen_order.append(event_id)
        self.seen.add(event_id)
        self.revision += 1
        # Every event invalidates prior snapshots until reconciliation.
        self.ready = False
        self.audit.append(AuditEvent("event", f"{event_type}: snapshot required"))
        self.journal.audit("account_event", "state changed; reconciling")
        return True

    async def refresh(self):
        self.ready = False
        if not self.connected:
            raise UnknownOutcome("Cannot reconcile disconnected stream")
        revision = self.revision
        if self.journal.has_unknown():
            self.audit.append(AuditEvent("reconciliation", "unknown intents remain"))
            raise UnknownOutcome("Unknown order intent requires independent confirmation")
        first = await self.reader.snapshot()
        second = await self.reader.snapshot()
        if first != second:
            self.audit.append(AuditEvent("reconciliation", "REST snapshots inconsistent"))
            self.journal.audit("reconciliation", "inconsistent REST snapshot")
            raise UnknownOutcome("Account changed during REST reconciliation")
        if self.journal.has_unknown() or not self.connected or revision != self.revision:
            raise UnknownOutcome("Account changed during reconciliation")
        self.snapshot = second
        self.last_heartbeat = self.clock()
        self.ready = True
        self.audit.append(AuditEvent("reconciliation", "two REST inventories matched"))
        self.journal.audit("reconciliation", "matched two read-only snapshots")
        return second

    def assert_opening_safe(self):
        if (not self.connected or not self.ready or self.last_heartbeat is None
                or self.clock() - self.last_heartbeat > self.stale_after
                or self.journal.has_unknown()):
            self.ready = False
            raise UnknownOutcome("Account stream not reconciled and fresh")
