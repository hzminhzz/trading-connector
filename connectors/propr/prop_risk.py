"""Propr-specific risk preflight, fail-closed challenge bounds and kill drills.

Risk inputs must come from verified challenge/account metadata and independent
market feeds; no guessed prop rule or synthetic risk state can arm remote orders.
"""
from dataclasses import dataclass
from decimal import Decimal

from .models import finite_decimal


@dataclass(frozen=True)
class PropLimits:
    account_id: str
    allowed_assets: frozenset[str]
    max_notional: Decimal
    max_leverage: Decimal
    max_daily_loss: Decimal
    max_drawdown: Decimal
    equity_floor: Decimal
    max_margin_fraction: Decimal

    def __post_init__(self):
        if not self.account_id or not self.allowed_assets:
            raise ValueError("Risk policy requires explicit account and assets")
        numeric = (
            self.max_notional, self.max_leverage, self.max_daily_loss,
            self.max_drawdown, self.equity_floor, self.max_margin_fraction,
        )
        if not all(x.is_finite() for x in numeric):
            raise ValueError("Non-finite risk limit")
        if not all(x > 0 for x in numeric[:4]) or not 0 < self.max_margin_fraction <= 1:
            raise ValueError("Invalid risk budget")
        if self.equity_floor < 0:
            raise ValueError("Negative equity floor")


@dataclass(frozen=True)
class PropRiskEvidence:
    account: object
    native_asset: str
    market_price: Decimal | None
    gross_notional_before: Decimal | None
    leverage: Decimal | None
    margin_mode: str | None
    day_start_equity: Decimal | None
    high_water_equity: Decimal | None
    day_key: str | None
    market_fresh: bool
    reconciled: bool
    websocket_healthy: bool
    challenge_rules_verified: bool
    leverage_limit_verified: bool
    existing_liquidation_prices_verified: bool


class PropRiskGate:
    """Latched stop; opening preflight only; does not execute remotely."""

    def __init__(self, limits: PropLimits, journal=None):
        self.limits = limits
        self.journal = journal
        self.halted = True
        self.reason = "Unqualified startup"

    def trip(self, reason):
        self.halted = True
        self.reason = reason
        if self.journal is not None:
            self.journal.audit("risk_halt", reason)
        raise PermissionError(reason)

    def reset(self, *, manual_approval=False, reconciled=False):
        if not manual_approval or not reconciled:
            raise PermissionError("Manual authorization and reconciliation required")
        self.halted = False
        self.reason = ""
        if self.journal is not None:
            self.journal.audit("risk_reset", "manual and reconciled")

    def evaluate(self, request, evidence: PropRiskEvidence):
        if self.halted:
            raise PermissionError(self.reason)
        if request.account_id != self.limits.account_id or evidence.account.account_id != self.limits.account_id:
            self.trip("Unapproved account identity")
        if evidence.native_asset not in self.limits.allowed_assets:
            self.trip("Unapproved native asset")
        if not all((
            evidence.market_fresh, evidence.reconciled,
            evidence.websocket_healthy, evidence.challenge_rules_verified,
            evidence.leverage_limit_verified, evidence.existing_liquidation_prices_verified,
        )):
            self.trip("Incomplete or stale risk evidence")
        if evidence.margin_mode not in ("cross", "isolated") or not evidence.day_key:
            self.trip("Margin mode or challenge daily reset is unverified")
        account = evidence.account
        required = (
            account.equity, account.buying_power, account.initial_margin,
            account.maintenance_margin, account.margin_used,
            evidence.market_price, evidence.gross_notional_before,
            evidence.leverage, evidence.day_start_equity,
            evidence.high_water_equity,
        )
        if any(x is None or not x.is_finite() for x in required):
            self.trip("Missing or invalid account/margin inputs")
        if account.equity <= self.limits.equity_floor:
            self.trip("Account equity floor breached")
        if evidence.day_start_equity - account.equity >= self.limits.max_daily_loss:
            self.trip("Daily loss limit breached")
        if evidence.high_water_equity - account.equity >= self.limits.max_drawdown:
            self.trip("Drawdown limit breached")
        if evidence.leverage <= 0 or evidence.leverage > self.limits.max_leverage:
            self.trip("Leverage limit breached")
        try:
            quantity = finite_decimal(request.quantity)
        except ValueError:
            self.trip("Invalid order size")
        if quantity <= 0 or evidence.market_price <= 0:
            self.trip("Invalid mark or quantity")
        notional = quantity * evidence.market_price
        if evidence.gross_notional_before + notional > self.limits.max_notional:
            self.trip("Gross exposure cap breached")
        if notional / evidence.leverage > account.buying_power:
            self.trip("Insufficient available margin")
        if account.margin_used + (notional / evidence.leverage) > account.equity * self.limits.max_margin_fraction:
            self.trip("Total margin budget breached")
        if self.journal is not None:
            self.journal.audit("risk_preflight", "passed")
        return True


@dataclass(frozen=True)
class KillAction:
    action: str
    confirmed: bool
    reason: str


class SyntheticKillDrill:
    """No live I/O: fake lifecycle gateway only. Never equate ACK with flat."""

    def __init__(self, risk_gate: PropRiskGate, journal, gateway):
        from .lifecycle import FakeLifecycleGateway
        if type(gateway) is not FakeLifecycleGateway:
            raise PermissionError("Kill-switch drills require fake gateway")
        self.risk_gate = risk_gate
        self.journal = journal
        self.gateway = gateway

    async def execute(self, *, order_intents=(), reductions=()):
        if not self.risk_gate.halted:
            raise PermissionError("Kill switch must be latched before response drill")
        outcomes = []
        for intent_id in order_intents:
            try:
                result = await self.gateway.cancel(intent_id)
                confirmed = result in ("cancelled", "filled", "expired", "rejected")
                reason = "terminal status verified" if confirmed else "unknown"
            except Exception:
                confirmed, reason = False, "cancel outcome unknown"
            outcomes.append(KillAction("cancel", confirmed, reason))
            self.journal.audit("kill_cancel", reason)
        for position_id, qty in reductions:
            try:
                await self.gateway.reduce(position_id, qty)
                # An ACK is not a fill or a verified flat position.
                reason = "reduce requested; fill and flat status unconfirmed"
            except Exception:
                reason = "reduce request failed or outcome unknown"
            outcomes.append(KillAction("reduce", False, reason))
            self.journal.audit("kill_reduce", reason)
        return tuple(outcomes)
