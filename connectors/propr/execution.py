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
    position_id: str | None = None

    def validate(self, instrument):
        if self.side not in ("buy", "sell") or self.position_side not in ("long", "short"):
            raise ValueError("Invalid side")
        if self.reduce_only and not self.position_id:
            raise ValueError("Reduce-only requires exact native position ID")
        if self.order_type not in ("market", "limit"):
            raise ValueError("Unsupported order type")
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("Limit price required")
        if self.order_type == "market" and self.limit_price is not None:
            raise ValueError("Market orders cannot specify limit price")
        instrument.validate(self.quantity, self.limit_price)

@dataclass(frozen=True)
class FakeOrderResult:
    status: str
    cumulative_quantity: Decimal


class FakeVenue:
    """No HTTP, network or real account; configurable deterministic outcomes."""
    def __init__(self):
        self.orders = {}
        self.outcome = FakeOrderResult("open", Decimal(0))
        self.calls = []

    async def submit(self, intent, request):
        self.calls.append(("submit", intent.id))
        if intent.id in self.orders:
            return self.orders[intent.id]
        if self.outcome == "timeout":
            raise TimeoutError("Ambiguous fake response")
        if isinstance(self.outcome, FakeOrderResult):
            result = self.outcome
        else:
            filled = request.quantity if self.outcome == "filled" else Decimal(0)
            result = FakeOrderResult(self.outcome, filled)
        self.orders[intent.id] = result
        return result

    async def status(self, intent_id):
        return self.orders.get(intent_id)


_KNOWN_OUTCOMES = {"pending", "open", "partial", "filled", "rejected", "expired", "cancelled"}


def _validate_result(result, quantity):
    if (not isinstance(result, FakeOrderResult)
            or not isinstance(result.cumulative_quantity, Decimal)):
        return False
    filled = result.cumulative_quantity
    if (result.status not in _KNOWN_OUTCOMES or not filled.is_finite()
            or filled < 0 or filled > quantity):
        return False
    if result.status in ("pending", "open", "rejected") and filled != 0:
        return False
    if result.status == "partial" and not 0 < filled < quantity:
        return False
    if result.status == "filled" and filled != quantity:
        return False
    if result.status in ("expired", "cancelled") and filled == quantity:
        return False
    return True


class FakeExecutionGateway:
    def __init__(self, journal: IntentJournal, venue: FakeVenue, registry):
        if type(venue) is not FakeVenue:
            raise PermissionError("Only deterministic fake venue is supported")
        self.journal, self.venue, self.registry = journal, venue, registry

    async def submit(self, request: OrderRequest):
        instrument = self.registry.by_canonical[request.asset]
        request.validate(instrument)
        intent = self.journal.stage(request.account_id, request.asset, request.side,
                                    request.position_side, request.quantity, request.reduce_only,
                                    request.position_id)
        return await self._send(intent, request)

    async def retry(self, intent_id, request: OrderRequest):
        request.validate(self.registry.by_canonical[request.asset])
        unresolved = {identifier: (intent, state)
                      for identifier, intent, state in self.journal.unresolved()}
        saved = unresolved.get(intent_id)
        if saved is None or saved[1] != "unknown":
            raise ValueError("Only an unresolved intent can be retried")
        intent = saved[0]
        if (intent.account_id != request.account_id or intent.asset != request.asset
                or intent.side != request.side or intent.position_side != request.position_side
                or Decimal(intent.quantity) != request.quantity
                or intent.reduce_only != request.reduce_only
                or intent.position_id != request.position_id):
            raise ValueError("Retry must match the original durable intent")
        return await self._send(intent, request)

    async def _send(self, intent, request):
        try:
            result = await self.venue.submit(intent, request)
        except (TimeoutError, ConnectionError):
            self.journal.mark(intent.id, "unknown")
            raise UnknownOutcome("Submission outcome unknown; reconcile before opening")
        if not _validate_result(result, request.quantity):
            self.journal.mark(intent.id, "unknown")
            raise UnknownOutcome("Unrecognized submission response")
        self.journal.mark(intent.id, result.status)
        return intent, result.status

    async def reconcile(self, intent_id):
        state = await self.venue.status(intent_id)
        intent = self.journal.get(intent_id)
        if not _validate_result(state, Decimal(intent.quantity)):
            raise UnknownOutcome("Cannot establish remote outcome")
        self.journal.mark(intent_id, state.status)
        return state.status

