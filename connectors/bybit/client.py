"""Asynchronous CCXT transport client for Bybit V5 Unified Trading Account."""
from __future__ import annotations

import logging
from typing import Any

import ccxt.async_support as ccxta

from connectors.bybit.config import BybitConfig, Environment
from connectors.bybit.models import (
    BybitAccountSummary,
    BybitAuthenticationError,
    BybitBalance,
    BybitError,
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
from connectors.common.validation import strict_decimal

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
