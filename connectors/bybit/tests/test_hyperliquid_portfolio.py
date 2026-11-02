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
                "qtyStep": "0.001", "minOrderQty": "0.001",
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
    path.write_text(json.dumps({"schema": "hyperliquid_chd_76_universe_v1", "timeframe": "1m", "symbols": ["BTC"]}))
    with pytest.raises(ValueError, match="REGISTRY"):
        load_registry(path)


def test_partition_path_preserves_builder_namespace_and_utc(tmp_path):
    t = datetime(2026, 11, 1, 12, tzinfo=UTC)
    path = partition_path(tmp_path, "xyz:SP500", t)
    assert "HYPERLIQUID_XYZ_PERP/symbol=SP500/year=2026/month=11/day=01/data.parquet" in str(path)
    assert source_store(tmp_path, "BTC").name == "crypto_futures_ohlcv_1m_HYPERLIQUID_BTC_PERP"
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
    assert match_bybit_market("BTC", {"BTC/USDT:USDT": good})["reason"] == "BYBIT_MINIMUM_OR_PRECISION_UNATTESTED"


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
    assert match_bybit_market("xyz:SP500", markets)["reason"] == "HIP3_BUILDER_NOT_EQUIVALENT_TO_BYBIT"


def test_signed_portfolio_targets_shadow_only_and_uses_bybit_mark_not_hl_mark():
    market = {"BTC/USDT:USDT": btc_market()}
    targets = {
        source_instrument("BTC"): "1000",
        source_instrument("xyz:SP500"): "-100",
    }
    plan = shadow_target_preview(
        targets, market, {"BTC/USDT:USDT": "80000"}, gross_cap_usd=Decimal("1200")
    )
    assert plan["status"] == "SHADOW_ONLY"
    assert plan["execution_authorized"] is False
    assert len(plan["actions"]) == 1
    assert plan["actions"][0]["quantity"] == "0.012"
    assert plan["actions"][0]["bybit_reference_price"] == "80000"
    assert plan["skipped"] == [{
        "source_instrument": source_instrument("xyz:SP500"),
        "reason": "HIP3_BUILDER_NOT_EQUIVALENT_TO_BYBIT",
    }]
    assert plan["total_gross_usd"] == "960.000"


def test_gross_cap_fails_closed_and_never_emits_orders():
    plan = shadow_target_preview(
        {source_instrument("BTC"): "1500"},
        {"BTC/USDT:USDT": btc_market()},
        {"BTC/USDT:USDT": "80000"},
        gross_cap_usd=Decimal("500"),
    )
    assert plan["status"] == "BLOCKED_GROSS_CAP"
    assert plan["actions"] == []
    assert plan["execution_authorized"] is False


def test_bybit_missing_marks_fail_closed():
    with pytest.raises(ValueError, match="NONFINITE"):
        shadow_target_preview(
            {source_instrument("BTC"): "500"},
            {"BTC/USDT:USDT": btc_market()},
            {},
            gross_cap_usd=Decimal("1000"),
        )


def test_unsupported_symbol_is_skipped_not_mapped():
    plan = shadow_target_preview(
        {"hyperliquid/mainnet:perp:default/DONTEXIST": "200"},
        {"BTC/USDT:USDT": btc_market()}, {},
        gross_cap_usd=Decimal("1000"),
    )
    assert plan["actions"] == []
    assert plan["skipped"][0]["reason"] == "NOT_REGISTERED"


def test_historical_parquet_bar_is_valuation_only(tmp_path):
    pl = pytest.importorskip("polars")
    t = datetime(2026, 11, 1, 12, tzinfo=UTC)
    path = partition_path(tmp_path, "BTC", t)
    path.parent.mkdir(parents=True)
    pl.DataFrame({
        "timestamp": [t], "symbol": ["BTC"],
        "open": [100.0], "high": [105.0],
        "low": [99.0], "close": [104.0], "volume": [123.0],
    }).write_parquet(path)
    path.with_name("source.json").write_text(json.dumps({"status": "fetched_from_chd_trades"}))
    with pytest.raises(ValueError, match="UNFINALIZED"):
        read_historical_minute(tmp_path, "BTC", t, decision_at=t)
    bar = read_historical_minute(tmp_path, "BTC", t, decision_at=t + timedelta(minutes=1))
    assert bar.valuation_only is True
    assert bar.observed_at is None
    assert bar.close == "104.0"
    assert bar.execution_venue == "hyperliquid"
    path.with_name("source.json").write_text(json.dumps({"status": "legacy"}))
    with pytest.raises(ValueError, match="UNQUALIFIED"):
        read_historical_minute(tmp_path, "BTC", t, decision_at=t + timedelta(minutes=2))
