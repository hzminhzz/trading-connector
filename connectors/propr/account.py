"""Read-only account binding through challenge-attempt ownership evidence."""
from dataclasses import dataclass
from decimal import Decimal

from .config import ProprConfig
from .rest import ProprRest, RemoteAPIError

@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str
    attempt_id: str | None
    equity: Decimal | None
    balance: Decimal | None
    buying_power: Decimal | None
    margin_used: Decimal | None
    unrealized_pnl: Decimal | None

    @property
    def complete(self):
        return all(v is not None for v in (
            self.equity, self.balance, self.buying_power,
            self.margin_used, self.unrealized_pnl,
        ))

def _decimal_or_none(value):
    return None if value is None else Decimal(str(value))

class ReadOnlyAccount:
    def __init__(self, config: ProprConfig, transport=None):
        if not config.account_id or not config.account_id.strip():
            raise ValueError("Explicit account ID required for account inspection")
        if not config.api_key:
            raise ValueError("Explicit API key required")
        if config.mode.value != "read_only":
            raise PermissionError("Account inspector is strictly read-only")
        self.config = config
        self.rest = ProprRest(config.endpoints[0], config.api_key, transport=transport)

    async def inspect(self):
        health = await self.rest.request("GET", "/health")
        if health.get("status") != "OK":
            raise RemoteAPIError("Health check failed")
        attempts = await self.rest.pages("/challenge-attempts")
        matching = [a for a in attempts if a.get("accountId") == self.config.account_id]
        if len(matching) != 1:
            raise RemoteAPIError("Account identity not uniquely established")
        attempt = matching[0]
        # Challenge attempt PnL and challenge initial balance are NOT account equity.
        # No authoritative account equity endpoint is documented in the inspected REST reference.
        snapshot = AccountSnapshot(
            account_id=self.config.account_id,
            attempt_id=attempt.get("attemptId"),
            equity=None, balance=None, buying_power=None,
            margin_used=None, unrealized_pnl=None,
        )
        return {"healthy": True, "account_bound": True,
                "financial_fields_verified": snapshot.complete,
                "snapshot": snapshot}

    async def close(self):
        await self.rest.close()
