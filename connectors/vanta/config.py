"""Fail-closed Vanta account and key identity configuration."""
from __future__ import annotations

from dataclasses import dataclass, field

BASE_URL = "https://app.vantatrading.io"
_PRINTABLE = frozenset(chr(code) for code in range(0x20, 0x7F))


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} required")
    if value != value.strip():
        raise ValueError(f"{name} must not contain surrounding whitespace")
    if any(character not in _PRINTABLE for character in value):
        raise ValueError(f"{name} must be printable ASCII")
    return value


@dataclass(frozen=True)
class VantaConfig:
    """Explicit account and key identity. Vanta has no sandbox or environment switch."""

    account_id: str
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_id", _text(self.account_id, "account_id"))
        object.__setattr__(self, "api_key", _text(self.api_key, "api_key"))
        object.__setattr__(self, "api_secret", _text(self.api_secret, "api_secret"))
        if not self.api_key.startswith("vk_"):
            raise ValueError("api_key must be the vk_ key id, not the secret")
        if not self.api_secret.startswith("vks_"):
            raise ValueError("api_secret must be the vks_ secret")

    @property
    def base_url(self) -> str:
        """Fixed official host; credentials are never sent anywhere else."""
        return BASE_URL
