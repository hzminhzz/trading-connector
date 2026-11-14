"""Hyperliquid connector configuration with fail-closed execution mode gates."""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from connectors.common.config import Mode, load_env_file


class Environment(str, Enum):
    TESTNET = "testnet"
    MAINNET = "mainnet"


@dataclass(frozen=True)
class HyperliquidConfig:
    environment: Environment = Environment.TESTNET
    mode: Mode = Mode.READ_ONLY
    wallet_address: str | None = None
    private_key: str | None = None
    default_type: str = "swap"  # Linear USDC perpetuals default
    timeout_ms: int = 10000
    enable_rate_limit: bool = True

    def __post_init__(self) -> None:
        if self.mode is Mode.EXECUTION:
            if not self.wallet_address or not self.private_key:
                raise ValueError(
                    "Wallet address and private key required for EXECUTION mode"
                )

    @classmethod
    def from_env(cls, env_path: str | Path | None = None) -> HyperliquidConfig:
        load_env_file(env_path)
        env_flag = os.environ.get("HYPERLIQUID_TESTNET", "true").strip().lower()
        environment = (
            Environment.TESTNET if env_flag in ("1", "true", "yes") else Environment.MAINNET
        )

        mode_str = os.environ.get("HYPERLIQUID_MODE", "read_only").strip().lower()
        mode = Mode(mode_str)

        wallet_address = os.environ.get("HYPERLIQUID_WALLET_ADDRESS")
        private_key = os.environ.get("HYPERLIQUID_PRIVATE_KEY")

        return cls(
            environment=environment,
            mode=mode,
            wallet_address=wallet_address,
            private_key=private_key,
        )
