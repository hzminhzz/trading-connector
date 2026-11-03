"""Fail-closed one-shot Bybit Demo canary guards; no live orders in CI."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from connectors.bybit.config import BybitConfig, Environment, Mode
from connectors.bybit.examples.demo_pm_canary import (
    assert_demo_config,
    claim_once,
    decimal,
    diagnostic_pm_report,
    fetch_trade_fills,
    reconcile_verified_fill,
    validate_demo_preflight,
    validate_demo_target,
)
from connectors.bybit.hyperliquid_portfolio import shadow_target_preview
from connectors.bybit.journal import BybitIntentJournal


def config(env=Environment.DEMO, mode=Mode.READ_ONLY):
    return BybitConfig(
        environment=env,
        mode=mode,
        api_key="synthetic-demo",
        api_secret="synthetic-secret",
        expected_demo_uid="synthetic-uid",
    )


def mapping():
    return {
        "mapping": "MATCHED_RESEARCH",
        "native_id": "XRPUSDT",
        "market": "XRP/USDT:USDT",
        "min_notional": "5",
        "min_qty": "1",
        "qty_step": "0.1",
    }


def preflight_kwargs():
    return dict(
        identity={
            "environment": "demo",
            "private_rest": "api-demo.bybit.com",
            "authenticated_uid_matches_expected": True,
            "dedicated_demo_key_matches": True,
            "unified_account_verified": True,
            "key_is_read_only": False,
        },
        positions=[],
        orders=[],
        account=SimpleNamespace(
            equity_usd=Decimal("10000"), available_usd=Decimal("1000")
        ),
        market=mapping(),
        last=Decimal("1.25"),
        ticker_timestamp=None,
        now=datetime(2026, 11, 3, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    "env,mode",
    [
        (Environment.TESTNET, Mode.READ_ONLY),
        (Environment.MAINNET, Mode.READ_ONLY),
        (Environment.DEMO, Mode.EXECUTION),
    ],
)
def test_default_environment_and_mode_must_fail_closed(env, mode):
    with pytest.raises(PermissionError, match="BYBIT_DEMO_READ_ONLY"):
        assert_demo_config(config(env, mode))


def test_demo_identity_and_global_flat_safety_guards():
    payload = preflight_kwargs()
    assert validate_demo_preflight(**payload)["key_is_read_only"] is False
    payload["positions"] = [object()]
    with pytest.raises(PermissionError, match="NOT_INITIAL_FLAT"):
        validate_demo_preflight(**payload)
    payload = preflight_kwargs()
    payload["identity"]["private_rest"] = "api.bybit.com"
    with pytest.raises(PermissionError, match="NOT_ATTESTED"):
        validate_demo_preflight(**payload)


def test_durable_daily_claim_is_not_reusable(tmp_path):
    claim_once(tmp_path, "safehash", "2026-11-03")
    with pytest.raises(FileExistsError):
        claim_once(tmp_path, "safehash", "2026-11-03")


def test_pm_real_engine_produces_bounded_demo_target(tmp_path):
    pytest.importorskip("copytrade_pm.pm_combo2")
    report = diagnostic_pm_report(tmp_path / "pm", Decimal("1.25"))
    assert report["hypothetical"] is True
    assert report["authorized_for_live"] is False
    plan = shadow_target_preview(
        report,
        {
            "XRP/USDT:USDT": {
                "symbol": "XRP/USDT:USDT",
                "id": "XRPUSDT",
                "base": "XRP",
                "quote": "USDT",
                "settle": "USDT",
                "linear": True,
                "swap": True,
                "active": True,
                "info": {
                    "status": "Trading",
                    "contractType": "LinearPerpetual",
                    "lotSizeFilter": {
                        "qtyStep": "0.1",
                        "minOrderQty": "1",
                        "minNotionalValue": "5",
                    },
                    "priceFilter": {"tickSize": "0.0001"},
                },
            }
        },
        {"XRP/USDT:USDT": "1.25"},
        {"hyperliquid/mainnet:perp:default/XRP": "1.25"},
        gross_cap_usd=Decimal("5000"),
        coin_gross_cap_usd=Decimal("1250"),
    )
    assert plan["status"] == "SHADOW_ONLY"
    target = validate_demo_target(plan, mapping())
    assert Decimal("14") <= Decimal(target["reference_notional"]) <= Decimal("15")
    assert Decimal(target["quantity"]) == Decimal("12")
    assert plan["execution_authorized"] is False


def test_target_notional_above_25_is_rejected():
    plan = {
        "status": "SHADOW_ONLY",
        "execution_authorized": False,
        "order_deltas_computed": False,
        "skipped": [],
        "gross_pre_net_source_usd": "30",
        "targets": [
            {
                "source_instrument": "hyperliquid/mainnet:perp:default/XRP",
                "bybit_market": "XRP/USDT:USDT",
                "kind": "SHADOW_TARGET_POSITION_NOT_ORDER",
                "target_side": "long",
                "target_quantity": "21",
                "bybit_reference_price": "1.25",
            }
        ],
    }
    with pytest.raises(ValueError, match="SIZE_OUT_OF_RANGE"):
        validate_demo_target(plan, mapping())


def test_independent_fill_list_ignores_unrelated_order_ids():
    client = MagicMock()
    client.exchange.private_get_v5_execution_list = AsyncMock(
        return_value={
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "orderId": "expected",
                        "execId": "ok",
                        "execQty": "10",
                        "execPrice": "1.25",
                    },
                    {
                        "orderId": "other",
                        "execId": "no",
                        "execQty": "100",
                        "execPrice": "1.25",
                    },
                ]
            },
        }
    )
    client._v5_result = lambda value, _: value["result"]
    result = asyncio.run(fetch_trade_fills(client, "XRPUSDT", "expected"))
    assert result["execution_rows"] == 1
    assert result["filled_quantity"] == "10"


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"nextPageCursor": "another-page"}, "CURSOR_INVALID"),
        (
            {
                "list": [
                    {
                        "orderId": "expected",
                        "execId": "same",
                        "execQty": "1",
                        "execPrice": "1.25",
                    },
                    {
                        "orderId": "expected",
                        "execId": "same",
                        "execQty": "1",
                        "execPrice": "1.25",
                    },
                ]
            },
            "IDS_MISSING_OR_DUPLICATED",
        ),
        (
            {
                "list": [
                    {
                        "orderId": "expected",
                        "execId": "",
                        "execQty": "1",
                        "execPrice": "1.25",
                    },
                ]
            },
            "IDS_MISSING_OR_DUPLICATED",
        ),
    ],
)
def test_independent_execution_rejects_truncated_or_duplicate_evidence(extra, expected):
    client = MagicMock()
    client.exchange.private_get_v5_execution_list = AsyncMock(
        return_value={
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "orderId": "expected",
                        "execId": "one",
                        "execQty": "2",
                        "execPrice": "1.25",
                    }
                ],
                **extra,
            },
        }
    )
    client._v5_result = lambda value, _: value["result"]
    with pytest.raises(RuntimeError, match=expected):
        asyncio.run(fetch_trade_fills(client, "XRPUSDT", "expected"))


def test_independent_execution_reads_all_pages_before_certifying_fill():
    client = MagicMock()
    client.exchange.private_get_v5_execution_list = AsyncMock(
        side_effect=[
            {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "orderId": "expected",
                            "execId": "fill-1",
                            "execQty": "5",
                            "execPrice": "1.25",
                        }
                    ],
                    "nextPageCursor": "next",
                },
            },
            {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "orderId": "unrelated",
                            "execId": "another",
                            "execQty": "100",
                            "execPrice": "1.25",
                        },
                        {
                            "orderId": "expected",
                            "execId": "fill-2",
                            "execQty": "7",
                            "execPrice": "1.26",
                        },
                    ],
                    "nextPageCursor": "",
                },
            },
        ]
    )
    client._v5_result = lambda value, _: value["result"]
    result = asyncio.run(fetch_trade_fills(client, "XRPUSDT", "expected"))
    assert result["execution_rows"] == 2
    assert result["pages_scanned"] == 2
    assert result["filled_quantity"] == "12"
    requests = client.exchange.private_get_v5_execution_list.call_args_list
    assert requests[0].args[0]["orderId"] == "expected"
    assert requests[0].args[0].get("cursor") is None
    assert requests[1].args[0]["cursor"] == "next"


def test_intent_journal_only_settles_after_exact_independent_execution(tmp_path):
    journal = BybitIntentJournal(tmp_path / "intent.sqlite")
    record = journal.stage(
        asset="XRP/USDT:USDT",
        side="buy",
        quantity=12.0,
        order_type="market",
    )
    journal.mark_submitted(record.id, "v5-order-1")
    with pytest.raises(ValueError, match="QUANTITY_NOT_EXACT"):
        reconcile_verified_fill(journal, "v5-order-1", Decimal("11"))
    assert (
        journal.connection.execute("SELECT state FROM intents").fetchone()[0]
        == "submitted"
    )
    reconcile_verified_fill(journal, "v5-order-1", Decimal("12"))
    reconcile_verified_fill(journal, "v5-order-1", Decimal("12"))
    assert (
        journal.connection.execute("SELECT state FROM intents").fetchone()[0]
        == "filled"
    )
    with pytest.raises(ValueError, match="NOT_UNIQUE"):
        reconcile_verified_fill(journal, "nonexistent-order", Decimal("12"))
    journal.close()


def test_invalid_decimal_fails_closed():
    with pytest.raises(ValueError, match="NONFINITE"):
        decimal("NaN")
