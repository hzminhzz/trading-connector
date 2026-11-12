"""Account/instrument-bound native semantics; evidence never grants trading authority."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any


@dataclass(frozen=True)
class CapabilityFact:
    status: str
    evidence_sha: str | None
    provenance: str | None

    def __post_init__(self) -> None:
        if self.status not in {"UNKNOWN", "UNSUPPORTED", "VERIFIED", "SYNTHETIC"}:
            raise ValueError("CAPABILITY_STATUS")
        if self.status in {"VERIFIED", "SYNTHETIC"} and (
            not isinstance(self.evidence_sha, str)
            or not re.fullmatch("[0-9a-f]{64}", self.evidence_sha)
            or not isinstance(self.provenance, str) or not self.provenance.strip()
        ):
            raise ValueError("CAPABILITY_EVIDENCE_REQUIRED")


@dataclass(frozen=True)
class ExecutionCapabilities:
    schema: str
    account: str
    endpoint: str
    instrument: str
    observed_at_ms: int
    known_at_ms: int
    valid_until_ms: int
    position_mode: str
    net_order: CapabilityFact
    reduce_only: CapabilityFact
    cross_zero: CapabilityFact

    def __post_init__(self) -> None:
        if (
            self.schema != "native_execution_capabilities_v1"
            or any(not isinstance(v, str) or not v.strip()
                   for v in (self.account, self.endpoint, self.instrument))
            or any(type(t) is not int for t in
                   (self.observed_at_ms, self.known_at_ms, self.valid_until_ms))
            or not 0 <= self.observed_at_ms <= self.known_at_ms <= self.valid_until_ms
            or self.position_mode not in {"UNKNOWN", "ONE_WAY", "HEDGE"}
            or any(not isinstance(v, CapabilityFact)
                   for v in (self.net_order, self.reduce_only, self.cross_zero))
        ):
            raise ValueError("CAPABILITY_BINDING_OR_CLOCK")
        if any(f.status == "SYNTHETIC" for f in (self.net_order, self.reduce_only, self.cross_zero)):
            if self.endpoint != "fake://broker" or self.account != "fixture-account":
                raise ValueError("SYNTHETIC_CAPABILITY_SCOPE")
        if any(f.status == "VERIFIED" for f in (self.net_order, self.reduce_only, self.cross_zero)):
            if not self.endpoint.startswith("https://"):
                raise ValueError("NATIVE_CAPABILITY_ENDPOINT")

    @classmethod
    def unknown(cls, *, account: str, endpoint: str, instrument: str,
                known_at_ms: int) -> ExecutionCapabilities:
        absent = CapabilityFact("UNKNOWN", None, None)
        return cls("native_execution_capabilities_v1", account, endpoint, instrument,
                   known_at_ms, known_at_ms, known_at_ms, "UNKNOWN", absent, absent, absent)

    def permits(self, feature: str, *, account: str, endpoint: str, instrument: str,
                now_ms: int, synthetic: bool) -> bool:
        if feature not in {"net_order", "reduce_only", "cross_zero"}:
            raise ValueError("CAPABILITY_FEATURE")
        fact = getattr(self, feature)
        return bool(
            type(now_ms) is int and type(synthetic) is bool
            and (self.account, self.endpoint, self.instrument) == (account, endpoint, instrument)
            and self.known_at_ms <= now_ms < self.valid_until_ms
            and self.position_mode == "ONE_WAY"
            and fact.status == ("SYNTHETIC" if synthetic else "VERIFIED")
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(asdict(self), trading_authorized=False)

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> ExecutionCapabilities:
        expected = set(cls.__dataclass_fields__) | {"trading_authorized"}
        if not isinstance(raw, dict) or set(raw) != expected or raw["trading_authorized"] is not False:
            raise ValueError("CAPABILITY_SCHEMA_OR_AUTHORITY")
        data = {k: v for k, v in raw.items() if k != "trading_authorized"}
        for name in ("net_order", "reduce_only", "cross_zero"):
            fact = data[name]
            if not isinstance(fact, dict) or set(fact) != set(CapabilityFact.__dataclass_fields__):
                raise ValueError("CAPABILITY_FACT_SCHEMA")
            data[name] = CapabilityFact(**fact)
        return cls(**data)
