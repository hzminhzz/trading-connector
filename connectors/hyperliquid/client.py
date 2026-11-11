# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportMissingImports=false
# type: ignore
"""Asynchronous CCXT transport client for Hyperliquid DEX."""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import ccxt
import ccxt.async_support as ccxta

from connectors.hyperliquid.config import Environment, HyperliquidConfig, Mode
from connectors.hyperliquid.models import (
    HyperliquidAuthenticationError,
    HyperliquidBalance,
    HyperliquidError,
    HyperliquidInsufficientFundsError,
    HyperliquidInvalidOrderError,
    HyperliquidNetworkError,
    HyperliquidOrderData,
    HyperliquidOrderNotFoundError,
    HyperliquidPositionData,
    HyperliquidRateLimitError,
)

logger = logging.getLogger(__name__)


def _strict_decimal(value: object, field: str) -> Decimal:
    """Reject absent, bool, malformed or non-finite provider finance."""
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise HyperliquidError(f"Invalid provider {field}")
    try:
        result = Decimal(str(value))
    except (ValueError, ArithmeticError) as exc:
        raise HyperliquidError(f"Invalid provider {field}") from exc
    if not result.is_finite() or len(result.as_tuple().digits) > 256 or abs(result.as_tuple().exponent) > 1000:
        raise HyperliquidError(f"Invalid provider {field}")
    return result


def _required_text(value: object, field: str, allowed: tuple[str, ...] | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HyperliquidError(f"Invalid provider {field}")
    normalized = value.strip().lower()
    if allowed is not None and normalized not in allowed:
        raise HyperliquidError(f"Unsupported provider {field}")
    return normalized




class HyperliquidClient:
    """Async transport client for Hyperliquid using ccxt.async_support."""

    def __init__(self, config: HyperliquidConfig, exchange: ccxta.hyperliquid | None = None) -> None:
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
            if config.wallet_address:
                exchange_params["walletAddress"] = config.wallet_address
            if config.private_key:
                exchange_params["privateKey"] = config.private_key
            if config.vault_address:
                exchange_params["vaultAddress"] = config.vault_address

            self._exchange = ccxta.hyperliquid(exchange_params)
            if config.environment == Environment.TESTNET:
                self._exchange.set_sandbox_mode(True)

        self._connected = False

    @property
    def exchange(self) -> ccxta.hyperliquid:
        return self._exchange

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _assert_execution_allowed(self, operation: str) -> None:
        if self.config.mode != Mode.EXECUTION:
            raise PermissionError(
                f"Remote order mutation ({operation}) prohibited in {self.config.mode.value} mode"
            )

    async def connect(self) -> None:
        """Loads markets and initializes connection."""
        try:
            await self._exchange.load_markets()
            self._connected = True
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def close(self) -> None:
        """Closes exchange async transport."""
        self._connected = False
        try:
            await self._exchange.close()
        except Exception as exc:
            logger.warning("Error closing Hyperliquid exchange: %s", exc)

    async def fetch_balance(self) -> dict[str, HyperliquidBalance]:
        """Fetches account balance (USDC cross margin)."""
        try:
            raw = await self._exchange.fetch_balance()
            balances: dict[str, HyperliquidBalance] = {}
            if not isinstance(raw, dict):
                raise HyperliquidError("Invalid balance response shape")
            total, free, used = (raw.get(field) for field in ("total", "free", "used"))
            if not all(isinstance(v, dict) for v in (total, free, used)):
                raise HyperliquidError("Incomplete provider balance inventory")
            for cur, tot_val in total.items():
                if not isinstance(cur, str) or not cur:
                    raise HyperliquidError("Invalid balance currency identity")
                amount = _strict_decimal(tot_val, "balance.total")
                available = _strict_decimal(free.get(cur), "balance.free")
                committed = _strict_decimal(used.get(cur), "balance.used")
                if amount < 0 or available < 0 or committed < 0:
                    raise HyperliquidError("Negative provider balance field")
                balances[cur] = HyperliquidBalance(
                    currency=cur, total=amount, free=available,
                    used=committed, raw=raw,
                )
            if set(free) != set(total) or set(used) != set(total):
                raise HyperliquidError("Inconsistent balance currency inventory")
            return balances
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def fetch_positions(self, symbols: list[str] | None = None) -> list[HyperliquidPositionData]:
        """Fetches active positions."""
        try:
            raw_positions = await self._exchange.fetch_positions(symbols)
            positions: list[HyperliquidPositionData] = []
            if not isinstance(raw_positions, list):
                raise HyperliquidError("Invalid positions response shape")
            seen = set()
            for p in raw_positions:
                if not isinstance(p, dict):
                    raise HyperliquidError("Invalid native position row")
                contracts = p.get("contracts")
                if contracts is None:
                    contracts = p.get("size")
                size_dec = _strict_decimal(contracts, "position quantity")
                if size_dec < 0:
                    raise HyperliquidError("Negative position quantity")
                if size_dec == 0:
                    continue  # Explicit zero is the only supported inactive row.
                symbol = p.get("symbol")
                _required_text(symbol, "position symbol")
                side = _required_text(p.get("side"), "position side",
                                      ("buy", "sell", "long", "short"))
                key = (symbol, side)
                if key in seen:
                    raise HyperliquidError("Duplicate active native position")
                seen.add(key)

                def optional_decimal(field):
                    value = p.get(field)
                    return _strict_decimal(value, field) if value is not None else None

                positions.append(
                    HyperliquidPositionData(
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

    async def fetch_open_orders(self, symbol: str | None = None) -> list[HyperliquidOrderData]:
        """Fetches current open orders."""
        try:
            raw_orders = await self._exchange.fetch_open_orders(symbol)
            orders: list[HyperliquidOrderData] = []
            if not isinstance(raw_orders, list):
                raise HyperliquidError("Invalid open order response shape")
            seen = set()
            for o in raw_orders:
                if not isinstance(o, dict):
                    raise HyperliquidError("Invalid native order row")
                order_id = o.get("id")
                _required_text(order_id, "order identity")
                # Provider IDs are opaque and may be case sensitive.
                if order_id in seen:
                    raise HyperliquidError("Duplicate native open order")
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
                    raise HyperliquidError("Inconsistent provider order quantity/fill")
                if native_status == "partially_filled" and not 0 < filled < amount:
                    raise HyperliquidError("Inconsistent partial-fill state")
                price = (_strict_decimal(o["price"], "order price")
                         if o.get("price") is not None else None)
                if native_type == "limit" and (price is None or price <= 0):
                    raise HyperliquidError("Missing valid limit order price")
                orders.append(
                    HyperliquidOrderData(
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

    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: float | None = None,
        reduce_only: bool = False,
        params: dict[str, Any] | None = None,
    ) -> HyperliquidOrderData:
        """Places an order on Hyperliquid."""
        self._assert_execution_allowed("create_order")
        order_params: dict[str, Any] = dict(params or {})
        if reduce_only:
            order_params["reduceOnly"] = True

        if order_type.lower() == "market" and price is None:
            ticker = await self._exchange.fetch_ticker(symbol)
            resolved = float(ticker.get("last") or ticker.get("close") or 0.0)
            if resolved <= 0:
                raise ValueError(f"Cannot resolve reference price for market order on {symbol}")
            price = resolved

        try:
            o = await self._exchange.create_order(
                symbol=symbol,
                type=order_type,
                side=side,
                amount=amount,
                price=price,
                params=order_params,
            )
            return HyperliquidOrderData(
                order_id=str(o.get("id", "")),
                client_order_id=o.get("clientOrderId"),
                symbol=str(o.get("symbol", symbol)),
                side=str(o.get("side", side)).lower(),
                order_type=str(o.get("type", order_type)).lower(),
                price=Decimal(str(o["price"])) if o.get("price") is not None else (Decimal(str(price)) if price is not None else None),
                quantity=Decimal(str(o.get("amount", amount))),
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
            raise ValueError("Hyperliquid DEX requires a symbol to cancel an order")
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
    ) -> HyperliquidOrderData:
        """Amends an open order on Hyperliquid."""
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
            return HyperliquidOrderData(
                order_id=str(o.get("id", order_id)),
                client_order_id=o.get("clientOrderId"),
                symbol=str(o.get("symbol", symbol)),
                side=str(o.get("side", side)).lower(),
                order_type=str(o.get("type", order_type)).lower(),
                price=Decimal(str(o["price"])) if o.get("price") is not None else (Decimal(str(price)) if price is not None else None),
                quantity=Decimal(str(o.get("amount", amount or 0))),
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

    def _wrap_exception(self, exc: Exception) -> HyperliquidError:
        """Maps CCXT exceptions to structured Hyperliquid exceptions without leaking keys."""
        # Untrusted CCXT error text may contain keys or private response bodies.
        msg = type(exc).__name__
        if isinstance(exc, ccxt.AuthenticationError):
            return HyperliquidAuthenticationError(f"Authentication failed: {msg}")
        if isinstance(exc, ccxt.RateLimitExceeded):
            return HyperliquidRateLimitError(f"Rate limit exceeded: {msg}")
        if isinstance(exc, ccxt.InsufficientFunds):
            return HyperliquidInsufficientFundsError(f"Insufficient funds: {msg}")
        if isinstance(exc, ccxt.OrderNotFound):
            return HyperliquidOrderNotFoundError(f"Order not found: {msg}")
        if isinstance(exc, ccxt.InvalidOrder):
            return HyperliquidInvalidOrderError(f"Invalid order parameters: {msg}")
        if isinstance(exc, ccxt.NetworkError):
            return HyperliquidNetworkError(f"Network error: {msg}")
        return HyperliquidError(f"Hyperliquid error: {msg}")
