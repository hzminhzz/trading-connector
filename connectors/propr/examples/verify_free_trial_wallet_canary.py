"""Read-only independent reconciliation of the one-shot Free Trial order canary.

Never imports ProprClient or calls any order mutation. Run once against the
private on-VPS canary audit, checking venue orders, fills, cash and flatness.
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import stat
from urllib.parse import quote

from connectors.propr.rest import ProprRest
from connectors.propr.examples.free_trial_wallet_canary import _d, _read_private


def evaluate(
    audit: dict,
    account: dict,
    orders: list[dict],
    fills: list[dict],
    active_positions: list[dict],
    active_orders: list[dict],
    journal_states: dict[str, str],
) -> dict:
    if (
        audit.get("schema") != "combo2_propr_free_trial_canary_v1"
        or audit.get("signal_kind") != "INITIAL_HOLDING_CANARY_NOT_NEW_FILL"
        or audit.get("live_funded_account") is not False
        or audit.get("production_copytrade_qualified") is not False
        or account.get("type") != "paper"
    ):
        raise ValueError("WRONG_PAPER_CANARY_AUDIT")
    opening, closing = audit["opening"], audit["closing"]
    order_by_id = {o.get("orderId"): o for o in orders}
    if set(order_by_id) != {opening["order_id"], closing["order_id"]}:
        raise ValueError("BROKER_ORDER_IDS_MISSING_OR_DUPLICATE")
    for item, side, reduction in ((opening, "buy", False), (closing, "sell", True)):
        row = order_by_id[item["order_id"]]
        if (
            row.get("intentId") != item["id"]
            or row.get("asset") != "HYPE"
            or row.get("accountId") != account.get("accountId")
            or row.get("side") != side
            or row.get("reduceOnly") is not reduction
            or row.get("status") != "filled"
            or _d(row["cumulativeQuantity"]) != Decimal("0.2")
            or journal_states.get(item["id"]) != "filled"
        ):
            raise ValueError("BROKER_ORDER_STATUS_OR_JOURNAL_MISMATCH")
    if len(fills) != 2 or {f.get("orderId") for f in fills} != set(order_by_id):
        raise ValueError("BROKER_FILLS_INCOMPLETE")
    for fill in fills:
        if _d(fill["quantity"]) != Decimal("0.2") or fill.get("asset") != "HYPE":
            raise ValueError("BROKER_FILL_QUANTITY_MISMATCH")
    if active_positions or active_orders:
        raise ValueError("CANARY_ACCOUNT_NOT_FLAT_OR_ORDER_PENDING")
    fees = sum((_d(f["fee"]) for f in fills), Decimal("0"))
    realized = sum((_d(f["realizedPnl"]) for f in fills), Decimal("0"))
    pre = _d(audit["preflight"]["balance"])
    after = _d(account["balance"])
    net = realized - fees
    if pre + net != after:
        raise ValueError("CANARY_BALANCE_PNL_RECONCILIATION_MISMATCH")
    return {
        "schema": "combo2_propr_free_trial_venue_reconciled_v1",
        "trial_account_type": "paper",
        "wallet_alias": "W028",
        "source_kind": audit["signal_kind"],
        "source_receipt_sha256": audit["source_receipt_sha256"],
        "opening_order_id": opening["order_id"],
        "closing_order_id": closing["order_id"],
        "opening_fill_price": str(
            _d(order_by_id[opening["order_id"]]["averageFillPrice"])
        ),
        "closing_fill_price": str(
            _d(order_by_id[closing["order_id"]]["averageFillPrice"])
        ),
        "buy_quantity": "0.2",
        "sell_quantity": "0.2",
        "realized_pnl": str(realized),
        "trading_fees": str(fees),
        "net_pnl": str(net),
        "balance_before": str(pre),
        "balance_after": str(after),
        "positions_open": 0,
        "orders_pending": 0,
        "venue_confirmed_fills": 2,
        "production_copytrade_qualified": False,
        "live_funded_account": False,
        "network_order_mutations_during_reconciliation": 0,
    }


async def read_venue(account_id: str, api_key: str, audit: dict):
    rest = ProprRest("https://api.propr.xyz/v1", api_key)
    base = "/accounts/" + quote(account_id, safe="")
    try:
        account_raw = await rest.request("GET", base)
        account = account_raw.get("data", account_raw)
        if account.get("accountId") != account_id:
            raise ValueError("READ_ONLY_ACCOUNT_IDENTITY_MISMATCH")
        orders, fills = [], []
        for item in (audit["opening"], audit["closing"]):
            oid = item["order_id"]
            payload = await rest.request(
                "GET",
                base + "/orders",
                params={"orderId": oid, "limit": 100, "offset": 0},
            )
            rows = payload.get("data", payload)
            orders.extend(r for r in rows if r.get("orderId") == oid)
            payload = await rest.request(
                "GET",
                base + "/trades",
                params={"orderId": oid, "limit": 100, "offset": 0},
            )
            rows = payload.get("data", payload)
            fills.extend(r for r in rows if r.get("orderId") == oid)
        pos = await rest.request(
            "GET",
            base + "/positions",
            params={"status": "open", "limit": 100, "offset": 0},
        )
        positions = [
            row for row in pos.get("data", pos) if _d(row.get("quantity", "0")) > 0
        ]
        pending = []
        for state in ("pending", "open", "partially_filled"):
            payload = await rest.request(
                "GET",
                base + "/orders",
                params={"status": state, "limit": 100, "offset": 0},
            )
            pending.extend(
                row
                for row in payload.get("data", payload)
                if row.get("status") == state
            )
        return account, orders, fills, positions, pending
    finally:
        await rest.close()


def run(private_root: Path, account_id: str, env_path: Path) -> dict:
    if not account_id.endswith("JhW9"):
        raise ValueError("FREE_TRIAL_ACCOUNT_BINDING_MISMATCH")
    report_path = private_root / "report.json"
    payload = _read_private(report_path)
    audit = json.loads(payload)
    verified_path = private_root / "venue-reconciliation.json"
    if verified_path.exists() or verified_path.is_symlink():
        raise ValueError("IMMUTABLE_VENUE_RECONCILIATION_ALREADY_EXISTS")
    from dotenv import dotenv_values

    secrets = dotenv_values(env_path)
    key = secrets.get("PROPR_LIVE_API_KEY") or secrets.get("PROPR_API_KEY")
    if not key or not key.startswith("pk_live_"):
        raise ValueError("MISSING_PROPR_READ_ONLY_KEY")
    journal_path = private_root / "intents.sqlite"
    if journal_path.is_symlink() or journal_path.stat().st_mode & 0o077:
        raise ValueError("JOURNAL_NOT_PRIVATE")
    with sqlite3.connect("file:" + str(journal_path) + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT id,state FROM intents").fetchall()
    journal_states = dict(rows)
    account, orders, fills, positions, pending = asyncio.run(
        read_venue(account_id, key, audit)
    )
    result = evaluate(audit, account, orders, fills, positions, pending, journal_states)
    result["local_canary_report_sha256"] = sha256(payload).hexdigest()
    with verified_path.open("x") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
    os.chmod(verified_path, stat.S_IRUSR | stat.S_IWUSR)
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--audit-dir", type=Path, required=True)
    p.add_argument("--account-id", required=True)
    p.add_argument("--env-file", type=Path, required=True)
    args = p.parse_args()
    print(
        json.dumps(run(args.audit_dir, args.account_id, args.env_file), sort_keys=True)
    )


if __name__ == "__main__":
    main()
