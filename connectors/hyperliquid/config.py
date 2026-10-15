# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportMissingImports=false
# type: ignore
"""Hyperliquid connector configuration and environment isolation."""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Environment(str, Enum):
    MAINNET = "mainnet"
    TESTNET = "testnet"


class Mode(str, Enum):
    READ_ONLY = "read_only"
    SHADOW = "shadow"
    EXECUTION = "execution"


@dataclass(frozen=True)
class HyperliquidConfig:
    environment: Environment = Environment.TESTNET
    mode: Mode = Mode.READ_ONLY
    wallet_address: str | None = None
    private_key: str | None = None
    vault_address: str | None = None
    default_type: str = "swap"  # Linear USDC perpetuals default

    def __post_init__(self) -> None:
        env = Environment(self.environment)
        mode = Mode(self.mode)
        object.__setattr__(self, "environment", env)
        object.__setattr__(self, "mode", mode)

        if mode is Mode.EXECUTION:
            if not self.wallet_address or not self.private_key:
                raise ValueError("Wallet address and private key required for EXECUTION mode")

    @classmethod
    def from_env(cls, env_path: str | Path | None = None) -> HyperliquidConfig:
        """Loads configuration from environment variables or .env file."""
        try:
            from dotenv import load_dotenv

            if env_path:
                _ = load_dotenv(env_path)
            else:
                _ = load_dotenv()
        except ImportError:
            pass

        testnet_raw = os.getenv("HYPERLIQUID_TESTNET", "true").lower()
        is_testnet = testnet_raw in ("true", "1", "yes", "t")
        env = Environment.TESTNET if is_testnet else Environment.MAINNET

        mode_str = os.getenv("HYPERLIQUID_MODE", Mode.READ_ONLY.value).lower()
        mode = Mode(mode_str)

        return cls(
            environment=env,
            mode=mode,
            wallet_address=os.getenv("HYPERLIQUID_WALLET_ADDRESS"),
            private_key=os.getenv("HYPERLIQUID_PRIVATE_KEY"),
            vault_address=os.getenv("HYPERLIQUID_VAULT_ADDRESS"),
            default_type=os.getenv("HYPERLIQUID_DEFAULT_TYPE", "swap"),
        )
