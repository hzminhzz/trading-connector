"""Shared durable SQLite WAL order-intent journal for CCXT brokers."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from connectors.common.intents import UnknownOutcome, new_ulid, open_wal_sqlite

_INTENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,36}$")

__all__ = ["CCXTIntentJournal", "IntentRecord", "UnknownOutcome", "new_ulid"]


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
    exchange_order_id: str | None
    updated_at: float
    detail: str | None = None


class CCXTIntentJournal:
    """Durable SQLite WAL write-ahead intent log for fail-closed order safety."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path) if str(db_path) != ":memory:" else db_path
        self.connection = open_wal_sqlite(db_path)
        self._init_schema()
        self.recover()

    def _init_schema(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS intents (
                id TEXT PRIMARY KEY,
                asset TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity TEXT NOT NULL,
                order_type TEXT NOT NULL,
                limit_price TEXT,
                reduce_only INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(
                    state IN ('staged','submitted','rejected','filled','cancelled','expired','unknown')
                ),
                exchange_order_id TEXT,
                updated_at REAL NOT NULL,
                detail TEXT
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_intents_state ON intents(state)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_intents_order_id ON intents(exchange_order_id)"
        )
        self.connection.commit()

    def recover(self) -> None:
        # Staged rows found at startup indicate a crash during network submission;
        # quarantine them as unknown rather than guessing whether the venue saw them.
        with self.connection:
            self.connection.execute(
                """
                UPDATE intents
                SET state = 'unknown', updated_at = ?, detail = COALESCE(detail, 'recovered_unconfirmed_in_flight')
                WHERE state = 'staged'
                """,
                (time.time(),),
            )

    def has_unknown(self) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM intents WHERE state = 'unknown' LIMIT 1"
        ).fetchone()
        return row is not None

    def assert_opening_safe(self, reduce_only: bool = False) -> None:
        if not reduce_only and self.has_unknown():
            raise UnknownOutcome("Unreconciled order intent exists: position opening halted")

    def stage(
        self,
        asset: str,
        side: str,
        quantity: str,
        order_type: str,
        limit_price: str | None = None,
        reduce_only: bool = False,
        intent_id: str | None = None,
    ) -> IntentRecord:
        self.assert_opening_safe(reduce_only=reduce_only)
        resolved_id = new_ulid() if intent_id is None else intent_id
        if not isinstance(resolved_id, str) or not _INTENT_ID_RE.fullmatch(resolved_id):
            raise ValueError(f"Invalid intent_id {resolved_id!r}: expected 1..36 [A-Za-z0-9_-] chars")
        now = time.time()
        record = IntentRecord(
            id=resolved_id,
            asset=asset,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            reduce_only=reduce_only,
            state="staged",
            exchange_order_id=None,
            updated_at=now,
            detail=None,
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO intents (
                    id, asset, side, quantity, order_type, limit_price,
                    reduce_only, state, exchange_order_id, updated_at, detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.asset,
                    record.side,
                    record.quantity,
                    record.order_type,
                    record.limit_price,
                    1 if record.reduce_only else 0,
                    record.state,
                    record.exchange_order_id,
                    record.updated_at,
                    record.detail,
                ),
            )
        return record

    def is_reduce_only_for_exchange_order(self, exchange_order_id: str) -> bool | None:
        row = self.connection.execute(
            "SELECT reduce_only FROM intents WHERE exchange_order_id = ? ORDER BY updated_at DESC LIMIT 1",
            (exchange_order_id,),
        ).fetchone()
        return bool(row[0]) if row is not None else None

    def mark_submitted(self, intent_id: str, exchange_order_id: str | None = None) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE intents
                SET state = 'submitted', exchange_order_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (exchange_order_id, time.time(), intent_id),
            )

    def mark_rejected(self, intent_id: str, reason: str = "") -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE intents
                SET state = 'rejected', updated_at = ?, detail = ?
                WHERE id = ?
                """,
                (time.time(), reason[:500] if reason else None, intent_id),
            )

    def mark_unknown(self, intent_id: str, error: str = "") -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE intents
                SET state = 'unknown', updated_at = ?, detail = ?
                WHERE id = ?
                """,
                (time.time(), error[:500] if error else None, intent_id),
            )

    def mark_terminal(self, intent_id: str, status: str, detail: str | None = None) -> None:
        if status not in ("filled", "cancelled", "expired", "rejected"):
            raise ValueError(f"Invalid terminal intent status: {status!r}")
        with self.connection:
            self.connection.execute(
                """
                UPDATE intents
                SET state = ?, updated_at = ?, detail = COALESCE(?, detail)
                WHERE id = ?
                """,
                (status, time.time(), detail, intent_id),
            )

    def get(self, intent_id: str) -> IntentRecord | None:
        row = self.connection.execute(
            """
            SELECT id, asset, side, quantity, order_type, limit_price,
                   reduce_only, state, exchange_order_id, updated_at, detail
            FROM intents WHERE id = ?
            """,
            (intent_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def all_intents(self) -> list[IntentRecord]:
        rows = self.connection.execute(
            """
            SELECT id, asset, side, quantity, order_type, limit_price,
                   reduce_only, state, exchange_order_id, updated_at, detail
            FROM intents ORDER BY rowid
            """
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def intents_for_exchange_order(self, exchange_order_id: str) -> list[IntentRecord]:
        rows = self.connection.execute(
            """
            SELECT id, asset, side, quantity, order_type, limit_price,
                   reduce_only, state, exchange_order_id, updated_at, detail
            FROM intents WHERE exchange_order_id = ?
            """,
            (exchange_order_id,),
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def asset_for_exchange_order(self, exchange_order_id: str) -> str | None:
        rows = self.intents_for_exchange_order(exchange_order_id)
        return rows[0].asset if rows else None

    @staticmethod
    def _row_to_record(row: tuple[object, ...]) -> IntentRecord:
        return IntentRecord(
            id=str(row[0]),
            asset=str(row[1]),
            side=str(row[2]),
            quantity=str(row[3]),
            order_type=str(row[4]),
            limit_price=str(row[5]) if row[5] is not None else None,
            reduce_only=bool(row[6]),
            state=str(row[7]),
            exchange_order_id=str(row[8]) if row[8] is not None else None,
            updated_at=float(row[9]),  # type: ignore[arg-type]
            detail=str(row[10]) if row[10] is not None else None,
        )

    def close(self) -> None:
        self.connection.close()
