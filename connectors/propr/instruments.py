"""Explicit reversible instrument registry; no guessed asset formatting."""
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class Instrument:
    canonical: str
    native: str
    lot: Decimal
    tick: Decimal
    minimum: Decimal

    def __post_init__(self):
        if not self.canonical or not self.native or min(self.lot, self.tick, self.minimum) <= 0:
            raise ValueError("Invalid instrument metadata")

    def validate(self, quantity: Decimal, price: Decimal | None = None):
        if quantity < self.minimum or quantity % self.lot:
            raise ValueError("Invalid quantity precision or minimum")
        if price is not None and (price <= 0 or price % self.tick):
            raise ValueError("Invalid price precision")

class InstrumentRegistry:
    def __init__(self, instruments):
        self.by_canonical = {}
        self.by_native = {}
        for item in instruments:
            if item.canonical in self.by_canonical or item.native in self.by_native:
                raise ValueError("Duplicate symbol mapping")
            self.by_canonical[item.canonical] = item
            self.by_native[item.native] = item

    def to_native(self, canonical):
        return self.by_canonical[canonical].native

    def to_canonical(self, native):
        return self.by_native[native].canonical
