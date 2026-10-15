# pyright: reportMissingImports=false
# type: ignore
"""Deploy the exploratory 30-minute 50-bar breakout through ml4t-live."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from ml4t.backtest import Strategy
from ml4t.backtest.types import OrderType
from ml4t.live.safety import LiveRiskConfig, SafeBroker

from connectors.propr.ml4t_bridge import ProprLiveBroker
from propr_sdk import ProprClient

ASSET = "xyz:XYZ100"


def fetch_bars() -> list[dict[str, Any]]:
    """Yahoo ^NDX 30m bars are a reference proxy, not CME NQ prices."""
    response = httpx.get(
        "https://query1.finance.yahoo.com/v8/finance/chart/%5ENDX",
        params={"range": "60d", "interval": "30m"}, timeout=20.0,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    result = response.json()["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    return [
        {"timestamp": datetime.fromtimestamp(ts, timezone.utc),
         "open": float(o), "high": float(h), "low": float(l),
         "close": float(c), "volume": float(v or 0)}
        for ts, o, h, l, c, v in zip(
            result["timestamp"], quote["open"], quote["high"], quote["low"], quote["close"], quote["volume"]
        ) if None not in (o, h, l, c)
    ]


class OneBarNqStrategy(Strategy):
    """One-contract 50-complete-bar breakout; loss cap assumes $20/point."""
    def __init__(self, asset: str = ASSET, capital: float | None = None, submit_orders: bool = False):
        super().__init__()
        self.asset, self.capital, self.submit_orders = asset, capital, submit_orders
        self.bars: list[dict[str, float]] = []
        self.entries: list[Any] = []
        self.exit_order = None
        self.position_seen = False

    def on_data(self, timestamp, data, context, broker):
        bar = data.get(self.asset)
        if not bar:
            return
        bar = {key: float(bar[key]) for key in ("open", "high", "low", "close")}
        pos = broker.get_position(self.asset)
        qty = float(pos.quantity) if pos else 0.0
        if qty:
            self.position_seen = True
            if self.submit_orders and self.exit_order is None:
                self.exit_order = broker.submit_order(self.asset, -qty, order_type=OrderType.MARKET)
        elif self.position_seen:
            self.position_seen = False
            self.exit_order = None

        if not qty and len(self.bars) >= 50:
            upper = max(item["high"] for item in self.bars[-50:])
            lower = min(item["low"] for item in self.bars[-50:])
            print(f"[30m signal] {timestamp.isoformat()} close={bar['close']:.2f} 50-bar high={upper:.2f} low={lower:.2f}")
            if self.submit_orders:
                for order in self.entries:
                    if order is not None and getattr(getattr(order, "status", None), "value", None) == "pending":
                        broker.cancel_order(order.order_id)
                self.entries = [
                    broker.submit_order(self.asset, 1, order_type=OrderType.STOP, stop_price=upper),
                    broker.submit_order(self.asset, -1, order_type=OrderType.STOP, stop_price=lower),
                ]
        self.bars.append(bar)
        self.bars = self.bars[-50:]


async def run_deployment(mode="shadow", once=True, capital=None):
    if not once:
        raise RuntimeError("A validated XYZ100 30-minute live feed is not configured; use --once for the reference-proxy signal check.")
    bars = fetch_bars()
    if len(bars) < 51:
        raise RuntimeError(f"Need at least 51 complete 30-minute bars; received {len(bars)}")
    strategy = OneBarNqStrategy(capital=capital, submit_orders=(mode == "live"))
    risk = SafeBroker(ProprLiveBroker(client=ProprClient()), LiveRiskConfig(
        execution_mode=mode, state_file=Path(f".ml4t_one_bar_nq_{mode}.json"),
        max_order_value=1000.0, max_position_value=1000.0, max_total_exposure=1000.0,
        allowed_assets={ASSET, "NQ/USDC"},
    ))
    await risk.connect()
    try:
        from ml4t.live.wrappers import ThreadSafeBrokerWrapper
        broker = ThreadSafeBrokerWrapper(risk, asyncio.get_running_loop())
        for item in bars[:-1]:
            strategy.bars.append({key: item[key] for key in ("open", "high", "low", "close")})
            strategy.bars = strategy.bars[-50:]
        last = bars[-1]
        risk.record_market_snapshot(ASSET, last["close"])
        await asyncio.to_thread(strategy.on_data, last["timestamp"], {ASSET: last}, {}, broker)
        print("Shadow/live signal check complete.")
    finally:
        await risk.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Deploy 30m 50-bar XYZ100 proxy breakout")
    parser.add_argument("--mode", choices=("shadow", "live"), default="shadow")
    parser.add_argument("--once", action="store_true", help="Evaluate latest completed bar and exit")
    parser.add_argument("--capital", type=float, default=None)
    args = parser.parse_args()
    asyncio.run(run_deployment(mode=args.mode, once=True, capital=args.capital))


if __name__ == "__main__":
    main()
