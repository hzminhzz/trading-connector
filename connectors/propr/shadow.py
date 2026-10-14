"""Provider-neutral audited shadow decisions, strictly without execution transport."""
from dataclasses import dataclass

from .execution import OrderRequest
from .state import UnknownOutcome


@dataclass(frozen=True)
class ShadowDecision:
    allowed: bool
    reason: str
    asset: str
    quantity: str
    intent_id: str | None = None


class ShadowGateway:
    def __init__(self, registry, reconciler, risk_gate=None, *, journal=None, risk_evidence=None):
        # No HTTP client or remotely mutation-capable object is accepted.
        self.registry = registry
        self.reconciler = reconciler
        self.risk_gate = risk_gate
        self.journal = journal
        self.risk_evidence = risk_evidence
        self.decisions = []

    def evaluate(self, request: OrderRequest):
        try:
            if not request.account_id:
                raise ValueError("Explicit account required")
            instrument = self.registry.by_canonical[request.asset]
            request.validate(instrument)
            self.reconciler.assert_opening_safe()
            if self.risk_gate is not None:
                if self.risk_evidence is None:
                    raise PermissionError("Risk evidence source required")
                self.risk_gate.evaluate(request, self.risk_evidence(request))
            identifier = None
            if self.journal is not None:
                intent = self.journal.stage(
                    request.account_id, request.asset, request.side,
                    request.position_side, request.quantity, request.reduce_only,
                    request.position_id,
                )
                identifier = intent.id
                # No remote I/O occurs between stage and reconciliation.
                self.journal.mark(intent.id, "reconciled")
                self.journal.audit("shadow_intent", "allowed; no network order mutation")
            result = ShadowDecision(
                True, "shadow only; no remote submission",
                request.asset, str(request.quantity), identifier,
            )
        except (ValueError, KeyError, PermissionError, UnknownOutcome) as exc:
            if self.journal is not None:
                self.journal.audit("shadow_intent", f"blocked: {type(exc).__name__}")
            result = ShadowDecision(
                False, type(exc).__name__, request.asset, str(request.quantity),
            )
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
