"""Shared durable-intent identity, ambiguity signal, and SQLite WAL helper."""
from __future__ import annotations

import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_UNSET = object()


class UnknownOutcome(RuntimeError):
    """Raised when execution or market-data state cannot be proven consistent."""


def new_ulid() -> str:
    """Generate a 26-character Crockford base32 time-ordered ULID."""
    timestamp_ms = int(time.time() * 1000)
    raw = (timestamp_ms << 80) | secrets.randbits(80)
    chars: list[str] = []
    for _ in range(26):
        chars.append(_CROCKFORD[raw & 0x1F])
        raw >>= 5
    return "".join(reversed(chars))


def open_wal_sqlite(
    db_path: str | Path,
    *,
    synchronous: str = "NORMAL",
    timeout: float | None = None,
    isolation_level: Any = _UNSET,
) -> sqlite3.Connection:
    """Open a SQLite connection configured with WAL mode and busy timeout."""
    path_str = str(db_path)
    if path_str != ":memory:":
        Path(path_str).parent.mkdir(parents=True, exist_ok=True)
    connect_kwargs: dict[str, Any] = {}
    if timeout is not None:
        connect_kwargs["timeout"] = timeout
    if isolation_level is not _UNSET:
        connect_kwargs["isolation_level"] = isolation_level
    conn = sqlite3.connect(path_str, **connect_kwargs)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA synchronous={synchronous}")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn
