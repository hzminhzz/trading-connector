import pytest
from connectors.propr.beta_safety import BetaEvidence, BetaSafety, DEFAULT_BETA_EVIDENCE
from connectors.propr.config import ProprConfig

def test_beta_unverified_by_default():
    assert DEFAULT_BETA_EVIDENCE.status is BetaSafety.UNVERIFIED
    with pytest.raises(PermissionError):
        DEFAULT_BETA_EVIDENCE.assert_simulated()

def test_caller_cannot_self_certify():
    fake = BetaEvidence(BetaSafety.VERIFIED_SIMULATED, "unverified URL", "demo",
                        "simulated", "no fees", "none")
    with pytest.raises(PermissionError):
        fake.assert_simulated()

def test_beta_config_cannot_arm_orders():
    config = ProprConfig("beta", account_id="selected", api_key="pk_beta_dummy")
    with pytest.raises(PermissionError):
        config.assert_paper_trading()
