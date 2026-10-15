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
            total = raw.get("total", {})
            free = raw.get("free", {})
            used = raw.get("used", {})

            for cur, tot_val in total.items():
                if tot_val is not None and float(tot_val) > 0:
                    balances[cur] = HyperliquidBalance(
                        currency=cur,
                        total=Decimal(str(tot_val)),
                        free=Decimal(str(free.get(cur, 0) or 0)),
                        used=Decimal(str(used.get(cur, 0) or 0)),
                        raw=raw,
                    )
            return balances
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def fetch_positions(self, symbols: list[str] | None = None) -> list[HyperliquidPositionData]:
        """Fetches active positions."""
        try:
            raw_positions = await self._exchange.fetch_positions(symbols)
            positions: list[HyperliquidPositionData] = []
            for p in raw_positions:
                contracts = p.get("contracts")
                if contracts is None:
                    contracts = p.get("size")
                if contracts is None:
                    continue
                size_dec = Decimal(str(contracts))
                if size_dec <= Decimal(0):
                    continue

                entry_px = Decimal(str(p["entryPrice"])) if p.get("entryPrice") is not None else None
                mark_px = Decimal(str(p["markPrice"])) if p.get("markPrice") is not None else None
                unrealized = Decimal(str(p["unrealizedPnl"])) if p.get("unrealizedPnl") is not None else None
                leverage = Decimal(str(p["leverage"])) if p.get("leverage") is not None else None
                liq_px = Decimal(str(p["liquidationPrice"])) if p.get("liquidationPrice") is not None else None

                positions.append(
                    HyperliquidPositionData(
                        symbol=p.get("symbol", ""),
                        side=str(p.get("side", "long")).lower(),
                        size=size_dec,
                        entry_price=entry_px,
                        mark_price=mark_px,
                        unrealized_pnl=unrealized,
                        leverage=leverage,
                        liquidation_price=liq_px,
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
            for o in raw_orders:
                orders.append(
                    HyperliquidOrderData(
                        order_id=str(o.get("id", "")),
                        client_order_id=o.get("clientOrderId"),
                        symbol=str(o.get("symbol", "")),
                        side=str(o.get("side", "")).lower(),
                        order_type=str(o.get("type", "")).lower(),
                        price=Decimal(str(o["price"])) if o.get("price") is not None else None,
                        quantity=Decimal(str(o.get("amount", 0) or 0)),
                        filled_quantity=Decimal(str(o.get("filled", 0) or 0)),
                        status=str(o.get("status", "")).lower(),
                        timestamp=o.get("timestamp"),
                        raw=o,
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
        msg = str(exc)
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
