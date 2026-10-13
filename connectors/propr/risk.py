"""Fail-closed state gates and manually resettable kill switch."""
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class RiskSnapshot:
    equity: Decimal | None
    available_margin: Decimal | None
    leverage: Decimal | None
    daily_loss: Decimal | None
    market_fresh: bool
    reconciled: bool
    websocket_healthy: bool

class RiskGate:
    def __init__(self, max_leverage: Decimal, max_daily_loss: Decimal):
        self.max_leverage = max_leverage
        self.max_daily_loss = max_daily_loss
        self.halted = True
        self.reason = "Unqualified startup"

    def evaluate(self, snapshot: RiskSnapshot):
        if self.halted:
            raise PermissionError(self.reason)
        if any(x is None for x in (snapshot.equity, snapshot.available_margin, snapshot.leverage, snapshot.daily_loss)):
            self.trip("Missing risk/account data")
        if not all((snapshot.market_fresh, snapshot.reconciled, snapshot.websocket_healthy)):
            self.trip("Stale or unreconciled state")
        if snapshot.leverage > self.max_leverage or snapshot.daily_loss >= self.max_daily_loss:
            self.trip("Risk threshold exceeded")
        return True

    def trip(self, reason):
        self.halted = True
        self.reason = reason
        raise PermissionError(reason)

    def reset(self, *, manual_approval: bool, reconciled: bool):
        if not manual_approval or not reconciled:
            raise PermissionError("Manual approval and reconciliation required")
        self.halted = False
        self.reason = ""
