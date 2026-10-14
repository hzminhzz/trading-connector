"""SQLite WAL intent journal: staged before submission, ambiguous outcomes halt openings."""
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass


class UnknownOutcome(RuntimeError):
    pass


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid() -> str:
    """26-character Crockford ULID using millisecond time and CSPRNG."""
    value = ((time.time_ns() // 1_000_000) << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


@dataclass(frozen=True)
class Intent:
    id: str
    account_id: str
    asset: str
    side: str
    position_side: str
    quantity: str
    reduce_only: bool
    position_id: str | None = None


class IntentJournal:
    def __init__(self, path):
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS intents (id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
            "at_ms INTEGER NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL)"
        )
        self.connection.commit()
        # Crash recovery is mandatory at startup; never silently reuse staged state.
        self.recover()

    def stage(self, account_id, asset, side, position_side, quantity, reduce_only=False, position_id=None):
        if not account_id or not asset or side not in ("buy", "sell") or position_side not in ("long", "short"):
            raise ValueError("Invalid durable order intent identity")
        if self.has_unknown() and not reduce_only:
            raise UnknownOutcome("Unreconciled intent: opening halted")
        intent = Intent(new_ulid(), account_id, asset, side, position_side, str(quantity), reduce_only, position_id)
        with self.connection:
            self.connection.execute(
                "INSERT INTO intents VALUES (?, ?, ?)",
                (intent.id, json.dumps(intent.__dict__), "staged"),
            )
        return intent

    def mark(self, intent_id, state):
        if state not in (
            "staged", "unknown", "accepted", "pending", "open", "partial",
            "filled", "rejected", "expired", "reconciled",
        ):
            raise ValueError("Invalid journal state")
        with self.connection:
            cur = self.connection.execute("UPDATE intents SET state=? WHERE id=?", (state, intent_id))
            if cur.rowcount != 1:
                raise KeyError(intent_id)

    def has_unknown(self):
        # Includes staged: an in-flight POST may have already left the process.
        return self.connection.execute(
            "SELECT 1 FROM intents WHERE state IN ('staged','unknown') LIMIT 1"
        ).fetchone() is not None

    def unresolved(self):
        rows = self.connection.execute(
            "SELECT id,payload,state FROM intents WHERE state IN ('staged','unknown') ORDER BY rowid"
        ).fetchall()
        return [(identifier, Intent(**json.loads(payload)), state) for identifier, payload, state in rows]

    def get(self, intent_id):
        row = self.connection.execute(
            "SELECT payload FROM intents WHERE id=?", (intent_id,)
        ).fetchone()
        if row is None:
            raise KeyError(intent_id)
        return Intent(**json.loads(row[0]))

    def recover(self):
        with self.connection:
            self.connection.execute("UPDATE intents SET state='unknown' WHERE state='staged'")

    def audit(self, kind, detail):
        # Caller provides sanitized status text; never include tokens / raw account data.
        with self.connection:
            self.connection.execute(
                "INSERT INTO audit (at_ms,kind,detail) VALUES (?,?,?)",
                (time.time_ns() // 1_000_000, kind, detail),
            )

    def audit_history(self):
        return self.connection.execute("SELECT kind,detail FROM audit ORDER BY seq").fetchall()

    def close(self):
        self.connection.close()
