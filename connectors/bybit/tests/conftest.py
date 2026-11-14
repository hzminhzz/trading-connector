# pyright: reportMissingImports=false
# pyright: reportUnknownMemberType=false, reportUnknownParameterType=false
# pyright: reportUntypedFunctionDecorator=false, reportUnusedFunction=false
from __future__ import annotations

import os
import pytest


@pytest.fixture(autouse=True)
def _isolate_bybit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    for key in list(os.environ):
        if key.startswith("BYBIT_"):
            monkeypatch.delenv(key, raising=False)


def make_linear_perp_market(
    base: str,
    *,
    quote: str = "USDT",
    qty_step: str = "0.1",
    min_qty: str = "1",
    min_notional: str = "5",
    tick_size: str = "0.0001",
) -> dict[str, object]:
    symbol = f"{base}/{quote}:{quote}"
    return {
        "symbol": symbol,
        "id": f"{base}{quote}",
        "base": base,
        "quote": quote,
        "settle": quote,
        "linear": True,
        "swap": True,
        "active": True,
        "info": {
            "status": "Trading",
            "contractType": "LinearPerpetual",
            "lotSizeFilter": {
                "qtyStep": qty_step,
                "minOrderQty": min_qty,
                "minNotionalValue": min_notional,
            },
            "priceFilter": {"tickSize": tick_size},
        },
    }
