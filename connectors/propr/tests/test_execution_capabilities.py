"""Provider-neutral facts never manufacture execution permission."""
from dataclasses import replace

import pytest

from connectors.execution_capabilities import CapabilityFact, ExecutionCapabilities


def fixture():
    yes = CapabilityFact("SYNTHETIC", "a" * 64, "fixture-native-mode")
    return ExecutionCapabilities("native_execution_capabilities_v1", "fixture-account",
                                 "fake://broker", "BTC", 1000, 1000, 2000,
                                 "ONE_WAY", yes, yes, yes)


def test_capability_binding_and_deadline_are_exact():
    proof = fixture()
    assert proof.permits("net_order", account="fixture-account", endpoint="fake://broker",
                         instrument="BTC", now_ms=1999, synthetic=True)
    for changed in (
        {"account": "wrong"}, {"endpoint": "https://other"}, {"instrument": "ETH"},
        {"now_ms": 2000}, {"now_ms": 999}, {"synthetic": False},
    ):
        args = dict(account="fixture-account", endpoint="fake://broker", instrument="BTC",
                    now_ms=1500, synthetic=True)
        args.update(changed)
        assert not proof.permits("net_order", **args)
    assert proof.to_dict()["trading_authorized"] is False
    assert ExecutionCapabilities.parse(proof.to_dict()) == proof


def test_unknown_and_hedge_mode_never_imply_net_or_cross_zero():
    proof = ExecutionCapabilities.unknown(account="account", endpoint="https://provider",
                                         instrument="BTC", known_at_ms=1000)
    for feature in ("net_order", "reduce_only", "cross_zero"):
        assert not proof.permits(feature, account="account", endpoint="https://provider",
                                 instrument="BTC", now_ms=1000, synthetic=False)
    hedge = replace(fixture(), position_mode="HEDGE")
    assert not hedge.permits("cross_zero", account="fixture-account", endpoint="fake://broker",
                             instrument="BTC", now_ms=1001, synthetic=True)


def test_native_verified_fact_requires_retained_evidence_and_provenance():
    with pytest.raises(ValueError):
        CapabilityFact("VERIFIED", None, None)
    raw = fixture().to_dict()
    raw["trading_authorized"] = True
    with pytest.raises(ValueError):
        ExecutionCapabilities.parse(raw)
    raw = fixture().to_dict()
    raw["valid_until_ms"] = True
    with pytest.raises(ValueError):
        ExecutionCapabilities.parse(raw)
