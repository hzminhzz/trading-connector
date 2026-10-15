# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportMissingImports=false
# type: ignore
"""Unit tests for HyperliquidConfig and environment handling."""
import pytest

from connectors.hyperliquid.config import Environment, HyperliquidConfig, Mode


def test_default_config_is_testnet_and_read_only():
    cfg = HyperliquidConfig()
    assert cfg.environment == Environment.TESTNET
    assert cfg.mode == Mode.READ_ONLY
    assert cfg.wallet_address is None
    assert cfg.private_key is None
    assert cfg.default_type == "swap"


def test_execution_mode_requires_credentials():
    with pytest.raises(ValueError, match="Wallet address and private key required for EXECUTION mode"):
        HyperliquidConfig(mode=Mode.EXECUTION)

    cfg = HyperliquidConfig(
        mode=Mode.EXECUTION,
        wallet_address="0x123",
        private_key="0xabc",
    )
    assert cfg.mode == Mode.EXECUTION
    assert cfg.wallet_address == "0x123"


def test_from_env_defaults(monkeypatch):
    monkeypatch.delenv("HYPERLIQUID_WALLET_ADDRESS", raising=False)
    monkeypatch.delenv("HYPERLIQUID_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("HYPERLIQUID_TESTNET", "true")
    monkeypatch.setenv("HYPERLIQUID_MODE", "shadow")

    cfg = HyperliquidConfig.from_env()
    assert cfg.environment == Environment.TESTNET
    assert cfg.mode == Mode.SHADOW


def test_from_env_mainnet(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_TESTNET", "false")
    monkeypatch.setenv("HYPERLIQUID_MODE", "read_only")
    monkeypatch.setenv("HYPERLIQUID_WALLET_ADDRESS", "0xprod")
    monkeypatch.setenv("HYPERLIQUID_PRIVATE_KEY", "0xkey")

    cfg = HyperliquidConfig.from_env()
    assert cfg.environment == Environment.MAINNET
    assert cfg.wallet_address == "0xprod"
    assert cfg.private_key == "0xkey"
