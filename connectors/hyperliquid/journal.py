# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportMissingImports=false
# type: ignore
"""Durable SQLite WAL order intent journal and reconciliation gate for Hyperliquid."""
from __future__ import annotations

import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class UnknownOutcome(RuntimeError):
    """Raised when an order result is ambiguous or an unreconciled intent exists."""


def new_intent_id() -> str:
    """Generates a 26-character Crockford ULID for durable order tracking."""
    value = ((time.time_ns() // 1_000_000) << 80) | secrets.randbits(80)
    chars: list[str] = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


@dataclass(frozen=True)
class IntentRecord:
    id: str
    asset: str
    side: str
    quantity: str
    order_type: str
    limit_price: str | None
    reduce_only: bool
    state: str
    exchange_order_id: str | None = None


class HyperliquidIntentJournal:
    """Manages pre-submission order persistence and crash recovery using SQLite WAL."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.path = str(db_path)
        self.connection = sqlite3.connect(self.path)
        if self.path != ":memory:":
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")

        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS intents (
                id TEXT PRIMARY KEY,
                asset TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity TEXT NOT NULL,
                order_type TEXT NOT NULL,
                limit_price TEXT,
                reduce_only INTEGER NOT NULL,
                state TEXT NOT NULL,
                exchange_order_id TEXT,
                created_at REAL NOT NULL,
                detail TEXT
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS audit (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                at_ms INTEGER NOT NULL,
                kind TEXT NOT NULL,
                detail TEXT NOT NULL
            )"""
        )
        self.connection.commit()
        self.recover()

    def recover(self) -> None:
        """Crash recovery: marks staged intents as unknown since transmission was interrupted."""
        with self.connection:
            cur = self.connection.execute(
                "SELECT id FROM intents WHERE state = 'staged'"
            )
            staged = [row[0] for row in cur.fetchall()]
            if staged:
                now_ms = int(time.time() * 1000)
                for intent_id in staged:
                    self.connection.execute(
                        "UPDATE intents SET state = 'unknown' WHERE id = ?", (intent_id,)
                    )
                    self.connection.execute(
                        "INSERT INTO audit (at_ms, kind, detail) VALUES (?, ?, ?)",
                        (now_ms, "recover_staged_to_unknown", f"Intent {intent_id} interrupted before confirmation"),
                    )

    def has_unknown(self) -> bool:
        """Returns True if any intent is in an ambiguous unreconciled state."""
        cur = self.connection.execute("SELECT 1 FROM intents WHERE state = 'unknown' LIMIT 1")
        return cur.fetchone() is not None

    def assert_opening_safe(self, reduce_only: bool = False) -> None:
        """Fail-closed gate: halts new position openings if ambiguous outcomes exist."""
        if self.has_unknown() and not reduce_only:
            raise UnknownOutcome("Unreconciled order intent exists: position opening halted")

    def stage(
        self,
        asset: str,
        side: str,
        quantity: float,
        order_type: str,
        limit_price: float | None = None,
        reduce_only: bool = False,
    ) -> IntentRecord:
        """Persists order intent before network submission."""
        self.assert_opening_safe(reduce_only=reduce_only)
        intent_id = new_intent_id()
        now_ms = int(time.time() * 1000)

        record = IntentRecord(
            id=intent_id,
            asset=asset,
            side=side,
            quantity=str(quantity),
            order_type=order_type,
            limit_price=str(limit_price) if limit_price is not None else None,
            reduce_only=reduce_only,
            state="staged",
        )

        with self.connection:
            self.connection.execute(
                """INSERT INTO intents (id, asset, side, quantity, order_type, limit_price, reduce_only, state, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'staged', ?)""",
                (
                    record.id,
                    record.asset,
                    record.side,
                    record.quantity,
                    record.order_type,
                    record.limit_price,
                    1 if record.reduce_only else 0,
                    time.time(),
                ),
            )
            self.connection.execute(
                "INSERT INTO audit (at_ms, kind, detail) VALUES (?, ?, ?)",
                (now_ms, "stage", f"Staged intent {intent_id} for {quantity} {asset} {side}"),
            )
        return record

    def mark_submitted(self, intent_id: str, exchange_order_id: str) -> None:
        """Marks intent submitted with exchange confirmation."""
        now_ms = int(time.time() * 1000)
        with self.connection:
            self.connection.execute(
                "UPDATE intents SET state = 'submitted', exchange_order_id = ? WHERE id = ?",
                (exchange_order_id, intent_id),
            )
            self.connection.execute(
                "INSERT INTO audit (at_ms, kind, detail) VALUES (?, ?, ?)",
                (now_ms, "submitted", f"Confirmed intent {intent_id} -> order {exchange_order_id}"),
            )

    def mark_unknown(self, intent_id: str, error: str) -> None:
        """Marks intent unknown when timeout or network drop makes exchange status ambiguous."""
        now_ms = int(time.time() * 1000)
        with self.connection:
            self.connection.execute(
                "UPDATE intents SET state = 'unknown', detail = ? WHERE id = ?",
                (error, intent_id),
            )
            self.connection.execute(
                "INSERT INTO audit (at_ms, kind, detail) VALUES (?, ?, ?)",
                (now_ms, "unknown", f"Intent {intent_id} entered unknown outcome: {error}"),
            )

    def mark_terminal(self, intent_id: str, status: str = "terminal") -> None:
        """Marks intent resolved (e.g. verified filled or cancelled)."""
        now_ms = int(time.time() * 1000)
        with self.connection:
            self.connection.execute(
                "UPDATE intents SET state = ? WHERE id = ?",
                (status, intent_id),
            )
            self.connection.execute(
                "INSERT INTO audit (at_ms, kind, detail) VALUES (?, ?, ?)",
                (now_ms, "terminal", f"Intent {intent_id} marked {status}"),
            )

    def close(self) -> None:
        self.connection.close()
