"""One-shot Free Trial account canary using a verified Hyperliquid wallet observation.

This is a diagnostic INITIAL-HOLDING signal, not a newly observed fill or
scientifically qualified copytrade. Never run unattended or on funded accounts.
Only the explicitly bound Propr Free Trial (account type: paper) can be used.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

from connectors.common.validation import parse_utc_iso
from connectors.propr.examples._trial_common import (
    check_trial,
    parse_decimal,
    poll_positions,
    read_private_bytes,
    require_propr_live_secret,
    submit_trial_order,
    write_private_json,
)
from connectors.propr.state import IntentJournal

SOURCE_WALLET = "W028"
ASSET = "HYPE"
QTY = Decimal("0.20")
MAX_NOTIONAL = Decimal("30")
MIN_NOTIONAL = Decimal("10")
MAX_SOURCE_AGE = 900


def captured_wallet_message(generation: Path, address: str, now: datetime) -> dict:
    """Bind the source's actual first-receipt and raw SHA, not a vendor fill time."""
    report = json.loads(read_private_bytes(generation / "report.json"))
    if (
        report.get("schema") != "combo2_rest_account_capture_v1"
        or report.get("all_subjects_captured") is not True
        or report.get("account_count") != 10
        or report.get("network_order_mutations") != 0
        or report.get("errors")
        or report.get("managers", {}).get("W028", {}).get("subaccount_count") != 7
    ):
        raise ValueError("INCOMPLETE_OR_UNTRUSTED_WALLET_RECEIPT")
    meta = json.loads(read_private_bytes(generation / "metadata" / "W028-dex-default.json"))
    raw = read_private_bytes(generation / "raw" / "W028-dex-default.json")
    digest = sha256(raw).hexdigest()
    if (
        meta.get("kind") != "clearinghouseState"
        or meta.get("sha256") != digest
        or meta.get("scope_hash") != sha256(address.lower().encode()).hexdigest()
        or report.get("subjects", {}).get("W028", {}).get("sha256")
        != sha256(address.lower().encode()).hexdigest()
    ):
        raise ValueError("WALLET_FRAME_PROVENANCE_MISMATCH")
    first = parse_utc_iso(meta["locally_received_at"], "locally_received_at")
    lag = (now - first).total_seconds()
    if not 0 <= lag <= MAX_SOURCE_AGE:
        raise ValueError("SOURCE_SIGNAL_STALE_OR_FUTURE")
    payload = json.loads(raw)
    positions = [
        p.get("position", {})
        for p in payload.get("assetPositions", [])
        if p.get("position", {}).get("coin") == ASSET
    ]
    if len(positions) != 1 or parse_decimal(positions[0].get("szi")) <= 0:
        raise ValueError("NO_VERIFIED_LONG_SOURCE_HOLDING")
    return {
        "schema": "combo2_initial_holding_canary_v1",
        "wallet_alias": SOURCE_WALLET,
        "asset": ASSET,
        "side": "buy",
        "source_first_known_at": first.isoformat(),
        "source_frame_sha256": digest,
        "source_signed_quantity": str(parse_decimal(positions[0]["szi"])),
        "is_new_trade_signal": False,
        "is_production_allocation": False,
        "source_equity_verified": False,
    }


def _submit(
    client, journal: IntentJournal, account_id: str, *, opening: bool, amount: Decimal
) -> dict:
    if amount <= 0 or amount > QTY:
        raise ValueError("CANARY_QUANTITY_OUT_OF_BOUNDS")
    intent_id, row, filled = submit_trial_order(
        client,
        journal,
        account_id=account_id,
        asset=ASSET,
        direction="long",
        opening=opening,
        quantity=amount,
        position_id="canary-existing-position",
        post_error_msg="CANARY_ORDER_OUTCOME_UNKNOWN_NO_RETRY",
        invalid_ack_msg="CANARY_ORDER_ACK_INVALID_NO_RETRY",
        unsettled_msg="CANARY_NONTERMINAL_ORDER_NO_RETRY",
    )
    return {
        "id": intent_id,
        "order_id": row["orderId"],
        "status": row.get("status"),
        "quantity": str(filled),
    }


def execute_once(
    client, account_id: str, journal: IntentJournal, signal: dict, market_mid: Decimal
) -> dict:
    """Only a deliberately small two-leg Free Trial canary, not wallet NAV sizing."""
    if (
        signal.get("schema") != "combo2_initial_holding_canary_v1"
        or signal.get("is_new_trade_signal") is not False
        or signal.get("wallet_alias") != SOURCE_WALLET
        or signal.get("asset") != ASSET
    ):
        raise ValueError("NOT_A_VERIFIED_INITIAL_HOLDING_MESSAGE")
    if journal.count():
        raise PermissionError("CANARY_ALREADY_ATTEMPTED_IN_THIS_JOURNAL")
    if (
        not market_mid.is_finite()
        or not MIN_NOTIONAL <= QTY * market_mid <= MAX_NOTIONAL
    ):
        raise PermissionError("REFERENCE_PRICE_OR_NOTIONAL_OUT_OF_BOUNDS")
    preflight = check_trial(client, account_id, asset=ASSET)
    opened = _submit(client, journal, account_id, opening=True, amount=QTY)
    state = {
        "schema": "combo2_propr_free_trial_canary_v1",
        "source_receipt_sha256": signal["source_frame_sha256"],
        "signal_kind": "INITIAL_HOLDING_CANARY_NOT_NEW_FILL",
        "reference_mid": str(market_mid),
        "nominal_notional": str(QTY * market_mid),
        "preflight": preflight,
        "opening": opened,
        "opening_was_live_api_order": True,
        "live_funded_account": False,
        "production_copytrade_qualified": False,
    }
    if opened["status"] != "filled":
        state["close"] = "NOT_APPLICABLE_OPENING_NOT_FILLED"
        return state
    positions = poll_positions(
        client,
        lambda rows: (
            len(rows) == 1
            and rows[0].get("positionSide") == "long"
            and parse_decimal(rows[0]["quantity"]) == QTY
        ),
        asset=ASSET,
    )
    if (
        len(positions) != 1
        or positions[0].get("positionSide") != "long"
        or parse_decimal(positions[0]["quantity"]) != QTY
    ):
        raise RuntimeError("CANARY_POSTFILL_POSITION_MISMATCH_DO_NOT_RETRY")
    closed = _submit(client, journal, account_id, opening=False, amount=QTY)
    state["closing"] = closed
    if closed["status"] == "filled":
        remaining = poll_positions(client, lambda rows: not rows, asset=ASSET)
        if remaining:
            raise RuntimeError("CANARY_REDUCE_ACK_NOT_FLAT")
        state["post_close_flat_verified"] = True
    return state


def run(args) -> dict:
    if not args.authorize_free_trial_canary:
        raise PermissionError("EXPLICIT_FREE_TRIAL_ORDER_AUTHORIZATION_REQUIRED")
    if args.audit_dir.exists() or args.audit_dir.is_symlink():
        raise PermissionError("CANARY_AUDIT_ALREADY_EXISTS_NO_REPEAT")
    old_umask = os.umask(0o077)
    try:
        args.audit_dir.mkdir(mode=0o700, parents=False)
    finally:
        os.umask(old_umask)
    if args.audit_dir.stat().st_mode & 0o077:
        raise PermissionError("AUDIT_DIR_NOT_PRIVATE")
    # Import account and SDK only after explicit authorization and filesystem
    # idempotency are established; never select another account automatically.
    from copytrade_pm.pm_sources import _resolved_identity
    from copytrade_pm.pm_e4_prospective import _guarded_info
    from propr_sdk import ProprClient

    identities = _resolved_identity(args.research_root)
    source_address = identities[SOURCE_WALLET]
    signal = captured_wallet_message(args.generation, source_address, datetime.now(UTC))
    receipt = parse_utc_iso(signal["source_first_known_at"], "source_first_known_at")
    if (datetime.now(UTC) - receipt).total_seconds() < 300:
        raise ValueError("INITIAL_HOLDING_CANARY_BATCH_NOT_DUE_300_SECONDS")
    live, _, _ = _guarded_info({"type": "clearinghouseState", "user": source_address})
    live_qty = [
        parse_decimal(p["position"]["szi"])
        for p in live.get("assetPositions", [])
        if p.get("position", {}).get("coin") == ASSET
    ]
    if len(live_qty) != 1 or live_qty[0] <= 0:
        raise ValueError("SOURCE_LONG_HOLDING_NO_LONGER_CONFIRMED")
    mids, _, _ = _guarded_info({"type": "allMids"})
    market_mid = parse_decimal(mids[ASSET])
    secret = require_propr_live_secret(args.env_file, missing_error="MISSING_VERIFIED_PROPR_CREDENTIAL")
    journal = IntentJournal(args.audit_dir / "intents.sqlite")
    os.chmod(args.audit_dir / "intents.sqlite", stat.S_IRUSR | stat.S_IWUSR)
    try:
        client = ProprClient(api_key=secret, base_url="https://api.propr.xyz/v1")
        try:
            result = execute_once(client, args.account_id, journal, signal, market_mid)
        except Exception as exc:
            # Preserve a redacted local incident even when the POST response is
            # ambiguous or the first leg filled but the close was not confirmed.
            incident = {
                "status": "CANARY_ABORTED_INSPECT_PROPR_BEFORE_ANY_RETRY",
                "error_type": type(exc).__name__,
                "source_frame_sha256": signal["source_frame_sha256"],
                "intent_states": journal.state_counts(),
            }
            write_private_json(args.audit_dir / "incident.json", incident)
            raise
    finally:
        journal.close()
    path = args.audit_dir / "report.json"
    write_private_json(path, result)
    return {
        "kind": result["schema"],
        "source_wallet": SOURCE_WALLET,
        "source_kind": result["signal_kind"],
        "opening": result["opening"],
        "closing": result.get("closing"),
        "post_close_flat_verified": result.get("post_close_flat_verified", False),
        "notional_reference": result["nominal_notional"],
        "report_file": str(path),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", required=True, type=Path)
    parser.add_argument("--research-root", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--audit-dir", required=True, type=Path)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--authorize-free-trial-canary", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
