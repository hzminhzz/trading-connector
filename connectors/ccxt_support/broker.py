"""Shared ML4T AsyncBrokerProtocol adapter backed by CCXTClient and CCXTIntentJournal."""
from __future__ import annotations

import asyncio
import math
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from connectors.ccxt_support.journal import CCXTIntentJournal
from connectors.common.config import Mode
from connectors.common.intents import UnknownOutcome

try:
    from ml4t.backtest.types import Order, OrderSide, OrderStatus, OrderType, Position
    from ml4t.live.protocols import ExecutionCapability
except ImportError:
    ExecutionCapability = None  # type: ignore[assignment,misc]
    Order = None  # type: ignore[assignment,misc]
    OrderSide = None  # type: ignore[assignment,misc]
    OrderStatus = None  # type: ignore[assignment,misc]
    OrderType = None  # type: ignore[assignment,misc]
    Position = None  # type: ignore[assignment,misc]


def require_ml4t() -> None:
    if Order is None:
        raise ImportError(
            "ml4t.live is required for CCXT LiveBroker; install with `pip install ml4t-live`."
        )


def ccxt_order_status_map() -> dict[str, Any]:
    require_ml4t()
    return {
        "open": OrderStatus.PENDING,
        "new": OrderStatus.PENDING,
        "partially_filled": OrderStatus.PENDING,
        "filled": OrderStatus.FILLED,
        "closed": OrderStatus.FILLED,
        "canceled": OrderStatus.CANCELLED,
        "cancelled": OrderStatus.CANCELLED,
        "rejected": OrderStatus.REJECTED,
        "expired": OrderStatus.CANCELLED,
    }


class BaseCCXTLiveBroker:
    """Shared ML4T AsyncBrokerProtocol adapter for perpetual-futures CCXT connectors."""

    _default_client_cls: type[Any] | None = None
    _provider_label: str = "Exchange"
    _quote_currency: str = "USDT"
    _terminal_rejection_errors: tuple[type[Exception], ...] = ()

    def __init__(
        self,
        client: Any | None = None,
        config: Any | None = None,
        journal: CCXTIntentJournal | None = None,
    ) -> None:
        require_ml4t()
        if client is None:
            if config is None or self._default_client_cls is None:
                raise ValueError(f"{type(self).__name__} requires a client or config")
            client = self._default_client_cls(config)
        self.client = client
        self.config = config or client.config
        self.journal = journal
        self._connected = False
        self._positions: dict[str, Any] = {}
        self._pending_orders: list[Any] = []
        self._account_value: float = 0.0
        self._cash: float | None = None

    @property
    def execution_capabilities(self) -> frozenset[Any]:
        if self.config.mode is Mode.EXECUTION:
            return frozenset({ExecutionCapability.LIMIT, ExecutionCapability.PARTIAL_FILL})
        return frozenset()

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def is_connected_async(self) -> bool:
        return self._connected

    @property
    def positions(self) -> dict[str, Any]:
        return dict(self._positions)

    @property
    def pending_orders(self) -> list[Any]:
        return list(self._pending_orders)

    async def connect(self) -> None:
        if self._connected:
            return
        await self.client.connect()
        try:
            await self._sync_state()
        except Exception:
            self._connected = False
            raise
        self._connected = True

    async def disconnect(self) -> None:
        await self.client.close()
        self._connected = False

    def assert_paper_trading(self) -> None:
        raise NotImplementedError

    def assert_live_trading(self) -> None:
        if not self._connected:
            raise RuntimeError("Live trading requires an active connected broker")
        env = self.config.environment
        if env.value != "mainnet":
            raise PermissionError(
                f"Live trading requires {self._provider_label} MAINNET, got {env.value}"
            )
        if self.config.mode is not Mode.EXECUTION:
            raise PermissionError("Live trading requires EXECUTION mode")

    def _assert_execution_allowed(self, operation: str) -> None:
        if self.config.mode is not Mode.EXECUTION:
            raise PermissionError(
                f"Broker order mutation '{operation}' denied in mode {self.config.mode.value!r}."
            )

    async def get_account_value_async(self) -> float:
        await self._sync_balance()
        return self._account_value

    async def get_cash_async(self) -> float:
        await self._sync_balance()
        if self._cash is None:
            raise ValueError(f"{self._provider_label} cash is unverified")
        return self._cash

    async def get_positions_async(self) -> dict[str, Any]:
        await self._sync_positions()
        return dict(self._positions)

    async def get_position_async(self, asset: str) -> Any | None:
        await self._sync_positions()
        if asset in self._positions:
            return self._positions[asset]
        ex_sym = self._to_exchange_symbol(asset)
        return self._positions.get(ex_sym)

    async def get_pending_orders_async(self, asset: str | None = None) -> list[Any]:
        await self._sync_orders(asset)
        if asset is None:
            return list(self._pending_orders)
        ex_sym = self._to_exchange_symbol(asset)
        return [o for o in self._pending_orders if o.asset in (asset, ex_sym)]

    def _submit_params(self, kwargs: dict[str, Any], intent_id: str) -> dict[str, Any]:
        return dict(kwargs)

    async def _prepare_submit_order(
        self,
        *,
        asset: str,
        ex_symbol: str,
        side: Any,
        order_type: Any,
        limit_price: float | None,
        stop_price: float | None,
        reduce_only: bool,
    ) -> float | None:
        if order_type == OrderType.LIMIT:
            if limit_price is None:
                raise ValueError("LIMIT orders require limit_price")
            return limit_price
        return limit_price

    def _stage_intent(
        self,
        *,
        asset: str,
        side: str,
        quantity: str,
        order_type: str,
        limit_price: str | None,
        reduce_only: bool,
        intent_idempotency_key: str | None,
    ) -> Any:
        if self.journal is None:
            return None
        try:
            return self.journal.stage(
                asset=asset,
                side=side,
                quantity=quantity,
                order_type=order_type,
                limit_price=limit_price,
                reduce_only=reduce_only,
                intent_id=intent_idempotency_key,
            )
        except sqlite3.IntegrityError as exc:
            raise UnknownOutcome(
                f"Duplicate order intent identity {intent_idempotency_key!r}: refusing re-send"
            ) from exc

    async def _run_journaled_mutation(self, *, intent: Any, action: Any) -> Any:
        raw_order: Any = None
        try:
            raw_order = await action()
            if self.journal is not None and intent is not None:
                self.journal.mark_submitted(intent.id, exchange_order_id=raw_order.order_id)
            try:
                await self._sync_state()
            except BaseException as sync_exc:
                if self.journal is not None and intent is not None:
                    self.journal.mark_unknown(
                        intent.id,
                        f"post_ack_snapshot_failed: {type(sync_exc).__name__}",
                    )
                raise
            return raw_order
        except UnknownOutcome:
            raise
        except self._terminal_rejection_errors as exc:
            if self.journal is not None and intent is not None:
                self.journal.mark_rejected(intent.id, str(exc))
            raise
        except BaseException as exc:
            if self.journal is not None and intent is not None and raw_order is None:
                self.journal.mark_unknown(intent.id, f"{type(exc).__name__}: {exc}")
            raise

    async def submit_order_async(
        self,
        asset: str,
        quantity: float,
        side: Any = None,
        order_type: Any = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        **kwargs: Any,
    ) -> Any:
        self._assert_execution_allowed("submit_order")
        if not math.isfinite(quantity) or quantity == 0:
            raise ValueError("quantity must be finite and nonzero")
        if side is None:
            side = OrderSide.SELL if quantity < 0 else OrderSide.BUY
        elif quantity < 0:
            raise ValueError("negative quantity requires side=None")
        quantity = abs(quantity)
        if order_type is None:
            order_type = OrderType.MARKET
        if order_type not in (OrderType.MARKET, OrderType.LIMIT):
            raise ValueError(f"Unsupported order_type: {order_type!r}")

        raw_params = kwargs.pop("params", None)
        if raw_params is not None:
            if not isinstance(raw_params, dict):
                raise ValueError("params must be a dict")
            for protected in ("orderLinkId", "clientOrderId", "reduceOnly"):
                if protected in raw_params:
                    raise ValueError(
                        f"Protected provider field {protected!r} cannot be overridden via params"
                    )
            kwargs.update(raw_params)

        intent_idempotency_key = kwargs.pop("intent_idempotency_key", None)
        explicit_ro = bool(kwargs.pop("reduce_only", False))
        reducing_risk = bool(kwargs.pop("reducing_risk", False))
        reduce_only = explicit_ro or reducing_risk

        pos = self._positions.get(asset) or self._positions.get(self._to_exchange_symbol(asset))
        if not reduce_only and pos is not None and (
            (side == OrderSide.SELL and pos.quantity > 0)
            or (side == OrderSide.BUY and pos.quantity < 0)
        ):
            reduce_only = True

        ex_symbol = self._to_exchange_symbol(asset)
        ccxt_side = "buy" if side == OrderSide.BUY else "sell"
        ccxt_type = "market" if order_type == OrderType.MARKET else "limit"
        price = await self._prepare_submit_order(
            asset=asset,
            ex_symbol=ex_symbol,
            side=side,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            reduce_only=reduce_only,
        )

        intent = self._stage_intent(
            asset=asset,
            side=ccxt_side,
            quantity=str(quantity),
            order_type=ccxt_type,
            limit_price=str(price) if price is not None else None,
            reduce_only=reduce_only,
            intent_idempotency_key=intent_idempotency_key,
        )

        params = self._submit_params(kwargs, intent.id if intent else "")
        if stop_price is not None:
            params["stopPrice"] = stop_price
        raw_order = await self._run_journaled_mutation(
            intent=intent,
            action=lambda: self.client.create_order(
                symbol=ex_symbol,
                side=ccxt_side,
                order_type=ccxt_type,
                amount=quantity,
                price=price,
                reduce_only=reduce_only,
                params=params,
            ),
        )
        order = self._parse_order(raw_order, default_asset=asset)
        if intent_idempotency_key is not None:
            order.intent_idempotency_key = intent_idempotency_key
        elif intent is not None:
            order.intent_idempotency_key = intent.id
        return order

    async def cancel_order_async(self, order_id: str) -> bool:
        self._assert_execution_allowed("cancel_order")
        symbol: str | None = None
        for o in self._pending_orders:
            if o.order_id == order_id:
                symbol = self._to_exchange_symbol(o.asset)
                break
        if symbol is None and self.journal is not None:
            asset = self.journal.asset_for_exchange_order(order_id)
            if asset:
                symbol = self._to_exchange_symbol(asset)
        if symbol is None:
            raise UnknownOutcome(
                f"Cannot resolve symbol for order {order_id} without active pending order or journal entry"
            )
        # A cancellation ACK is not a terminal order observation. Keep pending
        # exposure until a fresh provider snapshot or execution event resolves it.
        return await self.client.cancel_order(order_id, symbol=symbol)

    async def replace_order_async(
        self,
        order_id: str,
        quantity: float | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        *,
        reduce_only: bool | None = None,
        intent_idempotency_key: str | None = None,
    ) -> Any:
        self._assert_execution_allowed("replace_order")
        target = next((o for o in self._pending_orders if o.order_id == order_id), None)
        if target is None:
            raise UnknownOutcome(f"Cannot replace order {order_id} not in pending orders")

        ex_sym = self._to_exchange_symbol(target.asset)
        ccxt_side = "buy" if target.side == OrderSide.BUY else "sell"
        if target.order_type not in (OrderType.MARKET, OrderType.LIMIT):
            raise ValueError(f"Unsupported order_type: {target.order_type!r}")
        ccxt_type = "limit" if target.order_type == OrderType.LIMIT else "market"
        new_qty = quantity if quantity is not None else target.quantity
        new_price = limit_price if limit_price is not None else target.limit_price

        resolved_ro = reduce_only
        if resolved_ro is None and self.journal is not None:
            resolved_ro = self.journal.is_reduce_only_for_exchange_order(order_id)
        if resolved_ro is None:
            resolved_ro = False

        intent = self._stage_intent(
            asset=target.asset,
            side=ccxt_side,
            quantity=str(new_qty),
            order_type=ccxt_type,
            limit_price=str(new_price) if new_price is not None else None,
            reduce_only=resolved_ro,
            intent_idempotency_key=intent_idempotency_key,
        )
        params = self._submit_params(
            {"stopPrice": stop_price} if stop_price is not None else {},
            intent.id if intent else "",
        )
        raw_order = await self._run_journaled_mutation(
            intent=intent,
            action=lambda: self.client.edit_order(
                order_id=order_id,
                symbol=ex_sym,
                order_type=ccxt_type,
                side=ccxt_side,
                amount=new_qty,
                price=new_price,
                params=params,
            ),
        )
        order = self._parse_order(raw_order, default_asset=target.asset)
        if intent_idempotency_key is not None:
            order.intent_idempotency_key = intent_idempotency_key
        elif intent is not None:
            order.intent_idempotency_key = intent.id
        return order

    async def close_position_async(self, asset: str) -> Any | None:
        self._assert_execution_allowed("close_position")
        pos = await self.get_position_async(asset)
        if pos is None or pos.quantity == 0:
            return None
        close_side = OrderSide.SELL if pos.quantity > 0 else OrderSide.BUY
        return await self.submit_order_async(
            asset=asset,
            quantity=abs(pos.quantity),
            side=close_side,
            order_type=OrderType.MARKET,
            reduce_only=True,
        )

    async def _sync_state(self) -> None:
        await asyncio.gather(
            self._sync_balance(),
            self._sync_positions(),
            self._sync_orders(),
        )

    async def _sync_balance(self) -> None:
        bals = await self.client.fetch_balance()
        quote_bal = bals.get(self._quote_currency)
        if quote_bal is None:
            raise ValueError(
                f"{self._provider_label} {self._quote_currency} account balance unverified"
            )
        self._account_value = float(quote_bal.total)
        self._cash = float(quote_bal.free)

    def _position_entry_time(self, p: Any) -> datetime:
        return datetime.now(timezone.utc)

    async def _sync_positions(self) -> None:
        raw_positions = await self.client.fetch_positions()
        new_pos: dict[str, Any] = {}
        for p in raw_positions:
            if p.size == Decimal("0"):
                continue
            if p.side in ("long", "buy"):
                qty = float(p.size)
            elif p.side in ("short", "sell"):
                qty = -abs(float(p.size))
            else:
                raise ValueError(f"Unsupported {self._provider_label} position side: {p.side!r}")
            if p.entry_price is None or p.mark_price is None:
                raise ValueError(f"Missing {self._provider_label} active position price")
            pos = Position(
                asset=p.symbol,
                quantity=qty,
                entry_price=float(p.entry_price),
                entry_time=self._position_entry_time(p),
                current_price=float(p.mark_price),
            )
            new_pos[p.symbol] = pos
        self._positions = new_pos

    async def _sync_orders(self, asset: str | None = None) -> None:
        ex_sym = self._to_exchange_symbol(asset) if asset else None
        raw_orders = await self.client.fetch_open_orders(ex_sym)
        self._pending_orders = [self._parse_order(o) for o in raw_orders]

    def _parse_order(self, o: Any, default_asset: str = "") -> Any:
        status_map = ccxt_order_status_map()
        if o.side not in ("buy", "sell"):
            raise ValueError(f"Unsupported order side: {o.side!r}")
        if o.order_type not in ("market", "limit"):
            raise ValueError(f"Unsupported order type: {o.order_type!r}")
        if o.status not in status_map:
            raise ValueError(f"Unsupported order status: {o.status!r}")
        side = OrderSide.BUY if o.side == "buy" else OrderSide.SELL
        order_type = OrderType.MARKET if o.order_type == "market" else OrderType.LIMIT
        status = status_map[o.status]
        asset = self._normalize_symbol(o.symbol) or default_asset
        ts_sec = o.timestamp / 1000.0 if o.timestamp and o.timestamp > 1e11 else (o.timestamp or 0.0)
        created_at = datetime.fromtimestamp(ts_sec, tz=timezone.utc) if ts_sec else datetime.now(timezone.utc)
        return Order(
            order_id=o.order_id,
            asset=asset,
            side=side,
            quantity=float(o.quantity),
            order_type=order_type,
            limit_price=float(o.price) if o.price is not None else None,
            stop_price=None,
            status=status,
            filled_quantity=float(o.filled_quantity),
            filled_price=float(o.price) if o.price is not None and o.filled_quantity > 0 else None,
            created_at=created_at,
        )

    def _to_exchange_symbol(self, asset: str) -> str:
        if ":" in asset:
            return asset
        if "/" in asset:
            base, quote = asset.split("/", 1)
            return f"{base}/{quote}:{quote}"
        return f"{asset}/{self._quote_currency}:{self._quote_currency}"

    def _normalize_symbol(self, symbol: str) -> str:
        if not symbol:
            return ""
        return symbol.split(":")[0]
