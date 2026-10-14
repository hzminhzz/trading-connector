"""Read-only account discovery and inventory check. Prints no private account data."""
import argparse
import asyncio
import os

from connectors.propr.account import ReadOnlyAccount
from connectors.propr.config import Environment, ProprConfig
from connectors.propr.inventory import InventoryReader


async def main():
    parser = argparse.ArgumentParser()
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--account-id")
    identity.add_argument("--discover-unique", action="store_true")
    args = parser.parse_args()
    key = os.environ.get("PROPR_LIVE_API_KEY")
    if not key:
        parser.error("Missing PROPR_LIVE_API_KEY")
    reader = ReadOnlyAccount(
        ProprConfig(Environment.PRODUCTION, account_id=args.account_id, api_key=key)
    )
    try:
        result = await reader.inspect()
        snapshot = result["snapshot"]
        inventory = await InventoryReader(reader.rest, args.account_id).snapshot(snapshot.account_id)
        print(f"health={'OK' if result['healthy'] and result['services_healthy'] else 'FAILED'}")
        print(f"account_bound={result['account_bound']}")
        print(f"account_source={result['account_source']}")
        print(f"financial_fields_verified={result['financial_fields_verified']}")
        print(f"orders={len(inventory.orders)}")
        print(f"positions={len(inventory.positions)}")
        print(f"trades={len(inventory.trades)}")
    finally:
        await reader.close()


if __name__ == "__main__":
    asyncio.run(main())
