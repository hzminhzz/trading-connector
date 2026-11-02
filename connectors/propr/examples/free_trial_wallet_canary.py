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
from time import sleep

from connectors.propr.state import IntentJournal

SOURCE_WALLET = "W028"
ASSET = "HYPE"
QTY = Decimal("0.20")
MAX_NOTIONAL = Decimal("30")
MIN_NOTIONAL = Decimal("10")
MAX_SOURCE_AGE = 900


def _d(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("INVALID_NUMERIC_EVIDENCE")
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise ValueError("NONFINITE_NUMERIC_EVIDENCE")
    return amount


def _read_private(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("SOURCE_EVIDENCE_MUST_BE_PRIVATE_REGULAR_FILE")
    return path.read_bytes()


def captured_wallet_message(generation: Path, address: str, now: datetime) -> dict:
    """Bind the source's actual first-receipt and raw SHA, not a vendor fill time."""
    report = json.loads(_read_private(generation / "report.json"))
    if (
        report.get("schema") != "combo2_rest_account_capture_v1"
        or report.get("all_subjects_captured") is not True
        or report.get("account_count") != 10
        or report.get("network_order_mutations") != 0
        or report.get("errors")
        or report.get("managers", {}).get("W028", {}).get("subaccount_count") != 7
    ):
        raise ValueError("INCOMPLETE_OR_UNTRUSTED_WALLET_RECEIPT")
    meta = json.loads(_read_private(generation / "metadata" / "W028-dex-default.json"))
    raw = _read_private(generation / "raw" / "W028-dex-default.json")
    digest = sha256(raw).hexdigest()
    if (
        meta.get("kind") != "clearinghouseState"
        or meta.get("sha256") != digest
        or meta.get("scope_hash") != sha256(address.lower().encode()).hexdigest()
        or report.get("subjects", {}).get("W028", {}).get("sha256")
        != sha256(address.lower().encode()).hexdigest()
    ):
        raise ValueError("WALLET_FRAME_PROVENANCE_MISMATCH")
    first = datetime.fromisoformat(meta["locally_received_at"].replace("Z", "+00:00"))
    lag = (now - first).total_seconds()
    if not 0 <= lag <= MAX_SOURCE_AGE:
        raise ValueError("SOURCE_SIGNAL_STALE_OR_FUTURE")
    payload = json.loads(raw)
    positions = [
        p.get("position", {})
        for p in payload.get("assetPositions", [])
        if p.get("position", {}).get("coin") == ASSET
    ]
    if len(positions) != 1 or _d(positions[0].get("szi")) <= 0:
        raise ValueError("NO_VERIFIED_LONG_SOURCE_HOLDING")
    return {
        "schema": "combo2_initial_holding_canary_v1",
        "wallet_alias": SOURCE_WALLET,
        "asset": ASSET,
        "side": "buy",
        "source_first_known_at": first.isoformat(),
        "source_frame_sha256": digest,
        "source_signed_quantity": str(_d(positions[0]["szi"])),
        "is_new_trade_signal": False,
        "is_production_allocation": False,
        "source_equity_verified": False,
    }


def check_trial(client, explicit_account_id: str) -> dict:
    """Only active Free Trial paper accounts; no credential or account fallback."""
    if not explicit_account_id.startswith(
        "urn:prp-account:"
    ) or not explicit_account_id.endswith("JhW9"):
        raise PermissionError("EXACT_FREE_TRIAL_ACCOUNT_REQUIRED")
    active = client.get_challenge_attempts(status="active")
    matches = [a for a in active if a.get("accountId") == explicit_account_id]
    if len(matches) != 1:
        raise PermissionError("ACTIVE_CHALLENGE_BINDING_NOT_UNIQUE")
    attempt_id = matches[0].get("attemptId")
    if not attempt_id:
        raise PermissionError("MISSING_ACTIVE_TRIAL_ATTEMPT")
    info = client.get_challenge_attempt(attempt_id)
    info = info.get("data", info)
    challenge = info.get("challenge", {})
    if (
        info.get("status") != "active"
        or info.get("accountId") != explicit_account_id
        or challenge.get("name") != "Free Trial"
        or challenge.get("slug") != "free-trial"
        or _d(challenge.get("initialBalance")) != Decimal("5000")
    ):
        raise PermissionError("ACCOUNT_NOT_VERIFIED_ACTIVE_FREE_TRIAL")
    client.setup(account_id=explicit_account_id)
    acct = client.get_account()
    if (
        acct.get("accountId") != explicit_account_id
        or acct.get("type") != "paper"
        or _d(acct.get("marginBalance")) < Decimal("1000")
    ):
        raise PermissionError("ACCOUNT_NOT_PAPER_OR_INSUFFICIENT_MARGIN")
    if (
        client.get_open_positions()
        or client.get_orders(status="open")
        or client.get_orders(status="pending")
    ):
        raise PermissionError("ACCOUNT_NOT_FLAT_BEFORE_CANARY")
    instrument = client.get_margin_config(ASSET)
    instrument = instrument.get("data", instrument)
    if (
        instrument.get("asset") != ASSET
        or instrument.get("accountId") != explicit_account_id
    ):
        raise PermissionError("UNVERIFIED_PROPR_INSTRUMENT")
    return {
        "source": "active-Free-Trial",
        "account_type": "paper",
        "balance": str(_d(acct["balance"])),
        "account_id_matched": True,
        "margin_asset_matched": True,
    }


def _order(intent, account_id: str, *, opening: bool, amount: Decimal) -> dict:
    if amount <= 0 or amount > QTY:
        raise ValueError("CANARY_QUANTITY_OUT_OF_BOUNDS")
    return {
        "accountId": account_id,
        "intentId": intent.id,
        "exchange": "hyperliquid",
        "type": "market",
        "side": "buy" if opening else "sell",
        "positionSide": "long",
        "productType": "perp",
        "timeInForce": "IOC",
        "asset": ASSET,
        "base": ASSET,
        "quote": "USDC",
        "quantity": str(amount),
        "reduceOnly": not opening,
        "closePosition": False,
    }


def _submit(
    client, journal: IntentJournal, account_id: str, *, opening: bool, amount: Decimal
) -> dict:
    intent = journal.stage(
        account_id,
        ASSET,
        "buy" if opening else "sell",
        "long",
        str(amount),
        reduce_only=not opening,
        position_id=("canary-existing-position" if not opening else None),
    )
    payload = _order(intent, account_id, opening=opening, amount=amount)
    try:
        result = client.create_orders([payload])
    except Exception as exc:
        journal.mark(intent.id, "unknown")
        raise RuntimeError("CANARY_ORDER_OUTCOME_UNKNOWN_NO_RETRY") from exc
    if (
        not isinstance(result, list)
        or len(result) != 1
        or result[0].get("intentId") != intent.id
        or result[0].get("accountId") != account_id
        or result[0].get("asset") != ASSET
        or not isinstance(result[0].get("orderId"), str)
    ):
        journal.mark(intent.id, "unknown")
        raise RuntimeError("CANARY_ORDER_ACK_INVALID_NO_RETRY")
    status = result[0].get("status")
    filled = _d(result[0].get("cumulativeQuantity", "0"))
    if status == "filled" and filled == amount:
        journal.mark(intent.id, "filled")
    elif status in {"rejected", "expired"} and filled == 0:
        journal.mark(intent.id, status)
    elif status in {"partially_filled", "partial"} and 0 < filled < amount:
        journal.mark(intent.id, "partial")
    else:
        journal.mark(intent.id, "unknown")
        raise RuntimeError("CANARY_NONTERMINAL_ORDER_NO_RETRY")
    return {
        "id": intent.id,
        "order_id": result[0]["orderId"],
        "status": status,
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
    if journal.connection.execute("SELECT COUNT(*) FROM intents").fetchone()[0]:
        raise PermissionError("CANARY_ALREADY_ATTEMPTED_IN_THIS_JOURNAL")
    if (
        not market_mid.is_finite()
        or not MIN_NOTIONAL <= QTY * market_mid <= MAX_NOTIONAL
    ):
        raise PermissionError("REFERENCE_PRICE_OR_NOTIONAL_OUT_OF_BOUNDS")
    preflight = check_trial(client, account_id)
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
    positions = []
    for attempt in range(5):
        positions = [
            p
            for p in client.get_open_positions(base=ASSET)
            if p.get("asset") == ASSET
            and p.get("positionSide") == "long"
            and _d(p.get("quantity", "0")) > 0
        ]
        if len(positions) == 1 and _d(positions[0]["quantity"]) == QTY:
            break
        if attempt < 4:
            sleep(0.4)
    if len(positions) != 1 or _d(positions[0]["quantity"]) != QTY:
        raise RuntimeError("CANARY_POSTFILL_POSITION_MISMATCH_DO_NOT_RETRY")
    closed = _submit(client, journal, account_id, opening=False, amount=QTY)
    state["closing"] = closed
    if closed["status"] == "filled":
        for attempt in range(5):
            remaining = [
                p
                for p in client.get_open_positions(base=ASSET)
                if p.get("asset") == ASSET and _d(p.get("quantity", "0")) > 0
            ]
            if not remaining:
                break
            if attempt < 4:
                sleep(0.4)
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
    from dotenv import dotenv_values

    identities = _resolved_identity(args.research_root)
    source_address = identities[SOURCE_WALLET]
    signal = captured_wallet_message(args.generation, source_address, datetime.now(UTC))
    receipt = datetime.fromisoformat(signal["source_first_known_at"])
    if (datetime.now(UTC) - receipt).total_seconds() < 300:
        raise ValueError("INITIAL_HOLDING_CANARY_BATCH_NOT_DUE_300_SECONDS")
    live, _, _ = _guarded_info({"type": "clearinghouseState", "user": source_address})
    live_qty = [
        _d(p["position"]["szi"])
        for p in live.get("assetPositions", [])
        if p.get("position", {}).get("coin") == ASSET
    ]
    if len(live_qty) != 1 or live_qty[0] <= 0:
        raise ValueError("SOURCE_LONG_HOLDING_NO_LONGER_CONFIRMED")
    mids, _, _ = _guarded_info({"type": "allMids"})
    market_mid = _d(mids[ASSET])
    cfg = dotenv_values(args.env_file)
    secret = cfg.get("PROPR_LIVE_API_KEY") or cfg.get("PROPR_API_KEY")
    if not secret or not secret.startswith("pk_live_"):
        raise ValueError("MISSING_VERIFIED_PROPR_CREDENTIAL")
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
                "intent_states": journal.connection.execute(
                    "SELECT state,COUNT(*) FROM intents GROUP BY state"
                ).fetchall(),
            }
            path = args.audit_dir / "incident.json"
            path.write_text(json.dumps(incident, indent=2, sort_keys=True))
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            raise
    finally:
        journal.close()
    path = args.audit_dir / "report.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True))
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
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
