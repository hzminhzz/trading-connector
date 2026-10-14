"""Credential-free safety regression tests for Propr identity."""
import pytest
from connectors.propr.config import Environment, Mode, ProprConfig


def test_read_only_production_requires_no_credentials():
    cfg = ProprConfig(Environment.PRODUCTION)
    assert cfg.mode is Mode.READ_ONLY
    assert cfg.endpoints == ("https://api.propr.xyz/v1", "wss://api.propr.xyz/ws")


@pytest.mark.parametrize("kwargs", [
    {"environment": "unknown"},
    {"environment": "beta"},
    {"environment": "production", "api_url": "https://api.beta.propr.xyz/v1"},
    {"environment": "production", "ws_url": "wss://api.beta.propr.xyz/ws"},
    {"environment": "production", "api_key": "pk_beta_abc"},
    {"environment": "production", "mode": "shadow"},
    {"environment": "production", "mode": "execution", "account_id": "account"},
])
def test_invalid_identity_fails_closed(kwargs):
    with pytest.raises((ValueError, PermissionError)):
        ProprConfig(**kwargs)


def test_production_and_challenge_accounts_are_not_asserted_paper():
    cfg = ProprConfig("production", mode="shadow", account_id="explicit")
    with pytest.raises(PermissionError, match="not independently verified"):
        cfg.assert_paper_trading()
    with pytest.raises(PermissionError):
        cfg.assert_live_trading()
