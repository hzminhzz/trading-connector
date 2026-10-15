# pyright: reportMissingImports=false
# type: ignore
"""Deploy Donchian BTC Volatility-Targeted Trend Strategy via ML4T Live & Propr.

Integrates the promoted strategy from quant-research/promote/donchian_btc
with ml4t-live SafeBroker, LiveEngine, and ProprLiveBroker.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import math
import sys
from pathlib import Path
from typing import Any

import httpx
import polars as pl
from ml4t.backtest import Strategy
from ml4t.live import LiveEngine
from ml4t.live.safety import LiveRiskConfig, SafeBroker
from ml4t.specs.lifecycle import MarketEvent

# Ensure workspace paths are available
REPO_ROOT = Path(__file__).resolve().parent
QUANT_RESEARCH = Path("/home/quant/dev/quant/quant-research")
sys.path.insert(0, str(QUANT_RESEARCH))
sys.path.insert(0, str(QUANT_RESEARCH / "promote" / "donchian_btc" / "src"))

from independent_seven_native_features import DONCHIAN_WINDOWS, donchian_btc
from connectors.bybit.feed import BybitKlineFeed
from connectors.hyperliquid.feed import HyperliquidKlineFeed
from connectors.propr.feed import BinanceCompletedKlineFeed
from connectors.propr.ml4t_bridge import ProprLiveBroker
from propr_sdk import ProprClient


def fetch_warmup_klines(symbol: str = "BTCUSDT", limit: int = 250) -> pl.DataFrame:
    """Fetch recent historical daily klines from Binance public API."""
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit={limit}"
    resp = httpx.get(url, timeout=10.0)
    resp.raise_for_status()
    raw = resp.json()
    return pl.DataFrame({
        "timestamp": [datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc) for k in raw],
        "open": [float(k[1]) for k in raw],
        "high": [float(k[2]) for k in raw],
        "low": [float(k[3]) for k in raw],
        "close": [float(k[4]) for k in raw],
        "volume": [float(k[5]) for k in raw],
    })


class DonchianBTCStrategy(Strategy):
    """Donchian 9-channel volatility-targeted trend strategy for ML4T Live."""

    def __init__(self, asset: str = "BTC/USDC", min_trade_notional: float = 25.0, capital: float | None = None):
        super().__init__()
        self.asset = asset
        self.base = asset.split("/")[0]
        self.min_trade_notional = min_trade_notional
        self.capital = capital
        self.history_df: pl.DataFrame | None = None
        self.decisions: int = 0
        self.last_signal: dict[str, Any] = {}

    def warmup(self, df: pl.DataFrame) -> None:
        """Initialize rolling price history for 9 Donchian channels and 60d vol."""
        self.history_df = df.sort("timestamp")
        features = donchian_btc(self.history_df)
        last = features.tail(1)
        self.last_signal = {
            "close": float(last["close"][0]),
            "average_direction": float(last["average_direction"][0]),
            "daily_vol60": float(last["daily_vol60"][0]),
            "vol_leverage": float(last["vol_leverage"][0]),
            "target_exposure": float(last["target_exposure"][0]),
        }

    def on_data(self, timestamp: datetime, data: dict[str, Any], context: dict[str, Any], broker: Any) -> None:
        bar = data.get(self.asset) or data.get(self.base)
        if not bar:
            return

        price = float(bar["close"])
        if not math.isfinite(price) or price <= 0:
            return

        # Append new bar to history if streaming
        if self.history_df is not None:
            last_ts = self.history_df["timestamp"][-1]
            new_row = pl.DataFrame({
                "timestamp": [timestamp],
                "open": [float(bar.get("open", price))],
                "high": [float(bar.get("high", price))],
                "low": [float(bar.get("low", price))],
                "close": [price],
                "volume": [float(bar.get("volume", 0.0))],
            })
            if timestamp > last_ts:
                self.history_df = pl.concat([self.history_df.tail(299), new_row])
            elif timestamp == last_ts:
                self.history_df = pl.concat([self.history_df.head(-1), new_row])
            features = donchian_btc(self.history_df)
            last = features.tail(1)
            self.last_signal = {
                "close": price,
                "average_direction": float(last["average_direction"][0]),
                "daily_vol60": float(last["daily_vol60"][0]),
                "vol_leverage": float(last["vol_leverage"][0]),
                "target_exposure": float(last["target_exposure"][0]),
            }

        target_exposure = self.last_signal.get("target_exposure", 0.0)
        equity = float(broker.equity() if callable(getattr(broker, "equity", None)) else broker.get_account_value())
        if self.capital is not None:
            equity = min(equity, self.capital)
        if equity <= 0:
            return

        # Query existing position
        pos = broker.get_position(self.asset) or broker.get_position(self.base)
        current_qty = float(pos.quantity) if pos else 0.0

        # Target sizing in BTC
        target_qty = (equity * target_exposure) / price
        delta = target_qty - current_qty
        trade_notional = abs(delta) * price

        print(f"\n[Strategy Signal] Bar={timestamp.isoformat()} Price=${price:,.2f}")
        avg_dir = float(self.last_signal.get("average_direction") or 0.0)
        vol60 = float(self.last_signal.get("daily_vol60") or 0.0)
        vol_lev = float(self.last_signal.get("vol_leverage") or 0.0)
        print(f"  Direction={avg_dir:.2f}, Vol60={vol60:.4f}, Leverage={vol_lev:.2f}")
        print(f"  Target Exposure={target_exposure:.2%}")
        print(f"  Portfolio Equity=${equity:,.2f} -> Target Qty={target_qty:.4f} BTC (${target_qty * price:,.2f})")
        print(f"  Current Qty={current_qty:.4f} BTC -> Order Delta={delta:+.4f} BTC (${trade_notional:,.2f})")

        if trade_notional >= self.min_trade_notional:
            order_qty = round(delta, 4)
            print(f"  >> Submitting rebalance order: {order_qty:+.4f} {self.asset}")
            broker.submit_order(self.asset, order_qty)
            self.decisions += 1
        else:
            print(f"  -- Delta (${trade_notional:.2f}) below min trade notional (${self.min_trade_notional:.2f}); holding position.")


async def run_deployment(
    mode: str = "shadow",
    once: bool = True,
    max_bars: int | None = None,
    min_trade: float = 25.0,
    capital: float | None = None,
    feed_provider: str = "bybit",
) -> None:
    print(f"=== Deploying Donchian BTC Strategy (Mode: {mode.upper()}) ===")

    # 1. Warm up historical data
    print("Fetching historical warmup klines from Binance...")
    warmup_df = fetch_warmup_klines(symbol="BTCUSDT", limit=250)
    print(f"Loaded {len(warmup_df)} daily bars (latest close: ${warmup_df['close'][-1]:,.2f})")

    strategy = DonchianBTCStrategy(asset="BTC/USDC", min_trade_notional=min_trade, capital=capital)
    strategy.warmup(warmup_df)

    # 2. Configure Broker & SafeBroker
    raw_broker = ProprLiveBroker(client=ProprClient())
    risk_config = LiveRiskConfig(
        execution_mode=mode,
        state_file=Path(f".ml4t_state_{mode}.json"),
        max_order_value=50000.0,
        max_position_value=50000.0,
        max_total_exposure=100000.0,
        allowed_assets={"BTC/USDC", "BTC"},
    )
    safe_broker = SafeBroker(raw_broker, risk_config)

    # 3. Handle Single Evaluation vs Continuous Streaming
    if once:
        await safe_broker.connect()
        try:
            latest_bar = {
                "BTC/USDC": {
                    "open": warmup_df["open"][-1],
                    "high": warmup_df["high"][-1],
                    "low": warmup_df["low"][-1],
                    "close": warmup_df["close"][-1],
                    "volume": warmup_df["volume"][-1],
                }
            }
            latest_price = float(warmup_df["close"][-1])
            safe_broker.record_market_snapshot("BTC/USDC", latest_price)
            safe_broker.record_market_snapshot("BTC", latest_price)
            # Wrap safe_broker in strategy-compatible facade
            from ml4t.live.wrappers import ThreadSafeBrokerWrapper
            wrapped_broker = ThreadSafeBrokerWrapper(safe_broker, asyncio.get_running_loop())
            await asyncio.to_thread(
                strategy.on_data,
                warmup_df["timestamp"][-1],
                latest_bar,
                {},
                wrapped_broker,
            )
            print("\nExecution complete.")
        finally:
            await safe_broker.disconnect()
    else:
        if feed_provider == "hyperliquid":
            feed = HyperliquidKlineFeed(asset="BTC/USDC", symbol="BTC/USDC:USDC")
        elif feed_provider == "binance":
            feed = BinanceCompletedKlineFeed(asset="BTC/USDC", symbol="BTCUSDT")
        else:
            feed = BybitKlineFeed(asset="BTC/USDC", symbol="BTC/USDT:USDT")
        engine = LiveEngine(
            strategy=strategy,
            broker=safe_broker,
            feed=feed,
            auto_recover=True,
            halt_on_unhealthy=True,
        )
        print("Starting continuous LiveEngine. Listening for completed 1m bars...")
        await engine.connect()
        try:
            await engine.run()
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\nEngine interrupted by operator.")
        finally:
            await engine.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy Donchian BTC strategy via ML4T Live")
    parser.add_argument("--mode", choices=["shadow", "live"], default="shadow", help="Execution mode (default: shadow)")
    parser.add_argument("--once", action="store_true", default=False, help="Evaluate signal once and exit")
    parser.add_argument("--stream", action="store_true", default=False, help="Run live continuous WebSocket stream")
    parser.add_argument("--min-trade", type=float, default=25.0, help="Minimum trade notional in USD")
    parser.add_argument("--capital", type=float, default=None, help="Cap allocated capital (default: full account equity)")
    parser.add_argument(
        "--feed",
        choices=["bybit", "hyperliquid", "binance"],
        default="bybit",
        help="Market data feed provider (default: bybit, replacing legacy CryptoFeed)",
    )
    args = parser.parse_args()

    once_mode = args.once or not args.stream
    asyncio.run(run_deployment(
        mode=args.mode,
        once=once_mode,
        min_trade=args.min_trade,
        capital=args.capital,
        feed_provider=args.feed,
    ))


if __name__ == "__main__":
    main()
