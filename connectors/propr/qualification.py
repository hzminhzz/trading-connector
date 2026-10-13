"""No-network Beta qualification gate; unverified accounts always BLOCKED."""
from dataclasses import dataclass
from .beta_safety import DEFAULT_BETA_EVIDENCE

@dataclass(frozen=True)
class QualificationReport:
    status: str
    reason: str
    approved_instruments: tuple[str, ...] = ()
    remote_mutations: int = 0

def beta_order_qualification(*, account_id=None, scoped_approval=None, evidence=DEFAULT_BETA_EVIDENCE):
    if not account_id:
        return QualificationReport("BLOCKED", "Explicit Beta account identity required")
    try:
        evidence.assert_simulated()
    except PermissionError:
        return QualificationReport("BLOCKED", "Official Beta simulation-only proof unavailable")
    if not scoped_approval:
        return QualificationReport("BLOCKED", "Separate scoped human approval required")
    return QualificationReport("BLOCKED", "Remote order qualification not implemented; no mutations authorized")
