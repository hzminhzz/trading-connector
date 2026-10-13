"""Authenticated Beta read-only smoke test. No order or position mutation.

Run from repo root with the key already exported in the process environment:
  python -m connectors.propr.examples.beta_readonly_smoke --account-id YOUR_ACCOUNT_ID

Output is deliberately redacted (no credential, account ID, positions or equity).
"""
import argparse
import asyncio
import json
import os

from connectors.propr.account import ReadOnlyAccount
from connectors.propr.config import ProprConfig
from connectors.propr.inventory import InventoryReader
from connectors.propr.rest import RemoteAPIError


async def inspect(account_id, key):
    config = ProprConfig("beta", account_id=account_id, api_key=key)
    reader = ReadOnlyAccount(config)
    try:
        account_result = await reader.inspect()
        inventory = await InventoryReader(reader.rest, account_id).snapshot()
        return {
            "environment": "beta",
            "health": "OK" if account_result["healthy"] else "FAILED",
            "account_bound": account_result["account_bound"],
            "financial_fields_verified": account_result["financial_fields_verified"],
            "inventory": inventory.summary(),
            "remote_mutations": 0,
        }
    finally:
        await reader.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-id", required=True)
    args = parser.parse_args()
    key = os.environ.get("PROPR_BETA_API_KEY")
    if not key:
        print(json.dumps({"status": "BLOCKED", "reason": "PROPR_BETA_API_KEY unavailable"}))
        return 2
    try:
        report = asyncio.run(inspect(args.account_id, key))
        print(json.dumps(report, sort_keys=True))
        return 0
    except (RemoteAPIError, PermissionError, ValueError) as exc:
        # Only exception classes, no provider body or query arguments.
        print(json.dumps({"status": "FAILED", "reason": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
