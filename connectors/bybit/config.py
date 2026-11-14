"""Bybit connector configuration with fail-closed execution and Demo isolation gates."""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from connectors.common.config import Mode, load_env_file


class Environment(str, Enum):
    TESTNET = "testnet"
    MAINNET = "mainnet"
    DEMO = "demo"


@dataclass(frozen=True)
class BybitConfig:
    environment: Environment = Environment.TESTNET
    mode: Mode = Mode.READ_ONLY
    api_key: str | None = None
    api_secret: str | None = None
    default_type: str = "swap"  # Linear USDT perpetuals default
    subaccount_id: str | None = None
    expected_demo_uid: str | None = None
    timeout_ms: int = 10000
    enable_rate_limit: bool = True

    def __post_init__(self) -> None:
        if self.mode is Mode.EXECUTION:
            if not self.api_key or not self.api_secret:
                raise ValueError(
                    "API key and secret required for EXECUTION mode"
                )

    @classmethod
    def from_env(cls, env_path: str | Path | None = None) -> BybitConfig:
        load_env_file(env_path)
        explicit_environment = os.getenv("BYBIT_ENVIRONMENT")
        if explicit_environment:
            env = Environment(explicit_environment.strip().lower())
        else:
            testnet_raw = os.getenv("BYBIT_TESTNET", "true").strip().lower()
            env = Environment.TESTNET if testnet_raw in ("1", "true", "yes") else Environment.MAINNET

        mode_str = os.getenv("BYBIT_MODE", Mode.READ_ONLY.value).strip().lower()
        mode = Mode(mode_str)

        # Demo credentials belong to the separate Bybit Demo account.
        # Never silently fall back to mainnet or testnet API credentials.
        key_name = "BYBIT_DEMO_API_KEY" if env is Environment.DEMO else "BYBIT_API_KEY"
        secret_name = "BYBIT_DEMO_API_SECRET" if env is Environment.DEMO else "BYBIT_API_SECRET"
        return cls(
            environment=env,
            mode=mode,
            api_key=os.getenv(key_name),
            api_secret=os.getenv(secret_name),
            default_type=os.getenv("BYBIT_DEFAULT_TYPE", "swap"),
            subaccount_id=os.getenv("BYBIT_SUBACCOUNT_ID"),
            expected_demo_uid=(os.getenv("BYBIT_DEMO_EXPECTED_UID") if env is Environment.DEMO else None),
        )
