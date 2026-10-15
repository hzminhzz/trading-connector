# pyright: reportMissingImports=false
# type: ignore
"""Deploy the promoted RSI(5) 30/50 SPY-proxy strategy via ML4T Live and Propr."""
from __future__ import annotations
import argparse

import sys
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import polars as pl
from ml4t.backtest import Strategy
from ml4t.live import LiveEngine
from ml4t.live.safety import LiveRiskConfig, SafeBroker
QUANT_RESEARCH = Path("/home/quant/dev/quant/quant-research")
sys.path.insert(0, str(QUANT_RESEARCH / "promote" / "rsi_30_50_spy" / "src"))

from independent_seven_native_features import rsi_30_50
from connectors.propr.ml4t_bridge import ProprLiveBroker
from propr_sdk import ProprClient

ASSET = "xyz:SP500"


def fetch_warmup_daily(symbol: str = "SPY", limit: int = 260) -> pl.DataFrame:
    """Fetch daily SPY bars from Yahoo's public chart endpoint."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=2y&interval=1d"
    response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15.0)
    response.raise_for_status()
    result = response.json()["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    rows = [(datetime.fromtimestamp(ts, timezone.utc), float(quote["open"][i]),
             float(quote["high"][i]), float(quote["low"][i]), float(quote["close"][i]),
             float(quote["volume"][i] or 0))
            for i, ts in enumerate(result["timestamp"])
            if quote["close"][i] is not None]
    if len(rows) < 200:
        raise ValueError(f"Need at least 200 valid daily bars for SMA warmup; got {len(rows)}")
    return pl.DataFrame(rows[-limit:], schema=["timestamp", "open", "high", "low", "close", "volume"], orient="row")


class RsiSpyStrategy(Strategy):
    """Long 95% equity when RSI(5)<30 above SMA(200); exit at RSI>=50."""
    def __init__(self, asset: str = ASSET, capital: float | None = None):
        super().__init__()
        self.asset = asset
        self.capital = capital
        self.history: list[float] = []
        self.closes: list[float] = []
        self.last_signal: dict[str, float] = {}

    def warmup(self, df: pl.DataFrame) -> None:
        self.closes = [float(x) for x in df.sort("timestamp")["close"]]
        self._features()

    def _features(self) -> tuple[float, float]:
        if len(self.closes) < 200:
            return float("nan"), float("nan")
        prices = pl.DataFrame({
            "timestamp": [datetime.fromtimestamp(i * 86400, timezone.utc) for i in range(len(self.closes))],
            "open": self.closes, "high": self.closes, "low": self.closes,
            "close": self.closes, "volume": [0.0] * len(self.closes),
        })
        features = rsi_30_50(prices).tail(1)
        return float(features["rsi5"][0]), float(features["sma200"][0])

    def on_data(self, timestamp: datetime, data: dict[str, Any], context: dict[str, Any], broker: Any) -> None:
        bar = data.get(self.asset) or data.get("SPY") or data.get("SP500")
        if not bar:
            return
        close = float(bar.get("close", bar.get("price", 0)))
        if close <= 0:
            return
        self.closes.append(close)
        rsi, sma200 = self._features()
        self.last_signal = {"close": close, "rsi5": rsi, "sma200": sma200}
        print(f"\n[RSI SPY] {timestamp.isoformat()} close={close:.2f} RSI5={rsi:.2f} SMA200={sma200:.2f}")
        if len(self.closes) < 200:
            print("  -- waiting for 200-bar SMA warmup")
            return
        equity = float(broker.equity() if callable(getattr(broker, "equity", None)) else broker.get_account_value())
        if self.capital is not None:
            equity = min(equity, self.capital)
        if equity <= 0:
            return
        position = broker.get_position(self.asset)
        quantity = float(position.quantity) if position else 0.0
        target = 0.95 * equity / close if rsi < 30 and close > sma200 else (0.0 if rsi >= 50 else quantity)
        delta = target - quantity
        if abs(delta) * close >= 1.0:
            print(f"  >> Signal: {delta:+.6f} {self.asset} (${abs(delta)*close:,.2f})")
            broker.submit_order(self.asset, delta)
        else:
            print(f"  -- Holding; current quantity={quantity:.6f}")


async def run_deployment(mode: str = "shadow", once: bool = True, capital: float | None = None) -> None:
    warmup_df = fetch_warmup_daily()
    strategy = RsiSpyStrategy(capital=capital)
    strategy.warmup(warmup_df)
    print(f"Loaded {len(warmup_df)} SPY daily bars; last close=${warmup_df['close'][-1]:.2f}")
    broker = SafeBroker(ProprLiveBroker(client=ProprClient()), LiveRiskConfig(
        execution_mode=mode, state_file=Path(f".ml4t_rsi_spy_{mode}.json"),
        max_order_value=50000.0, max_position_value=50000.0, max_total_exposure=100000.0,
        allowed_assets={ASSET, "SP500", "SPY/USDC"}))
    if once:
        await broker.connect()
        try:
            price = float(warmup_df["close"][-1])
            broker.record_market_snapshot(ASSET, price)
            from ml4t.live.wrappers import ThreadSafeBrokerWrapper
            await asyncio.to_thread(strategy.on_data, warmup_df["timestamp"][-1],
                {ASSET: {"close": price}}, {}, ThreadSafeBrokerWrapper(broker, asyncio.get_running_loop()))
        finally:
            await broker.disconnect()
    else:
        raise RuntimeError("Continuous mode needs a daily XYZ:SP500 completed-bar feed; use --once")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["shadow", "live"], default="shadow")
    parser.add_argument("--once", action="store_true", default=False)
    parser.add_argument("--capital", type=float, default=None)
    args = parser.parse_args()
    asyncio.run(run_deployment(mode=args.mode, once=True, capital=args.capital))


if __name__ == "__main__":
    main()
