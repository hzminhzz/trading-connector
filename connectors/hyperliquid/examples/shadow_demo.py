# type: ignore
"""Shadow-mode drill for HyperliquidLiveBroker using SafeBroker pre-trade risk controls."""
from __future__ import annotations

import asyncio
from pathlib import Path

from ml4t.backtest.types import OrderSide, OrderType
from ml4t.live.safety import LiveRiskConfig, SafeBroker

from connectors.hyperliquid.broker import HyperliquidLiveBroker
from connectors.hyperliquid.config import Environment, HyperliquidConfig, Mode


async def main() -> None:
    print("=== Hyperliquid Shadow Mode Drill ===")
    config = HyperliquidConfig(Environment.TESTNET, Mode.SHADOW)
    raw_broker = HyperliquidLiveBroker(config=config)

    risk_config = LiveRiskConfig(
        execution_mode="shadow",
        max_order_value=500.0,
        max_position_value=2500.0,
        max_total_exposure=5000.0,
        allowed_assets={"BTC/USDC", "BTC/USDC:USDC", "ETH/USDC", "ETH/USDC:USDC"},
        state_file=Path(".ml4t_hyperliquid_shadow_state.json"),
    )

    safe_broker = SafeBroker(raw_broker, risk_config)
    try:
        await safe_broker.connect()
        print(f"SafeBroker connected in {risk_config.execution_mode} mode.")
        print(f"Broker cash: ${await safe_broker.get_cash_async():,.2f}")
        print(f"Broker account value: ${await safe_broker.get_account_value_async():,.2f}")

        # Record a market snapshot for reference price validation
        safe_broker.record_market_snapshot("BTC/USDC", 82_000.0)

        # Shadow order submission: evaluated against risk rules, recorded in shadow journal, never reaches DEX
        print("\nSubmitting shadow order (0.001 BTC)...")
        order = await safe_broker.submit_order_async(
            asset="BTC/USDC",
            quantity=0.001,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
        )
        print(f"Shadow order evaluated successfully: ID={order.order_id}, Status={order.status.value}")

    finally:
        await safe_broker.disconnect()
        if Path(".ml4t_hyperliquid_shadow_state.json").exists():
            Path(".ml4t_hyperliquid_shadow_state.json").unlink()


if __name__ == "__main__":
    asyncio.run(main())
