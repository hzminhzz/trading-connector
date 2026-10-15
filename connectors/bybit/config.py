# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportMissingImports=false
"""Bybit connector configuration and environment isolation."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Environment(str, Enum):
    MAINNET = "mainnet"
    TESTNET = "testnet"
    DEMO = "demo"


class Mode(str, Enum):
    READ_ONLY = "read_only"
    SHADOW = "shadow"
    EXECUTION = "execution"


@dataclass(frozen=True)
class BybitConfig:
    environment: Environment = Environment.TESTNET
    mode: Mode = Mode.READ_ONLY
    api_key: str | None = field(default=None, repr=False)
    api_secret: str | None = field(default=None, repr=False)
    default_type: str = "swap"  # Linear USDT perpetuals default
    subaccount_id: str | None = None
    expected_demo_uid: str | None = None

    def __post_init__(self) -> None:
        env = Environment(self.environment)
        mode = Mode(self.mode)
        object.__setattr__(self, "environment", env)
        object.__setattr__(self, "mode", mode)

        if mode is Mode.EXECUTION:
            if not self.api_key or not self.api_secret:
                raise ValueError("API key and secret required for EXECUTION mode")

    @classmethod
    def from_env(cls, env_path: str | Path | None = None) -> BybitConfig:
        if not os.getenv("PYTHON_DOTENV_DISABLED"):
            try:
                from dotenv import load_dotenv

                if env_path:
                    _ = load_dotenv(env_path)
                else:
                    demo_env = Path.home() / ".config" / "trading-connector" / "bybit-demo.env"
                    if demo_env.is_file():
                        _ = load_dotenv(demo_env)
                    _ = load_dotenv()
            except ImportError:
                pass

        explicit_environment = os.getenv("BYBIT_ENVIRONMENT")
        if explicit_environment is not None:
            env = Environment(explicit_environment.strip().lower())
        else:
            testnet_raw = os.getenv("BYBIT_TESTNET", "true").lower()
            is_testnet = testnet_raw in ("true", "1", "yes", "t")
            env = Environment.TESTNET if is_testnet else Environment.MAINNET

        mode_str = os.getenv("BYBIT_MODE", Mode.READ_ONLY.value).lower()
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
