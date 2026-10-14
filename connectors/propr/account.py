"""Read-only Propr account snapshot with exact account-source binding."""
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import quote

from .config import ProprConfig, Mode
from .models import finite_decimal
from .rest import ProprRest, RemoteAPIError


@dataclass(frozen=True)
class AccountBinding:
    account_id: str
    source: str
    attempt_id: str | None


async def discover_account(rest, account_id: str | None = None) -> AccountBinding:
    records = []
    for path, source in (
        ("/book-account-issuances", "funded"),
        ("/challenge-attempts?status=active", "challenge"),
    ):
        rows = await rest.pages(path)
        for row in rows:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("accountId"), str)
                or not row["accountId"]
            ):
                raise RemoteAPIError("Unexpected account-discovery response")
            attempt_id = row.get("attemptId")
            if attempt_id is not None and not isinstance(attempt_id, str):
                raise RemoteAPIError("Unexpected account-discovery response")
            records.append(AccountBinding(row["accountId"], source, attempt_id))

    matching = [record for record in records if account_id is None or record.account_id == account_id]
    if len(matching) != 1:
        raise RemoteAPIError("Account identity is absent or ambiguous")
    return matching[0]


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
        if config.account_id is not None and (
            not isinstance(config.account_id, str)
            or not config.account_id.strip()
            or "/" in config.account_id
        ):
            raise ValueError("Invalid account ID")
        if not config.api_key:
            raise ValueError("Explicit API key required")
        if config.mode is not Mode.READ_ONLY:
            raise PermissionError("Account inspector is strictly read-only")
        self.config = config
        self.rest = ProprRest(config.endpoints[0], config.api_key, transport=transport)

    async def inspect(self):
        health = await self.rest.request("GET", "/health")
        services = await self.rest.request("GET", "/health/services")
        if not isinstance(health, dict) or health.get("status") != "OK":
            raise RemoteAPIError("Health check failed")
        if not isinstance(services, dict) or services.get("core") != "OK":
            raise RemoteAPIError("Core service health check failed")
        binding = await discover_account(self.rest, self.config.account_id)
        account = await self.rest.request("GET", f"/accounts/{quote(binding.account_id, safe='')}")
        snapshot = parse_account(binding.account_id, binding.attempt_id, account)
        return {
            "healthy": True,
            "services_healthy": True,
            "account_bound": True,
            "account_source": binding.source,
            "financial_fields_verified": snapshot.complete,
            "snapshot": snapshot,
        }

    async def close(self):
        await self.rest.close()
