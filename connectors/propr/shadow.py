"""Provider-neutral shadow decision engine. Never accepts an execution transport."""
from dataclasses import dataclass
from decimal import Decimal
from .execution import OrderRequest
from .state import UnknownOutcome

@dataclass(frozen=True)
class ShadowDecision:
    allowed: bool
    reason: str
    asset: str
    quantity: str

class ShadowGateway:
    def __init__(self, registry, reconciler, risk_gate=None):
        self.registry = registry
        self.reconciler = reconciler
        self.risk_gate = risk_gate
        self.decisions = []

    def evaluate(self, request: OrderRequest):
        try:
            if not request.account_id:
                raise ValueError("Explicit account required")
            instrument = self.registry.by_canonical[request.asset]
            request.validate(instrument)
            self.reconciler.assert_opening_safe()
            if self.risk_gate is not None:
                self.risk_gate(request)
            result = ShadowDecision(True, "shadow only; no remote submission", request.asset, str(request.quantity))
        except (ValueError, KeyError, PermissionError, UnknownOutcome) as exc:
            result = ShadowDecision(False, type(exc).__name__, request.asset, str(request.quantity))
        self.decisions.append(result)
        return result

    async def submit(self, request):
        return self.evaluate(request)

    async def cancel(self, order_id):
        raise PermissionError("Shadow mode cannot cancel remote orders")

    async def close(self, position_id):
        raise PermissionError("Shadow mode cannot close remote positions")

    async def change_leverage(self, *args, **kwargs):
        raise PermissionError("Shadow mode cannot change leverage")
