# pyright: reportMissingImports=false
# type: ignore
"""Deploy Mechanical Premia dual-index sleeves through ML4T Live."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any

from ml4t.backtest import Strategy
from ml4t.live import LiveEngine
from ml4t.live.safety import LiveRiskConfig, SafeBroker

from connectors.propr.ml4t_bridge import ProprLiveBroker
from propr_sdk import ProprClient

ASSETS = {"US500": "xyz:SP500", "US100": "xyz:XYZ100"}
H1_SHARE, D1_SHARE = 0.2336, 0.7664


class MechanicalPremiaStrategy(Strategy):
    """Combined H1/D1 breakout sleeves with nominal ATR-based risk budgets."""

    def __init__(self, capital: float | None = None):
        super().__init__()
        self.capital = capital
        self.last_allocations: dict[str, float] = {}
        self.decisions = 0

    def on_data(self, timestamp: datetime, data: dict[str, Any], context: dict[str, Any], broker: Any) -> None:
        equity_fn = getattr(broker, "equity", None)
        equity = float(equity_fn() if callable(equity_fn) else broker.get_account_value())
        if self.capital is not None:
            equity = min(equity, self.capital)
        if not math.isfinite(equity) or equity <= 0:
            return
        reserved = 0.0
        # A native symbol per sleeve preserves independent D1 and H1 positions.
        for canonical, asset in ASSETS.items():
            bar = data.get(asset) or data.get(canonical) or {}
            close = float(bar.get("close", 0.0))
            if not math.isfinite(close) or close <= 0:
                continue
            for sleeve, share, multiplier in (("H", H1_SHARE, 2.0), ("D", D1_SHARE, 1.0)):
                key = f"{asset}_{sleeve}"
                signal = bar.get("signals", {})
                position = broker.get_position(key)
                qty = float(position.quantity) if position else 0.0
                atr_key = "last_h_atr" if sleeve == "H" else "last_d_atr"
                entry_key = "h_enter" if sleeve == "H" else "d_enter"
                exit_key = "d_exit"
                if qty > 0:
                    if sleeve == "D" and signal.get(exit_key, False):
                        broker.submit_order(key, -qty)
                        self.decisions += 1
                    continue
                atr = signal.get(atr_key)
                if not signal.get(entry_key, False) or atr is None or float(atr) <= 0:
                    continue
                allocation = share / 2
                distance = float(atr) * multiplier
                budget = max(0.0, min(equity * 0.01 * allocation,
                                      equity * 0.01 - reserved))
                quantity = min(budget / distance, (1.90 / 4) * equity / close)
                if quantity <= 0:
                    continue
                self.last_allocations[key] = budget
                order = broker.submit_order(key, quantity)
                if order is not None:
                    reserved += quantity * distance
                    self.decisions += 1
                    print(f"  {key}: risk=${budget:.2f} ({allocation:.2%} of 1% total), ATR distance={distance:.4f}, rebalance=+{quantity:.6f}")


async def run_deployment(mode: str = "shadow", once: bool = True, capital: float | None = None) -> None:
    strategy = MechanicalPremiaStrategy(capital)
    raw_broker = ProprLiveBroker(client=ProprClient())
    safe = SafeBroker(raw_broker, LiveRiskConfig(
        execution_mode=mode,
        state_file=Path(f".ml4t_mechanical_premia_{mode}.json"),
        max_order_value=50000.0, max_position_value=50000.0,
        max_total_exposure=100000.0,
        allowed_assets={f"{asset.replace('xyz:', 'XYZ:')}_{sleeve}" for asset in ASSETS.values() for sleeve in ("H", "D")},
    ))
    if once:
        await safe.connect()
        try:
            # Deterministic one-shot harness: completed-bar prices and positive
            # ATRs exercise each sleeve's sizing/rebalance path without claiming
            # to be live market signals.
            timestamp = datetime.now(timezone.utc)
            data = {
                asset: {"close": close, "signals": {
                    "h_enter": True, "d_enter": True,
                    "last_h_atr": close * 0.01, "last_d_atr": close * 0.02,
                }}
                for asset, close in zip(ASSETS.values(), (5000.0, 18000.0))
            }
            for asset, bar in data.items():
                safe.record_market_snapshot(f"{asset}_H", bar["close"])
                safe.record_market_snapshot(f"{asset}_D", bar["close"])
            from ml4t.live.wrappers import ThreadSafeBrokerWrapper
            wrapped = ThreadSafeBrokerWrapper(safe, asyncio.get_running_loop())
            await asyncio.to_thread(strategy.on_data, timestamp, data, {}, wrapped)
            print(f"Shadow evaluation complete: {strategy.decisions} sleeve rebalance(s); allocations={strategy.last_allocations}")
        finally:
            await safe.disconnect()
        return
    raise RuntimeError("Continuous mode requires a certified xyz:SP500/xyz:XYZ100 completed-bar feed; no such feed is configured")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy Mechanical Premia US500/US100 sleeves")
    parser.add_argument("--mode", choices=("shadow", "live"), default="shadow")
    parser.add_argument("--once", action="store_true", help="Evaluate a deterministic one-shot shadow rebalance")
    parser.add_argument("--capital", type=float, default=None, help="Cap risk sizing at this equity")
    args = parser.parse_args()
    asyncio.run(run_deployment(args.mode, once=True, capital=args.capital))


if __name__ == "__main__":
    main()
