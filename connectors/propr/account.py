"""Read-only Propr account snapshot with explicit challenge ownership binding."""
from dataclasses import dataclass
from decimal import Decimal

from .config import ProprConfig, Mode
from .models import finite_decimal
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
    initial_margin: Decimal | None = None
    maintenance_margin: Decimal | None = None
    high_water_mark: Decimal | None = None

    @property
    def complete(self):
        return all(v is not None for v in (
            self.equity, self.balance, self.buying_power,
            self.margin_used, self.unrealized_pnl,
            self.initial_margin, self.maintenance_margin,
        ))


def _optional_decimal(data, name):
    value = data.get(name)
    if value is None:
        return None
    try:
        return finite_decimal(value)
    except ValueError as exc:
        raise RemoteAPIError("Invalid account financial field") from exc


def parse_account(account_id, attempt_id, raw):
    if not isinstance(raw, dict):
        raise RemoteAPIError("Invalid account response shape")
    if "data" in raw and isinstance(raw["data"], dict):
        raw = raw["data"]
    if raw.get("accountId") is not None and raw["accountId"] != account_id:
        raise RemoteAPIError("Account snapshot identity mismatch")
    balance = _optional_decimal(raw, "balance")
    unrealized = _optional_decimal(raw, "totalUnrealizedPnl")
    isolated = _optional_decimal(raw, "isolatedPositionMargin")
    cross_position = _optional_decimal(raw, "crossPositionMargin")
    cross_order = _optional_decimal(raw, "crossOrderMargin")
    isolated_order = _optional_decimal(raw, "isolatedOrderMargin")
    reserved_components = (isolated, cross_position, cross_order, isolated_order)
    # Propr SDK formula: balance + totalUnrealizedPnl + isolatedPositionMargin.
    equity = (balance + unrealized + isolated) if None not in (balance, unrealized, isolated) else None
    margin_used = sum(reserved_components) if all(v is not None for v in reserved_components) else None
    return AccountSnapshot(
        account_id=account_id, attempt_id=attempt_id, equity=equity,
        balance=balance, buying_power=_optional_decimal(raw, "availableBalance"),
        margin_used=margin_used, unrealized_pnl=unrealized,
        initial_margin=_optional_decimal(raw, "totalInitialMargin"),
        maintenance_margin=_optional_decimal(raw, "totalMaintenanceMargin"),
        high_water_mark=_optional_decimal(raw, "highWaterMark"),
    )


class ReadOnlyAccount:
    def __init__(self, config: ProprConfig, transport=None):
        if not isinstance(config.account_id, str) or not config.account_id.strip() or "/" in config.account_id:
            raise ValueError("Explicit account ID required for account inspection")
        if not config.api_key:
            raise ValueError("Explicit API key required")
        if config.mode is not Mode.READ_ONLY:
            raise PermissionError("Account inspector is strictly read-only")
        self.config = config
        self.rest = ProprRest(config.endpoints[0], config.api_key, transport=transport)

    async def inspect(self):
        health = await self.rest.request("GET", "/health")
        if not isinstance(health, dict) or health.get("status") != "OK":
            raise RemoteAPIError("Health check failed")
        attempts = await self.rest.pages("/challenge-attempts")
        matching = [a for a in attempts if a.get("accountId") == self.config.account_id]
        if len(matching) != 1:
            raise RemoteAPIError("Account identity not uniquely established")
        attempt = matching[0]
        account = await self.rest.request("GET", f"/accounts/{self.config.account_id}")
        snapshot = parse_account(self.config.account_id, attempt.get("attemptId"), account)
        return {
            "healthy": True, "account_bound": True,
            "financial_fields_verified": snapshot.complete, "snapshot": snapshot,
        }

    async def close(self):
        await self.rest.close()
