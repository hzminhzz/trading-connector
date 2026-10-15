# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportMissingImports=false
"""Unit tests for BybitConfig and environment handling."""
import pytest

from connectors.bybit.config import BybitConfig, Environment, Mode


def test_default_config_is_testnet_and_read_only():
    cfg = BybitConfig()
    assert cfg.environment == Environment.TESTNET
    assert cfg.mode == Mode.READ_ONLY
    assert cfg.api_key is None
    assert cfg.api_secret is None
    assert cfg.default_type == "swap"


def test_execution_mode_requires_credentials():
    with pytest.raises(ValueError, match="API key and secret required for EXECUTION mode"):
        BybitConfig(mode=Mode.EXECUTION)

    cfg = BybitConfig(
        mode=Mode.EXECUTION,
        api_key="test_key",
        api_secret="test_secret",
    )
    assert cfg.mode == Mode.EXECUTION
    assert cfg.api_key == "test_key"


def test_from_env_defaults(monkeypatch):
    monkeypatch.delenv("BYBIT_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_API_SECRET", raising=False)
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    monkeypatch.setenv("BYBIT_MODE", "shadow")

    cfg = BybitConfig.from_env()
    assert cfg.environment == Environment.TESTNET
    assert cfg.mode == Mode.SHADOW


def test_from_env_mainnet(monkeypatch):
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    monkeypatch.setenv("BYBIT_MODE", "read_only")
    monkeypatch.setenv("BYBIT_API_KEY", "prod_key")
    monkeypatch.setenv("BYBIT_API_SECRET", "prod_secret")

    cfg = BybitConfig.from_env()
    assert cfg.environment == Environment.MAINNET
    assert cfg.api_key == "prod_key"
    assert cfg.api_secret == "prod_secret"
