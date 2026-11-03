"""Credential-free 76-market Hyperliquid research -> Bybit compatibility tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json

import pytest

from connectors.bybit.hyperliquid_portfolio import (
    coverage,
    inventory,
    load_registry,
    match_bybit_market,
    partition_path,
    read_historical_minute,
    shadow_target_preview,
    source_instrument,
    source_store,
)


def btc_market():
    return {
        "symbol": "BTC/USDT:USDT",
        "id": "BTCUSDT",
        "base": "BTC",
        "quote": "USDT",
        "settle": "USDT",
        "linear": True,
        "swap": True,
        "active": True,
        "info": {
            "status": "Trading",
            "contractType": "LinearPerpetual",
            "lotSizeFilter": {
                "qtyStep": "0.001",
                "minOrderQty": "0.001",
                "minNotionalValue": "5",
            },
            "priceFilter": {"tickSize": "0.1"},
        },
    }


def test_registry_pins_76_exact_50_plus_26():
    syms = load_registry()
    assert len(syms) == 76
    assert len([x for x in syms if x.startswith("xyz:")]) == 26
    assert len([x for x in syms if not x.startswith("xyz:")]) == 50
    assert source_instrument("BTC") == "hyperliquid/mainnet:perp:default/BTC"
    assert source_instrument("xyz:SP500") == "hyperliquid/mainnet:perp:xyz/SP500"
    with pytest.raises(ValueError, match="FROZEN"):
        source_instrument("xyz:AAPL")


def test_wrong_registry_rejected(tmp_path):
    path = tmp_path / "reg.json"
    path.write_text(
        json.dumps(
            {
                "schema": "hyperliquid_chd_76_universe_v1",
                "timeframe": "1m",
                "symbols": ["BTC"],
            }
        )
    )
    with pytest.raises(ValueError, match="REGISTRY"):
        load_registry(path)


def test_partition_path_preserves_builder_namespace_and_utc(tmp_path):
    t = datetime(2026, 11, 1, 12, tzinfo=UTC)
    path = partition_path(tmp_path, "xyz:SP500", t)
    assert (
        "HYPERLIQUID_XYZ_PERP/symbol=SP500/year=2026/month=11/day=01/data.parquet"
        in str(path)
    )
    assert (
        source_store(tmp_path, "BTC").name
        == "crypto_futures_ohlcv_1m_HYPERLIQUID_BTC_PERP"
    )
    with pytest.raises(ValueError, match="UTC_AWARE"):
        partition_path(tmp_path, "BTC", t.replace(tzinfo=None))
    with pytest.raises(ValueError, match="ONE_MINUTE"):
        partition_path(tmp_path, "BTC", t + timedelta(seconds=10))


def test_inventory_is_frozen_not_accidental_expanded_store(tmp_path):
    source_store(tmp_path, "BTC").mkdir()
    (tmp_path / "crypto_futures_ohlcv_1m_HYPERLIQUID_RANDOM_PERP").mkdir()
    result = inventory(tmp_path)
    assert result["registered"] == 76
    assert result["store_directories_present"] == 1
    assert result["missing"][0] != "BTC"
    assert result["bybit_execution_authorized"] is False


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("active", False),
        ("id", "BTCUSDC"),
        ("base", "1000BTC"),
        ("linear", False),
        ("swap", False),
        ("settle", "USDC"),
    ],
)
def test_bybit_market_is_exact_and_active(key, bad):
    good = btc_market()
    good[key] = bad
    result = match_bybit_market("BTC", {"BTC/USDT:USDT": good})
    assert result["mapping"] == "BLOCKED"
    assert result["order_authorized"] is False


@pytest.mark.parametrize(
    ("field", "bad"),
    [("status", "PreLaunch"), ("contractType", "LinearFutures")],
)
def test_bybit_instrument_status_contract_type_verified(field, bad):
    good = btc_market()
    good["info"][field] = bad
    assert match_bybit_market("BTC", {"BTC/USDT:USDT": good})["mapping"] == "BLOCKED"


def test_bybit_lot_precision_not_guessed():
    good = btc_market()
    del good["info"]["lotSizeFilter"]["qtyStep"]
    assert (
        match_bybit_market("BTC", {"BTC/USDT:USDT": good})["reason"]
        == "BYBIT_MINIMUM_OR_PRECISION_UNATTESTED"
    )


def test_builder_does_not_alias_bybit_equity_or_indices():
    markets = {
        "SP500/USDT:USDT": btc_market(),
        "BTC/USDT:USDT": btc_market(),
    }
    result = coverage(markets)
    assert result["registered"] == 76
    assert result["bybit_research_matches"] == 1
    assert result["blocked"] == 75
    assert result["account_challenge_rules_verified"] is False
    assert (
        match_bybit_market("xyz:SP500", markets)["reason"]
        == "HIP3_BUILDER_NOT_EQUIVALENT_TO_BYBIT"
    )


def pm_report(allowed, *, status="ACTIVE_PAPER", partial_book=False, risk_reason=None):
    """PM-03/Combo2 last_report shape: per-manager SOURCE BASE quantities."""
    return {
        "status": status,
        "risk_reason": risk_reason,
        "hypothetical": True,
        "authorized_for_live": False,
        "partial_book": partial_book,
        "skipped_unlisted": [],
        "allowed": {"W028": {}, "CA-008": {}, "W015": {}, **allowed},
    }


def preview(
    report, *, marks=None, markets=None, bybit_prices=None, cap="5000", coin_cap="1250"
):
    return shadow_target_preview(
        report,
        {"BTC/USDT:USDT": btc_market()} if markets is None else markets,
        {"BTC/USDT:USDT": "80000"} if bybit_prices is None else bybit_prices,
        {source_instrument("BTC"): "100000"} if marks is None else marks,
        gross_cap_usd=Decimal(cap),
        coin_gross_cap_usd=Decimal(coin_cap),
    )


def test_pm_100k_equity_300k_btc_sized_to_1250_uses_bybit_mark():
    # Golden PM output: 3 BTC * $100k / $100k * $1000 = $3000,
    # capped by PM to $1250 -> 0.0125 BTC, NOT a new Bybit sizing rule.
    report = pm_report({"W028": {source_instrument("BTC"): "0.0125"}})
    plan = preview(report)
    assert plan["status"] == "SHADOW_ONLY"
    assert plan["gross_pre_net_source_usd"] == "1250.0000"
    assert plan["source_manager_gross_usd"]["W028"] == "1250.0000"
    assert plan["execution_authorized"] is False
    assert plan["order_deltas_computed"] is False
    assert len(plan["targets"]) == 1
    target = plan["targets"][0]
    assert target["kind"] == "SHADOW_TARGET_POSITION_NOT_ORDER"
    assert target["source_net_notional_usd"] == "1250.0000"
    assert target["target_quantity"] == "0.015"
    assert target["bybit_target_notional_usd"] == "1200.000"
    assert target["notional_tracking_error_usd"] == "50.0000"


def test_opposing_managers_pre_net_gross_even_when_net_zero():
    # $625 long + $625 short = $1250 gross, zero external target.
    report = pm_report(
        {
            "W028": {source_instrument("BTC"): "0.00625"},
            "W015": {source_instrument("BTC"): "-0.00625"},
        }
    )
    plan = preview(report)
    assert plan["status"] == "SHADOW_ONLY"
    assert len(plan["targets"]) == 1
    assert plan["targets"][0]["target_side"] == "flat"
    assert plan["targets"][0]["target_quantity"] == "0"
    assert plan["order_deltas_computed"] is False
    assert plan["bybit_target_gross_usd"] == "0"
    assert plan["gross_pre_net_source_usd"] == "1250.00000"
    assert plan["source_manager_gross_usd"]["W015"] == "625.00000"
    too_small_cap = preview(report, coin_cap="1000")
    assert too_small_cap["status"] == "BLOCKED_PRE_NET_GROSS_CAP"
    assert too_small_cap["targets"] == []


def test_explicit_zero_manager_book_emits_flat_target_without_price():
    # Regression: skipping zero quantities hid a target-state flat/close.
    instrument = source_instrument("BTC")
    plan = preview(
        pm_report({"W028": {instrument: "0"}, "W015": {instrument: "0"}}),
        marks={},
        bybit_prices={},
    )
    assert plan["status"] == "SHADOW_ONLY"
    assert plan["gross_pre_net_source_usd"] == "0"
    assert len(plan["targets"]) == 1
    assert plan["targets"][0]["target_side"] == "flat"
    assert plan["targets"][0]["target_quantity"] == "0"
    assert plan["targets"][0]["source_manager_notionals_usd"] == {
        "W015": "0",
        "W028": "0",
    }
    assert plan["order_deltas_computed"] is False


def test_explicit_zero_unknown_instrument_not_silently_skipped():
    invalid = "hyperliquid/mainnet:perp:default/NONEXISTENT"
    plan = preview(pm_report({"W028": {invalid: "0"}}), marks={}, bybit_prices={})
    assert plan["status"] == "BLOCKED_INCOMPLETE_BYBIT_BOOK"
    assert plan["targets"] == []
    assert plan["skipped"] == [
        {"source_instrument": invalid, "reason": "NOT_REGISTERED"}
    ]


def test_manager_attribution_preserved_before_net_and_bybit_rounding():
    report = pm_report(
        {
            "W028": {source_instrument("BTC"): "0.0075"},
            "CA-008": {source_instrument("BTC"): "-0.005"},
        }
    )
    plan = preview(report)
    assert plan["status"] == "SHADOW_ONLY"
    assert plan["gross_pre_net_source_usd"] == "1250.0000"
    assert plan["source_coin_gross_usd"][source_instrument("BTC")] == "1250.0000"
    target = plan["targets"][0]
    assert target["source_manager_notionals_usd"] == {
        "CA-008": "-500.000",
        "W028": "750.0000",
    }
    assert target["source_net_notional_usd"] == "250.0000"
    assert target["target_quantity"] == "0.003"
    assert target["bybit_target_notional_usd"] == "240.000"


def test_unmapped_opposing_hedges_do_not_silently_disappear():
    symbol = source_instrument("xyz:SP500")
    report = pm_report(
        {
            "W028": {symbol: "1"},
            "W015": {symbol: "-1"},
        }
    )
    plan = preview(report, marks={symbol: "100"})
    assert plan["gross_pre_net_source_usd"] == "200"
    assert plan["status"] == "BLOCKED_INCOMPLETE_BYBIT_BOOK"
    assert plan["targets"] == []


def test_total_gross_cap_precedes_netted_bybit_positions():
    # Across two supported assets, pre-net gross > account cap even though
    # source net targets may be small. Do not divide by Bybit quote first.
    btc, eth = source_instrument("BTC"), source_instrument("ETH")
    report = pm_report(
        {
            "W028": {btc: "0.009", eth: "0.3"},
            "CA-008": {btc: "-0.008", eth: "-0.2"},
        }
    )
    plan = preview(
        report,
        marks={btc: "100000", eth: "10000"},
        cap="5000",
        coin_cap="6000",
    )
    assert plan["gross_pre_net_source_usd"] == "6700.000"
    assert plan["status"] == "BLOCKED_PRE_NET_GROSS_CAP"
    assert plan["targets"] == []


def test_gross_cap_fails_closed_before_bybit_rounding():
    report = pm_report({"W028": {source_instrument("BTC"): "0.015"}})
    plan = preview(report, cap="1250")
    assert plan["status"] == "BLOCKED_PRE_NET_GROSS_CAP"
    assert plan["targets"] == []
    assert plan["gross_pre_net_source_usd"] == "1500.000"
    assert plan["execution_authorized"] is False


def test_partial_book_never_suggests_tradeable_subset():
    report = pm_report(
        {
            "W028": {source_instrument("BTC"): "0.005"},
            "W015": {source_instrument("xyz:SP500"): "0.001"},
        }
    )
    source_marks = {
        source_instrument("BTC"): "100000",
        source_instrument("xyz:SP500"): "100000",
    }
    plan = preview(report, marks=source_marks)
    assert plan["status"] == "BLOCKED_INCOMPLETE_BYBIT_BOOK"
    assert plan["targets"] == []
    assert plan["skipped"][0]["reason"] == "HIP3_BUILDER_NOT_EQUIVALENT_TO_BYBIT"
    assert plan["gross_pre_net_source_usd"] == "600.000"


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"status": "FROZEN"}, "BLOCKED_PM_RISK"),
        ({"risk_reason": "MODELED_STRESS"}, "BLOCKED_PM_RISK"),
        ({"partial_book": True}, "BLOCKED_PM_PARTIAL_BOOK"),
        ({"skipped_unlisted": ["MISSING"]}, "BLOCKED_PM_PARTIAL_BOOK"),
    ],
)
def test_pm_freeze_or_partial_never_exposes_targets(changes, status):
    report = pm_report({"W028": {source_instrument("BTC"): "0.005"}})
    report.update(changes)
    plan = preview(report)
    assert plan["status"] == status
    assert plan["targets"] == []


def test_reject_net_only_targets_missing_manager_attribution():
    with pytest.raises(ValueError, match="MANAGER_ALLOWED_BOOK"):
        preview({source_instrument("BTC"): "500"})


def test_bybit_missing_marks_fail_closed():
    report = pm_report({"W028": {source_instrument("BTC"): "0.005"}})
    with pytest.raises(ValueError, match="MISSING_BYBIT"):
        preview(report, bybit_prices={})
    with pytest.raises(ValueError, match="NONFINITE"):
        preview(report, marks={})


def test_unsupported_symbol_is_blocked_not_mapped():
    inst = "hyperliquid/mainnet:perp:default/DONTEXIST"
    report = pm_report({"W028": {inst: "0.01"}})
    plan = preview(report)
    assert plan["status"] == "BLOCKED_INCOMPLETE_BYBIT_BOOK"
    assert plan["targets"] == []
    assert plan["skipped"][0]["reason"] == "NOT_REGISTERED"


def test_historical_parquet_bar_is_valuation_only(tmp_path):
    pl = pytest.importorskip("polars")
    t = datetime(2026, 11, 1, 12, tzinfo=UTC)
    path = partition_path(tmp_path, "BTC", t)
    path.parent.mkdir(parents=True)
    pl.DataFrame(
        {
            "timestamp": [t],
            "symbol": ["BTC"],
            "open": [100.0],
            "high": [105.0],
            "low": [99.0],
            "close": [104.0],
            "volume": [123.0],
        }
    ).write_parquet(path)
    path.with_name("source.json").write_text(
        json.dumps({"status": "fetched_from_chd_trades"})
    )
    with pytest.raises(ValueError, match="UNFINALIZED"):
        read_historical_minute(tmp_path, "BTC", t, decision_at=t)
    bar = read_historical_minute(
        tmp_path, "BTC", t, decision_at=t + timedelta(minutes=1)
    )
    assert bar.valuation_only is True
    assert bar.observed_at is None
    assert bar.close == "104.0"
    assert bar.execution_venue == "hyperliquid"
    path.with_name("source.json").write_text(json.dumps({"status": "legacy"}))
    with pytest.raises(ValueError, match="UNQUALIFIED"):
        read_historical_minute(tmp_path, "BTC", t, decision_at=t + timedelta(minutes=2))
