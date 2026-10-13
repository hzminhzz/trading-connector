"""Credential-free deterministic shadow demo: python -m connectors.propr.examples.shadow_demo"""
from decimal import Decimal
from connectors.propr.execution import OrderRequest
from connectors.propr.instruments import Instrument, InstrumentRegistry
from connectors.propr.shadow import ShadowGateway

class DemoReady:
    def assert_opening_safe(self):
        return None

def main():
    registry = InstrumentRegistry([Instrument("BTC", "BTC", Decimal(".01"), Decimal(".01"), Decimal(".01"))])
    gateway = ShadowGateway(registry, DemoReady())
    for qty in (Decimal(".10"), Decimal(".001")):
        result = gateway.evaluate(OrderRequest("fake-account", "BTC", "buy", "long", qty))
        print(f"allowed={result.allowed} reason={result.reason} qty={result.quantity}")

if __name__ == "__main__":
    main()
