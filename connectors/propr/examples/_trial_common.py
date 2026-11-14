"""Shared fail-closed helpers for one-shot Propr Free Trial canary diagnostics."""
from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
import json
import os
from pathlib import Path
import stat
from time import sleep

from connectors.common.validation import strict_decimal
from connectors.propr.state import IntentJournal


def parse_decimal(value: object) -> Decimal:
    return strict_decimal(value, error_cls=lambda _msg: ValueError("INVALID_NUMERIC_EVIDENCE"))


def read_private_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("SOURCE_EVIDENCE_MUST_BE_PRIVATE_REGULAR_FILE")
    return path.read_bytes()


def write_private_json(path: Path, payload: dict, *, exclusive: bool = False) -> None:
    mode = "x" if exclusive else "w"
    with path.open(mode) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def require_propr_live_secret(env_file: Path, *, missing_error: str) -> str:
    from dotenv import dotenv_values

    cfg = dotenv_values(env_file)
    secret = cfg.get("PROPR_LIVE_API_KEY") or cfg.get("PROPR_API_KEY")
    if not secret or not secret.startswith("pk_live_"):
        raise ValueError(missing_error)
    return secret


def check_trial(client, explicit_account_id: str, asset: str = "HYPE") -> dict:
    """Only active Free Trial paper accounts; no credential or account fallback."""
    if not explicit_account_id.startswith("urn:prp-account:") or not explicit_account_id.endswith("JhW9"):
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
        or parse_decimal(challenge.get("initialBalance")) != Decimal("5000")
    ):
        raise PermissionError("ACCOUNT_NOT_VERIFIED_ACTIVE_FREE_TRIAL")
    client.setup(account_id=explicit_account_id)
    acct = client.get_account()
    if (
        acct.get("accountId") != explicit_account_id
        or acct.get("type") != "paper"
        or parse_decimal(acct.get("marginBalance")) < Decimal("1000")
    ):
        raise PermissionError("ACCOUNT_NOT_PAPER_OR_INSUFFICIENT_MARGIN")
    if (
        client.get_open_positions()
        or client.get_orders(status="open")
        or client.get_orders(status="pending")
    ):
        raise PermissionError("ACCOUNT_NOT_FLAT_BEFORE_CANARY")
    instrument = client.get_margin_config(asset)
    instrument = instrument.get("data", instrument)
    if instrument.get("asset") != asset or instrument.get("accountId") != explicit_account_id:
        raise PermissionError("UNVERIFIED_PROPR_INSTRUMENT")
    return {
        "source": "active-Free-Trial",
        "account_type": "paper",
        "balance": str(parse_decimal(acct["balance"])),
        "account_id_matched": True,
        "margin_asset_matched": True,
    }


def open_positions_for(client, asset: str = "HYPE") -> list[dict]:
    return [
        p
        for p in client.get_open_positions(base=asset)
        if p.get("asset") == asset and parse_decimal(p.get("quantity", "0")) > 0
    ]


def poll_positions(
    client,
    predicate: Callable[[list[dict]], bool],
    *,
    asset: str = "HYPE",
    attempts: int = 5,
    delay_seconds: float = 0.4,
) -> list[dict]:
    positions: list[dict] = []
    for attempt in range(attempts):
        positions = open_positions_for(client, asset=asset)
        if predicate(positions):
            return positions
        if attempt < attempts - 1:
            sleep(delay_seconds)
    return positions


def submit_trial_order(
    client,
    journal: IntentJournal,
    *,
    account_id: str,
    asset: str,
    direction: str,
    opening: bool,
    quantity: Decimal,
    position_id: str | None,
    post_error_msg: str,
    invalid_ack_msg: str,
    unsettled_msg: str,
) -> tuple[str, dict, Decimal]:
    side = "buy" if ((direction == "long") == opening) else "sell"
    intent = journal.stage(
        account_id,
        asset,
        side,
        direction,
        str(quantity),
        reduce_only=not opening,
        position_id=position_id if not opening else None,
    )
    payload = {
        "accountId": account_id,
        "intentId": intent.id,
        "exchange": "hyperliquid",
        "type": "market",
        "side": side,
        "positionSide": direction,
        "productType": "perp",
        "timeInForce": "IOC",
        "asset": asset,
        "base": asset,
        "quote": "USDC",
        "quantity": intent.quantity,
        "reduceOnly": not opening,
        "closePosition": False,
    }
    try:
        result = client.create_orders([payload])
    except Exception as exc:
        journal.mark(intent.id, "unknown")
        raise RuntimeError(post_error_msg) from exc
    if (
        not isinstance(result, list)
        or len(result) != 1
        or result[0].get("intentId") != intent.id
        or result[0].get("accountId") != account_id
        or result[0].get("asset") != asset
        or not isinstance(result[0].get("orderId"), str)
    ):
        journal.mark(intent.id, "unknown")
        raise RuntimeError(invalid_ack_msg)
    row = result[0]
    status = row.get("status")
    filled = parse_decimal(row.get("cumulativeQuantity", "0"))
    if status == "filled" and filled == quantity:
        journal.mark(intent.id, "filled")
    elif status in {"rejected", "expired"} and filled == 0:
        journal.mark(intent.id, status)
    elif status in {"partially_filled", "partial"} and 0 < filled < quantity:
        journal.mark(intent.id, "partial")
    else:
        journal.mark(intent.id, "unknown")
        raise RuntimeError(unsettled_msg)
    return intent.id, row, filled
