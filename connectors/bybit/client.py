# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportMissingImports=false
# type: ignore
"""Asynchronous CCXT transport client for Bybit V5 Unified Trading Account."""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import ccxt
import ccxt.async_support as ccxta

from connectors.bybit.config import BybitConfig, Environment, Mode
from connectors.bybit.models import (
    BybitAccountSummary,
    BybitAuthenticationError,
    BybitSnapshotError,
    BybitBalance,
    BybitError,
    BybitInsufficientFundsError,
    BybitInvalidOrderError,
    BybitNetworkError,
    BybitOrderData,
    BybitOrderNotFoundError,
    BybitPositionData,
    BybitRateLimitError,
)

logger = logging.getLogger(__name__)


def _strict_decimal(value: object, field: str) -> Decimal:
    """Reject absent, bool, malformed or non-finite provider finance."""
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise BybitError(f"Invalid provider {field}")
    try:
        result = Decimal(str(value))
    except (ValueError, ArithmeticError) as exc:
        raise BybitError(f"Invalid provider {field}") from exc
    if not result.is_finite() or len(result.as_tuple().digits) > 256 or abs(result.as_tuple().exponent) > 1000:
        raise BybitError(f"Invalid provider {field}")
    return result


def _required_text(value: object, field: str, allowed: tuple[str, ...] | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BybitError(f"Invalid provider {field}")
    normalized = value.strip().lower()
    if allowed is not None and normalized not in allowed:
        raise BybitError(f"Unsupported provider {field}")
    return normalized




class BybitClient:
    """Async client managing Bybit REST commands with strict error translation."""

    def __init__(self, config: BybitConfig, exchange: ccxta.bybit | None = None) -> None:
        self.config = config
        if exchange is not None:
            self._exchange = exchange
        else:
            exchange_params: dict[str, Any] = {
                "enableRateLimit": True,
                "options": {
                    "defaultType": config.default_type,
                },
            }
            if config.api_key:
                exchange_params["apiKey"] = config.api_key
            if config.api_secret:
                exchange_params["secret"] = config.api_secret

            self._exchange = ccxta.bybit(exchange_params)
        # CCXT's demo mode routes authenticated REST requests to Bybit Demo,
        # which is separate from the Testnet sandbox. Public market-data feeds
        # should use the mainnet feed independently (BybitKlineFeed).
        if config.environment == Environment.TESTNET:
            if exchange is None:
                self._exchange.set_sandbox_mode(True)
        elif config.environment == Environment.DEMO:
            self._exchange.enable_demo_trading(True)

        self._connected = False
        self._verified_demo_identity: dict[str, Any] | None = None
        self._preflight_identity_evidence: dict[str, Any] = {}

    @property
    def demo_identity_verified(self) -> bool:
        return self._connected and self._verified_demo_identity is not None

    @property
    def demo_identity_evidence(self) -> dict[str, Any]:
        """Whitelisted non-identifying fields only; never expose API key or UID."""
        return dict(self._verified_demo_identity or {})

    @property
    def preflight_identity_evidence(self) -> dict[str, Any]:
        return dict(self._preflight_identity_evidence)

    @staticmethod
    def _v5_result(response: Any, endpoint: str) -> dict[str, Any]:
        if not isinstance(response, dict) or response.get("retCode") not in (0, "0"):
            raise BybitSnapshotError(f"{endpoint}: missing/failed V5 response")
        result = response.get("result")
        if not isinstance(result, dict):
            raise BybitSnapshotError(f"{endpoint}: invalid result object")
        return result

    def _require_demo_endpoint(self) -> None:
        exchange = self._exchange
        template = exchange.urls.get("api", {}).get("private", "")
        hostname = getattr(exchange, "hostname", "")
        endpoint = template.replace("{hostname}", hostname)
        if (
            self.config.environment is not Environment.DEMO
            or endpoint != "https://api-demo.bybit.com"
            or exchange.isSandboxModeEnabled is not False
            or exchange.options.get("enableDemoTrading") is not True
        ):
            raise BybitSnapshotError("demo_identity: private REST endpoint is not Bybit Demo")

    async def _verify_demo_identity(self) -> None:
        """Attest selected UID and API key through authenticated Demo REST, not flags alone."""
        cfg = self.config
        if not cfg.api_key or not cfg.api_secret or not cfg.expected_demo_uid:
            raise BybitSnapshotError("demo_identity: dedicated credentials and expected Demo UID required")
        self._require_demo_endpoint()
        if cfg.subaccount_id and cfg.subaccount_id != cfg.expected_demo_uid:
            raise BybitSnapshotError("demo_identity: conflicting selected account identifiers")
        key_info = self._v5_result(
            await self._exchange.private_get_v5_user_query_api(), "demo_key_info"
        )
        uid = str(key_info.get("userID", ""))
        if (
            not uid or uid != cfg.expected_demo_uid.strip()
            or key_info.get("apiKey") != cfg.api_key
            or str(key_info.get("uta")) != "1"
        ):
            raise BybitSnapshotError("demo_identity: authenticated UID, key or UTA does not match")
        account_info = self._v5_result(
            await self._exchange.private_get_v5_account_info(), "demo_account_info"
        )
        try:
            margin_status = int(account_info.get("unifiedMarginStatus"))
        except (TypeError, ValueError):
            margin_status = None
        if margin_status not in (3, 4, 5, 6):
            raise BybitSnapshotError("demo_identity: unsupported unified account status")
        self._verified_demo_identity = {
            "environment": "demo",
            "private_rest": "api-demo.bybit.com",
            "authenticated_uid_matches_expected": True,
            "dedicated_demo_key_matches": True,
            "unified_account_verified": True,
            "key_is_read_only": str(key_info.get("readOnly")) == "1",
        }

    async def fetch_demo_account_summary(self) -> BybitAccountSummary:
        """Read authoritative V5 USD totals; never infer equity from coin balances."""
        if not self.demo_identity_verified:
            raise BybitSnapshotError("demo_wallet: authenticated Demo identity not verified")
        result = self._v5_result(
            await self._exchange.private_get_v5_account_wallet_balance({"accountType": "UNIFIED"}),
            "demo_wallet",
        )
        accounts = result.get("list")
        if not isinstance(accounts, list) or len(accounts) != 1:
            raise BybitSnapshotError("demo_wallet: expected one UNIFIED account record")
        account = accounts[0]
        if not isinstance(account, dict) or account.get("accountType") != "UNIFIED":
            raise BybitSnapshotError("demo_wallet: account type mismatch")
        try:
            equity = Decimal(str(account["totalEquity"]))
            available = Decimal(str(account["totalAvailableBalance"]))
        except (KeyError, ValueError, ArithmeticError) as exc:
            raise BybitSnapshotError("demo_wallet: missing or invalid USD equity/availability") from exc
        if not equity.is_finite() or not available.is_finite():
            raise BybitSnapshotError("demo_wallet: non-finite USD values")
        return BybitAccountSummary(equity_usd=equity, available_usd=available)

    async def _demo_pages(self, endpoint: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.demo_identity_verified:
            raise BybitSnapshotError(f"{endpoint}: authenticated Demo identity not verified")
        request = dict(params)
        seen_cursors: set[str] = set()
        records: list[dict[str, Any]] = []
        for _ in range(100):
            result = self._v5_result(await getattr(self._exchange, endpoint)(request), endpoint)
            if result.get("category") != params["category"]:
                raise BybitSnapshotError(f"{endpoint}: category mismatch")
            page = result.get("list")
            if not isinstance(page, list) or not all(isinstance(x, dict) for x in page):
                raise BybitSnapshotError(f"{endpoint}: invalid list")
            records.extend(page)
            cursor = result.get("nextPageCursor") or ""
            if not cursor:
                return records
            if not isinstance(cursor, str) or cursor in seen_cursors:
                raise BybitSnapshotError(f"{endpoint}: pagination cursor invalid/repeated")
            seen_cursors.add(cursor)
            request["cursor"] = cursor
        raise BybitSnapshotError(f"{endpoint}: pagination exceeds safety limit")

    def _demo_market_symbol(self, native_symbol: Any) -> str:
        if not isinstance(native_symbol, str) or not native_symbol:
            raise BybitSnapshotError("demo_snapshot: missing instrument symbol")
        try:
            market = self._exchange.market(native_symbol)
            if (
                market.get("id") != native_symbol
                or market.get("linear") is not True
                or market.get("swap") is not True
                or market.get("settle") not in ("USDT", "USDC")
            ):
                raise ValueError("unsupported instrument")
            return str(market["symbol"])
        except Exception as exc:
            raise BybitSnapshotError("demo_snapshot: unsupported/unmapped instrument") from exc

    async def fetch_demo_positions(self) -> list[BybitPositionData]:
        """All linear derivatives; reject active categories ML4T cannot represent."""
        positions: list[BybitPositionData] = []
        for category, settle in (("linear", "USDT"), ("linear", "USDC"), ("inverse", None), ("option", None)):
            params: dict[str, Any] = {"category": category, "limit": 200}
            if settle:
                params["settleCoin"] = settle
            for row in await self._demo_pages("private_get_v5_position_list", params):
                try:
                    size = Decimal(str(row["size"]))
                except (KeyError, ValueError, ArithmeticError) as exc:
                    raise BybitSnapshotError("demo_positions: missing/invalid size") from exc
                if not size.is_finite() or size < 0:
                    raise BybitSnapshotError("demo_positions: invalid size")
                if size == 0:
                    continue
                if category != "linear" or row.get("positionIdx") not in (0, "0"):
                    raise BybitSnapshotError("demo_positions: active unsupported category/hedge position")
                if row.get("side") not in ("Buy", "Sell"):
                    raise BybitSnapshotError("demo_positions: unknown position side")
                try:
                    entry = Decimal(str(row["avgPrice"]))
                    mark = Decimal(str(row["markPrice"]))
                except (KeyError, ValueError, ArithmeticError) as exc:
                    raise BybitSnapshotError("demo_positions: entry/mark unavailable") from exc
                if not all(x.is_finite() and x > 0 for x in (entry, mark)):
                    raise BybitSnapshotError("demo_positions: invalid entry/mark")
                try:
                    created_ms = int(row["createdTime"])
                    if created_ms <= 0:
                        raise ValueError("nonpositive createdTime")
                except (KeyError, ValueError, TypeError) as exc:
                    raise BybitSnapshotError("demo_positions: missing/invalid creation time") from exc
                positions.append(BybitPositionData(
                    symbol=self._demo_market_symbol(row.get("symbol")),
                    side=row["side"].lower(), size=size,
                    entry_price=entry, mark_price=mark,
                    unrealized_pnl=None, leverage=None, liquidation_price=None, raw=row,
                ))
        symbols = [p.symbol for p in positions]
        if len(symbols) != len(set(symbols)):
            raise BybitSnapshotError("demo_positions: duplicate instrument/hedge mode")
        return positions

    async def fetch_demo_open_orders(self) -> list[BybitOrderData]:
        """All supported categories and pages; fail closed on unrepresentable open orders."""
        orders: list[BybitOrderData] = []
        scopes = (("linear", "USDT"), ("linear", "USDC"), ("inverse", None), ("spot", None), ("option", None))
        for category, settle in scopes:
            params: dict[str, Any] = {"category": category, "openOnly": 0, "limit": 50}
            if settle:
                params["settleCoin"] = settle
            for row in await self._demo_pages("private_get_v5_order_realtime", params):
                if category != "linear" or row.get("positionIdx") not in (0, "0"):
                    raise BybitSnapshotError("demo_orders: active unsupported category/hedge order")
                if row.get("stopOrderType") not in (None, "", "UNKNOWN"):
                    raise BybitSnapshotError("demo_orders: conditional order not representable")
                if row.get("orderStatus") not in ("New", "PartiallyFilled"):
                    raise BybitSnapshotError("demo_orders: unsupported open order status")
                if row.get("orderType") not in ("Market", "Limit") or row.get("side") not in ("Buy", "Sell"):
                    raise BybitSnapshotError("demo_orders: unsupported type/side")
                if not row.get("orderId"):
                    raise BybitSnapshotError("demo_orders: missing order ID")
                if not row.get("createdTime"):
                    raise BybitSnapshotError("demo_orders: missing creation timestamp")
                try:
                    amount = Decimal(str(row["qty"]))
                    filled = Decimal(str(row["cumExecQty"]))
                    price = Decimal(str(row["price"])) if row.get("price") not in (None, "") else None
                except (KeyError, ValueError, ArithmeticError) as exc:
                    raise BybitSnapshotError("demo_orders: invalid quantity/price") from exc
                if (
                    not amount.is_finite() or amount <= 0
                    or not filled.is_finite() or filled < 0 or filled > amount
                    or (price is not None and (not price.is_finite() or price <= 0))
                ):
                    raise BybitSnapshotError("demo_orders: inconsistent quantity/price")
                orders.append(BybitOrderData(
                    order_id=str(row["orderId"]), client_order_id=row.get("orderLinkId"),
                    symbol=self._demo_market_symbol(row.get("symbol")),
                    side=row["side"].lower(), order_type=row["orderType"].lower(),
                    price=price, quantity=amount, filled_quantity=filled,
                    status="partially_filled" if row["orderStatus"] == "PartiallyFilled" else "open",
                    timestamp=int(row["createdTime"]) if row.get("createdTime") else None,
                    raw=row,
                ))
        ids = [o.order_id for o in orders]
        if len(ids) != len(set(ids)):
            raise BybitSnapshotError("demo_orders: duplicate order ID")
        return orders

    @property
    def exchange(self) -> ccxta.bybit:
        return self._exchange

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        """Initialize markets and authenticate an explicitly selected Demo account."""
        self._connected = False
        self._verified_demo_identity = None
        self._preflight_identity_evidence = {}
        try:
            await self._exchange.load_markets()
            if self.config.environment is Environment.DEMO:
                await self._verify_demo_identity()
            self._connected = True
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def close(self) -> None:
        """Closes exchange async transport."""
        self._connected = False
        self._preflight_identity_evidence = dict(self._verified_demo_identity or {})
        self._verified_demo_identity = None
        try:
            await self._exchange.close()
        except Exception as exc:
            logger.warning("Error closing Bybit exchange: %s", type(exc).__name__)

    async def fetch_balance(self) -> dict[str, BybitBalance]:
        """Fetches unified account balances with Decimal values."""
        try:
            raw = await self._exchange.fetch_balance()
            balances: dict[str, BybitBalance] = {}
            if not isinstance(raw, dict):
                raise BybitError("Invalid balance response shape")
            total, free, used = (raw.get(field) for field in ("total", "free", "used"))
            if not all(isinstance(v, dict) for v in (total, free, used)):
                raise BybitError("Incomplete provider balance inventory")
            for cur, tot_val in total.items():
                if not isinstance(cur, str) or not cur:
                    raise BybitError("Invalid balance currency identity")
                amount = _strict_decimal(tot_val, "balance.total")
                available = _strict_decimal(free.get(cur), "balance.free")
                committed = _strict_decimal(used.get(cur), "balance.used")
                if amount < 0 or available < 0 or committed < 0:
                    raise BybitError("Negative provider balance field")
                balances[cur] = BybitBalance(
                    currency=cur, total=amount, free=available,
                    used=committed, raw=raw,
                )
            if set(free) != set(total) or set(used) != set(total):
                raise BybitError("Inconsistent balance currency inventory")
            return balances
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def fetch_positions(self, symbols: list[str] | None = None) -> list[BybitPositionData]:
        """Fetches active positions."""
        if self.config.environment is Environment.DEMO:
            positions = await self.fetch_demo_positions()
            return [p for p in positions if p.symbol in symbols] if symbols is not None else positions
        try:
            raw_positions = await self._exchange.fetch_positions(symbols)
            positions: list[BybitPositionData] = []
            if not isinstance(raw_positions, list):
                raise BybitError("Invalid positions response shape")
            seen = set()
            for p in raw_positions:
                if not isinstance(p, dict):
                    raise BybitError("Invalid native position row")
                contracts = p.get("contracts")
                if contracts is None:
                    contracts = p.get("size")
                size_dec = _strict_decimal(contracts, "position quantity")
                if size_dec < 0:
                    raise BybitError("Negative position quantity")
                if size_dec == 0:
                    continue  # Explicit zero is the only supported inactive row.
                symbol = p.get("symbol")
                _required_text(symbol, "position symbol")
                side = _required_text(p.get("side"), "position side",
                                      ("buy", "sell", "long", "short"))
                key = (symbol, side)
                if key in seen:
                    raise BybitError("Duplicate active native position")
                seen.add(key)

                def optional_decimal(field):
                    value = p.get(field)
                    return _strict_decimal(value, field) if value is not None else None

                positions.append(
                    BybitPositionData(
                        symbol=symbol, side=side, size=size_dec,
                        entry_price=optional_decimal("entryPrice"),
                        mark_price=optional_decimal("markPrice"),
                        unrealized_pnl=optional_decimal("unrealizedPnl"),
                        leverage=optional_decimal("leverage"),
                        liquidation_price=optional_decimal("liquidationPrice"),
                        raw=p,
                    )
                )
            return positions
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def fetch_open_orders(self, symbol: str | None = None) -> list[BybitOrderData]:
        """Fetches current open orders."""
        if self.config.environment is Environment.DEMO:
            orders = await self.fetch_demo_open_orders()
            return [o for o in orders if o.symbol == symbol] if symbol is not None else orders
        try:
            raw_orders = await self._exchange.fetch_open_orders(symbol)
            orders: list[BybitOrderData] = []
            if not isinstance(raw_orders, list):
                raise BybitError("Invalid open order response shape")
            seen = set()
            for o in raw_orders:
                if not isinstance(o, dict):
                    raise BybitError("Invalid native order row")
                order_id = o.get("id")
                _required_text(order_id, "order identity")
                # Provider IDs are opaque and may be case sensitive.
                if order_id in seen:
                    raise BybitError("Duplicate native open order")
                seen.add(order_id)
                native_symbol = o.get("symbol")
                _required_text(native_symbol, "order symbol")
                native_side = _required_text(o.get("side"), "order side", ("buy", "sell"))
                native_type = _required_text(o.get("type"), "order type",
                                             ("market", "limit"))
                native_status = _required_text(o.get("status"), "order status",
                                               ("open", "new", "partially_filled"))
                amount = _strict_decimal(o.get("amount"), "order quantity")
                filled = _strict_decimal(o.get("filled"), "order filled")
                if amount <= 0 or not 0 <= filled <= amount:
                    raise BybitError("Inconsistent provider order quantity/fill")
                if native_status == "partially_filled" and not 0 < filled < amount:
                    raise BybitError("Inconsistent partial-fill state")
                price = (_strict_decimal(o["price"], "order price")
                         if o.get("price") is not None else None)
                if native_type == "limit" and (price is None or price <= 0):
                    raise BybitError("Missing valid limit order price")
                orders.append(
                    BybitOrderData(
                        order_id=order_id, client_order_id=o.get("clientOrderId"),
                        symbol=native_symbol, side=native_side,
                        order_type=native_type, price=price,
                        quantity=amount, filled_quantity=filled,
                        status=native_status, timestamp=o.get("timestamp"), raw=o,
                    )
                )
            return orders
        except Exception as exc:
            raise self._wrap_exception(exc) from exc
    def _assert_execution_allowed(self, operation: str) -> None:
        if self.config.mode != Mode.EXECUTION:
            raise PermissionError(
                f"Remote order mutation ({operation}) prohibited in {self.config.mode.value} mode"
            )

    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: float | None = None,
        reduce_only: bool = False,
        params: dict[str, Any] | None = None,
    ) -> BybitOrderData:
        """Places a single order on Bybit."""
        self._assert_execution_allowed("create_order")
        order_params: dict[str, Any] = dict(params or {})
        if reduce_only:
            order_params["reduceOnly"] = True
        try:
            o = await self._exchange.create_order(
                symbol=symbol,
                type=order_type,
                side=side,
                amount=amount,
                price=price,
                params=order_params,
            )
            order_id = str(o.get("id", ""))
            if order_id and (o.get("timestamp") is None or o.get("status") is None):
                try:
                    hydrated = await self._exchange.fetch_order(order_id, symbol, params={"acknowledged": True})
                    if isinstance(hydrated, dict) and hydrated.get("id"):
                        o = hydrated
                except Exception as hydrate_err:
                    logger.debug("Failed to hydrate created order %s: %s", order_id, hydrate_err)
            return BybitOrderData(
                order_id=str(o.get("id", "")),
                client_order_id=o.get("clientOrderId"),
                symbol=str(o.get("symbol", symbol)),
                side=str(o.get("side", side)).lower(),
                order_type=str(o.get("type", order_type)).lower(),
                price=Decimal(str(o["price"])) if o.get("price") is not None else (Decimal(str(price)) if price is not None else None),
                quantity=Decimal(str(o["amount"])) if o.get("amount") is not None else Decimal(str(amount)),
                filled_quantity=Decimal(str(o.get("filled", 0) or 0)),
                status=str(o.get("status", "open")).lower(),
                timestamp=o.get("timestamp"),
                raw=o,
            )
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def cancel_order(self, order_id: str, symbol: str | None = None) -> bool:
        """Cancels an existing order by ID."""
        self._assert_execution_allowed("cancel_order")
        if symbol is None:
            raise ValueError("Bybit V5 requires a symbol to cancel an order")
        try:
            _ = await self._exchange.cancel_order(order_id, symbol)
            return True
        except ccxt.OrderNotFound:
            return False
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def edit_order(
        self,
        order_id: str,
        symbol: str,
        order_type: str,
        side: str,
        amount: float | None = None,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> BybitOrderData:
        """Natively amends an open order on Bybit."""
        self._assert_execution_allowed("edit_order")
        try:
            o = await self._exchange.edit_order(
                id=order_id,
                symbol=symbol,
                type=order_type,
                side=side,
                amount=amount,
                price=price,
                params=params or {},
            )
            order_id_str = str(o.get("id", order_id))
            if order_id_str and (o.get("timestamp") is None or o.get("status") is None):
                try:
                    hydrated = await self._exchange.fetch_order(order_id_str, symbol, params={"acknowledged": True})
                    if isinstance(hydrated, dict) and hydrated.get("id"):
                        o = hydrated
                except Exception as hydrate_err:
                    logger.debug("Failed to hydrate edited order %s: %s", order_id_str, hydrate_err)
            return BybitOrderData(
                order_id=str(o.get("id", order_id)),
                client_order_id=o.get("clientOrderId"),
                symbol=str(o.get("symbol", symbol)),
                side=str(o.get("side", side)).lower(),
                order_type=str(o.get("type", order_type)).lower(),
                price=Decimal(str(o["price"])) if o.get("price") is not None else (Decimal(str(price)) if price is not None else None),
                quantity=Decimal(str(o["amount"])) if o.get("amount") is not None else Decimal(str(amount or 0)),
                filled_quantity=Decimal(str(o.get("filled", 0) or 0)),
                status=str(o.get("status", "open")).lower(),
                timestamp=o.get("timestamp"),
                raw=o,
            )
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1m", limit: int = 250
    ) -> list[list[float]]:
        """Fetches historical candles."""
        try:
            return await self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    def _wrap_exception(self, exc: Exception) -> BybitError:
        """Maps CCXT exceptions to structured connector exceptions without leaking secrets."""
        if isinstance(exc, BybitError):
            return exc
        # CCXT errors may contain account bodies, URLs or credentials.
        msg = type(exc).__name__
        if isinstance(exc, ccxt.AuthenticationError):
            return BybitAuthenticationError(f"Authentication failed: {msg}")
        if isinstance(exc, ccxt.RateLimitExceeded):
            return BybitRateLimitError(f"Rate limit exceeded: {msg}")
        if isinstance(exc, ccxt.InsufficientFunds):
            return BybitInsufficientFundsError(f"Insufficient funds: {msg}")
        if isinstance(exc, ccxt.OrderNotFound):
            return BybitOrderNotFoundError(f"Order not found: {msg}")
        if isinstance(exc, ccxt.InvalidOrder):
            return BybitInvalidOrderError(f"Invalid order parameters: {msg}")
        if isinstance(exc, ccxt.NetworkError):
            return BybitNetworkError(f"Network error: {msg}")
        return BybitError(f"Bybit error: {msg}")
