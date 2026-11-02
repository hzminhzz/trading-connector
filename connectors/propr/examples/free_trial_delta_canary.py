"""Opt-in, one-shot new-wallet-delta canary for Propr's verified Free Trial.

This is a deliberately isolated *diagnostic* order path, not a Combo2
production allocation, not source-equity sizing, and not a recurring service.
The research package owns only public/read-only position deltas. This module
alone may translate a selected verified delta into a capped trial order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path
import sqlite3
import stat
from time import sleep

from connectors.propr.examples.free_trial_wallet_canary import (
    _d,
    check_trial,
)
from connectors.propr.state import IntentJournal

ACCOUNT_ID = "urn:prp-account:exampleJhW9"  # nonreal test sentinel only
HYPE = "hyperliquid/mainnet:perp:default/HYPE"
VALID_KINDS = frozenset({"OPEN_LONG", "OPEN_SHORT", "INCREASE_LONG", "INCREASE_SHORT"})
MAX_NOTIONAL = Decimal("25")
TARGET_NOTIONAL = Decimal("20")
MIN_NOTIONAL = Decimal("10")
LOT = Decimal("0.01")
MAX_QUANTITY = Decimal("0.20")
BATCH_SECONDS = 300
DEADLINE_SECONDS = 900
CANARY_STATE_ROOT = Path.home() / ".local/share/copytrade/free-trial-delta-canary"
TRUSTED_CAPTURE_ROOT = (
    Path.home() / ".local/share/copytrade/reports/combo2-source-20261102"
)


def _when(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("SIGNAL_TIMESTAMP_MUST_BE_UTC")
    return dt.astimezone(UTC)


def eligible_signal(signal: dict, now: datetime, reference_price: Decimal) -> dict:
    """Require new signed change, T+300, fresh deadline and bounded test size."""
    if (
        not isinstance(signal, dict)
        or signal.get("schema") != "combo2_signed_rest_position_delta_v1"
        or signal.get("instrument") != HYPE
        or signal.get("manager") not in ("W028", "CA-008", "W015")
        or signal.get("change_kind") not in VALID_KINDS
        or signal.get("source_equity_verified") is not False
        or signal.get("execution_authorized") is not False
        or signal.get("source_event_at") is not None
        or signal.get("provenance")
        != "REST_TWO_COMPLETE_NONATOMIC_BOOKS_NOT_SOURCE_FILL"
        or not isinstance(signal.get("signal_id"), str)
        or len(signal["signal_id"]) != 64
        or any(x not in "0123456789abcdef" for x in signal["signal_id"])
    ):
        raise ValueError("NOT_AN_ELIGIBLE_VERIFIED_SOURCE_DELTA")
    first = _when(signal["first_known_at"])
    due = _when(signal["due_at"])
    if due != first + timedelta(seconds=BATCH_SECONDS):
        raise ValueError("SIGNAL_BATCH_DEADLINE_MUTATED")
    if now.tzinfo is None or not due <= now.astimezone(UTC) <= due + timedelta(
        seconds=DEADLINE_SECONDS
    ):
        raise ValueError("SOURCE_SIGNAL_NOT_DUE_OR_EXPIRED")
    previous = _d(signal["signed_quantity_before"])
    after = _d(signal["signed_quantity_after"])
    delta = _d(signal["signed_delta"])
    if delta != after - previous or delta == 0:
        raise ValueError("INCONSISTENT_SIGNED_SOURCE_DELTA")
    direction = "long" if signal["change_kind"].endswith("LONG") else "short"
    if (
        (direction == "long" and (delta <= 0 or after <= 0))
        or (direction == "short" and (delta >= 0 or after >= 0))
        or (
            signal["change_kind"].startswith("INCREASE")
            and (previous == 0 or previous * after <= 0)
        )
        or (signal["change_kind"].startswith("OPEN") and previous != 0)
    ):
        raise ValueError("SOURCE_DELTA_DIRECTION_INVALID")
    price = _d(reference_price)
    if price <= 0:
        raise ValueError("INVALID_REFERENCE_PRICE")
    quantity = min(
        MAX_QUANTITY,
        (TARGET_NOTIONAL / price / LOT).to_integral_value(rounding=ROUND_DOWN) * LOT,
    )
    notional = quantity * price
    if not MIN_NOTIONAL <= notional <= MAX_NOTIONAL:
        raise ValueError("DIAGNOSTIC_NOTIONAL_OUTSIDE_BOUNDS")
    return {
        "signal_id": signal["signal_id"],
        "manager": signal["manager"],
        "instrument": HYPE,
        "direction": direction,
        "quantity": str(quantity),
        "price": str(price),
        "reference_notional": str(notional),
        "first_known_at": first.isoformat(),
        "due_at": due.isoformat(),
        "source_after": str(after),
        "source_equity_verified": False,
        "production_authorized": False,
    }


class ClaimStore:
    """Owner-private durable one-attempt-per-day gate shared across processes."""

    def __init__(self, path: Path):
        path = Path(path)
        if (
            not path.parent.exists()
            or path.parent.is_symlink()
            or path.parent.stat().st_mode & 0o077
            or (path.exists() and (path.is_symlink() or path.stat().st_mode & 0o077))
        ):
            raise ValueError("PRIVATE_CANARY_CLAIM_STORE_REQUIRED")
        self.connection = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS attempts ("
            "account_id TEXT NOT NULL, signal_id TEXT NOT NULL, utc_day TEXT NOT NULL,"
            "created_at TEXT NOT NULL, status TEXT NOT NULL,"
            "PRIMARY KEY(account_id,signal_id))"
        )
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS one_free_trial_canary_per_day "
            "ON attempts(account_id,utc_day)"
        )
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

    def claim(self, account_id: str, signal_id: str, now: datetime) -> None:
        if not account_id.startswith("urn:prp-account:") or not account_id.endswith(
            "JhW9"
        ):
            raise PermissionError("UNSUPPORTED_FREE_TRIAL_CANARY_ACCOUNT")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                "INSERT INTO attempts VALUES (?,?,?,?,?)",
                (
                    account_id,
                    signal_id,
                    now.astimezone(UTC).date().isoformat(),
                    now.astimezone(UTC).isoformat(),
                    "UNKNOWN_UNTIL_RECONCILED",
                ),
            )
        except sqlite3.IntegrityError as exc:
            self.connection.execute("ROLLBACK")
            raise PermissionError("CANARY_SIGNAL_DUPLICATE_OR_DAILY_LIMIT") from exc
        self.connection.execute("COMMIT")

    def mark(self, account_id: str, signal_id: str, state: str) -> None:
        if state not in {"COMPLETED_FLAT", "ABORTED", "REJECTED"}:
            raise ValueError("INVALID_CANARY_FINAL_STATE")
        with self.connection:
            cur = self.connection.execute(
                "UPDATE attempts SET status=? WHERE account_id=? AND signal_id=?",
                (state, account_id, signal_id),
            )
            if cur.rowcount != 1:
                raise ValueError("CANARY_CLAIM_NOT_FOUND")

    def close(self):
        self.connection.close()


def confirm_source(
    signal: dict, research_root: Path, *, later: Path, reader=None
) -> dict:
    """Fresh read-only aggregate all matching manager subaccounts, no equity claim."""
    from copytrade_pm.pm_sources import _resolved_identity
    from copytrade_pm.pm_e4_prospective import _subaccount_roster
    from copytrade_pm.pm_combo2_rest_capture import read_info
    from copytrade_pm.pm_combo2_signed_delta import _generation

    api = reader or (lambda query: read_info(query)[0])
    identities = _resolved_identity(research_root)
    master = identities[signal["manager"]]
    current_children = _subaccount_roster(
        api({"type": "subAccounts", "user": master}), master
    )
    frozen = _generation(later)
    aliases = [signal["manager"]] + [
        f"{signal['manager']}-sub{i:02d}" for i in range(len(current_children))
    ]
    required = {
        key
        for key in frozen["scopes"]
        if key == signal["manager"] or key.startswith(signal["manager"] + "-sub")
    }
    if set(aliases) != required:
        raise ValueError("SOURCE_SUBACCOUNT_SCOPE_DRIFT")
    quantity = Decimal(0)
    for alias, address in zip(aliases, [master, *current_children]):
        expected_identity, expected_mode = frozen["scopes"][alias]
        if sha256(address.lower().encode()).hexdigest() != expected_identity:
            raise ValueError("SOURCE_SUBACCOUNT_IDENTITY_DRIFT")
        mode = api({"type": "userAbstraction", "user": address})
        if mode != expected_mode or (
            alias == signal["manager"] and mode != "unifiedAccount"
        ):
            raise ValueError("SOURCE_ACCOUNT_MODE_DRIFT")
        data = api({"type": "clearinghouseState", "user": address})
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("marginSummary"), dict)
            or not isinstance(data.get("assetPositions"), list)
        ):
            raise ValueError("SOURCE_CONFIRMATION_INCOMPLETE")
        matches = [
            x.get("position", {})
            for x in data["assetPositions"]
            if isinstance(x, dict) and x.get("position", {}).get("coin") == "HYPE"
        ]
        if len(matches) > 1:
            raise ValueError("SOURCE_DUPLICATE_HYPE_POSITION")
        if matches:
            quantity += _d(matches[0].get("szi"))
    after = _d(signal["signed_quantity_after"])
    if (
        (after > 0 and quantity < after)
        or (after < 0 and quantity > after)
        or quantity == 0
    ):
        raise ValueError("FRESH_LEADER_BOOK_REVERSED_OR_REDUCED")
    return {
        "confirmed_quantity": str(quantity),
        "verified_account_count": len(aliases),
        "source_first_known_fill": False,
    }


def _order_payload(
    intent, *, account_id: str, side: str, position_side: str, opening: bool
) -> dict:
    return {
        "accountId": account_id,
        "intentId": intent.id,
        "exchange": "hyperliquid",
        "type": "market",
        "side": side,
        "positionSide": position_side,
        "productType": "perp",
        "timeInForce": "IOC",
        "asset": "HYPE",
        "base": "HYPE",
        "quote": "USDC",
        "quantity": intent.quantity,
        "reduceOnly": not opening,
        "closePosition": False,
    }


def _submit_once(
    client,
    journal: IntentJournal,
    *,
    account_id: str,
    opening: bool,
    direction: str,
    quantity: Decimal,
) -> dict:
    buy = (direction == "long") == opening
    side = "buy" if buy else "sell"
    intent = journal.stage(
        account_id,
        "HYPE",
        side,
        direction,
        str(quantity),
        reduce_only=not opening,
        position_id="diagnostic-signal-position" if not opening else None,
    )
    payload = _order_payload(
        intent,
        account_id=account_id,
        side=side,
        position_side=direction,
        opening=opening,
    )
    try:
        result = client.create_orders([payload])
    except Exception as exc:
        journal.mark(intent.id, "unknown")
        raise RuntimeError("CANARY_POST_UNCERTAIN_NO_RETRY") from exc
    if (
        not isinstance(result, list)
        or len(result) != 1
        or result[0].get("accountId") != account_id
        or result[0].get("intentId") != intent.id
        or result[0].get("asset") != "HYPE"
        or not isinstance(result[0].get("orderId"), str)
    ):
        journal.mark(intent.id, "unknown")
        raise RuntimeError("CANARY_ACK_INVALID_NO_RETRY")
    row = result[0]
    filled = _d(row.get("cumulativeQuantity", "0"))
    status = row.get("status")
    if status == "filled" and filled == quantity:
        journal.mark(intent.id, "filled")
    elif status in {"rejected", "expired"} and filled == 0:
        journal.mark(intent.id, status)
    elif status in {"partial", "partially_filled"} and 0 < filled < quantity:
        journal.mark(intent.id, "partial")
    else:
        journal.mark(intent.id, "unknown")
        raise RuntimeError("CANARY_ORDER_UNSETTLED_NO_RETRY")
    return {
        "intent_id": intent.id,
        "order_id": row["orderId"],
        "status": status,
        "filled_quantity": str(filled),
    }


def _position_book(client) -> list[dict]:
    return [
        x
        for x in client.get_open_positions(base="HYPE")
        if x.get("asset") == "HYPE" and _d(x.get("quantity", "0")) > 0
    ]


def execute_one(
    client, journal: IntentJournal, target: dict, *, account_id: str = ACCOUNT_ID
) -> dict:
    """Execute a single capped Free Trial canary and verify reduce-only flatness."""
    if journal.connection.execute("SELECT COUNT(*) FROM intents").fetchone()[0]:
        raise PermissionError("DIAGNOSTIC_JOURNAL_ALREADY_HAS_INTENTS")
    check_trial(client, account_id)
    qty = _d(target["quantity"])
    if (
        qty <= 0
        or qty > MAX_QUANTITY
        or _d(target["reference_notional"]) > MAX_NOTIONAL
    ):
        raise ValueError("DIAGNOSTIC_QUANTITY_INVALID")
    opened = _submit_once(
        client,
        journal,
        account_id=account_id,
        opening=True,
        direction=target["direction"],
        quantity=qty,
    )
    output = {
        "schema": "combo2_free_trial_delta_canary_v1",
        "signal_id": target["signal_id"],
        "opening": opened,
        "source_equity_verified": False,
        "live_funded_account": False,
        "production_copytrade_qualified": False,
    }
    if opened["status"] in {"rejected", "expired"}:
        output["closing"] = None
        return output
    actual_open = _d(opened["filled_quantity"])
    if not 0 < actual_open <= qty:
        raise RuntimeError("DIAGNOSTIC_OPEN_FILL_UNRESOLVED")
    positions = []
    for i in range(5):
        positions = _position_book(client)
        if (
            len(positions) == 1
            and positions[0].get("positionSide") == target["direction"]
            and _d(positions[0]["quantity"]) == actual_open
        ):
            break
        if i < 4:
            sleep(0.4)
    if (
        len(positions) != 1
        or positions[0].get("positionSide") != target["direction"]
        or _d(positions[0]["quantity"]) != actual_open
    ):
        raise RuntimeError("DIAGNOSTIC_POSITION_AFTER_OPEN_UNKNOWN")
    closed = _submit_once(
        client,
        journal,
        account_id=account_id,
        opening=False,
        direction=target["direction"],
        quantity=actual_open,
    )
    output["closing"] = closed
    if closed["status"] != "filled":
        raise RuntimeError("DIAGNOSTIC_EXIT_NOT_FULLY_FILLED")
    for i in range(5):
        positions = _position_book(client)
        if not positions:
            break
        if i < 4:
            sleep(0.4)
    if positions:
        raise RuntimeError("DIAGNOSTIC_NOT_FLAT_AFTER_REDUCE_ONLY")
    output["post_close_flat_verified"] = True
    return output


def run(args) -> dict:
    from copytrade_pm.pm_combo2_signed_delta import compare_generations

    batch = compare_generations(args.earlier, args.later)
    available = [s for s in batch["signals"] if s["signal_id"] == args.signal_id]
    if len(available) != 1:
        raise ValueError("SIGNAL_ID_NOT_PRESENT_IN_VERIFIED_CAPTURE_PAIR")
    if not args.authorize_free_trial_delta_canary:
        return {
            "execution": "NO_TRADE_READ_ONLY",
            "selected_signal": available[0],
            "source_equity_verified": False,
        }
    if (
        args.earlier.parent.resolve() != TRUSTED_CAPTURE_ROOT.resolve()
        or args.later.parent.resolve() != TRUSTED_CAPTURE_ROOT.resolve()
    ):
        raise PermissionError("TRIAL_CANARY_REQUIRES_OWNER_CAPTURE_STORE")
    now = datetime.now(UTC)
    # The canonical market quote is a separate read-only Hyperliquid /info
    # query, not a price embedded in a vendor's historical source timestamp.
    from copytrade_pm.pm_e4_prospective import _guarded_info

    prices, _, _ = _guarded_info({"type": "allMids"})
    target = eligible_signal(available[0], now, _d(prices["HYPE"]))
    confirmation = confirm_source(available[0], args.research_root, later=args.later)
    if not args.account_id.startswith(
        "urn:prp-account:"
    ) or not args.account_id.endswith("JhW9"):
        raise PermissionError("NOT_THE_EXPLICIT_FREE_TRIAL_ACCOUNT")
    if args.audit_dir.exists() or args.audit_dir.is_symlink():
        raise PermissionError("NONUNIQUE_PRIVATE_DIAGNOSTIC_AUDIT_DIR")
    if (
        args.audit_dir.parent.is_symlink()
        or args.audit_dir.parent.stat().st_mode & 0o077
    ):
        raise PermissionError("AUDIT_PARENT_MUST_BE_PRIVATE")
    from dotenv import dotenv_values
    from propr_sdk import ProprClient

    creds = dotenv_values(args.env_file)
    token = creds.get("PROPR_LIVE_API_KEY") or creds.get("PROPR_API_KEY")
    if not token or not token.startswith("pk_live_"):
        raise ValueError("PROPR_TRIAL_CREDENTIAL_UNAVAILABLE")
    client = ProprClient(api_key=token, base_url="https://api.propr.xyz/v1")
    # The account is verified read-only before consuming the global single
    # attempt for the day. Only the verified test account is supported.
    check_trial(client, args.account_id)
    CANARY_STATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    claims = ClaimStore(CANARY_STATE_ROOT / "claims.sqlite")
    try:
        claims.claim(args.account_id, target["signal_id"], datetime.now(UTC))
        old_umask = os.umask(0o077)
        try:
            args.audit_dir.mkdir(mode=0o700)
        finally:
            os.umask(old_umask)
        journal = IntentJournal(args.audit_dir / "intents.sqlite")
        os.chmod(args.audit_dir / "intents.sqlite", 0o600)
        report = {"source_confirmation": confirmation, "decision": target}
        try:
            try:
                report["execution"] = execute_one(
                    client, journal, target, account_id=args.account_id
                )
            except Exception as exc:
                report["exception_type"] = type(exc).__name__
                report["status"] = "HALT_INSPECT_BROKER_BEFORE_RETRY"
                raise
        finally:
            report["intent_states"] = journal.connection.execute(
                "SELECT state,COUNT(*) FROM intents GROUP BY state"
            ).fetchall()
            journal.close()
            path = args.audit_dir / "execution-report.json"
            with path.open("x") as handle:
                json.dump(report, handle, indent=2, sort_keys=True)
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        claims.mark(
            args.account_id,
            target["signal_id"],
            "COMPLETED_FLAT"
            if report["execution"].get("post_close_flat_verified")
            else "REJECTED",
        )
        return report
    finally:
        claims.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--earlier", required=True, type=Path)
    parser.add_argument("--later", required=True, type=Path)
    parser.add_argument("--signal-id", required=True)
    parser.add_argument("--research-root", required=True, type=Path)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--audit-dir", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--authorize-free-trial-delta-canary", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args), sort_keys=True))


if __name__ == "__main__":
    main()
