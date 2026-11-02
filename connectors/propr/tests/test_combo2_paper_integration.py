"""Cross-repository paper replay -> SQLite, with zero broker routes.

Requires copytrade-pm installed in the test environment. Otherwise the
connector-only CI stays isolated and skips the optional integration test.
"""

from datetime import UTC, datetime, timedelta
import os
import sqlite3

import pytest

from connectors.propr.examples.project_combo2_paper import verified_projection
from connectors.propr.state import IntentJournal

Combo2PaperTracker = pytest.importorskip("copytrade_pm.pm_combo2").Combo2PaperTracker

BASE = datetime(2026, 11, 2, 15, tzinfo=UTC)
BTC = "hyperliquid/mainnet:perp:default/BTC"
IDS = {"W028": "a" * 64, "CA-008": "b" * 64, "W015": "c" * 64}


def moment(delta=0):
    return (BASE + timedelta(seconds=delta)).isoformat()


def record(engine, *, kind, event_id, sec, data, source_sec=0):
    return engine.record(
        kind=kind,
        event_id=event_id,
        source_event_at=moment(source_sec),
        first_known_at=moment(sec),
        decision_at=moment(sec),
        hypothetical_fill_at=moment(sec),
        data=data,
    )


def make_paper(tmp_path):
    root = tmp_path / "paper"
    engine = Combo2PaperTracker(
        root, run_id="combo2-local", started_at=moment(), source_subject_hashes=IDS
    )
    record(
        engine,
        kind="SOURCE_RECEIPT",
        event_id="r1",
        sec=0,
        data={"manager": "W028", "instrument": BTC},
    )
    source = {
        "manager": "W028",
        "subject_sha": IDS["W028"],
        "quality": "OFFICIAL_ALL_DEXS_READ_ONLY",
        "full_book": True,
        "all_dexes_complete": True,
        "equity": "100000",
        "positions": {BTC: {"quantity": "3", "mark": "100000"}},
        "event_at": moment(300),
        "first_known_at": moment(300),
    }
    quote = {
        "first_known_at": moment(300),
        "mark": "100000",
        "lot": "0.0000001",
        "minimum": "0.0000001",
        "volume_base": "1000",
        "fee_bps": "0",
        "spread_bps": "0",
        "slippage_bps": "0",
        "session_open": True,
        "supported": True,
        "native_market_id": "BTC-PERP",
    }
    record(
        engine,
        kind="PAPER_REBALANCE",
        event_id="d1",
        sec=300,
        data={
            "leaders": {"W028": source},
            "markets": {BTC: quote},
            "shocks": [{BTC: "-0.01"}],
        },
    )
    assert len(engine.verified_paper_decisions()) == 1
    return root, engine


def test_verified_paper_chain_projects_once_without_touching_real_intents(tmp_path):
    paper_root, engine = make_paper(tmp_path)
    journal_path = tmp_path / "intent-journal.sqlite"
    journal = IntentJournal(journal_path)
    real_intent = journal.stage("real-account", "BTC", "buy", "long", "0.2")
    journal.close()
    os.chmod(journal_path, 0o600)
    before = engine.state()
    result = verified_projection(paper_root, "combo2-local", journal_path)
    assert result["new_paper_action_rows"] == 1
    assert result["verified_source_paper_decisions"] == 1
    assert result["real_propr_account_queried"] is False
    assert result["remote_order_transport"] == "ABSENT"
    assert (
        verified_projection(paper_root, "combo2-local", journal_path)[
            "new_paper_action_rows"
        ]
        == 0
    )
    assert engine.state() == before
    with sqlite3.connect(journal_path) as conn:
        assert conn.execute("SELECT id,state FROM intents").fetchall() == [
            (real_intent.id, "staged")
        ]
        assert (
            conn.execute("SELECT COUNT(*) FROM combo2_paper_actions").fetchone()[0] == 1
        )


def test_corrupt_paper_journal_never_projects_new_records(tmp_path):
    paper_root, _ = make_paper(tmp_path)
    journal_path = tmp_path / "intent-journal.sqlite"
    journal = IntentJournal(journal_path)
    journal.close()
    os.chmod(journal_path, 0o600)
    source_path = paper_root / "combo2-local" / "events.jsonl"
    with source_path.open("ab") as stream:
        stream.write(b"truncated")
    with pytest.raises(ValueError, match="TRUNCATED_EVENT_TAIL"):
        verified_projection(paper_root, "combo2-local", journal_path)
    with sqlite3.connect(journal_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 0
        assert "combo2_paper_decisions" not in {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master")
        }


def test_verified_replay_and_projection_never_open_socket(tmp_path, monkeypatch):
    paper_root, _ = make_paper(tmp_path)
    journal_path = tmp_path / "intent-journal.sqlite"
    journal = IntentJournal(journal_path)
    journal.close()
    os.chmod(journal_path, 0o600)
    attempts = []

    def prohibited(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("OUTBOUND_REQUEST_PROHIBITED")

    monkeypatch.setattr("socket.socket", prohibited)
    monkeypatch.setattr("urllib.request.urlopen", prohibited)
    assert (
        verified_projection(paper_root, "combo2-local", journal_path)[
            "new_paper_action_rows"
        ]
        == 1
    )
    assert not attempts
