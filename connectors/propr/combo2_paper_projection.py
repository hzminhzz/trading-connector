"""Deterministic Combo2 PAPER projections into a local IntentJournal SQLite DB.

The existing broker-intent tables are not modified. Paper intents and simulated
fills have their own tables, so a model outcome can never become a broker fill
or a broker-reconciled order. No account client, network or execution gateway
may be passed to this component.
"""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import stat

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ACCOUNT_ALIAS = "#JhW9"
_PAPER_KINDS = frozenset({"EXTERNAL_PAPER", "INTERNAL_CROSS", "REJECTED"})
_FILL_STATES = frozenset({"FILLED", "PARTIAL"})


def _blob(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _finite(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("INVALID_PAPER_AMOUNT")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("INVALID_PAPER_AMOUNT")
    return number


def _safe_paper_action(action: dict) -> None:
    if not isinstance(action, dict) or action.get("kind") not in _PAPER_KINDS:
        raise ValueError("UNKNOWN_PAPER_ACTION")
    if not isinstance(action.get("instrument"), str) or not action[
        "instrument"
    ].startswith("hyperliquid/mainnet:perp:"):
        raise ValueError("UNVERIFIED_PAPER_INSTRUMENT")
    if action["kind"] == "EXTERNAL_PAPER":
        status = action.get("status")
        if not isinstance(status, str):
            raise ValueError("MISSING_PAPER_OUTCOME")
        if status in _FILL_STATES:
            if action.get("hypothetical") is not True:
                raise ValueError("MODEL_FILL_MUST_BE_EXPLICIT")
            qty = _finite(action["quantity"])
            price = _finite(action["price"])
            fee = _finite(action["fee"])
            allocation = action["allocation"]
            if not isinstance(allocation, dict) or not allocation:
                raise ValueError("MISSING_MANAGER_ATTRIBUTION")
            if (
                not qty
                or price <= 0
                or fee < 0
                or sum(
                    (_finite(amount) for amount in allocation.values()), Decimal("0")
                )
                != qty
            ):
                raise ValueError("INCONSISTENT_HYPOTHETICAL_FILL")
        elif "quantity" in action:
            raise ValueError("REJECTED_PAPER_INTENT_CANNOT_HAVE_FILL_QUANTITY")
    elif action["kind"] == "INTERNAL_CROSS":
        if _finite(action.get("quantity")) <= 0:
            raise ValueError("INVALID_PAPER_INTERNAL_CROSS")
    elif not isinstance(action.get("reason"), str):
        raise ValueError("MISSING_REJECTION_REASON")


class Combo2PaperProjection:
    """Append-only, exactly-once SQLite projection; never mutates broker state."""

    def __init__(self, journal_db: Path):
        self.path = Path(journal_db)
        if self.path.is_symlink():
            raise ValueError("PAPER_SQLITE_SYMLINK")
        if not self.path.parent.exists() or self.path.parent.stat().st_mode & 0o077:
            raise ValueError("PRIVATE_JOURNAL_DIRECTORY_REQUIRED")
        if self.path.exists() and (
            not stat.S_ISREG(self.path.stat().st_mode)
            or self.path.stat().st_mode & 0o077
        ):
            raise ValueError("PAPER_SQLITE_NOT_PRIVATE")
        if not self.path.exists():
            raise FileNotFoundError("CREATE_INTENT_JOURNAL_SEPARATELY")
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        # Existing IntentJournal broker-intent tables are authoritative and
        # completely untouched by this paper-only projection.
        names = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not {"intents", "audit"}.issubset(names):
            self.connection.close()
            raise ValueError("NOT_A_PROPR_INTENT_JOURNAL")
        with self.connection:
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS combo2_paper_decisions ("
                "run_id TEXT NOT NULL, event_id TEXT NOT NULL, paper_sha TEXT NOT NULL, "
                "payload_sha TEXT NOT NULL, payload_json TEXT NOT NULL, "
                "PRIMARY KEY(run_id,event_id))"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS combo2_paper_actions ("
                "run_id TEXT NOT NULL, event_id TEXT NOT NULL, action_index INTEGER NOT NULL, "
                "kind TEXT NOT NULL, simulated_status TEXT, payload_json TEXT NOT NULL, "
                "PRIMARY KEY(run_id,event_id,action_index), "
                "FOREIGN KEY(run_id,event_id) REFERENCES combo2_paper_decisions(run_id,event_id))"
            )

    def close(self) -> None:
        self.connection.close()

    def project(
        self,
        *,
        run_id: str,
        event_id: str,
        paper_event_sha: str,
        account_alias: str,
        source_report: dict,
    ) -> int:
        """Project verified paper output only; never remote-accepted broker fills.

        The caller must verify the PM journal chain and post-state before passing
        the report. Original paper journal is the sole accounting authority.
        """
        if (
            not run_id
            or "/" in run_id
            or not event_id
            or not _SHA.fullmatch(paper_event_sha)
            or account_alias != _ACCOUNT_ALIAS
            or not isinstance(source_report, dict)
            or source_report.get("hypothetical") is not True
            or source_report.get("authorized_for_live") is not False
        ):
            raise ValueError("UNVERIFIED_PAPER_DECISION")
        actions = source_report.get("actions")
        if not isinstance(actions, list) or len(actions) > 400:
            raise ValueError("PAPER_ACTION_LIMIT")
        for action in actions:
            _safe_paper_action(action)
        payload = {
            "run_id": run_id,
            "event_id": event_id,
            "paper_event_sha": paper_event_sha,
            "account_alias": account_alias,
            "hypothetical": True,
            "real_broker_filled": False,
            "actions": actions,
        }
        body = _blob(payload)
        digest = sha256(body).hexdigest()
        with self.connection:
            # Atomicity: a process killed after the first INSERT has no
            # committed decision or simulated action.
            self.connection.execute("BEGIN IMMEDIATE")
            existing = self.connection.execute(
                "SELECT paper_sha,payload_sha FROM combo2_paper_decisions "
                "WHERE run_id=? AND event_id=?",
                (run_id, event_id),
            ).fetchone()
            if existing:
                if existing != (paper_event_sha, digest):
                    raise ValueError("CONFLICTING_PAPER_DECISION_REPLAY")
                return 0
            self.connection.execute(
                "INSERT INTO combo2_paper_decisions VALUES (?,?,?,?,?)",
                (run_id, event_id, paper_event_sha, digest, body.decode()),
            )
            for index, action in enumerate(actions):
                self.connection.execute(
                    "INSERT INTO combo2_paper_actions VALUES (?,?,?,?,?,?)",
                    (
                        run_id,
                        event_id,
                        index,
                        action["kind"],
                        action.get("status"),
                        _blob(action).decode(),
                    ),
                )
        return len(actions)

    def summary(self) -> dict:
        decisions = self.connection.execute(
            "SELECT COUNT(*) FROM combo2_paper_decisions"
        ).fetchone()[0]
        actions = self.connection.execute(
            "SELECT kind,simulated_status,COUNT(*) FROM combo2_paper_actions "
            "GROUP BY kind,simulated_status"
        ).fetchall()
        return {
            "schema": "combo2_paper_projection_v1",
            "local_journal_only": True,
            "network_order_mutations": 0,
            "paper_decisions": decisions,
            "paper_actions": [
                {"kind": kind, "simulated_status": status, "count": count}
                for kind, status, count in actions
            ],
            "real_account_monitor": "SEPARATE_NOT_IMPORTED",
            "paper_fills_are_not_broker_fills": True,
        }
