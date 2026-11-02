"""Direct Free Trial canary safety, source provenance and simulated broker tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import os

import pytest

from connectors.propr.examples.free_trial_wallet_canary import (
    QTY,
    captured_wallet_message,
    check_trial,
    execute_once,
)
from connectors.propr.state import IntentJournal

ADDR = "0x" + "1" * 40
ACCOUNT = "urn:prp-account:exampleJhW9"
NOW = datetime(2026, 11, 2, 15, tzinfo=UTC)


class TrialFake:
    def __init__(
        self, *, challenge="Free Trial", account_type="paper", open_outcome="filled"
    ):
        self.challenge = challenge
        self.account_type = account_type
        self.open_outcome = open_outcome
        self.order_calls = []
        self.positions = []
        self.account_id = None

    def get_challenge_attempts(self, status):
        assert status == "active"
        return [{"accountId": ACCOUNT, "attemptId": "attempt"}]

    def get_challenge_attempt(self, attempt_id):
        assert attempt_id == "attempt"
        return {
            "data": {
                "status": "active",
                "accountId": ACCOUNT,
                "challenge": {
                    "name": self.challenge,
                    "slug": "free-trial",
                    "initialBalance": "5000",
                },
            }
        }

    def setup(self, account_id):
        self.account_id = account_id

    def get_account(self):
        return {
            "accountId": ACCOUNT,
            "type": self.account_type,
            "marginBalance": "4999.2",
            "balance": "4999.2",
        }

    def get_margin_config(self, asset):
        return {
            "asset": asset,
            "accountId": ACCOUNT,
            "marginMode": "cross",
            "leverage": "1",
        }

    def get_open_positions(self, base=None):
        return list(self.positions)

    def get_orders(self, status=None):
        return []

    def create_orders(self, orders):
        assert len(orders) == 1
        order = orders[0]
        assert order["accountId"] == ACCOUNT
        self.order_calls.append(dict(order))
        if order["reduceOnly"]:
            assert order["side"] == "sell"
            if self.positions:
                self.positions = []
            outcome = "filled"
        else:
            assert order["side"] == "buy"
            outcome = self.open_outcome
            if outcome == "filled":
                self.positions = [
                    {
                        "asset": "HYPE",
                        "base": "HYPE",
                        "positionSide": "long",
                        "quantity": order["quantity"],
                        "status": "open",
                    }
                ]
        return [
            {
                "accountId": ACCOUNT,
                "asset": "HYPE",
                "intentId": order["intentId"],
                "orderId": "fake-" + str(len(self.order_calls)),
                "status": outcome,
                "cumulativeQuantity": order["quantity"] if outcome == "filled" else "0",
            }
        ]


def source_signal(tmp_path, age=1):
    root = tmp_path / "gen"
    (root / "raw").mkdir(parents=True)
    (root / "metadata").mkdir()
    raw = json.dumps(
        {
            "marginSummary": {"accountValue": "100"},
            "assetPositions": [{"position": {"coin": "HYPE", "szi": "464.18"}}],
        }
    ).encode()
    meta = {
        "kind": "clearinghouseState",
        "sha256": sha256(raw).hexdigest(),
        "scope_hash": sha256(ADDR.lower().encode()).hexdigest(),
        "locally_received_at": (NOW - timedelta(seconds=age)).isoformat(),
    }
    report = {
        "schema": "combo2_rest_account_capture_v1",
        "all_subjects_captured": True,
        "account_count": 10,
        "network_order_mutations": 0,
        "errors": [],
        "managers": {"W028": {"subaccount_count": 7}},
        "subjects": {"W028": {"sha256": sha256(ADDR.lower().encode()).hexdigest()}},
    }
    for name, blob in (
        ("raw/W028-dex-default.json", raw),
        ("metadata/W028-dex-default.json", json.dumps(meta).encode()),
        ("report.json", json.dumps(report).encode()),
    ):
        f = root / name
        f.write_bytes(blob)
        os.chmod(f, 0o600)
    return root


def local_journal(tmp_path):
    return IntentJournal(tmp_path / "intents.sqlite")


def test_initial_holding_receipt_is_real_evidence_not_new_trade(tmp_path):
    root = source_signal(tmp_path)
    msg = captured_wallet_message(root, ADDR, NOW)
    assert msg["source_signed_quantity"] == "464.18"
    assert msg["source_frame_sha256"]
    assert msg["is_new_trade_signal"] is False
    assert msg["source_equity_verified"] is False
    with pytest.raises(ValueError, match="SOURCE_SIGNAL_STALE"):
        captured_wallet_message(root, ADDR, NOW + timedelta(seconds=1000))


def test_propr_free_trial_paper_account_required():
    fake = TrialFake()
    assert check_trial(fake, ACCOUNT)["account_type"] == "paper"
    for altered in (
        TrialFake(challenge="Paid Challenge"),
        TrialFake(account_type="live"),
    ):
        with pytest.raises(PermissionError):
            check_trial(altered, ACCOUNT)
        assert altered.order_calls == []


def test_real_sdk_shape_test_double_buy_then_reduce_only_close(tmp_path, monkeypatch):
    import socket
    import urllib.request

    monkeypatch.setattr(
        socket,
        "socket",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("LIVE_SOCKET_CALLED_IN_TEST")
        ),
    )
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("LIVE_HTTP_CALLED_IN_TEST")
        ),
    )
    signal = captured_wallet_message(source_signal(tmp_path), ADDR, NOW)
    journal = local_journal(tmp_path)
    fake = TrialFake()
    result = execute_once(fake, ACCOUNT, journal, signal, Decimal("86.0735"))
    assert result["opening"]["status"] == "filled"
    assert result["closing"]["status"] == "filled"
    assert result["post_close_flat_verified"]
    assert [p["reduceOnly"] for p in fake.order_calls] == [False, True]
    assert [p["quantity"] for p in fake.order_calls] == [str(QTY), str(QTY)]
    assert fake.get_open_positions() == []
    assert journal.has_unknown() is False
    assert journal.connection.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 2
    with pytest.raises(PermissionError, match="CANARY_ALREADY_ATTEMPTED"):
        execute_once(fake, ACCOUNT, journal, signal, Decimal("86.0735"))
    assert len(fake.order_calls) == 2
    journal.close()


def test_rejected_market_order_never_sends_second_order(tmp_path):
    msg = captured_wallet_message(source_signal(tmp_path), ADDR, NOW)
    fake = TrialFake(open_outcome="rejected")
    journal = local_journal(tmp_path)
    report = execute_once(fake, ACCOUNT, journal, msg, Decimal("86.0735"))
    assert report["opening"]["status"] == "rejected"
    assert report["close"] == "NOT_APPLICABLE_OPENING_NOT_FILLED"
    assert len(fake.order_calls) == 1
    assert (
        journal.connection.execute("SELECT state FROM intents").fetchone()[0]
        == "rejected"
    )
    journal.close()


def test_unknown_create_result_latches_durable_intent_without_retry(tmp_path):
    msg = captured_wallet_message(source_signal(tmp_path), ADDR, NOW)
    fake = TrialFake(open_outcome="pending")
    journal = local_journal(tmp_path)
    with pytest.raises(RuntimeError, match="CANARY_NONTERMINAL_ORDER_NO_RETRY"):
        execute_once(fake, ACCOUNT, journal, msg, Decimal("86.0735"))
    assert len(fake.order_calls) == 1
    assert journal.has_unknown() is True
    journal.close()
    recovered = local_journal(tmp_path)
    assert recovered.has_unknown()
    recovered.close()


def test_price_and_signal_guard_fail_before_any_mutation(tmp_path):
    msg = captured_wallet_message(source_signal(tmp_path), ADDR, NOW)
    fake = TrialFake()
    journal = local_journal(tmp_path)
    for bad_price in ("0", "1", "200", "NaN"):
        with pytest.raises(PermissionError):
            execute_once(fake, ACCOUNT, journal, msg, Decimal(bad_price))
    broken = dict(msg, is_new_trade_signal=True)
    with pytest.raises(ValueError, match="NOT_A_VERIFIED_INITIAL_HOLDING_MESSAGE"):
        execute_once(fake, ACCOUNT, journal, broken, Decimal("86"))
    assert fake.order_calls == []
    assert journal.connection.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 0
    journal.close()
