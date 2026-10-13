"""SQLite write-ahead intent journal; ambiguity blocks new exposure."""
import json
import sqlite3
import uuid
from dataclasses import dataclass

class UnknownOutcome(RuntimeError):
    pass

@dataclass(frozen=True)
class Intent:
    id: str
    account_id: str
    asset: str
    side: str
    position_side: str
    quantity: str
    reduce_only: bool

class IntentJournal:
    def __init__(self, path):
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS intents (id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL)")
        self.connection.commit()

    def stage(self, account_id, asset, side, position_side, quantity, reduce_only=False):
        if self.has_unknown() and not reduce_only:
            raise UnknownOutcome("Unreconciled intent: opening halted")
        intent = Intent(str(uuid.uuid4()), account_id, asset, side, position_side, str(quantity), reduce_only)
        with self.connection:
            self.connection.execute("INSERT INTO intents VALUES (?, ?, ?)", (intent.id, json.dumps(intent.__dict__), "staged"))
        return intent

    def mark(self, intent_id, state):
        if state not in ("staged", "unknown", "accepted", "rejected", "reconciled"):
            raise ValueError("Invalid journal state")
        with self.connection:
            cur = self.connection.execute("UPDATE intents SET state=? WHERE id=?", (state, intent_id))
            if cur.rowcount != 1:
                raise KeyError(intent_id)

    def has_unknown(self):
        return self.connection.execute("SELECT 1 FROM intents WHERE state='unknown' LIMIT 1").fetchone() is not None

    def recover(self):
        # A staged command might have been sent before process crash.
        with self.connection:
            self.connection.execute("UPDATE intents SET state='unknown' WHERE state='staged'")

    def close(self):
        self.connection.close()
