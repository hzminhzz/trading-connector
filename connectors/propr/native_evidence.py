"""Versioned, read-only Propr evidence. No provider history cutoff is proven."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from ..execution_capabilities import ExecutionCapabilities
from .account import discover_account, parse_account
from .inventory import Inventory, InventoryReader
from .models import finite_decimal, one_way_positions
from .rest import RemoteAPIError

SCHEMA_VERSION = "propr_native_evidence_v1"


@dataclass(frozen=True)
class FinancialField:
    value: Decimal | None
    provider_field: str | None
    provenance: str


def field(value, native, derived=False):
    return FinancialField(value, native if value is not None else None,
                          "UNKNOWN" if value is None else "DERIVED_SDK_FORMULA" if derived else "PROVIDER")


@dataclass(frozen=True)
class NativeFinancial:
    balance: FinancialField
    equity: FinancialField
    buying_power: FinancialField
    cash: FinancialField
    unrealized_pnl: FinancialField
    isolated_position_margin: FinancialField
    initial_margin: FinancialField
    maintenance_margin: FinancialField


@dataclass(frozen=True)
class NativeEvidence:
    schema: str
    endpoint: str
    account_id: str
    attempt_id: str | None
    account_source: str | None
    account_bound: bool
    provider_time: str | None
    local_known_at_ms: int
    financial: NativeFinancial
    financial_complete: bool
    inventory: Inventory | None
    inventory_read_complete: bool
    stream_connected: bool
    history_complete: bool
    cutoff: str | None
    opening_ready: bool
    reasons: tuple[str, ...]
    # No current provider reader establishes account-specific execution semantics.
    execution_capabilities: tuple[ExecutionCapabilities, ...] = ()


def unknown_financial():
    missing = FinancialField(None, None, "UNKNOWN")
    return NativeFinancial(*(missing for _ in range(8)))


def financial_from(raw, account_id, attempt_id):
    snap = parse_account(account_id, attempt_id, raw)
    native = raw.get("data", raw)
    isolated = native.get("isolatedPositionMargin") if isinstance(native, dict) else None
    isolated = finite_decimal(isolated) if isolated is not None else None
    return NativeFinancial(
        field(snap.balance, "balance"),
        field(snap.equity, "balance + totalUnrealizedPnl + isolatedPositionMargin", True),
        field(snap.buying_power, "availableBalance"),
        FinancialField(None, None, "UNKNOWN"),
        field(snap.unrealized_pnl, "totalUnrealizedPnl"),
        field(isolated, "isolatedPositionMargin"),
        field(snap.initial_margin, "totalInitialMargin"),
        field(snap.maintenance_margin, "totalMaintenanceMargin"),
    )


async def inspect_native_evidence(rest: Any, *, account_id: str, endpoint: str,
                                  known_at_ms: int, stream_connected: bool = False) -> NativeEvidence:
    """Provider-native diagnostics; never maps missing evidence to verified flat/cash."""
    reasons: list[str] = []
    financial = unknown_financial()
    inventory = None
    source = attempt = provider_time = None
    bound = finance_complete = inventory_complete = False

    def result():
        return NativeEvidence(
            SCHEMA_VERSION, endpoint, account_id, attempt, source, bound, provider_time,
            known_at_ms, financial, finance_complete, inventory, inventory_complete,
            stream_connected, False, None, False, tuple(sorted(set(reasons))),
        )

    if (not isinstance(account_id, str) or not account_id.strip() or "/" in account_id
            or not isinstance(endpoint, str) or type(known_at_ms) is not int or known_at_ms < 0):
        reasons.append("INVALID_EVIDENCE_REQUEST")
        return result()
    if str(getattr(getattr(rest, "client", None), "base_url", "")).rstrip("/") != endpoint.rstrip("/"):
        reasons.append("ENDPOINT_BINDING_MISMATCH")
        return result()
    reasons.extend(("PROVIDER_CUTOFF_UNPROVEN", "RECONCILIATION_UNCERTAIN"))
    if not stream_connected:
        reasons.append("STREAM_NOT_CONNECTED")

    try:
        identity = await discover_account(rest, account_id)
    except (RemoteAPIError, ValueError, TypeError):
        reasons.append("ACCOUNT_UNVERIFIED")
        return result()
    if identity.account_id != account_id:
        reasons.append("ACCOUNT_UNVERIFIED")
        return result()
    source, attempt, bound = identity.source, identity.attempt_id, True

    try:
        raw = await rest.request("GET", f"/accounts/{quote(account_id, safe='')}")
        native = raw.get("data", raw) if isinstance(raw, dict) else None
        if not isinstance(native, dict) or "accountId" not in native:
            bound = False
            reasons.append("ACCOUNT_SNAPSHOT_IDENTITY_UNATTESTED")
            return result()
        financial = financial_from(raw, account_id, attempt)
    except (RemoteAPIError, ValueError, TypeError, KeyError):
        bound = False
        reasons.append("ACCOUNT_FINANCIAL_INVALID")
        return result()
    native = raw.get("data", raw)
    provider_time = native.get("updatedAt") if isinstance(native, dict) else None
    try:
        if not isinstance(provider_time, str):
            raise ValueError("missing time")
        parsed = datetime.fromisoformat(provider_time.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("naive provider time")
        stamp_ms = int(parsed.timestamp() * 1000)
        if not 0 <= known_at_ms - stamp_ms <= 30000:
            reasons.append("PROVIDER_TIME_STALE")
    except (ValueError, OverflowError):
        reasons.append("PROVIDER_TIME_UNVERIFIED")

    for attr, reason in (
        ("balance", "MISSING_BALANCE"), ("equity", "MISSING_EQUITY"),
        ("buying_power", "MISSING_BUYING_POWER"),
        ("unrealized_pnl", "MISSING_UNREALIZED_PNL"),
        ("initial_margin", "MISSING_INITIAL_MARGIN"),
        ("maintenance_margin", "MISSING_MAINTENANCE_MARGIN"),
    ):
        if getattr(financial, attr).value is None:
            reasons.append(reason)
    if any(getattr(financial, attr).value is not None
           and getattr(financial, attr).value < 0
           for attr in ("initial_margin", "maintenance_margin")):
        reasons.append("INVALID_MARGIN")
    reasons.append("PROVIDER_CASH_UNVERIFIED")
    finance_complete = not any(reason.startswith(("MISSING_", "INVALID_")) for reason in reasons)
    # An unknown ledger-cash meaning prevents a financially complete Guardian view.
    finance_complete = finance_complete and financial.cash.value is not None

    try:
        inventory = await InventoryReader(rest, account_id).snapshot()
        inventory_complete = True
        if any(item.account_id is None for group in (
            inventory.orders, inventory.positions, inventory.trades,
        ) for item in group):
            reasons.append("INVENTORY_ROW_ACCOUNT_UNVERIFIED")
        try:
            one_way_positions(inventory.positions)
        except ValueError:
            reasons.append("HEDGE_MODE_UNSUPPORTED_FOR_GUARDIAN")
    except (RemoteAPIError, ValueError, TypeError, KeyError):
        reasons.append("INVENTORY_UNVERIFIED")
        inventory = None
    return result()
