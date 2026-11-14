# type: ignore
"""Read-only inspection tool for Hyperliquid DEX markets and account."""
from __future__ import annotations

import asyncio

from connectors.hyperliquid.client import HyperliquidClient
from connectors.hyperliquid.config import HyperliquidConfig


async def main() -> None:
    config = HyperliquidConfig.from_env()
    print(f"=== Hyperliquid Account Inspection ({config.environment.value.upper()}) ===")
    print(f"Mode: {config.mode.value}")
    print(f"Default Contract Type: {config.default_type}")
    print(
        f"Credentials Configured: Wallet={'YES' if config.wallet_address else 'NO'}, "
        f"Key={'YES' if config.private_key else 'NO'}"
    )

    client = HyperliquidClient(config)
    try:
        print("\nConnecting and loading markets...")
        await client.connect()
        markets_count = len(client.exchange.markets) if client.exchange.markets else 0
        print(f"Markets Loaded: {markets_count}")

        ticker = await client.exchange.fetch_ticker("BTC/USDC:USDC")
        print(f"Public BTC/USDC:USDC Mark Price: ${float(ticker.get('last', 0) or 0):,.2f}")

        if config.wallet_address and config.private_key:
            print("\nQuerying authenticated account inventory...")
            balances = await client.fetch_balance()
            print(f"Currencies with balance: {len(balances)}")

            positions = await client.fetch_positions()
            print(f"Open Positions: {len(positions)}")

            orders = await client.fetch_open_orders()
            print(f"Open Orders: {len(orders)}")
        else:
            print("\nSkipping authenticated queries (no HYPERLIQUID_WALLET_ADDRESS / HYPERLIQUID_PRIVATE_KEY in environment).")

        print("\nInspection Status: HEALTHY (Read-only query completed)")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
