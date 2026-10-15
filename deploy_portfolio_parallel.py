"""Evaluate four promoted strategies concurrently through one Propr broker."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deploy_donchian_btc import DonchianBTCStrategy, fetch_warmup_klines
from deploy_rsi_spy import RsiSpyStrategy, fetch_warmup_daily
from deploy_one_bar_nq import OneBarNqStrategy, fetch_bars
from deploy_mechanical_premia import MechanicalPremiaStrategy
from connectors.propr.ml4t_bridge import ProprLiveBroker
from propr_sdk import ProprClient
from ml4t.live.safety import LiveRiskConfig, SafeBroker
from ml4t.live.wrappers import ThreadSafeBrokerWrapper


async def run_portfolio(mode: str = "shadow", once: bool = True,
                        total_capital: float | None = None, interval: float = 60.0) -> None:
    if not once:
        raise RuntimeError("Continuous parallel execution requires completed-bar feeds for all assets; use --once")
    if total_capital is not None and total_capital <= 0:
        raise ValueError("--total-capital must be positive")
    capital = total_capital / 4 if total_capital is not None else None

    btc_df, spy_df, nq_bars = await asyncio.gather(
        asyncio.to_thread(fetch_warmup_klines, "BTCUSDT", 250),
        asyncio.to_thread(fetch_warmup_daily),
        asyncio.to_thread(fetch_bars),
    )
    btc = DonchianBTCStrategy(capital=capital)
    btc.warmup(btc_df)
    spy = RsiSpyStrategy(capital=capital)
    spy.warmup(spy_df)
    nq = OneBarNqStrategy(capital=capital, submit_orders=(mode == "live"))
    premia = MechanicalPremiaStrategy(capital=capital)

    safe = SafeBroker(ProprLiveBroker(client=ProprClient()), LiveRiskConfig(
        execution_mode=mode,
        state_file=Path(f".ml4t_portfolio_state_{mode}.json"),
        max_order_value=50_000.0,
        max_position_value=50_000.0,
        max_total_exposure=100_000.0,
        allowed_assets={"BTC/USDC", "BTC", "xyz:SP500", "SPY/USDC", "xyz:XYZ100", "NQ/USDC",
                        "xyz:SP500_H", "xyz:SP500_D", "xyz:XYZ100_H", "xyz:XYZ100_D"},
    ))
    await safe.connect()
    wrapper = ThreadSafeBrokerWrapper(safe, asyncio.get_running_loop())
    try:
        def bar_dict(row: Any) -> dict[str, float]:
            return {key: float(row[key]) for key in ("open", "high", "low", "close", "volume")}
        btc_time, spy_time = btc_df["timestamp"][-1], spy_df["timestamp"][-1]

        nq_time = nq_bars[-1]["timestamp"]
        def nq_timestamp(value: Any) -> datetime:
            return value if isinstance(value, datetime) else datetime.fromtimestamp(value / 1000, timezone.utc)
        nq_bar = nq_bars[-1]
        nq.bars = [{key: float(row[key]) for key in ("open", "high", "low", "close")}
                   for row in nq_bars[:-1][-50:]]
        btc_bar, spy_bar = bar_dict(btc_df.row(-1, named=True)), bar_dict(spy_df.row(-1, named=True))
        all_prices = {
            "BTC/USDC": btc_bar["close"], "BTC": btc_bar["close"],
            "xyz:SP500": spy_bar["close"], "SPY/USDC": spy_bar["close"],
            "xyz:XYZ100": float(nq_bar["close"]), "NQ/USDC": float(nq_bar["close"]),
        }
        for asset, price in all_prices.items():
            safe.record_market_snapshot(asset, price)

        async def evaluate(name: str, strategy: Any, timestamp: Any, data: dict[str, Any]) -> None:
            print(f"[{name}] evaluating")
            await asyncio.to_thread(strategy.on_data, timestamp, data, {}, wrapper)
            print(f"[{name}] complete")

        premia_data = {
            "xyz:SP500": {"close": spy_bar["close"], "signals": {}},
            "xyz:XYZ100": {"close": float(nq_bar["close"]), "signals": {}},
        }
        await asyncio.gather(
            evaluate("Donchian BTC", btc, btc_time, {"BTC/USDC": btc_bar}),
            evaluate("RSI SPY", spy, spy_time, {"xyz:SP500": spy_bar}),
            evaluate("One-bar NQ", nq, nq_time, {"xyz:XYZ100": nq_bar}),
            evaluate("Mechanical Premia", premia, nq_time, premia_data),
        )
        print("Portfolio pass complete.")
    finally:
        await safe.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("shadow", "live"), default="shadow")
    parser.add_argument("--once", action="store_true", help="Run one portfolio pass (default)")
    parser.add_argument("--total-capital", type=float)
    parser.add_argument("--interval", type=float, default=60.0, help="Reserved for continuous feed cadence")
    args = parser.parse_args()
    asyncio.run(run_portfolio(args.mode, True, args.total_capital, args.interval))


if __name__ == "__main__":
    main()
