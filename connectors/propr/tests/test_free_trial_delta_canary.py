"""Full signed-source-delta -> T+300 -> isolated Free Trial canary tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json

import pytest

pm_sources = pytest.importorskip("copytrade_pm.pm_sources")
# This historical canary needs the explicit Combo2 research extension. A base
# research package is insufficient and must not break native-provider discovery.
capture = pytest.importorskip("copytrade_pm.pm_combo2_rest_capture")
compare_generations = pytest.importorskip(
    "copytrade_pm.pm_combo2_signed_delta"
).compare_generations

from connectors.propr.examples import free_trial_delta_canary as canary  # noqa: E402
from connectors.propr.state import IntentJournal  # noqa: E402

IDENTITIES = {
    "W028": "0x" + "1" * 40,
    "CA-008": "0x" + "2" * 40,
    "W015": "0x" + "3" * 40,
}
CHILDREN = ["0x" + format(x, "x") * 40 for x in range(4, 11)]


def fake_source_response(query, parent_qty):
    kind = query["type"]
    addr = query.get("user", "").lower()
    if kind == "perpDexs":
        val = [None]
    elif kind == "subAccounts":
        val = (
            [{"master": addr, "subAccountUser": c} for c in CHILDREN]
            if addr == IDENTITIES["W028"]
            else None
        )
    elif kind == "userAbstraction":
        val = "unifiedAccount"
    elif kind == "spotClearinghouseState":
        val = {"balances": []}
    elif kind == "clearinghouseState":
        val = {
            "marginSummary": {"accountValue": "0"},
            "assetPositions": [{"position": {"coin": "HYPE", "szi": str(parent_qty)}}]
            if addr == IDENTITIES["W028"]
            else [],
        }
    else:
        raise AssertionError("network order path must be absent")
    return val, json.dumps(val, sort_keys=True).encode()


def signal_pair(tmp_path, monkeypatch, before="2", after="3"):
    monkeypatch.setattr(capture, "_resolved_identity", lambda _: IDENTITIES)
    monkeypatch.setattr(
        pm_sources, "_resolved_identity", lambda _: IDENTITIES, raising=False
    )
    old = tmp_path / "before"
    new = tmp_path / "after"
    capture.collect(
        tmp_path, old, fetch=lambda q: fake_source_response(q, before), sleep_between=0
    )
    capture.collect(
        tmp_path, new, fetch=lambda q: fake_source_response(q, after), sleep_between=0
    )
    pair = compare_generations(old, new)
    assert len(pair["signals"]) == 1
    return old, new, pair["signals"][0]


def eligible(signal, *, delta=305, price="86"):
    first = datetime.fromisoformat(signal["first_known_at"])
    return canary.eligible_signal(
        signal, first + timedelta(seconds=delta), Decimal(price)
    )


class FakeTrial:
    def __init__(self, *, outcome="filled"):
        self.outcome = outcome
        self.orders = []
        self.positions = []
        self.account = None

    def get_challenge_attempts(self, status):
        assert status == "active"
        return [{"accountId": canary.ACCOUNT_ID, "attemptId": "t"}]

    def get_challenge_attempt(self, attempt_id):
        return {
            "status": "active",
            "accountId": canary.ACCOUNT_ID,
            "challenge": {
                "name": "Free Trial",
                "slug": "free-trial",
                "initialBalance": "5000",
            },
        }

    def setup(self, account_id):
        self.account = account_id

    def get_account(self):
        return {
            "accountId": canary.ACCOUNT_ID,
            "type": "paper",
            "marginBalance": "4999",
            "balance": "4999",
        }

    def get_orders(self, status):
        return []

    def get_margin_config(self, asset):
        return {"accountId": canary.ACCOUNT_ID, "asset": asset}

    def get_open_positions(self, base=None):
        return list(self.positions)

    def create_orders(self, orders):
        assert len(orders) == 1
        item = dict(orders[0])
        assert item["accountId"] == canary.ACCOUNT_ID
        self.orders.append(item)
        if item["reduceOnly"]:
            assert item["side"] != self.orders[0]["side"]
            assert item["positionSide"] == self.orders[0]["positionSide"]
            assert self.positions
            assert Decimal(item["quantity"]) == Decimal(self.positions[0]["quantity"])
            self.positions = []
            outcome = "filled"
            filled = item["quantity"]
        else:
            outcome = self.outcome
            filled = (
                item["quantity"]
                if outcome == "filled"
                else "0.1"
                if outcome == "partial"
                else "0"
            )
            if Decimal(filled):
                self.positions = [
                    {
                        "asset": "HYPE",
                        "positionSide": item["positionSide"],
                        "quantity": filled,
                    }
                ]
        return [
            {
                "accountId": canary.ACCOUNT_ID,
                "intentId": item["intentId"],
                "asset": "HYPE",
                "orderId": f"fake-{len(self.orders)}",
                "status": outcome,
                "cumulativeQuantity": filled,
            }
        ]


def journal(tmp_path):
    return IntentJournal(tmp_path / "canary-journal.db")


def test_genuine_signed_change_waits_300s_and_does_not_use_unknown_nav(
    tmp_path, monkeypatch
):
    earlier, later, signal = signal_pair(tmp_path, monkeypatch)
    assert signal["change_kind"] == "INCREASE_LONG"
    assert signal["signed_delta"] == "1"
    assert signal["source_equity_verified"] is False
    with pytest.raises(ValueError, match="NOT_DUE"):
        eligible(signal, delta=299)
    target = eligible(signal, delta=300)
    assert target["direction"] == "long"
    assert target["quantity"] == "0.20"
    assert target["reference_notional"] == "17.20"
    assert target["source_equity_verified"] is False
    with pytest.raises(ValueError, match="EXPIRED"):
        eligible(signal, delta=1201)
    assert (
        compare_generations(earlier, later)["signals"][0]["signal_id"]
        == signal["signal_id"]
    )


def test_false_signal_and_wrong_market_cannot_be_sent(tmp_path, monkeypatch):
    _, _, signal = signal_pair(tmp_path, monkeypatch)
    altered = dict(signal, source_equity_verified=True)
    with pytest.raises(ValueError, match="NOT_AN_ELIGIBLE"):
        eligible(altered)
    altered = dict(signal, instrument="hyperliquid/mainnet:perp:default/SOL")
    with pytest.raises(ValueError, match="NOT_AN_ELIGIBLE"):
        eligible(altered)
    altered = dict(signal, signed_delta="-1")
    with pytest.raises(ValueError, match="INCONSISTENT_SIGNED"):
        eligible(altered)
    with pytest.raises(ValueError, match="DIAGNOSTIC_NOTIONAL"):
        eligible(signal, price="25")


def test_latest_live_book_requires_exact_child_hashes(tmp_path, monkeypatch):
    _, later, signal = signal_pair(tmp_path, monkeypatch)
    monkeypatch.setattr(
        pm_sources, "_resolved_identity", lambda _: IDENTITIES, raising=False
    )
    # confirm_source imports _resolved_identity at call time, patched above.
    good = canary.confirm_source(
        signal,
        tmp_path,
        later=later,
        reader=lambda q: fake_source_response(q, "3")[0],
    )
    assert good["confirmed_quantity"] == "3"
    assert good["verified_account_count"] == 8
    with pytest.raises(ValueError, match="SOURCE_SUBACCOUNT_SCOPE_DRIFT"):
        canary.confirm_source(
            signal,
            tmp_path,
            later=later,
            reader=lambda q: (
                None if q["type"] == "subAccounts" else fake_source_response(q, "3")[0]
            ),
        )
    with pytest.raises(ValueError, match="FRESH_LEADER_BOOK_REVERSED"):
        canary.confirm_source(
            signal,
            tmp_path,
            later=later,
            reader=lambda q: fake_source_response(q, "1")[0],
        )


@pytest.mark.parametrize("direction", ["long", "short"])
def test_real_sdk_transport_shape_with_fake_client_flat_and_no_socket(
    tmp_path, monkeypatch, direction
):
    import socket
    import urllib.request

    def blocked(*args, **kwargs):
        raise AssertionError("unexpected remote network operation")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    client = FakeTrial()
    store = journal(tmp_path)
    target = {
        "signal_id": "a" * 64,
        "direction": direction,
        "quantity": "0.20",
        "reference_notional": "17.20",
    }
    out = canary.execute_one(client, store, target)
    assert out["post_close_flat_verified"] is True
    assert out["opening"]["status"] == "filled"
    assert out["closing"]["status"] == "filled"
    assert len(client.orders) == 2
    assert client.orders[0]["reduceOnly"] is False
    assert client.orders[1]["reduceOnly"] is True
    assert client.orders[0]["positionSide"] == direction
    assert client.orders[0]["side"] == ("buy" if direction == "long" else "sell")
    assert client.orders[1]["side"] != client.orders[0]["side"]
    assert not client.positions
    assert store.connection.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 2
    with pytest.raises(PermissionError, match="ALREADY_HAS_INTENTS"):
        canary.execute_one(client, store, target)
    store.close()


def test_partial_fill_is_closed_only_at_actual_partial_quantity(tmp_path):
    client = FakeTrial(outcome="partial")
    store = journal(tmp_path)
    out = canary.execute_one(
        client,
        store,
        {
            "signal_id": "e" * 64,
            "direction": "long",
            "quantity": "0.20",
            "reference_notional": "17.20",
        },
    )
    assert out["opening"]["status"] == "partial"
    assert out["closing"]["filled_quantity"] == "0.1"
    assert client.orders[-1]["quantity"] == "0.1"
    assert out["post_close_flat_verified"]
    store.close()


def test_unknown_ack_is_latched_without_automatic_retry(tmp_path):
    class UnknownClient(FakeTrial):
        def create_orders(self, orders):
            self.orders.extend(orders)
            raise ConnectionError("timed out after sending")

    store = journal(tmp_path)
    client = UnknownClient()
    with pytest.raises(RuntimeError, match="CANARY_POST_UNCERTAIN_NO_RETRY"):
        canary.execute_one(
            client,
            store,
            {
                "signal_id": "b" * 64,
                "direction": "long",
                "quantity": "0.20",
                "reference_notional": "17.20",
            },
        )
    assert len(client.orders) == 1
    assert store.has_unknown()
    store.close()
    reopened = journal(tmp_path)
    assert reopened.has_unknown()
    reopened.close()


def test_global_claim_store_prevents_replay_and_daily_multiple(tmp_path):
    file = tmp_path / "claims.sqlite"
    now = datetime.now(UTC)
    first = canary.ClaimStore(file)
    first.claim(canary.ACCOUNT_ID, "a" * 64, now)
    first.mark(canary.ACCOUNT_ID, "a" * 64, "COMPLETED_FLAT")
    first.close()
    second = canary.ClaimStore(file)
    with pytest.raises(PermissionError, match="DUPLICATE_OR_DAILY_LIMIT"):
        second.claim(canary.ACCOUNT_ID, "a" * 64, now)
    with pytest.raises(PermissionError, match="DUPLICATE_OR_DAILY_LIMIT"):
        second.claim(canary.ACCOUNT_ID, "b" * 64, now)
    second.close()


def test_no_trade_cli_requires_no_secret_or_order_transport(tmp_path, monkeypatch):
    from types import SimpleNamespace

    earlier, later, signal = signal_pair(tmp_path, monkeypatch)
    args = SimpleNamespace(
        earlier=earlier,
        later=later,
        signal_id=signal["signal_id"],
        authorize_free_trial_delta_canary=False,
    )
    result = canary.run(args)
    assert result["execution"] == "NO_TRADE_READ_ONLY"
    assert result["selected_signal"]["signal_id"] == signal["signal_id"]


def test_authorized_one_shot_replay_to_fake_propr_is_durable_and_never_repeated(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    from copytrade_pm import pm_e4_prospective

    earlier, later, signal = signal_pair(tmp_path, monkeypatch)
    test_client = FakeTrial()
    due = datetime.fromisoformat(signal["due_at"]) + timedelta(seconds=2)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return due

    monkeypatch.setattr(canary, "datetime", FrozenDatetime)
    monkeypatch.setattr(canary, "TRUSTED_CAPTURE_ROOT", tmp_path)
    monkeypatch.setattr(canary, "CANARY_STATE_ROOT", tmp_path / "private-claims")
    monkeypatch.setattr(
        capture,
        "read_info",
        lambda query: (fake_source_response(query, "3")[0], b"offline"),
    )
    monkeypatch.setattr(
        pm_e4_prospective,
        "_guarded_info",
        lambda query: ({"HYPE": "86"}, b"offline", "0" * 64),
    )
    monkeypatch.setattr("propr_sdk.ProprClient", lambda **kwargs: test_client)
    monkeypatch.setattr(
        "dotenv.dotenv_values",
        lambda path: {"PROPR_LIVE_API_KEY": "pk_live_test_only"},
    )
    args = SimpleNamespace(
        earlier=earlier,
        later=later,
        signal_id=signal["signal_id"],
        authorize_free_trial_delta_canary=True,
        research_root=tmp_path,
        account_id=canary.ACCOUNT_ID,
        audit_dir=tmp_path / "diagnostic-1",
        env_file=tmp_path / "mock-secret-not-created",
    )
    result = canary.run(args)
    assert result["execution"]["post_close_flat_verified"]
    assert result["decision"]["source_equity_verified"] is False
    assert result["source_confirmation"]["verified_account_count"] == 8
    assert len(test_client.orders) == 2
    assert (args.audit_dir / "execution-report.json").is_file()
    assert (tmp_path / "private-claims" / "claims.sqlite").is_file()
    args.audit_dir = tmp_path / "diagnostic-2"
    with pytest.raises(PermissionError, match="DUPLICATE_OR_DAILY_LIMIT"):
        canary.run(args)
    assert len(test_client.orders) == 2
