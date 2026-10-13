"""Fake-only order lifecycle: durable intent, no real transport mutations."""
from dataclasses import dataclass
from decimal import Decimal

from .state import IntentJournal, UnknownOutcome

@dataclass(frozen=True)
class OrderRequest:
    account_id: str
    asset: str
    side: str
    position_side: str
    quantity: Decimal
    reduce_only: bool = False
    order_type: str = "market"
    limit_price: Decimal | None = None

    def validate(self, instrument):
        if self.side not in ("buy", "sell") or self.position_side not in ("long", "short"):
            raise ValueError("Invalid side")
        if self.order_type not in ("market", "limit"):
            raise ValueError("Unsupported order type")
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("Limit price required")
        if self.order_type == "market" and self.limit_price is not None:
            raise ValueError("Market orders cannot specify limit price")
        instrument.validate(self.quantity, self.limit_price)

class FakeVenue:
    """No HTTP, network or real account; configurable deterministic outcomes."""
    def __init__(self):
        self.orders = {}
        self.outcome = "accepted"
        self.calls = []

    async def submit(self, intent, request):
        self.calls.append(("submit", intent.id))
        if self.outcome == "timeout":
            raise TimeoutError("Ambiguous fake response")
        if intent.id in self.orders:
            return self.orders[intent.id]
        self.orders[intent.id] = self.outcome
        return self.outcome

    async def status(self, intent_id):
        return self.orders.get(intent_id)

class FakeExecutionGateway:
    def __init__(self, journal: IntentJournal, venue: FakeVenue, registry):
        if type(venue) is not FakeVenue:
            raise PermissionError("Only deterministic fake venue is supported")
        self.journal, self.venue, self.registry = journal, venue, registry

    async def submit(self, request: OrderRequest):
        instrument = self.registry.by_canonical[request.asset]
        request.validate(instrument)
        intent = self.journal.stage(request.account_id, request.asset, request.side,
                                    request.position_side, request.quantity, request.reduce_only)
        try:
            status = await self.venue.submit(intent, request)
        except (TimeoutError, ConnectionError):
            self.journal.mark(intent.id, "unknown")
            raise UnknownOutcome("Submission outcome unknown; reconcile before opening")
        if status not in ("accepted", "rejected"):
            self.journal.mark(intent.id, "unknown")
            raise UnknownOutcome("Unrecognized submission response")
        self.journal.mark(intent.id, status)
        return intent, status

    async def reconcile(self, intent_id):
        state = await self.venue.status(intent_id)
        if state not in ("accepted", "rejected"):
            raise UnknownOutcome("Cannot establish remote outcome")
        self.journal.mark(intent_id, "reconciled")
        return state
