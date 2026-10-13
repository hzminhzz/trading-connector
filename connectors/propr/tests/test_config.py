"""Credential-free safety regression tests for Propr identity."""
import pytest
from connectors.propr.config import Environment, Mode, ProprConfig


def test_read_only_requires_no_credentials():
    assert ProprConfig(Environment.BETA).mode is Mode.READ_ONLY


@pytest.mark.parametrize("kwargs", [
    {"environment": "unknown"},
    {"environment": "beta", "api_url": "https://api.propr.xyz/v1"},
    {"environment": "production", "ws_url": "wss://api.beta.propr.xyz/ws"},
    {"environment": "beta", "api_key": "pk_live_abc"},
    {"environment": "production", "api_key": "pk_beta_abc"},
    {"environment": "beta", "mode": "shadow"},
    {"environment": "beta", "mode": "execution", "account_id": "account"},
])
def test_invalid_identity_fails_closed(kwargs):
    with pytest.raises((ValueError, PermissionError)):
        ProprConfig(**kwargs)


def test_beta_is_not_automatically_paper():
    cfg = ProprConfig("beta", mode="shadow", account_id="explicit")
    with pytest.raises(PermissionError):
        cfg.assert_paper_trading()
    with pytest.raises(PermissionError):
        cfg.assert_live_trading()
