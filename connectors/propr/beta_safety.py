"""Explicit evidence classification: Beta is NOT assumed simulated."""
from dataclasses import dataclass
from enum import Enum

class BetaSafety(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED_SIMULATED = "verified_simulated"

@dataclass(frozen=True)
class BetaEvidence:
    status: BetaSafety = BetaSafety.UNVERIFIED
    official_source: str | None = None
    account_class: str | None = None
    routing_guarantee: str | None = None
    financial_exposure_guarantee: str | None = None
    funded_evaluation_impact: str | None = None

    def assert_simulated(self):
        # No verified official provider proof is currently bundled or authenticated.
        # A caller cannot self-certify by supplying arbitrary evidence strings.
        raise PermissionError("Propr Beta simulated-only status has no authoritative verification")

DEFAULT_BETA_EVIDENCE = BetaEvidence()
