"""Paper projection is durable, account-separated, and has zero network transport."""

from pathlib import Path
import os
import sqlite3

import pytest

from connectors.propr.combo2_paper_projection import Combo2PaperProjection
from connectors.propr.state import IntentJournal


SHA = "a" * 64
BTC = "hyperliquid/mainnet:perp:default/BTC"


def report():
    return {
        "hypothetical": True,
        "authorized_for_live": False,
        "actions": [
            {
                "kind": "EXTERNAL_PAPER",
                "instrument": BTC,
                "status": "FILLED",
                "quantity": "0.01",
                "price": "100000",
                "fee": "0.1",
                "allocation": {"W028": "0.01"},
                "hypothetical": True,
                "unfilled": "0",
            },
            {
                "kind": "INTERNAL_CROSS",
                "instrument": BTC,
                "quantity": "0.02",
                "buy": "W028",
                "sell": "W015",
            },
        ],
    }


def local_journal(tmp_path: Path):
    path = tmp_path / "journal.db"
    journal = IntentJournal(path)
    original = journal.stage("account-actual", "BTC", "buy", "long", "0.1")
    journal.close()
    os.chmod(path, 0o600)
    return path, original.id


def push(projection, paper_report=None, event_id="rb1"):
    return projection.project(
        run_id="combo2-safe",
        event_id=event_id,
        paper_event_sha=SHA,
        account_alias="#JhW9",
        source_report=paper_report or report(),
    )


def test_idempotent_projection_restarts_without_mutating_broker_intents(tmp_path):
    path, broker_id = local_journal(tmp_path)
    projection = Combo2PaperProjection(path)
    assert push(projection) == 2
    assert push(projection) == 0
    assert projection.summary()["paper_decisions"] == 1
    assert projection.summary()["paper_fills_are_not_broker_fills"]
    projection.close()
    second = Combo2PaperProjection(path)
    assert push(second) == 0
    assert second.summary()["paper_decisions"] == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT id,state FROM intents").fetchall() == [
            (broker_id, "staged")
        ]
        assert (
            conn.execute("SELECT COUNT(*) FROM combo2_paper_actions").fetchone()[0] == 2
        )
    second.close()


def test_conflicting_paper_replay_halts_without_partial_writes(tmp_path):
    path, _ = local_journal(tmp_path)
    projection = Combo2PaperProjection(path)
    assert push(projection) == 2
    forged = report()
    forged["actions"][0]["fee"] = "3.5"
    with pytest.raises(ValueError, match="CONFLICTING_PAPER_DECISION"):
        push(projection, forged)
    assert projection.summary()["paper_decisions"] == 1
    projection.close()


def test_invalid_fill_or_real_broker_claim_rejected_atomically(tmp_path):
    path, _ = local_journal(tmp_path)
    projection = Combo2PaperProjection(path)
    invalid = report()
    invalid["actions"][0]["allocation"] = {"W028": "0.02"}
    with pytest.raises(ValueError, match="INCONSISTENT_HYPOTHETICAL_FILL"):
        push(projection, invalid)
    invalid = report()
    invalid["authorized_for_live"] = True
    with pytest.raises(ValueError, match="UNVERIFIED_PAPER_DECISION"):
        push(projection, invalid)
    invalid = report()
    invalid["actions"][0]["hypothetical"] = False
    with pytest.raises(ValueError, match="MODEL_FILL_MUST_BE_EXPLICIT"):
        push(projection, invalid)
    assert projection.summary()["paper_decisions"] == 0
    projection.close()


def test_crash_rollback_then_replay_is_exactly_once(tmp_path):
    path, _ = local_journal(tmp_path)
    projection = Combo2PaperProjection(path)
    projection.connection.execute(
        "CREATE TRIGGER fail_paper_action BEFORE INSERT ON combo2_paper_actions "
        "BEGIN SELECT RAISE(ABORT, 'paper crash'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="paper crash"):
        push(projection)
    assert projection.summary()["paper_decisions"] == 0
    projection.connection.execute("DROP TRIGGER fail_paper_action")
    assert push(projection) == 2
    projection.close()
    restarted = Combo2PaperProjection(path)
    assert push(restarted) == 0
    assert restarted.summary()["paper_decisions"] == 1
    restarted.close()


def test_projection_has_no_network_order_transport_even_when_injected(
    tmp_path, monkeypatch
):
    path, _ = local_journal(tmp_path)
    attempts = []

    def fail(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("NETWORK_TRANSPORT_REACHABLE")

    monkeypatch.setattr("socket.socket", fail)
    monkeypatch.setattr("urllib.request.urlopen", fail)
    projection = Combo2PaperProjection(path)
    assert push(projection) == 2
    assert projection.summary()["network_order_mutations"] == 0
    assert attempts == []
    projection.close()


def test_rejects_untrusted_path_or_not_intent_journal(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        Combo2PaperProjection(missing)
    invalid = tmp_path / "other.db"
    sqlite3.connect(invalid).close()
    os.chmod(invalid, 0o600)
    with pytest.raises(ValueError, match="NOT_A_PROPR_INTENT_JOURNAL"):
        Combo2PaperProjection(invalid)
    path, _ = local_journal(tmp_path)
    symlink = tmp_path / "alias.db"
    symlink.symlink_to(path)
    with pytest.raises(ValueError, match="SYMLINK"):
        Combo2PaperProjection(symlink)
