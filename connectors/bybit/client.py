"""Asynchronous CCXT transport client for Bybit V5 Unified Trading Account."""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import ccxt.async_support as ccxta

from connectors.bybit.config import BybitConfig, Environment
from connectors.bybit.models import (
    BybitAccountSummary,
    BybitAuthenticationError,
    BybitBalance,
    BybitError,
    BybitExecutionData,
    BybitInsufficientFundsError,
    BybitInvalidOrderError,
    BybitNetworkError,
    BybitOrderData,
    BybitOrderNotFoundError,
    BybitPositionData,
    BybitRateLimitError,
    BybitSnapshotError,
)
from connectors.ccxt_support.client import BaseCCXTClient
from connectors.ccxt_support.models import CCXTErrorSpec
from connectors.common.validation import required_text, strict_decimal

logger = logging.getLogger(__name__)

_ERROR_SPEC = CCXTErrorSpec(
    provider_label="Bybit",
    base_error=BybitError,
    auth_error=BybitAuthenticationError,
    rate_limit_error=BybitRateLimitError,
    insufficient_funds_error=BybitInsufficientFundsError,
    order_not_found_error=BybitOrderNotFoundError,
    invalid_order_error=BybitInvalidOrderError,
    network_error=BybitNetworkError,
)


class BybitClient(BaseCCXTClient):
    """Async client managing Bybit REST commands with strict error translation."""

    _balance_cls = BybitBalance
    _position_cls = BybitPositionData
    _order_cls = BybitOrderData
    _cancel_symbol_required_msg = "Bybit V5 requires a symbol to cancel an order"

    def __init__(self, config: BybitConfig, exchange: ccxta.bybit | None = None) -> None:
        super().__init__(config=config, error_spec=_ERROR_SPEC, exchange=exchange)
        # CCXT's demo mode routes authenticated REST requests to Bybit Demo,
        # which is separate from the Testnet sandbox. Public market-data feeds
        # should use the mainnet feed independently (BybitKlineFeed).
        if exchange is not None and config.environment is Environment.DEMO:
            exchange.enable_demo_trading(True)

        self._verified_demo_identity: dict[str, Any] | None = None
        self._preflight_identity_evidence: dict[str, Any] = {}

    def _build_exchange(self) -> ccxta.bybit:
        exchange_params: dict[str, Any] = {
            "enableRateLimit": self.config.enable_rate_limit,
            "timeout": self.config.timeout_ms,
            "options": {"defaultType": self.config.default_type},
        }
        if self.config.api_key:
            exchange_params["apiKey"] = self.config.api_key
        if self.config.api_secret:
            exchange_params["secret"] = self.config.api_secret
        exchange = ccxta.bybit(exchange_params)
        if self.config.environment == Environment.TESTNET:
            exchange.set_sandbox_mode(True)
        elif self.config.environment == Environment.DEMO:
            exchange.enable_demo_trading(True)
        return exchange

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
    def v5_result(response: Any, endpoint: str) -> dict[str, Any]:
        if not isinstance(response, dict) or response.get("retCode") not in (0, "0"):
            raise BybitSnapshotError(f"{endpoint}: missing/failed V5 response")
        result = response.get("result")
        if not isinstance(result, dict):
            raise BybitSnapshotError(f"{endpoint}: invalid result object")
        return result

    def _require_demo_endpoint(self) -> None:
        exchange = self.exchange
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
        key_info = self.v5_result(
            await self.exchange.private_get_v5_user_query_api(), "demo_key_info"
        )
        uid = str(key_info.get("userID", ""))
        if (
            not uid or uid != cfg.expected_demo_uid.strip()
            or key_info.get("apiKey") != cfg.api_key
            or str(key_info.get("uta")) != "1"
        ):
            raise BybitSnapshotError("demo_identity: authenticated UID, key or UTA does not match")
        account_info = self.v5_result(
            await self.exchange.private_get_v5_account_info(), "demo_account_info"
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
        result = self.v5_result(
            await self.exchange.private_get_v5_account_wallet_balance({"accountType": "UNIFIED"}),
            "demo_wallet",
        )
        accounts = result.get("list")
        if not isinstance(accounts, list) or len(accounts) != 1:
            raise BybitSnapshotError("demo_wallet: expected one UNIFIED account record")
        account = accounts[0]
        if not isinstance(account, dict) or account.get("accountType") != "UNIFIED":
            raise BybitSnapshotError("demo_wallet: account type mismatch")
        try:
            equity = strict_decimal(account.get("totalEquity"), "totalEquity")
            available = strict_decimal(account.get("totalAvailableBalance"), "totalAvailableBalance")
        except ValueError as exc:
            raise BybitSnapshotError("demo_wallet: missing or invalid USD equity/availability") from exc
        return BybitAccountSummary(equity_usd=equity, available_usd=available)

    async def _demo_pages(self, endpoint: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.demo_identity_verified:
            raise BybitSnapshotError(f"{endpoint}: authenticated Demo identity not verified")
        request = dict(params)
        seen_cursors: set[str] = set()
        records: list[dict[str, Any]] = []
        for _ in range(100):
            result = self.v5_result(await getattr(self.exchange, endpoint)(request), endpoint)
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
            market = self.exchange.market(native_symbol)
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

    @staticmethod
    def _demo_created_ms(row: dict[str, Any], endpoint: str) -> int:
        try:
            created_ms = int(row["createdTime"])
            if created_ms <= 0:
                raise ValueError("nonpositive createdTime")
            return created_ms
        except (KeyError, ValueError, TypeError) as exc:
            raise BybitSnapshotError(f"{endpoint}: missing/invalid creation time") from exc

    async def fetch_demo_positions(self) -> list[BybitPositionData]:
        """All linear derivatives; reject active categories ML4T cannot represent."""
        positions: list[BybitPositionData] = []
        for category, settle in (("linear", "USDT"), ("linear", "USDC"), ("inverse", None), ("option", None)):
            params: dict[str, Any] = {"category": category, "limit": 200}
            if settle:
                params["settleCoin"] = settle
            for row in await self._demo_pages("private_get_v5_position_list", params):
                try:
                    size = strict_decimal(row.get("size"), "size")
                except ValueError as exc:
                    raise BybitSnapshotError("demo_positions: missing/invalid size") from exc
                if size < 0:
                    raise BybitSnapshotError("demo_positions: invalid size")
                if size == 0:
                    continue
                if category != "linear" or row.get("positionIdx") not in (0, "0"):
                    raise BybitSnapshotError("demo_positions: active unsupported category/hedge position")
                if row.get("side") not in ("Buy", "Sell"):
                    raise BybitSnapshotError("demo_positions: unknown position side")
                try:
                    entry = strict_decimal(row.get("avgPrice"), "avgPrice")
                    mark = strict_decimal(row.get("markPrice"), "markPrice")
                except ValueError as exc:
                    raise BybitSnapshotError("demo_positions: entry/mark unavailable") from exc
                if entry <= 0 or mark <= 0:
                    raise BybitSnapshotError("demo_positions: invalid entry/mark")
                self._demo_created_ms(row, "demo_positions")
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
                created_ms = self._demo_created_ms(row, "demo_orders")
                try:
                    amount = strict_decimal(row.get("qty"), "qty")
                    filled = strict_decimal(row.get("cumExecQty"), "cumExecQty")
                    raw_price = row.get("price")
                    price = strict_decimal(raw_price, "price") if raw_price not in (None, "") else None
                except ValueError as exc:
                    raise BybitSnapshotError("demo_orders: invalid quantity/price") from exc
                if (
                    amount <= 0
                    or filled < 0
                    or filled > amount
                    or (price is not None and price <= 0)
                ):
                    raise BybitSnapshotError("demo_orders: inconsistent quantity/price")
                orders.append(BybitOrderData(
                    order_id=str(row["orderId"]), client_order_id=row.get("orderLinkId"),
                    symbol=self._demo_market_symbol(row.get("symbol")),
                    side=row["side"].lower(), order_type=row["orderType"].lower(),
                    price=price, quantity=amount, filled_quantity=filled,
                    status="partially_filled" if row["orderStatus"] == "PartiallyFilled" else "open",
                    timestamp=created_ms,
                    raw=row,
                ))
        ids = [o.order_id for o in orders]
        if len(ids) != len(set(ids)):
            raise BybitSnapshotError("demo_orders: duplicate order ID")
        return orders

    @staticmethod
    def _extract_unified_account_record(raw_balance: dict[str, Any]) -> dict[str, Any]:
        """Extract the single UNIFIED account info record from a CCXT balance payload."""
        if not isinstance(raw_balance, dict):
            raise BybitSnapshotError("demo_wallet_ccxt: invalid balance payload")
        info = raw_balance.get("info")
        if not isinstance(info, dict):
            raise BybitSnapshotError("demo_wallet_ccxt: missing raw info payload")
        candidates: Any = None
        result = info.get("result")
        if isinstance(result, dict) and isinstance(result.get("list"), list):
            candidates = result["list"]
        elif isinstance(info.get("list"), list):
            candidates = info["list"]
        elif isinstance(info.get("data"), list):
            candidates = info["data"]
        if not isinstance(candidates, list) or len(candidates) != 1:
            raise BybitSnapshotError("demo_wallet_ccxt: expected one UNIFIED account record")
        account = candidates[0]
        if not isinstance(account, dict):
            raise BybitSnapshotError("demo_wallet_ccxt: invalid account record")
        account_type = account.get("accountType")
        if account_type not in (None, "UNIFIED"):
            raise BybitSnapshotError("demo_wallet_ccxt: account type mismatch")
        if account_type is None and "totalEquity" not in account:
            raise BybitSnapshotError("demo_wallet_ccxt: missing UNIFIED equity field")
        return account

    @classmethod
    def parse_ccxt_account_summary(cls, raw_balance: dict[str, Any]) -> BybitAccountSummary:
        """Parse exact UNIFIED Decimal conservation and margin fields from CCXT balance data."""
        account = cls._extract_unified_account_record(raw_balance)
        required_fields = (
            "totalEquity",
            "totalAvailableBalance",
            "totalWalletBalance",
            "totalPerpUPL",
            "totalInitialMargin",
            "totalMaintenanceMargin",
        )
        parsed: dict[str, Decimal] = {}
        for field_name in required_fields:
            if field_name not in account or account[field_name] in (None, ""):
                raise BybitSnapshotError(f"demo_wallet_ccxt: missing {field_name}")
            parsed[field_name] = strict_decimal(
                account[field_name], f"ccxt.{field_name}", error_cls=BybitError
            )
        return BybitAccountSummary(
            equity_usd=parsed["totalEquity"],
            available_usd=parsed["totalAvailableBalance"],
            wallet_balance_usd=parsed["totalWalletBalance"],
            unrealized_pnl_usd=parsed["totalPerpUPL"],
            initial_margin_usd=parsed["totalInitialMargin"],
            maintenance_margin_usd=parsed["totalMaintenanceMargin"],
        )

    @staticmethod
    def _coerce_bool_flag(value: object, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            norm = value.strip().lower()
            if norm in ("true", "1"):
                return True
            if norm in ("false", "0", ""):
                return False
        return default

    def _parse_ccxt_position(self, row: dict[str, Any], *, category: str) -> BybitPositionData | None:
        if not isinstance(row, dict):
            raise BybitSnapshotError("demo_positions_ccxt: invalid row")
        info = row.get("info") if isinstance(row.get("info"), dict) else {}
        raw_size = info.get("size") if info.get("size") not in (None, "") else row.get("contracts")
        size = strict_decimal(raw_size, "position.size", error_cls=BybitError)
        if size < 0:
            raise BybitSnapshotError("demo_positions_ccxt: negative position size")
        if size == 0:
            return None
        pos_idx = info.get("positionIdx")
        if category != "linear" or pos_idx not in (None, 0, "0"):
            raise BybitSnapshotError("demo_positions_ccxt: active unsupported category/hedge position")
        raw_side = info.get("side") or row.get("side")
        side_norm = required_text(
            raw_side, "position.side", allowed={"buy", "sell", "long", "short"}, error_cls=BybitError
        )
        if side_norm == "long":
            side_norm = "buy"
        elif side_norm == "short":
            side_norm = "sell"
        raw_symbol = info.get("symbol") or row.get("symbol")
        symbol = (
            self._demo_market_symbol(raw_symbol)
            if isinstance(info.get("symbol"), str) and info.get("symbol")
            else str(raw_symbol or "")
        )
        if not symbol:
            raise BybitSnapshotError("demo_positions_ccxt: missing symbol")
        entry_raw = info.get("avgPrice") if info.get("avgPrice") not in (None, "") else row.get("entryPrice")
        mark_raw = info.get("markPrice") if info.get("markPrice") not in (None, "") else row.get("markPrice")
        entry = strict_decimal(entry_raw, "position.entry_price", error_cls=BybitError)
        mark = strict_decimal(mark_raw, "position.mark_price", error_cls=BybitError)
        if entry <= 0 or mark <= 0:
            raise BybitSnapshotError("demo_positions_ccxt: invalid entry/mark price")
        upl_raw = info.get("unrealisedPnl") if info.get("unrealisedPnl") not in (None, "") else row.get("unrealizedPnl")
        unrealized = (
            strict_decimal(upl_raw, "position.unrealized_pnl", error_cls=BybitError)
            if upl_raw not in (None, "")
            else Decimal("0")
        )
        return BybitPositionData(
            symbol=symbol,
            side=side_norm,
            size=size,
            entry_price=entry,
            mark_price=mark,
            unrealized_pnl=unrealized,
            leverage=Decimal("0"),
            liquidation_price=None,
            raw=row,
        )

    def parse_ccxt_order(self, row: dict[str, Any], *, category: str = "linear") -> BybitOrderData:
        if not isinstance(row, dict):
            raise BybitSnapshotError("demo_orders_ccxt: invalid order row")
        info = row.get("info") if isinstance(row.get("info"), dict) else {}
        if category != "linear":
            raise BybitSnapshotError("demo_orders_ccxt: active unsupported category order")
        pos_idx = info.get("positionIdx")
        if pos_idx not in (None, 0, "0"):
            raise BybitSnapshotError("demo_orders_ccxt: hedge mode order unsupported")
        if info.get("stopOrderType") not in (None, "", "UNKNOWN"):
            raise BybitSnapshotError("demo_orders_ccxt: conditional order not representable")
        order_id = str(info.get("orderId") or row.get("id") or "").strip()
        if not order_id:
            raise BybitSnapshotError("demo_orders_ccxt: missing order ID")
        raw_coid = info.get("orderLinkId")
        if raw_coid in (None, ""):
            raw_coid = row.get("clientOrderId")
        client_order_id = str(raw_coid).strip() if isinstance(raw_coid, str) and raw_coid.strip() else None
        native_symbol = info.get("symbol")
        if isinstance(native_symbol, str) and native_symbol:
            symbol = self._demo_market_symbol(native_symbol)
        else:
            symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            raise BybitSnapshotError("demo_orders_ccxt: missing symbol")
        side = required_text(info.get("side") or row.get("side"), "order.side", allowed={"buy", "sell"}, error_cls=BybitError)
        order_type = required_text(
            info.get("orderType") or row.get("type"), "order.type", allowed={"market", "limit"}, error_cls=BybitError
        )
        qty_raw = info.get("qty") if info.get("qty") not in (None, "") else row.get("amount")
        filled_raw = info.get("cumExecQty") if info.get("cumExecQty") not in (None, "") else row.get("filled", "0")
        price_raw = info.get("price") if info.get("price") not in (None, "") else row.get("price")
        qty = strict_decimal(qty_raw, "order.quantity", error_cls=BybitError)
        filled = strict_decimal(filled_raw, "order.filled_quantity", error_cls=BybitError)
        price = strict_decimal(price_raw, "order.price", error_cls=BybitError) if price_raw not in (None, "", 0, "0") else None
        if qty <= 0 or filled < 0 or filled > qty or (price is not None and price <= 0):
            raise BybitSnapshotError("demo_orders_ccxt: inconsistent quantity/price")
        raw_status = str(info.get("orderStatus") or row.get("status") or "").strip().lower()
        status_map = {
            "new": "open",
            "open": "open",
            "partiallyfilled": "partially_filled",
            "partially_filled": "partially_filled",
            "filled": "filled",
            "closed": "filled",
            "cancelled": "canceled",
            "canceled": "canceled",
            "rejected": "rejected",
            "deactivated": "expired",
            "expired": "expired",
        }
        status = status_map.get(raw_status)
        if status is None:
            raise BybitSnapshotError(f"demo_orders_ccxt: unsupported order status {raw_status!r}")
        ts_raw = row.get("timestamp") if row.get("timestamp") is not None else info.get("createdTime")
        ts = int(ts_raw) if ts_raw not in (None, "") else 0
        reduce_only = self._coerce_bool_flag(
            row.get("reduceOnly") if row.get("reduceOnly") is not None else info.get("reduceOnly")
        )
        return BybitOrderData(
            order_id=order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            price=price,
            quantity=qty,
            filled_quantity=filled,
            status=status,
            timestamp=ts,
            raw=row,
            reduce_only=reduce_only,
        )

    def parse_ccxt_execution(self, row: dict[str, Any]) -> BybitExecutionData:
        if not isinstance(row, dict):
            raise BybitSnapshotError("demo_executions_ccxt: invalid execution row")
        info = row.get("info") if isinstance(row.get("info"), dict) else {}
        exec_id = str(info.get("execId") or row.get("id") or "").strip()
        order_id = str(info.get("orderId") or row.get("order") or "").strip()
        if not exec_id or not order_id:
            raise BybitSnapshotError("demo_executions_ccxt: missing execId or orderId")
        raw_coid = info.get("orderLinkId")
        if raw_coid in (None, ""):
            raw_coid = row.get("clientOrderId")
        client_order_id = str(raw_coid).strip() if isinstance(raw_coid, str) and raw_coid.strip() else None
        native_symbol = info.get("symbol")
        if isinstance(native_symbol, str) and native_symbol:
            symbol = self._demo_market_symbol(native_symbol)
        else:
            symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            raise BybitSnapshotError("demo_executions_ccxt: missing symbol")
        side = required_text(info.get("side") or row.get("side"), "execution.side", allowed={"buy", "sell"}, error_cls=BybitError)
        qty_raw = info.get("execQty") if info.get("execQty") not in (None, "") else row.get("amount")
        price_raw = info.get("execPrice") if info.get("execPrice") not in (None, "") else row.get("price")
        fee_dict = row.get("fee") if isinstance(row.get("fee"), dict) else {}
        fee_raw = info.get("execFee") if info.get("execFee") not in (None, "") else fee_dict.get("cost")
        qty = strict_decimal(qty_raw, "execution.quantity", error_cls=BybitError)
        price = strict_decimal(price_raw, "execution.price", error_cls=BybitError)
        fee = strict_decimal(fee_raw, "execution.fee", error_cls=BybitError)
        if qty <= 0 or price <= 0:
            raise BybitSnapshotError("demo_executions_ccxt: non-positive execution qty/price")
        ts_raw = row.get("timestamp") if row.get("timestamp") is not None else info.get("execTime")
        try:
            ts_ms = int(ts_raw)
        except (TypeError, ValueError) as exc:
            raise BybitSnapshotError("demo_executions_ccxt: invalid execution timestamp") from exc
        if ts_ms <= 0:
            raise BybitSnapshotError("demo_executions_ccxt: non-positive execution timestamp")
        fee_currency = info.get("feeCurrency") or fee_dict.get("currency")
        return BybitExecutionData(
            execution_id=exec_id,
            order_id=order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            quantity=qty,
            price=price,
            fee=fee,
            fee_currency=str(fee_currency) if fee_currency else None,
            timestamp_ms=ts_ms,
            raw=row,
        )

    async def fetch_server_time_ms(self) -> int:
        """Read authoritative exchange server time in epoch milliseconds via CCXT."""
        try:
            server_ms = await self.exchange.fetch_time()
            if not isinstance(server_ms, int) or isinstance(server_ms, bool) or server_ms <= 0:
                raise BybitSnapshotError("demo_time: invalid server timestamp")
            return server_ms
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def fetch_demo_account_summary_ccxt(self) -> BybitAccountSummary:
        """Read UNIFIED equity, cash, unrealized PnL, and margin totals via CCXT."""
        if not self.demo_identity_verified:
            raise BybitSnapshotError("demo_wallet_ccxt: authenticated Demo identity not verified")
        try:
            raw_balance = await self.exchange.fetch_balance(params={"type": "unified"})
            return self.parse_ccxt_account_summary(raw_balance)
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def fetch_demo_positions_ccxt(self) -> list[BybitPositionData]:
        """Read all active positions via CCXT and fail closed on non-linear/hedge exposure."""
        if not self.demo_identity_verified:
            raise BybitSnapshotError("demo_positions_ccxt: authenticated Demo identity not verified")
        positions: list[BybitPositionData] = []
        seen_symbols: set[str] = set()
        try:
            scopes: tuple[tuple[str, dict[str, Any]], ...] = (
                ("linear", {"type": "swap", "subType": "linear", "settleCoin": "USDT", "paginate": True, "limit": 200}),
                ("linear", {"type": "swap", "subType": "linear", "settleCoin": "USDC", "paginate": True, "limit": 200}),
                ("inverse", {"type": "swap", "subType": "inverse", "paginate": True, "limit": 200}),
                ("option", {"type": "option", "paginate": True, "limit": 200}),
            )
            for category, params in scopes:
                rows = await self.exchange.fetch_positions(params=params)
                if not isinstance(rows, list):
                    raise BybitSnapshotError("demo_positions_ccxt: invalid positions list")
                for row in rows:
                    parsed = self._parse_ccxt_position(row, category=category)
                    if parsed is None:
                        continue
                    if parsed.symbol in seen_symbols:
                        raise BybitSnapshotError("demo_positions_ccxt: duplicate instrument position")
                    seen_symbols.add(parsed.symbol)
                    positions.append(parsed)
            return positions
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def fetch_demo_orders_ccxt(self, since_ms: int | None = None) -> list[BybitOrderData]:
        """Read open plus canceled/closed linear orders via CCXT and reject unsupported active categories."""
        if not self.demo_identity_verified:
            raise BybitSnapshotError("demo_orders_ccxt: authenticated Demo identity not verified")
        orders_by_id: dict[str, BybitOrderData] = {}
        try:
            open_scopes: tuple[tuple[str, dict[str, Any]], ...] = (
                ("linear", {"type": "swap", "subType": "linear", "settleCoin": "USDT", "paginate": True, "limit": 50}),
                ("linear", {"type": "swap", "subType": "linear", "settleCoin": "USDC", "paginate": True, "limit": 50}),
                ("inverse", {"type": "swap", "subType": "inverse", "paginate": True, "limit": 50}),
                ("spot", {"type": "spot", "paginate": True, "limit": 50}),
                ("option", {"type": "option", "paginate": True, "limit": 50}),
            )
            for category, params in open_scopes:
                rows = await self.exchange.fetch_open_orders(params=params)
                if not isinstance(rows, list):
                    raise BybitSnapshotError("demo_orders_ccxt: invalid open orders response")
                for row in rows:
                    parsed = self.parse_ccxt_order(row, category=category)
                    orders_by_id[parsed.order_id] = parsed
            history_scopes = (
                {"type": "swap", "subType": "linear", "settleCoin": "USDT", "paginate": True, "limit": 50},
                {"type": "swap", "subType": "linear", "settleCoin": "USDC", "paginate": True, "limit": 50},
            )
            for params in history_scopes:
                rows = await self.exchange.fetch_canceled_and_closed_orders(since=since_ms, params=params)
                if not isinstance(rows, list):
                    raise BybitSnapshotError("demo_orders_ccxt: invalid history orders response")
                for row in rows:
                    parsed = self.parse_ccxt_order(row, category="linear")
                    if parsed.order_id not in orders_by_id:
                        orders_by_id[parsed.order_id] = parsed
            return list(orders_by_id.values())
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def fetch_demo_executions_ccxt(
        self,
        since_ms: int | None = None,
        symbol: str | None = None,
    ) -> list[BybitExecutionData]:
        """Read actual linear trade executions via CCXT, deduplicated by execId."""
        if not self.demo_identity_verified:
            raise BybitSnapshotError("demo_executions_ccxt: authenticated Demo identity not verified")
        execs_by_id: dict[str, BybitExecutionData] = {}
        try:
            scopes = (
                ({"type": "swap", "subType": "linear", "settleCoin": "USDT", "paginate": True, "limit": 100},)
                if symbol is not None
                else (
                    {"type": "swap", "subType": "linear", "settleCoin": "USDT", "paginate": True, "limit": 100},
                    {"type": "swap", "subType": "linear", "settleCoin": "USDC", "paginate": True, "limit": 100},
                )
            )
            for params in scopes:
                rows = await self.exchange.fetch_my_trades(symbol=symbol, since=since_ms, params=params)
                if not isinstance(rows, list):
                    raise BybitSnapshotError("demo_executions_ccxt: invalid executions response")
                for row in rows:
                    parsed = self.parse_ccxt_execution(row)
                    execs_by_id[parsed.execution_id] = parsed
            return sorted(execs_by_id.values(), key=lambda ex: (ex.timestamp_ms, ex.execution_id))
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def connect(self) -> None:
        """Initialize markets and authenticate an explicitly selected Demo account."""
        self._connected = False
        self._verified_demo_identity = None
        self._preflight_identity_evidence = {}
        try:
            await self.exchange.load_markets()
            if self.config.environment is Environment.DEMO:
                await self._verify_demo_identity()
            self._connected = True
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def close(self) -> None:
        """Close exchange async transport and clear live Demo identity verification."""
        self._preflight_identity_evidence = dict(self._verified_demo_identity or {})
        self._verified_demo_identity = None
        await super().close()

    async def fetch_positions(self, symbols: list[str] | None = None) -> list[BybitPositionData]:
        """Fetch active positions, routing Demo accounts through V5 direct pagination."""
        if self.config.environment is Environment.DEMO:
            positions = await self.fetch_demo_positions()
            return [p for p in positions if p.symbol in symbols] if symbols is not None else positions
        return await super().fetch_positions(symbols)

    async def fetch_open_orders(self, symbol: str | None = None) -> list[BybitOrderData]:
        """Fetch current open orders, routing Demo accounts through V5 direct pagination."""
        if self.config.environment is Environment.DEMO:
            orders = await self.fetch_demo_open_orders()
            return [o for o in orders if o.symbol == symbol] if symbol is not None else orders
        return await super().fetch_open_orders(symbol)

    async def _parse_mutated_order(
        self,
        raw: Any,
        *,
        fallback_order_id: str,
        symbol: str,
        side: str,
        order_type: str,
        amount: float | None,
        price: float | None,
    ) -> BybitOrderData:
        if not isinstance(raw, dict):
            return self.parse_order_row(raw, allow_terminal=True)
        order_id = str(raw.get("id") or fallback_order_id)
        if order_id and (raw.get("timestamp") is None or raw.get("status") is None):
            try:
                hydrated = await self.exchange.fetch_order(
                    order_id, symbol, params={"acknowledged": True}
                )
                if isinstance(hydrated, dict) and hydrated.get("id"):
                    raw = hydrated
            except Exception as hydrate_err:
                logger.debug("Failed to hydrate order %s: %s", order_id, hydrate_err)
        merged: dict[str, Any] = {
            **raw,
            "id": raw.get("id") if raw.get("id") is not None else fallback_order_id,
            "symbol": raw.get("symbol") or symbol,
            "side": raw.get("side") or side,
            "type": raw.get("type") or order_type,
            "filled": raw.get("filled") if raw.get("filled") is not None else 0,
            "status": raw.get("status") or "open",
            "price": raw.get("price") if raw.get("price") is not None else price,
        }
        if raw.get("amount") is not None:
            merged["amount"] = raw.get("amount")
        elif amount is not None:
            merged["amount"] = amount
        parsed = self.parse_order_row(merged, allow_terminal=True)
        parsed.raw = raw
        return parsed

    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: float | None = None,
        reduce_only: bool = False,
        client_order_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> BybitOrderData:
        """Place a single order on Bybit with optional orderLinkId validation."""
        order_params: dict[str, Any] = dict(params or {})
        if client_order_id is not None:
            normalized_coid = client_order_id.strip()
            if not normalized_coid or len(normalized_coid) > 36:
                raise BybitInvalidOrderError(
                    "client_order_id must be 1..36 characters for Bybit orderLinkId"
                )
            existing_coid = order_params.get("orderLinkId")
            if existing_coid is not None and str(existing_coid) != normalized_coid:
                raise BybitInvalidOrderError("Conflicting orderLinkId and client_order_id")
            order_params["orderLinkId"] = normalized_coid
        order = await super().create_order(
            symbol=symbol,
            side=side,
            order_type=order_type,
            amount=amount,
            price=price,
            reduce_only=reduce_only,
            params=order_params,
        )
        if not order.client_order_id and "orderLinkId" in order_params:
            order.client_order_id = str(order_params["orderLinkId"])
        raw_ro = order.raw.get("reduceOnly") if isinstance(order.raw, dict) else None
        order.reduce_only = self._coerce_bool_flag(raw_ro, default=reduce_only)
        return order
