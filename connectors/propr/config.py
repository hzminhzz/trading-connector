"""Fail-closed Propr environment and execution identity configuration."""
from dataclasses import dataclass
from enum import Enum


class Environment(str, Enum):
    PRODUCTION = "production"


class Mode(str, Enum):
    READ_ONLY = "read_only"
    SHADOW = "shadow"
    EXECUTION = "execution"


PRODUCTION_WS_URL = "wss://api.propr.xyz/ws"
_ENDPOINTS = ("https://api.propr.xyz/v1", PRODUCTION_WS_URL)


@dataclass(frozen=True)
class ProprConfig:
    environment: Environment
    mode: Mode = Mode.READ_ONLY
    account_id: str | None = None
    api_key: str | None = None
    api_url: str | None = None
    ws_url: str | None = None

    def __post_init__(self) -> None:
        env = Environment(self.environment)
        mode = Mode(self.mode)
        object.__setattr__(self, "environment", env)
        object.__setattr__(self, "mode", mode)
        expected_api, expected_ws = _ENDPOINTS
        if self.api_url is not None and self.api_url != expected_api:
            raise ValueError("API endpoint does not match selected environment")
        if self.ws_url is not None and self.ws_url != expected_ws:
            raise ValueError("WebSocket endpoint does not match selected environment")
        if self.api_key is not None and not self.api_key.startswith("pk_live_"):
            raise ValueError("Production requires a live credential")
        if mode is not Mode.READ_ONLY and not (self.account_id and self.account_id.strip()):
            raise ValueError("Explicit account identity required")
        if mode is Mode.EXECUTION:
            raise PermissionError("Remote order execution remains disabled")

    @property
    def endpoints(self) -> tuple[str, str]:
        return _ENDPOINTS

    def assert_paper_trading(self) -> None:
        raise PermissionError("Propr challenge-account paper execution is not independently verified")

    def assert_live_trading(self) -> None:
        raise PermissionError("Production trading is not authorized")
