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
VALID_SOURCE_STATUSES = frozenset(
    {
        "fetched_from_chd_trades",
        "rebuilt_from_local_chd_trades",
        "fetched_from_chd_candles",
    }
)


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
            or x.startswith("xyz:")
            and (not x[4:].isascii() or not x[4:].isalnum() or x[4:] != x[4:].upper())
            or not x.startswith("xyz:")
            and (not x.isascii() or not x.isalnum() or x != x.upper())
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
        return (
            root
            / "crypto_futures_ohlcv_1m_HYPERLIQUID_XYZ_PERP"
            / ("symbol=" + symbol[4:])
        )
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
    o, h, low, c, v = (
        _decimal(row[k]) for k in ("open", "high", "low", "close", "volume")
    )
    if o <= 0 or low <= 0 or not low <= o <= h or not low <= c <= h or v < 0:
        raise ValueError("INVALID_HYPERLIQUID_BAR")
    return HistoricalMinute(
        source_instrument=source_instrument(symbol),
        bar_open_at=at.isoformat(),
        observed_at=None,  # Retrospective Parquet is never a first-known live timestamp
        open=str(o),
        high=str(h),
        low=str(low),
        close=str(c),
        volume=str(v),
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
    result.update(
        {
            "market": bybit,
            "native_id": market["id"],
            "mapping": "MATCHED_RESEARCH",
            "reason": None,
            "qty_step": str(qty_step),
            "min_qty": str(min_qty),
            "min_notional": str(min_notional),
            "tick_size": str(tick_size),
        }
    )
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
    pm_report: dict[str, Any],
    markets: dict[str, Any],
    bybit_marks: dict[str, Any],
    source_marks: dict[str, Any],
    *,
    gross_cap_usd: Decimal,
    coin_gross_cap_usd: Decimal,
) -> dict[str, Any]:
    """Validate PM's *allowed per-manager quantities* before a Bybit shadow target.

    This is a target-state preview, NOT a broker order/delta. PM alone owns
    leader-equity sizing and the pre-net coin/portfolio clamps. The source
    marks must be the same frozen marks used by that PM rebalance: they
    convert the already-allowed source quantities back to USD notionals.
    Bybit reference marks then convert net USD to Bybit target quantities.
    No leader NAV, order inventory or execution is inferred here.
    """
    cap = _decimal(gross_cap_usd)
    coin_cap = _decimal(coin_gross_cap_usd)
    if cap <= 0 or coin_cap <= 0:
        raise ValueError("INVALID_PAPER_GROSS_CAP")
    if not all(
        isinstance(x, dict) for x in (pm_report, markets, bybit_marks, source_marks)
    ):
        raise ValueError("INVALID_PAPER_PORTFOLIO")
    if (
        pm_report.get("hypothetical") is not True
        or pm_report.get("authorized_for_live") is not False
        or not isinstance(pm_report.get("allowed"), dict)
        or set(pm_report["allowed"]) != {"W028", "CA-008", "W015"}
        or any(not isinstance(x, dict) for x in pm_report["allowed"].values())
    ):
        raise ValueError("VERIFIED_PM_MANAGER_ALLOWED_BOOK_REQUIRED")

    # The PM reports quantities per independent manager. A net-only input
    # cannot prove gross caps or retain attribution and is rejected.
    instrument_to_symbol = {source_instrument(s): s for s in load_registry()}
    contributions: dict[str, dict[str, Decimal]] = {}
    invalid = []
    for manager in sorted(pm_report["allowed"]):
        for instrument, raw_quantity in pm_report["allowed"][manager].items():
            if not isinstance(instrument, str):
                raise ValueError("INVALID_SOURCE_INSTRUMENT")
            quantity = _decimal(raw_quantity)
            if instrument not in instrument_to_symbol:
                invalid.append(
                    {"source_instrument": instrument, "reason": "NOT_REGISTERED"}
                )
                continue
            if quantity == 0:
                # An explicit manager flat must remain in the target state:
                # omitting it could conceal a required close of an old position.
                # No market mark is needed to value exactly zero base units.
                contributions.setdefault(instrument, {})[manager] = Decimal(0)
                continue
            price = _decimal(source_marks.get(instrument))
            if price <= 0:
                raise ValueError("INVALID_OR_MISSING_PM_SOURCE_MARK")
            contributions.setdefault(instrument, {})[manager] = quantity * price

    coin_gross = {
        instrument: sum((abs(x) for x in book.values()), Decimal(0))
        for instrument, book in contributions.items()
    }
    gross = sum(coin_gross.values(), Decimal(0))
    base = {
        "gross_pre_net_source_usd": str(gross),
        "gross_cap_usd": str(cap),
        "coin_gross_cap_usd": str(coin_cap),
        "source_coin_gross_usd": {k: str(v) for k, v in sorted(coin_gross.items())},
        "source_manager_gross_usd": {
            manager: str(
                sum(
                    (
                        abs(book.get(manager, Decimal(0)))
                        for book in contributions.values()
                    ),
                    Decimal(0),
                )
            )
            for manager in sorted(pm_report["allowed"])
        },
        "targets": [],
        "execution_authorized": False,
        "order_deltas_computed": False,
        "bybit_challenge_account_attested": False,
        "source": "COPYTRADE_PM_ALLOWED_MANAGER_BOOK_NOT_BROKER_ORDERS",
    }
    # Cannot release an actionable subset from a frozen/partial source book.
    if (
        pm_report.get("status") != "ACTIVE_PAPER"
        or pm_report.get("risk_reason") is not None
    ):
        return {**base, "status": "BLOCKED_PM_RISK", "skipped": invalid}
    if (
        pm_report.get("partial_book") is not False
        or pm_report.get("skipped_unlisted") != []
    ):
        return {**base, "status": "BLOCKED_PM_PARTIAL_BOOK", "skipped": invalid}
    # PRE-NET. Opposing manager books cannot bypass either gross cap.
    if gross > cap or any(value > coin_cap for value in coin_gross.values()):
        return {**base, "status": "BLOCKED_PRE_NET_GROSS_CAP", "skipped": invalid}

    targets = []
    skipped = list(invalid)
    bybit_target_gross = Decimal(0)
    for instrument, book in sorted(contributions.items()):
        if instrument not in instrument_to_symbol:
            continue
        net = sum(book.values(), Decimal(0))
        symbol = instrument_to_symbol[instrument]
        binding = match_bybit_market(symbol, markets)
        if binding["mapping"] != "MATCHED_RESEARCH":
            skipped.append(
                {"source_instrument": instrument, "reason": binding["reason"]}
            )
            continue
        market = binding["market"]
        if net == 0:
            # Zero external exposure is an explicit FLAT target, not an
            # instruction to skip a potential prior Bybit position close.
            # Without reconciled broker inventory no order delta is possible.
            targets.append(
                {
                    "kind": "SHADOW_TARGET_POSITION_NOT_ORDER",
                    "source_instrument": instrument,
                    "bybit_market": market,
                    "source_manager_notionals_usd": {
                        k: str(v) for k, v in sorted(book.items())
                    },
                    "source_net_notional_usd": "0",
                    "target_side": "flat",
                    "target_quantity": "0",
                    "bybit_reference_price": None,
                    "bybit_target_notional_usd": "0",
                    "notional_tracking_error_usd": "0",
                    "hypothetical": True,
                }
            )
            continue
        if market not in bybit_marks:
            raise ValueError("MISSING_BYBIT_REFERENCE_PRICE")
        bybit_price = _decimal(bybit_marks[market])
        if bybit_price <= 0:
            raise ValueError("INVALID_BYBIT_REFERENCE_PRICE")
        step = _decimal(binding["qty_step"])
        quantity = ((abs(net) / bybit_price) / step).to_integral_value(
            rounding=ROUND_DOWN
        ) * step
        if quantity < _decimal(binding["min_qty"]) or quantity * bybit_price < _decimal(
            binding["min_notional"]
        ):
            skipped.append(
                {"source_instrument": instrument, "reason": "BYBIT_MIN_ORDER_NOT_MET"}
            )
            continue
        signed_bybit_notional = quantity * bybit_price * (1 if net > 0 else -1)
        bybit_target_gross += abs(signed_bybit_notional)
        targets.append(
            {
                "kind": "SHADOW_TARGET_POSITION_NOT_ORDER",
                "source_instrument": instrument,
                "bybit_market": market,
                "source_manager_notionals_usd": {
                    k: str(v) for k, v in sorted(book.items())
                },
                "source_net_notional_usd": str(net),
                "target_side": "long" if net > 0 else "short",
                "target_quantity": str(quantity),
                "bybit_reference_price": str(bybit_price),
                "bybit_target_notional_usd": str(signed_bybit_notional),
                "notional_tracking_error_usd": str(net - signed_bybit_notional),
                "hypothetical": True,
            }
        )
    result = {
        **base,
        "bybit_target_gross_usd": str(bybit_target_gross),
        "skipped": skipped,
    }
    if skipped:
        # Do not silently drop a manager's unsupported or sub-minimum leg
        # while suggesting the remaining target can represent the portfolio.
        return {**result, "status": "BLOCKED_INCOMPLETE_BYBIT_BOOK"}
    return {**result, "status": "SHADOW_ONLY", "targets": targets}
