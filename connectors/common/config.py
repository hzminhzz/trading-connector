"""Shared execution mode enum and optional .env loader."""
from __future__ import annotations

from enum import Enum
from pathlib import Path


class Mode(str, Enum):
    READ_ONLY = "read_only"
    SHADOW = "shadow"
    EXECUTION = "execution"


def load_env_file(env_path: str | Path | None = None) -> None:
    """Load environment variables from .env via python-dotenv when available."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    if env_path is not None:
        load_dotenv(env_path)
    else:
        load_dotenv()
