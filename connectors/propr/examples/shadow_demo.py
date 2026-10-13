"""Credential-free deterministic shadow demo: python -m connectors.propr.examples.shadow_demo"""
from decimal import Decimal
from pathlib import Path
import tempfile

from connectors.propr.execution import OrderRequest
from connectors.propr.instruments import Instrument, InstrumentRegistry
from connectors.propr.shadow import ShadowGateway
from connectors.propr.state import IntentJournal


class DemoReady:
    def assert_opening_safe(self):
        return None


def main():
    registry = InstrumentRegistry([
        Instrument("BTC", "BTC", Decimal(".01"), Decimal(".01"), Decimal(".01"))
    ])
    with tempfile.TemporaryDirectory() as temporary:
        journal = IntentJournal(Path(temporary) / "shadow.db")
        try:
            gateway = ShadowGateway(registry, DemoReady(), journal=journal)
            for qty in (Decimal(".10"), Decimal(".001")):
                decision = gateway.evaluate(OrderRequest(
                    "fake-account", "BTC", "buy", "long", qty
                ))
                print(
                    f"allowed={decision.allowed} reason={decision.reason} "
                    f"qty={decision.quantity}"
                )
            assert len(journal.audit_history()) == 2
        finally:
            journal.close()


if __name__ == "__main__":
    main()
