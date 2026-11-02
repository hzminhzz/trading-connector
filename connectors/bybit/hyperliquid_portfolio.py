"""Read-only Hyperliquid 76-market OHLCV -> Bybit research compatibility.

Never routes orders. Hyperliquid bar prices are not Bybit execution prices.
The frozen universe is a point-in-time research input, not a venue allowlist.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import json
from pathlib import Path
from typing import Any

SCHEMA = "hyperliquid_chd_76_universe_v1"
REGISTRY = Path(__file__).with_name("hyperliquid_76.json")
VALID_SOURCE_STATUSES = frozenset({
    "fetched_from_chd_trades",
    "rebuilt_from_local_chd_trades",
    "fetched_from_chd_candles",
})


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("NONFINITE_MARKET_NUMBER")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("NONFINITE_MARKET_NUMBER") from exc
    if not result.is_finite():
        raise ValueError("NONFINITE_MARKET_NUMBER")
    return result


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("UTC_AWARE_TIMESTAMP_REQUIRED")
    return value.astimezone(UTC)


def load_registry(path: Path = REGISTRY) -> tuple[str, ...]:
    """Load the EXACT original 50 standard + 26 builder cohort; no expansion."""
    data = json.loads(Path(path).read_text())
    if data.get("schema") != SCHEMA or data.get("timeframe") != "1m":
        raise ValueError("WRONG_HYPERLIQUID_UNIVERSE")
    symbols = data.get("symbols")
    if (
        not isinstance(symbols, list)
        or len(symbols) != 76
        or len(set(symbols)) != 76
        or sum(x.startswith("xyz:") for x in symbols if isinstance(x, str)) != 26
        or any(
            not isinstance(x, str)
            or x.startswith("xyz:") and (
                not x[4:].isascii() or not x[4:].isalnum() or x[4:] != x[4:].upper()
            )
            or not x.startswith("xyz:") and (
                not x.isascii() or not x.isalnum() or x != x.upper()
            )
            for x in symbols
        )
    ):
        raise ValueError("UNVERIFIED_76_SYMBOL_REGISTRY")
    return tuple(symbols)


def source_instrument(symbol: str) -> str:
    """Keep HIP-3 namespaces intact; never erase the builder DEX."""
    symbols = load_registry()
    if symbol not in symbols:
        raise ValueError("SOURCE_SYMBOL_NOT_IN_FROZEN_76")
    if symbol.startswith("xyz:"):
        return "hyperliquid/mainnet:perp:xyz/" + symbol[4:]
    return "hyperliquid/mainnet:perp:default/" + symbol


def source_store(store_root: Path, symbol: str) -> Path:
    source_instrument(symbol)
    root = Path(store_root)
    if symbol.startswith("xyz:"):
        return root / "crypto_futures_ohlcv_1m_HYPERLIQUID_XYZ_PERP" / ("symbol=" + symbol[4:])
    return root / ("crypto_futures_ohlcv_1m_HYPERLIQUID_" + symbol + "_PERP")


def partition_path(store_root: Path, symbol: str, minute: datetime) -> Path:
    at = _utc(minute)
    if at.second or at.microsecond:
        raise ValueError("ONE_MINUTE_TIMESTAMP_REQUIRED")
    return (
        source_store(store_root, symbol)
        / f"year={at.year:04d}"
        / f"month={at.month:02d}"
        / f"day={at.day:02d}"
        / "data.parquet"
    )


def inventory(store_root: Path) -> dict[str, Any]:
    """Check 76 registered paths without scanning unrelated expanded markets."""
    found = []
    missing = []
    for symbol in load_registry():
        path = source_store(store_root, symbol)
        (found if path.is_dir() and not path.is_symlink() else missing).append(symbol)
    return {
        "schema": SCHEMA,
        "registered": 76,
        "standard_registered": 50,
        "builder_registered": 26,
        "store_directories_present": len(found),
        "missing": missing,
        "data_is_historical_research_only": True,
        "bybit_execution_authorized": False,
    }


@dataclass(frozen=True)
class HistoricalMinute:
    source_instrument: str
    bar_open_at: str
    observed_at: str | None
    open: str
    high: str
    low: str
    close: str
    volume: str
    provenance_status: str
    valuation_only: bool = True
    execution_venue: str = "hyperliquid"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_historical_minute(
    store_root: Path, symbol: str, minute: datetime, *, decision_at: datetime
) -> HistoricalMinute:
    """Read only a complete 1m bar. Historical availability is NOT live receipt."""
    at = _utc(minute)
    decision = _utc(decision_at)
    if decision < at + timedelta(minutes=1):
        raise ValueError("UNFINALIZED_BAR_LOOKAHEAD")
    path = partition_path(store_root, symbol, at)
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("HYPERLIQUID_MINUTE_PARTITION_UNAVAILABLE")
    meta_path = path.with_name("source.json")
    if meta_path.is_symlink() or not meta_path.is_file():
        raise ValueError("HYPERLIQUID_SOURCE_PROVENANCE_MISSING")
    meta = json.loads(meta_path.read_text())
    status = meta.get("status")
    if status not in VALID_SOURCE_STATUSES:
        raise ValueError("UNQUALIFIED_HYPERLIQUID_PARTITION")
    try:
        import polars as pl
    except ImportError as exc:
        raise ImportError("polars is required to read ml4t-data Parquet") from exc
    rows = pl.read_parquet(path).filter(pl.col("timestamp") == at)
    if rows.height != 1:
        raise ValueError("MISSING_OR_DUPLICATED_HYPERLIQUID_MINUTE")
    row = rows.row(0, named=True)
    if row["symbol"] != symbol.removeprefix("xyz:"):
        raise ValueError("HYPERLIQUID_SYMBOL_MISMATCH")
    o, h, low, c, v = (_decimal(row[k]) for k in ("open", "high", "low", "close", "volume"))
    if o <= 0 or low <= 0 or not low <= o <= h or not low <= c <= h or v < 0:
        raise ValueError("INVALID_HYPERLIQUID_BAR")
    return HistoricalMinute(
        source_instrument=source_instrument(symbol),
        bar_open_at=at.isoformat(),
        observed_at=None,  # Retrospective Parquet is never a first-known live timestamp
        open=str(o), high=str(h), low=str(low), close=str(c), volume=str(v),
        provenance_status=status,
    )


def match_bybit_market(symbol: str, markets: dict[str, Any]) -> dict[str, Any]:
    """Exact native Bybit USDT linear-perp match only, never inferred HIP-3 aliases."""
    instrument = source_instrument(symbol)
    result = {
        "source_instrument": instrument,
        "venue": "bybit",
        "market": None,
        "native_id": None,
        "mapping": "BLOCKED",
        "reason": "UNVERIFIED_VENUE_MARKET",
        "order_authorized": False,
    }
    if symbol.startswith("xyz:"):
        result["reason"] = "HIP3_BUILDER_NOT_EQUIVALENT_TO_BYBIT"
        return result
    bybit = f"{symbol}/USDT:USDT"
    market = markets.get(bybit)
    if not isinstance(market, dict):
        result["reason"] = "NOT_LISTED_ON_BYBIT_USDT_PERPS"
        return result
    info = market.get("info")
    info = info if isinstance(info, dict) else {}
    if not (
        market.get("symbol") == bybit
        and market.get("id") == symbol + "USDT"
        and market.get("base") == symbol
        and market.get("quote") == "USDT"
        and market.get("settle") == "USDT"
        and market.get("linear") is True
        and market.get("swap") is True
        and market.get("active") is True
        and info.get("status") == "Trading"
        and info.get("contractType") == "LinearPerpetual"
    ):
        result["reason"] = "BYBIT_CONTRACT_ID_OR_TRADING_STATUS_UNVERIFIED"
        return result
    lot = info.get("lotSizeFilter")
    tick = info.get("priceFilter")
    try:
        qty_step = _decimal(lot["qtyStep"])
        min_qty = _decimal(lot["minOrderQty"])
        min_notional = _decimal(lot["minNotionalValue"])
        tick_size = _decimal(tick["tickSize"])
        if min(qty_step, min_qty, min_notional, tick_size) <= 0:
            raise ValueError("BAD_PRECISION")
    except (KeyError, TypeError, ValueError):
        result["reason"] = "BYBIT_MINIMUM_OR_PRECISION_UNATTESTED"
        return result
    result.update({
        "market": bybit,
        "native_id": market["id"],
        "mapping": "MATCHED_RESEARCH",
        "reason": None,
        "qty_step": str(qty_step),
        "min_qty": str(min_qty),
        "min_notional": str(min_notional),
        "tick_size": str(tick_size),
    })
    return result


def coverage(markets: dict[str, Any], store_root: Path | None = None) -> dict[str, Any]:
    """Show mapping candidates; never mark any as challenge-account approved."""
    rows = [match_bybit_market(sym, markets) for sym in load_registry()]
    result = {
        "schema": "hyperliquid_76_bybit_compatibility_v1",
        "registered": len(rows),
        "bybit_research_matches": sum(r["mapping"] == "MATCHED_RESEARCH" for r in rows),
        "blocked": sum(r["mapping"] == "BLOCKED" for r in rows),
        "rows": rows,
        "account_challenge_rules_verified": False,
        "execution_authorized": False,
    }
    if store_root is not None:
        result["local_data"] = inventory(store_root)
    return result


def shadow_target_preview(
    targets: dict[str, Any],
    markets: dict[str, Any],
    bybit_marks: dict[str, Any],
    *,
    gross_cap_usd: Decimal,
) -> dict[str, Any]:
    """Translate *already sized* PM notionals into non-executable Bybit previews.

    Targets keyed by exact Hyperliquid instrument. No wallet weights, NAV, or
    source timestamps are invented. Price inputs MUST be Bybit marks, not HL bars.
    """
    cap = _decimal(gross_cap_usd)
    if cap <= 0 or not isinstance(targets, dict):
        raise ValueError("INVALID_PAPER_PORTFOLIO")
    if not isinstance(bybit_marks, dict):
        raise ValueError("BYBIT_MARKS_REQUIRED")
    if len(targets) > 76:
        raise ValueError("UNBOUNDED_PM_PORTFOLIO")
    candidates = []
    skipped = []
    gross = Decimal(0)
    for instrument, signed in sorted(targets.items()):
        hits = [sym for sym in load_registry() if source_instrument(sym) == instrument]
        if len(hits) != 1:
            skipped.append({"source_instrument": instrument, "reason": "NOT_REGISTERED"})
            continue
        symbol = hits[0]
        amount = _decimal(signed)
        binding = match_bybit_market(symbol, markets)
        if binding["mapping"] != "MATCHED_RESEARCH":
            skipped.append({"source_instrument": instrument, "reason": binding["reason"]})
            continue
        if amount == 0:
            continue
        bybit_price = _decimal(bybit_marks.get(binding["market"]))
        if bybit_price <= 0:
            raise ValueError("INVALID_BYBIT_REFERENCE_PRICE")
        qty_step = _decimal(binding["qty_step"])
        qty = ((abs(amount) / bybit_price) / qty_step).to_integral_value(rounding=ROUND_DOWN) * qty_step
        if qty < _decimal(binding["min_qty"]) or qty * bybit_price < _decimal(binding["min_notional"]):
            skipped.append({"source_instrument": instrument, "reason": "BYBIT_MIN_ORDER_NOT_MET"})
            continue
        gross += qty * bybit_price
        candidates.append({
            "source_instrument": instrument,
            "bybit_market": binding["market"],
            "signed_notional_usd": str(amount),
            "side": "buy" if amount > 0 else "sell",
            "quantity": str(qty),
            "bybit_reference_price": str(bybit_price),
            "hypothetical": True,
        })
    if gross > cap:
        return {
            "status": "BLOCKED_GROSS_CAP",
            "total_gross_usd": str(gross),
            "gross_cap_usd": str(cap),
            "actions": [],
            "skipped": skipped,
            "execution_authorized": False,
        }
    return {
        "status": "SHADOW_ONLY",
        "total_gross_usd": str(gross),
        "gross_cap_usd": str(cap),
        "actions": candidates,
        "skipped": skipped,
        "execution_authorized": False,
        "bybit_challenge_account_attested": False,
        "source": "COPYTRADE_PM_RESEARCH_TARGETS_NOT_BROKER_ORDERS",
    }
