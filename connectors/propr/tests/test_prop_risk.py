import asyncio
from decimal import Decimal as D
import pytest

from connectors.propr.account import AccountSnapshot
from connectors.propr.execution import OrderRequest
from connectors.propr.instruments import Instrument, InstrumentRegistry
from connectors.propr.lifecycle import (
    FakeLifecycleGateway, FakeLifecycleVenue, NativePosition,
)
from connectors.propr.prop_risk import (
    PropLimits, PropRiskEvidence, PropRiskGate, SyntheticKillDrill,
)
from connectors.propr.state import IntentJournal


def limits():
    return PropLimits(
        "fake-account", frozenset({"BTC"}), D("10000"), D("5"),
        D("100"), D("200"), D("500"), D(".5"),
    )


def snapshot(*, equity=D("1000"), margin=D("100")):
    return AccountSnapshot(
        "fake-account", "fake-attempt", equity, D("1000"),
        D("900"), margin, D("-5"), D("120"), D("80"),
    )


def evidence(*, account=None, **overrides):
    data = {
        "account": account or snapshot(), "native_asset": "BTC",
        "market_price": D("100"), "gross_notional_before": D("100"),
        "leverage": D("2"), "margin_mode": "cross",
        "day_start_equity": D("1000"), "high_water_equity": D("1000"),
        "day_key": "2026-10-13:UTC", "market_fresh": True,
        "reconciled": True, "websocket_healthy": True,
        "challenge_rules_verified": True, "leverage_limit_verified": True,
        "existing_liquidation_prices_verified": True,
    }
    data.update(overrides)
    return PropRiskEvidence(**data)


def order():
    return OrderRequest("fake-account", "BTC", "buy", "long", D("1"))


def test_risk_passes_only_after_manual_arming(tmp_path):
    journal = IntentJournal(tmp_path / "risk.db")
    gate = PropRiskGate(limits(), journal)
    with pytest.raises(PermissionError, match="Unqualified"):
        gate.evaluate(order(), evidence())
    with pytest.raises(PermissionError):
        gate.reset(manual_approval=True, reconciled=False)
    gate.reset(manual_approval=True, reconciled=True)
    assert gate.evaluate(order(), evidence())
    assert ("risk_preflight", "passed") in journal.audit_history()
    journal.close()


@pytest.mark.parametrize("changed,reason", [
    ({"market_fresh": False}, "stale"),
    ({"websocket_healthy": False}, "stale"),
    ({"challenge_rules_verified": False}, "stale"),
    ({"market_price": None}, "Missing"),
    ({"leverage": D("6")}, "Leverage"),
    ({"native_asset": "xyz:AAPL"}, "Unapproved"),
    ({"day_key": None}, "daily reset"),
    ({"day_start_equity": D("1200")}, "Daily loss"),
    ({"high_water_equity": D("1300")}, "Drawdown"),
    ({"account": snapshot(equity=D("400"))}, "equity floor"),
    ({"gross_notional_before": D("9999")}, "exposure"),
    ({"reconciled": False}, "stale"),
    ({"leverage_limit_verified": False}, "stale"),
    ({"existing_liquidation_prices_verified": False}, "stale"),
    ({"margin_mode": None}, "Margin mode"),
    ({"gross_notional_before": None}, "Missing"),
    ({"account": snapshot(margin=D("600"))}, "Total margin budget"),
])
def test_risk_breaches_latch(tmp_path, changed, reason):
    journal = IntentJournal(tmp_path / "risk.db")
    gate = PropRiskGate(limits(), journal)
    gate.reset(manual_approval=True, reconciled=True)
    with pytest.raises(PermissionError, match=reason):
        gate.evaluate(order(), evidence(**changed))
    with pytest.raises(PermissionError):
        gate.evaluate(order(), evidence())
    journal.close()


def test_kill_drill_reports_ack_vs_confirmed(tmp_path):
    journal = IntentJournal(tmp_path / "risk.db")
    gate = PropRiskGate(limits(), journal)  # halted until manually reset
    registry = InstrumentRegistry([
        Instrument("BTC", "BTC", D(".01"), D(".01"), D(".01"))
    ])
    venue = FakeLifecycleVenue()
    gateway = FakeLifecycleGateway(journal, venue, registry)
    venue.positions["short-pos"] = NativePosition(
        "short-pos", "fake-account", "BTC", "short", D("2")
    )

    async def run():
        intent, _ = await gateway.submit(order())
        drill = SyntheticKillDrill(gate, journal, gateway)
        results = await drill.execute(
            order_intents=(intent.id, "missing"),
            reductions=(("short-pos", D("1")),),
        )
        assert len(results) == 3
        assert results[0].confirmed
        assert not results[1].confirmed
        assert not results[2].confirmed  # reduce order ACK is NOT verified flat
        assert "unconfirmed" in results[2].reason

    asyncio.run(run())
    assert ("kill_cancel", "terminal status verified") in journal.audit_history()
    journal.close()


def test_fake_only_kill_drill(tmp_path):
    journal = IntentJournal(tmp_path / "j.db")
    with pytest.raises(PermissionError):
        SyntheticKillDrill(PropRiskGate(limits()), journal, object())
    journal.close()
