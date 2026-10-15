"""Credential-free regression tests for Bybit Demo Trading environment routing."""
from unittest.mock import MagicMock

import pytest

from connectors.bybit.broker import BybitLiveBroker
from connectors.bybit.client import BybitClient
from connectors.bybit.config import BybitConfig, Environment, Mode
from connectors.bybit.journal import BybitIntentJournal


def test_demo_environment_uses_distinct_credentials(monkeypatch):
    monkeypatch.setenv("BYBIT_ENVIRONMENT", "demo")
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    monkeypatch.setenv("BYBIT_MODE", "execution")
    monkeypatch.setenv("BYBIT_API_KEY", "mainnet-key")
    monkeypatch.setenv("BYBIT_API_SECRET", "mainnet-secret")
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "demo-key")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "demo-secret")

    cfg = BybitConfig.from_env()
    assert cfg.environment is Environment.DEMO
    assert cfg.mode is Mode.EXECUTION
    assert cfg.api_key == "demo-key"
    assert cfg.api_secret == "demo-secret"


def test_demo_environment_never_falls_back_to_mainnet_credentials(monkeypatch):
    monkeypatch.setenv("BYBIT_ENVIRONMENT", "demo")
    monkeypatch.setenv("BYBIT_MODE", "execution")
    monkeypatch.setenv("BYBIT_API_KEY", "mainnet-key")
    monkeypatch.setenv("BYBIT_API_SECRET", "mainnet-secret")
    monkeypatch.delenv("BYBIT_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_DEMO_API_SECRET", raising=False)

    with pytest.raises(ValueError, match="API key and secret required"):
        BybitConfig.from_env()


def test_explicit_environment_overrides_legacy_testnet_flag(monkeypatch):
    monkeypatch.setenv("BYBIT_ENVIRONMENT", "demo")
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    monkeypatch.setenv("BYBIT_MODE", "read_only")
    monkeypatch.delenv("BYBIT_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_DEMO_API_SECRET", raising=False)

    cfg = BybitConfig.from_env()
    assert cfg.environment is Environment.DEMO
    assert cfg.api_key is None
    assert cfg.api_secret is None


def test_demo_ccxt_uses_demo_rest_without_sandbox():
    cfg = BybitConfig(environment=Environment.DEMO)
    client = BybitClient(cfg)
    assert client.exchange.options["enableDemoTrading"] is True
    assert client.exchange.isSandboxModeEnabled is False
    urls = client.exchange.urls["api"]
    assert "api-demo" in urls["private"]
    assert "api-demo" in urls["public"]


def test_demo_routing_on_injected_exchange():
    exchange = MagicMock()
    BybitClient(BybitConfig(environment=Environment.DEMO), exchange=exchange)
    exchange.enable_demo_trading.assert_called_once_with(True)
    exchange.set_sandbox_mode.assert_not_called()


def test_demo_is_paper_but_not_live():
    client = MagicMock()
    client.config = BybitConfig(environment=Environment.DEMO, mode=Mode.EXECUTION, api_key="demo", api_secret="demo")
    client.demo_identity_verified = True  # synthetic authenticated Demo fixture
    broker = BybitLiveBroker(client=client, journal=BybitIntentJournal(":memory:"))
    broker._connected = True
    broker.assert_paper_trading()
    with pytest.raises(PermissionError, match="MAINNET"):
        broker.assert_live_trading()


def test_demo_read_only_disallows_remote_mutations():
    cfg = BybitConfig(environment=Environment.DEMO, mode=Mode.READ_ONLY)
    client = BybitClient(cfg, exchange=MagicMock())
    with pytest.raises(PermissionError, match="Remote order mutation"):
        client._assert_execution_allowed("create_order")
