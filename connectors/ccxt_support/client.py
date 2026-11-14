"""Shared async CCXT client core for balance, position, order, and OHLCV boundary handling."""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from connectors.ccxt_support.models import (
    CCXTBalance,
    CCXTErrorSpec,
    CCXTOrderData,
    CCXTPositionData,
    wrap_ccxt_exception,
)
from connectors.common.config import Mode
from connectors.common.validation import optional_strict_decimal, required_text, strict_decimal

import ccxt

_ALLOWED_ORDER_TYPES = frozenset({"market", "limit"})
_ALLOWED_OPEN_STATUSES = frozenset({"open", "new", "partially_filled"})
_ALLOWED_ALL_STATUSES = _ALLOWED_OPEN_STATUSES | frozenset(
    {"filled", "closed", "canceled", "cancelled", "rejected", "expired"}
)


class BaseCCXTClient:
    """Shared CCXT async wrapper enforcing fail-closed validation and execution-mode gates."""

    _balance_cls: type[CCXTBalance] = CCXTBalance
    _position_cls: type[CCXTPositionData] = CCXTPositionData
    _order_cls: type[CCXTOrderData] = CCXTOrderData
    _cancel_symbol_required_msg: str = "Exchange requires a symbol to cancel an order"

    def __init__(
        self,
        config: Any,
        error_spec: CCXTErrorSpec,
        exchange: Any | None = None,
    ) -> None:
        self.config = config
        self._error_spec = error_spec
        self._exchange = exchange
        self._connected = False

    @property
    def exchange(self) -> Any:
        if self._exchange is None:
            self._exchange = self._build_exchange()
        return self._exchange

    def _build_exchange(self) -> Any:
        raise NotImplementedError

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _assert_execution_allowed(self, operation: str) -> None:
        if self.config.mode is not Mode.EXECUTION:
            raise PermissionError(
                f"Remote order mutation '{operation}' denied in mode {self.config.mode.value!r}. "
                "Only EXECUTION mode permits remote mutations."
            )

    def _wrap_exception(self, exc: Exception) -> Exception:
        return wrap_ccxt_exception(exc, self._error_spec)

    async def connect(self) -> None:
        try:
            await self.exchange.load_markets()
            self._connected = True
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def close(self) -> None:
        if self._exchange is not None:
            try:
                await self._exchange.close()
            except Exception as exc:
                raise self._wrap_exception(exc) from exc
            finally:
                self._connected = False

    def parse_balance_payload(self, raw: Any) -> dict[str, Any]:
        err = self._error_spec.base_error
        if not isinstance(raw, dict):
            raise err(f"Invalid balance payload: expected dict, got {type(raw).__name__}")
        top_free = raw.get("free") if isinstance(raw.get("free"), dict) else {}
        top_used = raw.get("used") if isinstance(raw.get("used"), dict) else {}
        top_total = raw.get("total") if isinstance(raw.get("total"), dict) else {}

        ignored = {"info", "free", "used", "total", "debt", "timestamp", "datetime"}
        currencies: set[str] = {
            k for k, v in raw.items() if k not in ignored and isinstance(v, dict)
        }
        for top_map in (top_free, top_used, top_total):
            currencies.update(k for k in top_map if isinstance(k, str))

        balances: dict[str, Any] = {}
        for currency in sorted(currencies):
            entry = raw.get(currency) if isinstance(raw.get(currency), dict) else None
            resolved: dict[str, Decimal] = {}
            for scope_name, top_map in (("free", top_free), ("used", top_used), ("total", top_total)):
                has_entry = entry is not None and scope_name in entry
                has_top = currency in top_map
                if not has_entry and not has_top:
                    raise err(f"Missing {currency}.{scope_name} balance field")
                if has_entry:
                    assert entry is not None
                    scoped_val = strict_decimal(
                        entry.get(scope_name), f"{currency}.{scope_name}", error_cls=err
                    )
                else:
                    scoped_val = None
                if has_top:
                    top_val = strict_decimal(
                        top_map[currency], f"{scope_name}.{currency}", error_cls=err
                    )
                else:
                    top_val = None
                if scoped_val is not None and top_val is not None and scoped_val != top_val:
                    raise err(
                        f"Conflicting {currency} {scope_name} scope: {scoped_val} vs {top_val}"
                    )
                val = scoped_val if scoped_val is not None else top_val
                assert val is not None
                if val < Decimal("0"):
                    raise err(f"Negative {currency}.{scope_name} balance field: {val}")
                resolved[scope_name] = val

            balances[currency] = self._balance_cls(
                currency=currency,
                total=resolved["total"],
                free=resolved["free"],
                used=resolved["used"],
                raw=entry if entry is not None else {k: str(v) for k, v in resolved.items()},
            )
        return balances

    def parse_positions_payload(self, raw_positions: Any) -> list[Any]:
        err = self._error_spec.base_error
        if not isinstance(raw_positions, list):
            raise err(f"Invalid positions payload: expected list, got {type(raw_positions).__name__}")
        seen: set[tuple[str, str]] = set()
        results: list[Any] = []
        for pos in raw_positions:
            if not isinstance(pos, dict):
                raise err(f"Invalid position entry: expected dict, got {type(pos).__name__}")
            raw_contracts = pos.get("contracts")
            if raw_contracts is None and pos.get("size") is not None:
                raw_contracts = pos.get("size")
            contracts = strict_decimal(raw_contracts, "contracts", error_cls=err)
            if contracts < Decimal("0"):
                raise err(f"Negative position contracts: {contracts}")
            # Explicit zero is the only supported inactive row.
            if contracts == Decimal("0"):
                continue

            symbol = required_text(pos.get("symbol"), "symbol", error_cls=err)
            raw_side = required_text(
                pos.get("side"), "side", allowed={"long", "short", "buy", "sell"}, error_cls=err
            )
            side = "long" if raw_side in ("long", "buy") else "short"
            if (symbol, side) in seen:
                raise err(f"Duplicate active native position {(symbol, side)!r}")
            seen.add((symbol, side))
            entry_price = optional_strict_decimal(pos.get("entryPrice"), "entryPrice", error_cls=err)
            mark_price = optional_strict_decimal(pos.get("markPrice"), "markPrice", error_cls=err)
            unrealized_pnl = optional_strict_decimal(
                pos.get("unrealizedPnl"), "unrealizedPnl", error_cls=err
            )
            leverage = optional_strict_decimal(pos.get("leverage"), "leverage", error_cls=err)
            liq_price = optional_strict_decimal(
                pos.get("liquidationPrice"), "liquidationPrice", error_cls=err
            )
            results.append(
                self._position_cls(
                    symbol=symbol,
                    side=side,
                    size=contracts,
                    entry_price=entry_price,
                    mark_price=mark_price,
                    unrealized_pnl=unrealized_pnl,
                    leverage=leverage,
                    liquidation_price=liq_price,
                    raw=pos,
                )
            )
        return results

    def parse_order_row(self, row: Any, *, allow_terminal: bool = False) -> Any:
        err = self._error_spec.base_error
        if not isinstance(row, dict):
            raise err(f"Invalid order entry: expected dict, got {type(row).__name__}")
        # Provider IDs are opaque and may be case sensitive.
        order_id = required_text(row.get("id"), "id", error_cls=err)
        symbol = required_text(row.get("symbol"), "symbol", error_cls=err)
        side = required_text(row.get("side"), "side", allowed={"buy", "sell"}, error_cls=err)
        order_type = required_text(
            row.get("type"), "type", allowed=_ALLOWED_ORDER_TYPES, error_cls=err
        )
        allowed_statuses = _ALLOWED_ALL_STATUSES if allow_terminal else _ALLOWED_OPEN_STATUSES
        status = required_text(
            row.get("status"), "status", allowed=allowed_statuses, error_cls=err
        )
        quantity = strict_decimal(row.get("amount"), "amount", error_cls=err)
        if quantity <= Decimal("0"):
            raise err(f"Order amount must be positive: {quantity}")
        raw_filled = row.get("filled")
        if raw_filled is None and allow_terminal:
            raw_filled = Decimal("0")
        filled = strict_decimal(raw_filled, "filled", error_cls=err)
        if filled < Decimal("0") or filled > quantity:
            raise err(f"Order filled {filled} out of range [0, {quantity}]")
        if not allow_terminal and status == "partially_filled" and not (Decimal("0") < filled < quantity):
            raise err(f"Inconsistent partial-fill state: filled={filled}, amount={quantity}")

        raw_price = row.get("price")
        if (order_type == "limit" and not allow_terminal) or raw_price is not None:
            price: Decimal | None = strict_decimal(raw_price, "price", error_cls=err)
            if not allow_terminal and order_type == "limit" and price <= Decimal("0"):
                raise err(f"Invalid limit order price: {price}")
        else:
            price = None

        raw_ts = row.get("timestamp")
        ts = 0 if raw_ts is None else int(strict_decimal(raw_ts, "timestamp", error_cls=err))

        raw_cid = row.get("clientOrderId")
        client_order_id = str(raw_cid) if raw_cid is not None else None
        return self._order_cls(
            order_id=order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            price=price,
            quantity=quantity,
            filled_quantity=filled,
            status=status,
            timestamp=ts,
            raw=row,
        )

    def parse_open_orders_payload(self, raw_orders: Any) -> list[Any]:
        err = self._error_spec.base_error
        if not isinstance(raw_orders, list):
            raise err(f"Invalid open orders payload: expected list, got {type(raw_orders).__name__}")
        seen_ids: set[str] = set()
        results: list[Any] = []
        for row in raw_orders:
            parsed = self.parse_order_row(row)
            if parsed.order_id in seen_ids:
                raise err(f"Duplicate order id {parsed.order_id!r} in open orders snapshot")
            seen_ids.add(parsed.order_id)
            results.append(parsed)
        return results

    async def fetch_balance(self) -> dict[str, Any]:
        try:
            raw = await self.exchange.fetch_balance()
            return self.parse_balance_payload(raw)
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def fetch_positions(self, symbols: Sequence[str] | None = None) -> list[Any]:
        try:
            raw = await self.exchange.fetch_positions(symbols)
            return self.parse_positions_payload(raw)
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def fetch_open_orders(self, symbol: str | None = None) -> list[Any]:
        try:
            raw = await self.exchange.fetch_open_orders(symbol)
            return self.parse_open_orders_payload(raw)
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def _resolve_order_price(
        self,
        symbol: str,
        order_type: str,
        price: float | None,
    ) -> float | None:
        return price

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
    ) -> Any:
        return self.parse_order_row(raw, allow_terminal=True)

    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: float | None = None,
        reduce_only: bool = False,
        params: dict[str, Any] | None = None,
    ) -> Any:
        self._assert_execution_allowed("create_order")
        order_params = dict(params or {})
        if reduce_only:
            order_params["reduceOnly"] = True
        try:
            resolved_price = await self._resolve_order_price(symbol, order_type, price)
            raw = await self.exchange.create_order(
                symbol=symbol,
                type=order_type,
                side=side,
                amount=amount,
                price=resolved_price,
                params=order_params,
            )
            return await self._parse_mutated_order(
                raw,
                fallback_order_id="",
                symbol=symbol,
                side=side,
                order_type=order_type,
                amount=amount,
                price=resolved_price,
            )
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
    ) -> Any:
        self._assert_execution_allowed("edit_order")
        try:
            raw = await self.exchange.edit_order(
                id=order_id,
                symbol=symbol,
                type=order_type,
                side=side,
                amount=amount,
                price=price,
                params=dict(params or {}),
            )
            return await self._parse_mutated_order(
                raw,
                fallback_order_id=order_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                amount=amount,
                price=price,
            )
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def cancel_order(self, order_id: str, symbol: str | None = None) -> bool:
        self._assert_execution_allowed("cancel_order")
        if not symbol:
            raise ValueError(self._cancel_symbol_required_msg)
        try:
            await self.exchange.cancel_order(order_id, symbol)
            return True
        except ccxt.OrderNotFound:
            return False
        except Exception as exc:
            raise self._wrap_exception(exc) from exc

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 100,
    ) -> list[list[float]]:
        try:
            return await self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except Exception as exc:
            raise self._wrap_exception(exc) from exc
