"""Real copytrade-pm PM-03/Combo2 sizing -> Bybit shadow target contract.

Optional only when copytrade_pm is installed. No wallet networks or broker calls.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

pm = pytest.importorskip("copytrade_pm.pm_combo2")
from connectors.bybit.hyperliquid_portfolio import shadow_target_preview  # noqa: E402

Combo2PaperTracker = pm.Combo2PaperTracker
BTC = "hyperliquid/mainnet:perp:default/BTC"
BASE = datetime(2026, 11, 2, 10, tzinfo=UTC)
MANAGERS = {"W028": "a" * 64, "CA-008": "b" * 64, "W015": "c" * 64}


def at(seconds=0):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def quote():
    return {
        "first_known_at": at(300),
        "mark": "100000",
        "lot": "0.0000001",
        "minimum": "0.0000001",
        "volume_base": "1000",
        "fee_bps": "0",
        "spread_bps": "0",
        "slippage_bps": "0",
        "session_open": True,
        "supported": True,
        "native_market_id": "BTC-PERP",
    }


def source(manager, quantity):
    return {
        "manager": manager,
        "subject_sha": MANAGERS[manager],
        "quality": "OFFICIAL_ALL_DEXS_READ_ONLY",
        "full_book": True,
        "all_dexes_complete": True,
        "equity": "100000",
        "positions": {BTC: {"quantity": quantity, "mark": "100000"}},
        "event_at": at(300),
        "first_known_at": at(300),
    }


def actual_pm_report(tmp_path, positions, *, hedge="0", shocks=None):
    engine = Combo2PaperTracker(
        tmp_path,
        run_id="bybit-provenance-contract",
        started_at=at(),
        source_subject_hashes=MANAGERS,
    )
    for i, manager in enumerate(positions):
        engine.record(
            kind="SOURCE_RECEIPT",
            event_id=f"r{i}",
            source_event_at=at(),
            first_known_at=at(),
            decision_at=at(),
            hypothetical_fill_at=at(),
            data={"manager": manager, "instrument": BTC},
        )
    result = engine.record(
        kind="PAPER_REBALANCE",
        event_id="rebalance",
        source_event_at=at(),
        first_known_at=at(300),
        decision_at=at(300),
        hypothetical_fill_at=at(300),
        data={
            "leaders": {m: source(m, q) for m, q in positions.items()},
            "markets": {BTC: quote()},
            "shocks": [{BTC: "-0.01"}] if shocks is None else shocks,
            "hedge_break_loss": hedge,
        },
    )
    return result["meta"]["last_report"]


def bybit_market():
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


def bridge(report):
    return shadow_target_preview(
        report,
        {"BTC/USDT:USDT": bybit_market()},
        {"BTC/USDT:USDT": "80000"},
        {BTC: "100000"},
        gross_cap_usd=Decimal("5000"),
        coin_gross_cap_usd=Decimal("1250"),
    )


def test_true_pm_equity_sizing_and_coin_clamp_preserved_in_bybit_shadow(tmp_path):
    report = actual_pm_report(tmp_path, {"W028": "3"})
    # Canonical PM -- NOT bridge -- performs ($300k / $100k)*$1000.
    assert Decimal(report["desired"]["W028"][BTC]) == Decimal("0.03")
    assert Decimal(report["allowed"]["W028"][BTC]) == Decimal("0.0125")
    assert Decimal(report["coin_clamp_factors"][BTC]) == Decimal("1250") / Decimal(
        "3000"
    )
    plan = bridge(report)
    assert plan["status"] == "SHADOW_ONLY"
    assert Decimal(plan["gross_pre_net_source_usd"]) == Decimal("1250")
    target = plan["targets"][0]
    assert Decimal(target["target_quantity"]) == Decimal("0.015")
    assert Decimal(target["bybit_target_notional_usd"]) == Decimal("1200")
    assert plan["execution_authorized"] is False
    assert plan["order_deltas_computed"] is False


def test_true_pm_opposing_managers_gross_not_net_and_no_order(tmp_path):
    report = actual_pm_report(
        tmp_path,
        {"W028": "1", "W015": "-1"},
        hedge="1",
        shocks=[{}],
    )
    assert Decimal(report["coin_clamp_factors"][BTC]) == Decimal("0.625")
    assert Decimal(report["allowed"]["W028"][BTC]) == Decimal("0.00625")
    assert Decimal(report["allowed"]["W015"][BTC]) == Decimal("-0.00625")
    plan = bridge(report)
    assert plan["status"] == "SHADOW_ONLY"
    assert Decimal(plan["gross_pre_net_source_usd"]) == Decimal("1250")
    assert plan["targets"][0]["target_side"] == "flat"
    assert plan["targets"][0]["target_quantity"] == "0"
    assert plan["bybit_target_gross_usd"] == "0"
