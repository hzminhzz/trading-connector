"""Explicit one-shot Bybit DEMO real-API canary using a synthetic Combo2 PM target.

Default is authenticated read-only inspection. Only --execute may submit ONE
small Demo opening order and one reduce-only close; never Testnet/mainnet.
This validates transport, sizing translation and reconciliation -- NOT a
genuine source-wallet signal, economic qualification or prop challenge pass.

Requires the optional copytrade_pm research package in PYTHONPATH. All audit
bytes remain owner-private outside Git; account secrets/UID are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import tempfile
from typing import Any

from connectors.bybit.broker import BybitLiveBroker
from connectors.bybit.client import BybitClient
from connectors.bybit.config import BybitConfig, Environment, Mode
from connectors.bybit.hyperliquid_portfolio import (
    match_bybit_market,
    shadow_target_preview,
)
from connectors.bybit.journal import BybitIntentJournal

MARKET = "XRP/USDT:USDT"
SOURCE_INSTRUMENT = "hyperliquid/mainnet:perp:default/XRP"
MAX_ORDER_USD = Decimal("25")
TARGET_USD = Decimal("15")
LEADER_EQUITY = Decimal("100000")
SLEEVE_USD = Decimal("1000")
GROSS_CAP_USD = Decimal("5000")
COIN_CAP_USD = Decimal("1250")
PRIVATE_ROOT = Path.home() / ".local/share/copytrade/bybit-demo-pm-canary"


def decimal(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("UNAVAILABLE_CANARY_NUMBER")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("NONFINITE_CANARY_NUMBER")
    return result


def assert_demo_config(config: BybitConfig) -> None:
    if (
        config.environment is not Environment.DEMO
        or config.mode is not Mode.READ_ONLY
        or not config.api_key
        or not config.api_secret
        or not config.expected_demo_uid
    ):
        raise PermissionError("BYBIT_DEMO_READ_ONLY_CREDENTIALS_AND_UID_REQUIRED")


def validate_demo_preflight(
    identity: dict[str, Any],
    positions: list[Any],
    orders: list[Any],
    account: Any,
    market: dict[str, Any],
    last: Decimal,
    ticker_timestamp: int | None,
    now: datetime,
) -> dict[str, Any]:
    if (
        identity.get("environment") != "demo"
        or identity.get("private_rest") != "api-demo.bybit.com"
        or identity.get("authenticated_uid_matches_expected") is not True
        or identity.get("dedicated_demo_key_matches") is not True
        or identity.get("unified_account_verified") is not True
    ):
        raise PermissionError("DEMO_IDENTITY_NOT_ATTESTED")
    if positions or orders:
        raise PermissionError("DEMO_ACCOUNT_NOT_INITIAL_FLAT")
    if account.equity_usd <= 0 or account.available_usd < Decimal("50"):
        raise PermissionError("DEMO_ACCOUNT_UNFUNDED_OR_INSUFFICIENT")
    if market.get("mapping") != "MATCHED_RESEARCH":
        raise ValueError("DEMO_XRP_MARKET_NOT_MAPPED")
    if last <= 0:
        raise ValueError("DEMO_MARK_NONPOSITIVE")
    if ticker_timestamp is not None:
        ticker_at = datetime.fromtimestamp(ticker_timestamp / 1000, UTC)
        if not timedelta(0) <= now - ticker_at <= timedelta(seconds=120):
            raise ValueError("DEMO_TICKER_STALE")
    return {
        "environment": "demo",
        "endpoint": "api-demo.bybit.com",
        "uid_and_key_verified": True,
        "key_is_read_only": identity.get("key_is_read_only"),
        "initial_positions": 0,
        "initial_orders": 0,
        "equity_positive": True,
        "symbol": MARKET,
        "bybit_last": str(last),
        "ticker_exchange_timestamp_present": ticker_timestamp is not None,
    }


def diagnostic_pm_report(root: Path, source_mark: Decimal) -> dict[str, Any]:
    """Run the REAL PM engine with synthetic observations; never a wallet receipt."""
    from copytrade_pm.pm_combo2 import Combo2PaperTracker

    if source_mark <= 0:
        raise ValueError("INVALID_SYNTHETIC_MARK")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    # The synthetic 5-minute chronology is a deterministic ENGINEERING FIXTURE.
    # The source price is current, so this IS NOT a point-in-time research datum.
    base = datetime(2026, 11, 2, 10, tzinfo=UTC)
    decision = base + timedelta(seconds=300)
    ids = {"W028": "a" * 64, "CA-008": "b" * 64, "W015": "c" * 64}
    tracker = Combo2PaperTracker(
        root,
        run_id="synthetic-bybit-demo-transport",
        started_at=base.isoformat(),
        source_subject_hashes=ids,
    )
    source_quantity = TARGET_USD * LEADER_EQUITY / SLEEVE_USD / source_mark
    tracker.record(
        kind="SOURCE_RECEIPT",
        event_id="synthetic-receipt",
        source_event_at=base.isoformat(),
        first_known_at=base.isoformat(),
        decision_at=base.isoformat(),
        hypothetical_fill_at=base.isoformat(),
        data={"manager": "W028", "instrument": SOURCE_INSTRUMENT},
    )
    quote = {
        "first_known_at": decision.isoformat(),
        "mark": str(source_mark),
        "lot": "0.0000001",
        "minimum": "0.0000001",
        "volume_base": "1000000000",
        "fee_bps": "0",
        "spread_bps": "0",
        "slippage_bps": "0",
        "session_open": True,
        "supported": True,
        "native_market_id": "XRP-PERP",
    }
    source = {
        "manager": "W028",
        "subject_sha": ids["W028"],
        "quality": "OFFICIAL_ALL_DEXS_READ_ONLY",
        "full_book": True,
        "all_dexes_complete": True,
        "equity": str(LEADER_EQUITY),
        "positions": {
            SOURCE_INSTRUMENT: {
                "quantity": str(source_quantity),
                "mark": str(source_mark),
            }
        },
        "event_at": decision.isoformat(),
        "first_known_at": decision.isoformat(),
    }
    result = tracker.record(
        kind="PAPER_REBALANCE",
        event_id="synthetic-rebalance",
        source_event_at=base.isoformat(),
        first_known_at=decision.isoformat(),
        decision_at=decision.isoformat(),
        hypothetical_fill_at=decision.isoformat(),
        data={
            "leaders": {"W028": source},
            "markets": {SOURCE_INSTRUMENT: quote},
            "shocks": [{SOURCE_INSTRUMENT: "-0.01"}],
            "hedge_break_loss": "0",
        },
    )
    return result["meta"]["last_report"]


def validate_demo_target(
    plan: dict[str, Any], market: dict[str, Any]
) -> dict[str, Any]:
    if (
        plan.get("status") != "SHADOW_ONLY"
        or plan.get("execution_authorized") is not False
        or plan.get("order_deltas_computed") is not False
        or len(plan.get("targets", [])) != 1
        or plan.get("skipped") != []
    ):
        raise PermissionError("PM_TO_BYBIT_CANARY_PLAN_BLOCKED")
    target = plan["targets"][0]
    if (
        target.get("source_instrument") != SOURCE_INSTRUMENT
        or target.get("bybit_market") != MARKET
        or target.get("kind") != "SHADOW_TARGET_POSITION_NOT_ORDER"
        or target.get("target_side") != "long"
    ):
        raise ValueError("UNEXPECTED_CANARY_TARGET")
    qty = decimal(target["target_quantity"])
    price = decimal(target["bybit_reference_price"])
    notional = qty * price
    step = decimal(market["qty_step"])
    if (
        qty <= 0
        or notional > MAX_ORDER_USD
        or notional < decimal(market["min_notional"])
        or qty < decimal(market["min_qty"])
        or (qty / step).to_integral_value(rounding=ROUND_DOWN) * step != qty
    ):
        raise ValueError("CANARY_BROKER_SIZE_OUT_OF_RANGE")
    if decimal(plan["gross_pre_net_source_usd"]) > COIN_CAP_USD:
        raise ValueError("PM_CANARY_GROSS_CAP_BREACH")
    return {"quantity": str(qty), "reference_notional": str(notional), "market": MARKET}


async def fetch_trade_fills(
    client: BybitClient, native_id: str, order_id: str
) -> dict[str, Any]:
    """Independent Bybit V5 execution list, NOT a create-order acknowledgement."""
    if not order_id:
        raise ValueError("ORDER_ID_REQUIRED")
    response = await client.exchange.private_get_v5_execution_list(
        {"category": "linear", "symbol": native_id, "orderId": order_id, "limit": 50}
    )
    result = client._v5_result(response, "demo_execution_list")
    rows = result.get("list")
    if not isinstance(rows, list):
        raise RuntimeError("DEMO_EXECUTION_LIST_UNAVAILABLE")
    matches = [x for x in rows if isinstance(x, dict) and x.get("orderId") == order_id]
    filled = sum((decimal(row.get("execQty")) for row in matches), Decimal(0))
    if any(decimal(row.get("execPrice")) <= 0 for row in matches):
        raise RuntimeError("DEMO_EXECUTION_PRICE_INVALID")
    return {
        "order_id": order_id,
        "execution_rows": len(matches),
        "filled_quantity": str(filled),
        "execution_ids_present": all(bool(x.get("execId")) for x in matches),
    }


async def await_fills(
    client: BybitClient, native_id: str, order_id: str
) -> dict[str, Any]:
    result = {"execution_rows": 0, "filled_quantity": "0"}
    for _ in range(8):
        result = await fetch_trade_fills(client, native_id, order_id)
        if decimal(result["filled_quantity"]) > 0:
            return result
        await asyncio.sleep(0.6)
    return result


async def scoped_position(client: BybitClient) -> Decimal:
    positions = await client.fetch_demo_positions()
    if any(p.symbol != MARKET for p in positions):
        raise RuntimeError("UNRELATED_DEMO_POSITION_APPEARED")
    if len(positions) > 1:
        raise RuntimeError("AMBIGUOUS_DEMO_POSITION")
    if not positions:
        return Decimal(0)
    if positions[0].side not in ("buy", "sell"):
        raise RuntimeError("UNKNOWN_DEMO_POSITION_SIDE")
    sign = 1 if positions[0].side == "buy" else -1
    return positions[0].size * sign


def reconcile_verified_fill(
    journal: BybitIntentJournal, order_id: str, executed_quantity: Decimal
) -> None:
    """Promote a staged/submitted intent ONLY after exact independent V5 fill."""
    rows = journal.connection.execute(
        "SELECT id, quantity, state FROM intents WHERE exchange_order_id = ?",
        (order_id,),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError("CANARY_INTENT_ORDER_ID_NOT_UNIQUE")
    intent_id, requested, state = rows[0]
    if executed_quantity <= 0 or decimal(requested) != executed_quantity:
        raise ValueError("CANARY_INTENT_FILL_QUANTITY_NOT_EXACT")
    if state == "filled":
        return
    if state != "submitted":
        raise ValueError("CANARY_INTENT_NOT_SUBMITTED")
    journal.mark_terminal(intent_id, "filled")


async def verify_existing(audit_dir: Path) -> dict[str, Any]:
    """Fresh READ-ONLY Demo API reconciliation plus local journal settlement."""
    root = PRIVATE_ROOT.resolve()
    target = Path(audit_dir)
    if (
        target.is_symlink()
        or target.resolve().parent != root
        or not target.is_dir()
        or target.stat().st_mode & 0o077
    ):
        raise PermissionError("PRIVATE_CANARY_AUDIT_ROOT_REQUIRED")
    evidence_file = target / "report.json"
    if evidence_file.is_symlink() or evidence_file.stat().st_mode & 0o077:
        raise PermissionError("PRIVATE_CANARY_REPORT_REQUIRED")
    report = json.loads(evidence_file.read_text())
    if (
        report.get("schema") != "bybit_combo2_synthetic_pm_real_demo_canary_v1"
        or report.get("status") != "VERIFIED_DEMO_FILL_AND_FLAT"
        or report.get("bybit_environment") != "demo"
        or report.get("real_wallet_signal_verified") is not False
        or report.get("order_mutations") != 2
    ):
        raise ValueError("UNVERIFIED_CANARY_REPORT")
    config = BybitConfig.from_env()
    assert_demo_config(config)
    client = BybitClient(config)
    await client.connect()
    try:
        identity = client.demo_identity_evidence
        if not (
            identity.get("authenticated_uid_matches_expected") is True
            and identity.get("private_rest") == "api-demo.bybit.com"
        ):
            raise PermissionError("DEMO_IDENTITY_DRIFT")
        if await scoped_position(client) != 0 or await client.fetch_demo_open_orders():
            raise RuntimeError("DEMO_NOT_FLAT_ON_INDEPENDENT_RECONNECT")
        market = match_bybit_market("XRP", client.exchange.markets)
        if market.get("mapping") != "MATCHED_RESEARCH":
            raise RuntimeError("DEMO_MARKET_UNAVAILABLE")
        opening = await await_fills(
            client, market["native_id"], report["opening_order_id"]
        )
        closing = await await_fills(
            client, market["native_id"], report["closing_order_id"]
        )
    finally:
        await client.close()
    size = decimal(report["target"]["quantity"])
    if (
        decimal(opening["filled_quantity"]) != size
        or decimal(closing["filled_quantity"]) != size
        or opening["execution_ids_present"] is not True
        or closing["execution_ids_present"] is not True
    ):
        raise RuntimeError("INDEPENDENT_BROKER_EXECUTION_QUANTITY_MISMATCH")
    journal_path = target / "intents.sqlite"
    if journal_path.is_symlink():
        raise PermissionError("CANARY_JOURNAL_SYMLINK")
    journal = BybitIntentJournal(journal_path)
    try:
        reconcile_verified_fill(journal, report["opening_order_id"], size)
        reconcile_verified_fill(journal, report["closing_order_id"], size)
        states = journal.connection.execute(
            "SELECT state, COUNT(*) FROM intents GROUP BY state"
        ).fetchall()
        if states != [("filled", 2)]:
            raise RuntimeError("CANARY_JOURNAL_NOT_RECONCILED")
    finally:
        journal.close()
    os.chmod(journal_path, 0o600)
    report["fresh_read_only_reconciliation"] = {
        "at": datetime.now(UTC).isoformat(),
        "opening": opening,
        "closing": closing,
        "flat": True,
        "pending_orders": 0,
        "journal_filled_intents": 2,
        "network_order_mutations": 0,
    }
    write_report(target, report)
    return {
        "status": "VERIFIED_FRESH_READ_ONLY_RECONCILIATION",
        "opening_execution_quantity": opening["filled_quantity"],
        "closing_execution_quantity": closing["filled_quantity"],
        "journal_filled_intents": 2,
        "account_flat": True,
        "new_order_mutations": 0,
        "audit_dir": str(target),
    }


def private_run_root(root: Path) -> Path:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or root.stat().st_mode & 0o077:
        raise PermissionError("CANARY_PRIVATE_ROOT_REQUIRED")
    return root


def claim_once(root: Path, account_hash: str, day: str) -> Path:
    # Durable per-account per-UTC-day claim. No automatic opening-order retries.
    private_run_root(root)
    claim = root / f"{day}-{account_hash}.claimed"
    old = os.umask(0o077)
    try:
        fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write("DEMO_CANARY_ONE_ATTEMPT\n")
            fh.flush()
            os.fsync(fh.fileno())
    finally:
        os.umask(old)
    run_dir = root / f"{day}-{account_hash}-attempt"
    run_dir.mkdir(mode=0o700)
    return run_dir


def write_report(directory: Path, report: dict[str, Any]) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = directory / "report.json"
    temp = directory / ("." + secrets.token_hex(8) + ".tmp")
    old = os.umask(0o077)
    try:
        with open(temp, "x", encoding="utf-8") as handle:
            json.dump(report, handle, sort_keys=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, name)
    finally:
        os.umask(old)


async def run(execute: bool, *, root: Path = PRIVATE_ROOT) -> dict[str, Any]:
    config = BybitConfig.from_env()
    assert_demo_config(config)
    preview_client = BybitClient(config)
    await preview_client.connect()
    try:
        identity = preview_client.demo_identity_evidence
        account = await preview_client.fetch_demo_account_summary()
        positions = await preview_client.fetch_demo_positions()
        orders = await preview_client.fetch_demo_open_orders()
        binding = match_bybit_market("XRP", preview_client.exchange.markets)
        ticker = await preview_client.exchange.fetch_ticker(MARKET)
        price = decimal(ticker.get("last"))
        preflight = validate_demo_preflight(
            identity,
            positions,
            orders,
            account,
            binding,
            price,
            ticker.get("timestamp"),
            datetime.now(UTC),
        )
    finally:
        await preview_client.close()

    # This temporary PM run only has synthetic source data; its paper ledger is
    # never mixed with genuine receipt artifacts or used as proof of signal PIT.
    with tempfile.TemporaryDirectory(prefix="bybit-demo-pm-") as folder:
        pm_report = diagnostic_pm_report(Path(folder), price)
        plan = shadow_target_preview(
            pm_report,
            {MARKET: preview_client.exchange.markets[MARKET]},
            {MARKET: str(price)},
            {SOURCE_INSTRUMENT: str(price)},
            gross_cap_usd=GROSS_CAP_USD,
            coin_gross_cap_usd=COIN_CAP_USD,
        )
    target = validate_demo_target(plan, binding)
    base_report = {
        "schema": "bybit_combo2_synthetic_pm_real_demo_canary_v1",
        "source_signal": "SYNTHETIC_DIAGNOSTIC_NOT_REAL_WALLET",
        "pm_sizing_path": "REAL_COPYTRADE_PM_ENGINE",
        "first_known_300s_validated_live": False,
        "bybit_environment": "demo",
        "preflight": preflight,
        "pm_allowed_base": pm_report["allowed"]["W028"][SOURCE_INSTRUMENT],
        "pm_gross_pre_net_usd": plan["gross_pre_net_source_usd"],
        "target": target,
        "order_mutations": 0,
        "status": "PREFLIGHT_ONLY",
        "real_wallet_signal_verified": False,
        "scientifically_qualified": False,
        "production_authorized": False,
    }
    if not execute:
        return base_report
    if identity.get("key_is_read_only") is not False:
        return {**base_report, "status": "BLOCKED_DEMO_KEY_READ_ONLY"}

    uid_hash = sha256(config.expected_demo_uid.encode()).hexdigest()[:16]
    run_dir = claim_once(root, uid_hash, datetime.now(UTC).date().isoformat())
    report = {**base_report, "status": "CLAIMED_NO_ORDER"}
    write_report(run_dir, report)
    from ml4t.backtest.types import OrderSide, OrderType
    from ml4t.live.safety import LiveRiskConfig, SafeBroker

    exec_client = BybitClient(replace(config, mode=Mode.EXECUTION))
    journal = BybitIntentJournal(run_dir / "intents.sqlite")
    os.chmod(run_dir / "intents.sqlite", 0o600)
    broker = BybitLiveBroker(client=exec_client, journal=journal)
    safe = SafeBroker(
        broker,
        LiveRiskConfig(
            execution_mode="paper",
            max_order_value=float(MAX_ORDER_USD),
            max_position_value=float(MAX_ORDER_USD),
            max_total_exposure=float(MAX_ORDER_USD),
            allowed_assets={MARKET},
            state_file=str(run_dir / "risk_state.json"),
        ),
    )
    opening_order_id: str | None = None
    closing_order_id: str | None = None
    opening_sent = False
    try:
        await safe.connect()
        safe.assert_paper_trading()
        if exec_client.demo_identity_evidence != identity:
            raise PermissionError("DEMO_IDENTITY_DRIFT")
        if exec_client.demo_identity_evidence.get("key_is_read_only") is not False:
            raise PermissionError("DEMO_KEY_READ_ONLY")
        if (
            await scoped_position(exec_client) != 0
            or await exec_client.fetch_demo_open_orders()
        ):
            raise PermissionError("DEMO_INVENTORY_DRIFT")
        # refresh the market mark just before committing an order; reject a
        # material change to avoid an unbounded notional despite stale preview.
        quote = await exec_client.exchange.fetch_ticker(MARKET)
        actual_mark = decimal(quote.get("last"))
        qty = decimal(target["quantity"])
        if (
            actual_mark <= 0
            or qty * actual_mark > MAX_ORDER_USD
            or abs(actual_mark / price - 1) > Decimal("0.02")
        ):
            raise ValueError("DEMO_PRICE_MOVED_BEYOND_TEST_GUARD")
        safe.record_market_snapshot(MARKET, float(actual_mark))
        report["status"] = "OPEN_SUBMISSION_MAY_BE_UNKNOWN"
        write_report(run_dir, report)
        opening_sent = True
        order = await safe.submit_order_async(
            asset=MARKET,
            quantity=float(qty),
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            params={"orderLinkId": "CPMDEMO" + secrets.token_hex(8)},
        )
        opening_order_id = order.order_id
        report["opening_order_id"] = opening_order_id
        report["order_mutations"] += 1
        report["opening_ack_status"] = str(order.status)
        report["status"] = "OPEN_ACK_RECONCILING"
        write_report(run_dir, report)
        opened = await await_fills(exec_client, binding["native_id"], opening_order_id)
        report["opening_independent_executions"] = opened
        if decimal(opened["filled_quantity"]) <= 0:
            raise RuntimeError("NO_VERIFIED_OPEN_EXECUTION")
        if decimal(opened["filled_quantity"]) > qty:
            raise RuntimeError("OPEN_EXECUTED_MORE_THAN_BOUNDED_QTY")
        pos = await scoped_position(exec_client)
        if pos != decimal(opened["filled_quantity"]):
            raise RuntimeError("BROKER_POSITION_NOT_EQUAL_TO_OPEN_FILLS")
        report["status"] = "OPEN_VERIFIED_CLOSING"
        write_report(run_dir, report)
    except BaseException as exc:
        report["error_type"] = type(exc).__name__
        report["status"] = "HALT_INSPECT_EXCHANGE"
        write_report(run_dir, report)
        if not opening_sent:
            raise
    finally:
        # A positive, independently observed position from this initially flat
        # account is eligible for ONE reduce-only close. Never blind-retry an
        # unknown close and never overwrite unrelated account inventory.
        try:
            if broker.is_connected and opening_sent:
                pos = await scoped_position(exec_client)
                if pos > 0 and pos * price <= MAX_ORDER_USD * Decimal("1.1"):
                    close = await broker.submit_order_async(
                        asset=MARKET,
                        quantity=float(pos),
                        side=OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        reduce_only=True,
                        params={"orderLinkId": "CPMCLS" + secrets.token_hex(8)},
                    )
                    closing_order_id = close.order_id
                    report["closing_order_id"] = closing_order_id
                    report["order_mutations"] += 1
                    report["status"] = "CLOSE_ACK_RECONCILING"
                    write_report(run_dir, report)
                    closed = await await_fills(
                        exec_client, binding["native_id"], closing_order_id
                    )
                    report["closing_independent_executions"] = closed
                    if decimal(closed["filled_quantity"]) != pos:
                        raise RuntimeError("CLOSE_FILL_QUANTITY_MISMATCH")
                    if await scoped_position(exec_client) != 0:
                        raise RuntimeError("DEMO_NOT_FLAT_AFTER_CLOSE")
                    if await exec_client.fetch_demo_open_orders():
                        raise RuntimeError("DEMO_OPEN_ORDERS_AFTER_CLOSE")
                    if (
                        decimal(
                            report.get("opening_independent_executions", {}).get(
                                "filled_quantity", "0"
                            )
                        )
                        > 0
                    ):
                        report["status"] = "VERIFIED_DEMO_FILL_AND_FLAT"
                    else:
                        report["status"] = "FLAT_RECOVERED_BUT_OPEN_FILL_UNVERIFIED"
                elif pos == 0 and opening_sent:
                    if report["status"] == "OPEN_ACK_RECONCILING":
                        report["status"] = "NO_OPEN_POSITION_RECONCILE_ACK"
                else:
                    report["status"] = "HALT_POSITION_TOO_LARGE_OR_WRONG_SIDE"
        except BaseException as exc:
            report["close_error_type"] = type(exc).__name__
            report["status"] = "HALT_UNRESOLVED_BROKER_POSITION"
        finally:
            try:
                if broker.is_connected:
                    await broker.disconnect()
                else:
                    await exec_client.close()
            finally:
                write_report(run_dir, report)
    return {**report, "audit_dir": str(run_dir)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    flags = parser.add_mutually_exclusive_group()
    flags.add_argument(
        "--execute",
        action="store_true",
        help="EXPLICIT one-shot Bybit Demo order, then reduce-only close",
    )
    flags.add_argument(
        "--verify-audit",
        type=Path,
        help="Fresh read-only Bybit reconciliation of an existing canary report",
    )
    args = parser.parse_args()
    if args.verify_audit:
        result = asyncio.run(verify_existing(args.verify_audit))
    else:
        result = asyncio.run(run(execute=args.execute))
    print(json.dumps(result, sort_keys=True, indent=2))
    if result["status"] not in (
        "PREFLIGHT_ONLY",
        "VERIFIED_DEMO_FILL_AND_FLAT",
        "VERIFIED_FRESH_READ_ONLY_RECONCILIATION",
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
