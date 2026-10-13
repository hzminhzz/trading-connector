"""Fake-only cancellation, non-atomic replacement and precise reductions."""
from dataclasses import dataclass
from decimal import Decimal
from .execution import FakeVenue, FakeExecutionGateway, OrderRequest
from .state import UnknownOutcome

FINAL = frozenset(("cancelled", "filled", "rejected", "expired"))

@dataclass(frozen=True)
class NativePosition:
    id: str
    account_id: str
    asset: str
    side: str
    quantity: Decimal

class FakeLifecycleVenue(FakeVenue):
    def __init__(self):
        super().__init__()
        self.cancel_outcome = "cancelled"
        self.positions = {}
        self.filled_quantities = {}

    async def cancel(self, intent_id):
        self.calls.append(("cancel", intent_id))
        if self.cancel_outcome == "timeout":
            raise TimeoutError("Cancel result ambiguous")
        if self.cancel_outcome == "400":
            raise ValueError("Cancel rejected")
        if intent_id not in self.orders:
            raise KeyError(intent_id)
        if self.orders[intent_id] not in FINAL:
            self.orders[intent_id] = self.cancel_outcome
        return self.orders[intent_id]

class FakeLifecycleGateway(FakeExecutionGateway):
    def __init__(self, journal, venue, registry):
        if type(venue) is not FakeLifecycleVenue:
            raise PermissionError("Lifecycle only supports fake venue")
        super().__init__(journal, FakeVenue(), registry)
        self.venue = venue

    async def cancel(self, intent_id):
        try:
            await self.venue.cancel(intent_id)
        except (TimeoutError, ValueError, KeyError):
            pass
        state = await self.venue.status(intent_id)
        if state not in FINAL:
            self.journal.mark(intent_id, "unknown")
            raise UnknownOutcome("Cancel not verified final; opening halted")
        self.journal.mark(intent_id, "reconciled")
        return state

    async def replace(self, original_intent_id, request):
        # This is not atomic. Record the gap before trying the cancel.
        self.journal.mark(original_intent_id, "unknown")
        status = await self.cancel(original_intent_id)
        if status != "cancelled":
            raise UnknownOutcome("Replace aborted: old order may have filled")
        # Reject if any partial fill occurred, even when cancellation succeeded.
        if self.venue.filled_quantities.get(original_intent_id, Decimal(0)) != 0:
            raise UnknownOutcome("Replace aborted: partial fill during cancel gap")
        return await self.submit(request)

    async def reduce(self, position_id, quantity):
        position = self.venue.positions.get(position_id)
        if not isinstance(position, NativePosition):
            raise UnknownOutcome("Native position ID unavailable")
        if quantity <= 0 or quantity > position.quantity:
            raise ValueError("Invalid reduction size")
        side = "sell" if position.side == "long" else "buy"
        request = OrderRequest(position.account_id, position.asset, side,
                               position.side, quantity, reduce_only=True)
        return await self.submit(request)
