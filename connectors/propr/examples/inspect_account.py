"""Read-only Propr account identity check. Prints no private account data."""
import argparse
import asyncio
import os

from connectors.propr.account import ReadOnlyAccount
from connectors.propr.config import ProprConfig

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", choices=("beta", "production"), required=True)
    parser.add_argument("--account-id", required=True)
    args = parser.parse_args()
    env_name = "PROPR_BETA_API_KEY" if args.environment == "beta" else "PROPR_LIVE_API_KEY"
    key = os.environ.get(env_name)
    if not key:
        parser.error(f"Missing {env_name}")
    reader = ReadOnlyAccount(ProprConfig(args.environment, account_id=args.account_id, api_key=key))
    try:
        result = await reader.inspect()
        print(f"health={'OK' if result['healthy'] else 'FAILED'}")
        print(f"account_bound={result['account_bound']}")
        print(f"financial_fields_verified={result['financial_fields_verified']}")
    finally:
        await reader.close()

if __name__ == "__main__":
    asyncio.run(main())
