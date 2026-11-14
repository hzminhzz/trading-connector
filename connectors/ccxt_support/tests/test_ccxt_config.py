"""Contract tests for Bybit and Hyperliquid configuration defaults and env parsing."""
from __future__ import annotations

import pytest

from connectors.bybit.config import BybitConfig, Environment as BybitEnv, Mode as BybitMode
from connectors.hyperliquid.config import Environment as HLEnv, HyperliquidConfig, Mode as HLMode


def test_bybit_config_defaults_and_env(monkeypatch):
    default = BybitConfig()
    assert default.environment == BybitEnv.TESTNET
    assert default.mode == BybitMode.READ_ONLY
    assert default.api_key is None
    assert default.api_secret is None
    assert default.default_type == "swap"

    with pytest.raises(ValueError, match="API key and secret required for EXECUTION mode"):
        BybitConfig(mode=BybitMode.EXECUTION)

    exec_cfg = BybitConfig(mode=BybitMode.EXECUTION, api_key="k", api_secret="s")
    assert exec_cfg.mode == BybitMode.EXECUTION

    monkeypatch.delenv("BYBIT_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_API_SECRET", raising=False)
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    monkeypatch.setenv("BYBIT_MODE", "shadow")
    shadow_cfg = BybitConfig.from_env()
    assert shadow_cfg.environment == BybitEnv.TESTNET
    assert shadow_cfg.mode == BybitMode.SHADOW

    monkeypatch.setenv("BYBIT_TESTNET", "false")
    monkeypatch.setenv("BYBIT_MODE", "read_only")
    monkeypatch.setenv("BYBIT_API_KEY", "prod_key")
    monkeypatch.setenv("BYBIT_API_SECRET", "prod_secret")
    prod_cfg = BybitConfig.from_env()
    assert prod_cfg.environment == BybitEnv.MAINNET
    assert prod_cfg.api_key == "prod_key"
    assert prod_cfg.api_secret == "prod_secret"


def test_hyperliquid_config_defaults_and_env(monkeypatch):
    default = HyperliquidConfig()
    assert default.environment == HLEnv.TESTNET
    assert default.mode == HLMode.READ_ONLY
    assert default.wallet_address is None
    assert default.private_key is None
    assert default.default_type == "swap"

    with pytest.raises(ValueError, match="Wallet address and private key required for EXECUTION mode"):
        HyperliquidConfig(mode=HLMode.EXECUTION)

    exec_cfg = HyperliquidConfig(mode=HLMode.EXECUTION, wallet_address="0x123", private_key="0xabc")
    assert exec_cfg.mode == HLMode.EXECUTION

    monkeypatch.delenv("HYPERLIQUID_WALLET_ADDRESS", raising=False)
    monkeypatch.delenv("HYPERLIQUID_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("HYPERLIQUID_TESTNET", "true")
    monkeypatch.setenv("HYPERLIQUID_MODE", "shadow")
    shadow_cfg = HyperliquidConfig.from_env()
    assert shadow_cfg.environment == HLEnv.TESTNET
    assert shadow_cfg.mode == HLMode.SHADOW

    monkeypatch.setenv("HYPERLIQUID_TESTNET", "false")
    monkeypatch.setenv("HYPERLIQUID_MODE", "read_only")
    monkeypatch.setenv("HYPERLIQUID_WALLET_ADDRESS", "0xprod")
    monkeypatch.setenv("HYPERLIQUID_PRIVATE_KEY", "0xkey")
    prod_cfg = HyperliquidConfig.from_env()
    assert prod_cfg.environment == HLEnv.MAINNET
    assert prod_cfg.wallet_address == "0xprod"
    assert prod_cfg.private_key == "0xkey"
