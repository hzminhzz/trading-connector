from decimal import Decimal as D
import pytest
from connectors.propr.instruments import Instrument, InstrumentRegistry
from connectors.propr.models import Position, one_way_positions, Order, OrderStatus
from connectors.propr.state import IntentJournal, UnknownOutcome
from connectors.propr.risk import RiskGate, RiskSnapshot

def test_case_sensitive_roundtrip():
    registry = InstrumentRegistry([Instrument("HIP3-AAPL", "xyz:AAPL", D(".01"), D(".01"), D(".01"))])
    assert registry.to_native("HIP3-AAPL") == "xyz:AAPL"
    assert registry.to_canonical("xyz:AAPL") == "HIP3-AAPL"
    with pytest.raises(KeyError):
        registry.to_canonical("xyz:aapl")

@pytest.mark.parametrize("quantity,price", [(D(".005"), D("2")), (D(".01"), D("2.005")), (D("0"), None)])
def test_precision_rejected(quantity, price):
    instrument = Instrument("BTC", "BTC", D(".01"), D(".01"), D(".01"))
    with pytest.raises(ValueError):
        instrument.validate(quantity, price)

def test_native_hedge_rejected_in_one_way():
    with pytest.raises(ValueError):
        one_way_positions([Position("1","BTC","long",D("1")), Position("2","BTC","short",D("1"))])

def test_fill_bounds():
    with pytest.raises(ValueError):
        Order("1",None,"BTC","buy","long",OrderStatus.PARTIAL,D("1"),D("2"))

def test_journal_recovery_freezes_openings(tmp_path):
    path = tmp_path / "journal.db"
    journal = IntentJournal(path)
    intent = journal.stage("acct", "BTC", "buy", "long", D("1"))
    journal.close()
    journal = IntentJournal(path)
    journal.recover()
    with pytest.raises(UnknownOutcome):
        journal.stage("acct", "BTC", "buy", "long", D("1"))
    journal.mark(intent.id, "reconciled")
    assert not journal.has_unknown()
    journal.close()

def test_risk_requires_manual_reset():
    gate = RiskGate(D("3"), D("100"))
    snapshot = RiskSnapshot(D("1000"), D("500"), D("2"), D("0"), True, True, True)
    with pytest.raises(PermissionError):
        gate.evaluate(snapshot)
    with pytest.raises(PermissionError):
        gate.reset(manual_approval=False, reconciled=True)
    gate.reset(manual_approval=True, reconciled=True)
    assert gate.evaluate(snapshot)
    with pytest.raises(PermissionError):
        gate.evaluate(RiskSnapshot(None, D("500"), D("2"), D("0"), True, True, True))
    with pytest.raises(PermissionError):
        gate.evaluate(snapshot)
