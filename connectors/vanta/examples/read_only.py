"""Inspect a selected Vanta account without submitting orders."""
import argparse
import asyncio
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from connectors.vanta.client import VantaClient  # noqa: E402
from connectors.vanta.config import VantaConfig  # noqa: E402


async def inspect(account_id: str) -> None:
    key = os.environ.get("VANTA_KEY_ID")
    secret = os.environ.get("VANTA_SECRET")
    if not key or not secret:
        raise SystemExit("VANTA_KEY_ID and VANTA_SECRET are required")
    client = VantaClient(VantaConfig(account_id=account_id, api_key=key, api_secret=secret))
    try:
        await client.get_account()
        print("account_read=ok")
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True)
    args = parser.parse_args()
    asyncio.run(inspect(args.account_id))


if __name__ == "__main__":
    main()
