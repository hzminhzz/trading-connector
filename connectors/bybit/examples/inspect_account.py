# type: ignore
"""Read-only inspection tool for Bybit V5 account and markets."""
from __future__ import annotations

import asyncio
import logging

from connectors.bybit.client import BybitClient
from connectors.bybit.config import BybitConfig

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("bybit_inspect")


async def main() -> None:
    config = BybitConfig.from_env()
    print(f"=== Bybit Account Inspection ({config.environment.value.upper()}) ===")
    print(f"Mode: {config.mode.value}")
    print(f"Default Contract Type: {config.default_type}")
    print(f"Credentials Configured: API Key={'YES' if config.api_key else 'NO'}, Secret={'YES' if config.api_secret else 'NO'}")

    client = BybitClient(config)
    try:
        print("\nConnecting and loading markets...")
        await client.connect()
        markets_count = len(client.exchange.markets) if client.exchange.markets else 0
        print(f"Markets Loaded: {markets_count}")

        # Check public ticker
        ticker = await client.exchange.fetch_ticker("BTC/USDT:USDT")
        print(f"Public BTC/USDT:USDT Mark Price: ${float(ticker.get('last', 0) or 0):,.2f}")

        if config.api_key and config.api_secret:
            print("\nQuerying authenticated account inventory...")
            balances = await client.fetch_balance()
            print(f"Currencies with balance: {len(balances)}")

            positions = await client.fetch_positions()
            print(f"Open Positions: {len(positions)}")

            orders = await client.fetch_open_orders()
            print(f"Open Orders: {len(orders)}")
        else:
            print("\nSkipping authenticated queries (no BYBIT_API_KEY / BYBIT_API_SECRET in environment).")

        print("\nInspection Status: HEALTHY (Read-only query completed)")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
