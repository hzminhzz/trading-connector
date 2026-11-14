"""Continuous CCXT REST + ccxt.pro WebSocket reconciliation for Bybit V5 UNIFIED Demo."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from connectors.bybit.client import BybitClient
from connectors.bybit.config import Environment
from connectors.bybit.models import (
    BybitAccountSummary,
    BybitDemoReconciliationSnapshot,
    BybitError,
    BybitExecutionData,
    BybitOrderData,
    BybitPositionData,
    BybitSnapshotError,
)
from connectors.common.validation import strict_decimal

logger = logging.getLogger(__name__)

DEMO_PRIVATE_WS_ENDPOINT = "wss://stream-demo.bybit.com/v5/private"


def _default_now_ms() -> int:
    return int(time.time() * 1000)


class BybitDemoReconciler:
    """Maintains an atomic, fail-closed Bybit V5 Demo state via CCXT REST + ccxt.pro WS."""

    def __init__(
        self,
        client: BybitClient,
        *,
        ws_exchange: Any = None,
        symbols: tuple[str, ...] = (),
        since_ms: int | None = None,
        now_ms_fn: Callable[[], int] | None = None,
    ) -> None:
        if client.config.environment is not Environment.DEMO:
            raise BybitSnapshotError("demo_reconciler: client must target Environment.DEMO")
        self.client = client
        self._symbols: tuple[str, ...] = tuple(dict.fromkeys(s for s in symbols if s))
        self._since_ms = since_ms
        self._now_ms_fn = now_ms_fn or _default_now_ms
        if ws_exchange is None:
            import ccxt.pro as ccxtpro

            params: dict[str, Any] = {
                "enableRateLimit": True,
                "options": {"defaultType": client.config.default_type},
            }
            if client.config.api_key:
                params["apiKey"] = client.config.api_key
            if client.config.api_secret:
                params["secret"] = client.config.api_secret
            ws_exchange = ccxtpro.bybit(params)
            ws_exchange.enable_demo_trading(True)
        self._ws = ws_exchange
        self._verify_ws_demo_endpoint(self._ws)

        self._lock = threading.RLock()
        self._epoch = 0
        self._connected = False
        self._history_complete = False
        self._financial_complete = False
        self._cutoff: str | None = None
        self._observed_at_ms = 0
        self._received_at_ms = 0
        self._server_time_ms = 0
        self._account: BybitAccountSummary | None = None
        self._positions_by_symbol: dict[str, BybitPositionData] = {}
        self._orders_by_id: dict[str, BybitOrderData] = {}
        self._executions_by_id: dict[str, BybitExecutionData] = {}
        self._market_marks: dict[str, tuple[Decimal, int]] = {}
        self._uncertainty_reasons: list[str] = []

        self._running = False
        self._tasks: list[asyncio.Task[None]] = []

    @staticmethod
    def _verify_ws_demo_endpoint(ws: Any) -> None:
        urls = getattr(ws, "urls", None)
        private_ws = ""
        if isinstance(urls, dict):
            ws_urls = urls.get("api", {}).get("ws", {})
            if isinstance(ws_urls, dict):
                private = ws_urls.get("private", {})
                if isinstance(private, dict):
                    private_ws = str(private.get("contract") or "")
                elif isinstance(private, str):
                    private_ws = private
        hostname = str(getattr(ws, "hostname", "bybit.com"))
        resolved = private_ws.replace("{hostname}", hostname)
        if (
            resolved != DEMO_PRIVATE_WS_ENDPOINT
            or getattr(ws, "isSandboxModeEnabled", True) is not False
            or getattr(ws, "options", {}).get("enableDemoTrading") is not True
        ):
            raise BybitSnapshotError(
                "demo_reconciler: ws exchange is not pinned to Bybit Demo private stream"
            )

    def _fail_closed(self, reason: str) -> None:
        with self._lock:
            self._connected = False
            if reason and reason not in self._uncertainty_reasons:
                self._uncertainty_reasons.append(reason)

    def _record_observation_clock(self, provider_ts_ms: int | None) -> tuple[int, int]:
        recv_ms = int(self._now_ms_fn())
        obs_ms = int(provider_ts_ms) if provider_ts_ms and provider_ts_ms > 0 else recv_ms
        return obs_ms, recv_ms

    def _parse_ticker_mark(
        self, symbol: str, ticker: dict[str, Any], fallback_ms: int
    ) -> tuple[Decimal, int]:
        if not isinstance(ticker, dict):
            raise BybitSnapshotError(f"demo_ticker: invalid ticker payload for {symbol}")
        info = ticker.get("info") if isinstance(ticker.get("info"), dict) else {}
        mark_raw = (
            info.get("markPrice")
            if info.get("markPrice") not in (None, "")
            else ticker.get("markPrice")
        )
        if mark_raw in (None, ""):
            mark_raw = ticker.get("last")
        mark = strict_decimal(mark_raw, f"ticker.markPrice({symbol})", error_cls=BybitError)
        if mark <= 0:
            raise BybitSnapshotError(f"demo_ticker: non-positive mark price for {symbol}")
        ts_raw = ticker.get("timestamp") if ticker.get("timestamp") is not None else fallback_ms
        try:
            known_ms = int(ts_raw)
        except (TypeError, ValueError) as exc:
            raise BybitSnapshotError(f"demo_ticker: invalid timestamp for {symbol}") from exc
        if known_ms <= 0:
            known_ms = fallback_ms
        return mark, known_ms

    def _apply_balance(self, raw_balance: dict[str, Any], ts_ms: int | None = None) -> None:
        account = self.client.parse_ccxt_account_summary(raw_balance)
        raw_ts = raw_balance.get("timestamp")
        provider_ms = int(raw_ts) if isinstance(raw_ts, int) and raw_ts > 0 else ts_ms
        with self._lock:
            self._account = account
            self._financial_complete = True
            obs_ms, recv_ms = self._record_observation_clock(provider_ms)
            self._observed_at_ms = max(self._observed_at_ms, obs_ms)
            self._received_at_ms = max(self._received_at_ms, recv_ms)

    def _apply_positions(self, rows: list[dict[str, Any]], ts_ms: int | None = None) -> None:
        if not isinstance(rows, list):
            raise BybitSnapshotError("demo_positions_ws: invalid positions list")
        with self._lock:
            updated = dict(self._positions_by_symbol)
            for row in rows:
                if not isinstance(row, dict):
                    raise BybitSnapshotError("demo_positions_ws: invalid position row")
                info = row.get("info") if isinstance(row.get("info"), dict) else {}
                cat = info.get("category")
                category = str(cat).lower() if cat else "linear"
                parsed = self.client._parse_ccxt_position(row, category=category)
                if parsed is None:
                    raw_sym = info.get("symbol") or row.get("symbol")
                    if isinstance(raw_sym, str) and raw_sym:
                        sym = (
                            self.client._demo_market_symbol(info["symbol"])
                            if isinstance(info.get("symbol"), str) and info.get("symbol")
                            else raw_sym
                        )
                        updated.pop(sym, None)
                    continue
                updated[parsed.symbol] = parsed
                mark_ts = ts_ms or self._observed_at_ms or self._now_ms_fn()
                self._market_marks[parsed.symbol] = (parsed.mark_price, mark_ts)
            self._positions_by_symbol = updated
            if ts_ms:
                obs_ms, recv_ms = self._record_observation_clock(ts_ms)
                self._observed_at_ms = max(self._observed_at_ms, obs_ms)
                self._received_at_ms = max(self._received_at_ms, recv_ms)

    def _apply_orders(self, rows: list[dict[str, Any]], ts_ms: int | None = None) -> None:
        if not isinstance(rows, list):
            raise BybitSnapshotError("demo_orders_ws: invalid orders list")
        with self._lock:
            for row in rows:
                if not isinstance(row, dict):
                    raise BybitSnapshotError("demo_orders_ws: invalid order row")
                info = row.get("info") if isinstance(row.get("info"), dict) else {}
                category = str(info.get("category") or "linear").lower()
                parsed = self.client.parse_ccxt_order(row, category=category)
                self._orders_by_id[parsed.order_id] = parsed
            if ts_ms:
                obs_ms, recv_ms = self._record_observation_clock(ts_ms)
                self._observed_at_ms = max(self._observed_at_ms, obs_ms)
                self._received_at_ms = max(self._received_at_ms, recv_ms)

    def _apply_executions(self, rows: list[dict[str, Any]], ts_ms: int | None = None) -> None:
        if not isinstance(rows, list):
            raise BybitSnapshotError("demo_executions_ws: invalid executions list")
        max_exec_ts = ts_ms or 0
        with self._lock:
            for row in rows:
                parsed = self.client.parse_ccxt_execution(row)
                self._executions_by_id[parsed.execution_id] = parsed
                if parsed.timestamp_ms > max_exec_ts:
                    max_exec_ts = parsed.timestamp_ms
            if max_exec_ts > 0:
                obs_ms, recv_ms = self._record_observation_clock(max_exec_ts)
                self._observed_at_ms = max(self._observed_at_ms, obs_ms)
                self._received_at_ms = max(self._received_at_ms, recv_ms)

    def _apply_ticker(self, symbol: str, ticker: dict[str, Any]) -> None:
        fallback_ms = self._observed_at_ms or self._now_ms_fn()
        mark, known_ms = self._parse_ticker_mark(symbol, ticker, fallback_ms)
        with self._lock:
            self._market_marks[symbol] = (mark, known_ms)
            if symbol in self._positions_by_symbol:
                pos = self._positions_by_symbol[symbol]
                self._positions_by_symbol[symbol] = BybitPositionData(
                    symbol=pos.symbol,
                    side=pos.side,
                    size=pos.size,
                    entry_price=pos.entry_price,
                    mark_price=mark,
                    unrealized_pnl=pos.unrealized_pnl,
                    leverage=pos.leverage,
                    liquidation_price=pos.liquidation_price,
                    raw=pos.raw,
                )
            obs_ms, recv_ms = self._record_observation_clock(known_ms)
            self._observed_at_ms = max(self._observed_at_ms, obs_ms)
            self._received_at_ms = max(self._received_at_ms, recv_ms)

    async def catch_up(self) -> BybitDemoReconciliationSnapshot:
        """Perform an authenticated REST catch-up across UNIFIED finance, positions, orders, and fills."""
        try:
            if not self.client.demo_identity_verified:
                await self.client.connect()
            account, positions, orders, executions = await asyncio.gather(
                self.client.fetch_demo_account_summary_ccxt(),
                self.client.fetch_demo_positions_ccxt(),
                self.client.fetch_demo_orders_ccxt(since_ms=self._since_ms),
                self.client.fetch_demo_executions_ccxt(since_ms=self._since_ms),
            )
            missing_mark_symbols = [
                symbol for symbol in self._symbols if all(pos.symbol != symbol for pos in positions)
            ]
            tickers = (
                await asyncio.gather(
                    *(self.client.exchange.fetch_ticker(symbol) for symbol in missing_mark_symbols)
                )
                if missing_mark_symbols
                else []
            )
            server_ms = await self.client.fetch_server_time_ms()

            marks: dict[str, tuple[Decimal, int]] = {
                pos.symbol: (pos.mark_price, server_ms) for pos in positions
            }
            for symbol, ticker in zip(missing_mark_symbols, tickers, strict=True):
                mark, known_ms = self._parse_ticker_mark(symbol, ticker, server_ms)
                marks[symbol] = (mark, min(known_ms, server_ms))

            recv_ms = int(self._now_ms_fn())
            with self._lock:
                self._epoch += 1
                self._server_time_ms = server_ms
                self._observed_at_ms = server_ms
                self._received_at_ms = recv_ms
                self._account = account
                self._positions_by_symbol = {p.symbol: p for p in positions}
                self._orders_by_id = {o.order_id: o for o in orders}
                for ex in executions:
                    self._executions_by_id[ex.execution_id] = ex
                self._market_marks = marks
                self._history_complete = True
                self._financial_complete = True
                self._connected = True
                self._uncertainty_reasons.clear()
                self._cutoff = f"bybit-demo:{server_ms}:e{self._epoch}"
                return self.snapshot()
        except Exception as exc:
            self._fail_closed(f"CATCH_UP_FAILED:{type(exc).__name__}")
            raise

    async def stream_once(self) -> None:
        """Consume one round of ccxt.pro private & mark streams, failing closed on errors."""
        try:
            balance, positions, orders, trades = await asyncio.gather(
                self._ws.watch_balance(),
                self._ws.watch_positions(),
                self._ws.watch_orders(),
                self._ws.watch_my_trades(),
            )
            if isinstance(balance, dict) and balance:
                self._apply_balance(balance)
            if isinstance(positions, list) and positions:
                self._apply_positions(positions, ts_ms=self._now_ms_fn())
            if isinstance(orders, list) and orders:
                self._apply_orders(orders, ts_ms=self._now_ms_fn())
            if isinstance(trades, list) and trades:
                self._apply_executions(trades)
            watch_symbols = tuple(dict.fromkeys((*self._symbols, *self._positions_by_symbol.keys())))
            for symbol in watch_symbols:
                ticker = await self._ws.watch_ticker(symbol)
                if isinstance(ticker, dict) and ticker:
                    self._apply_ticker(symbol, ticker)
        except Exception as exc:
            self._fail_closed(f"WS_STREAM_ERROR:{type(exc).__name__}")
            raise

    async def _watch_loop(
        self, name: str, coro_fn: Callable[[], Any], apply_fn: Callable[[Any], None]
    ) -> None:
        while self._running:
            try:
                payload = await coro_fn()
                if payload:
                    apply_fn(payload)
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._fail_closed(f"WS_{name.upper()}_FAILED:{type(exc).__name__}")
                self._running = False
                logger.warning("Bybit Demo WS watcher %s failed closed: %s", name, type(exc).__name__)
                raise

    async def start(self) -> None:
        """Catch up via REST then start concurrent ccxt.pro WS private & ticker streams."""
        if self._running:
            return
        await self.catch_up()
        self._running = True
        self._tasks = [
            asyncio.create_task(self._watch_loop("balance", self._ws.watch_balance, self._apply_balance)),
            asyncio.create_task(
                self._watch_loop(
                    "positions",
                    self._ws.watch_positions,
                    lambda rows: self._apply_positions(rows, ts_ms=self._now_ms_fn()),
                )
            ),
            asyncio.create_task(
                self._watch_loop(
                    "orders",
                    self._ws.watch_orders,
                    lambda rows: self._apply_orders(rows, ts_ms=self._now_ms_fn()),
                )
            ),
            asyncio.create_task(self._watch_loop("trades", self._ws.watch_my_trades, self._apply_executions)),
        ]
        watch_symbols = tuple(dict.fromkeys((*self._symbols, *self._positions_by_symbol.keys())))
        for symbol in watch_symbols:
            self._tasks.append(
                asyncio.create_task(
                    self._watch_loop(
                        f"ticker:{symbol}",
                        lambda s=symbol: self._ws.watch_ticker(s),
                        lambda payload, s=symbol: self._apply_ticker(s, payload),
                    )
                )
            )

    async def stop(self) -> None:
        """Stop stream tasks and close WebSocket and REST transports."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
        self._fail_closed("RECONCILER_STOPPED")
        if hasattr(self._ws, "close"):
            try:
                await self._ws.close()
            except Exception as exc:
                logger.debug("Ignoring error closing ccxt.pro WS exchange: %s", type(exc).__name__)
        try:
            await self.client.close()
        except Exception as exc:
            logger.debug("Ignoring error closing REST client: %s", type(exc).__name__)

    def snapshot(self) -> BybitDemoReconciliationSnapshot:
        """Return an immutable point-in-time view of reconciled Bybit Demo state."""
        with self._lock:
            orders = tuple(
                sorted(self._orders_by_id.values(), key=lambda o: (o.timestamp or 0, o.order_id))
            )
            executions = tuple(
                sorted(
                    self._executions_by_id.values(), key=lambda ex: (ex.timestamp_ms, ex.execution_id)
                )
            )
            positions = tuple(sorted(self._positions_by_symbol.values(), key=lambda p: p.symbol))
            marks = tuple((sym, price, ts) for sym, (price, ts) in sorted(self._market_marks.items()))
            return BybitDemoReconciliationSnapshot(
                endpoint="https://api-demo.bybit.com",
                observed_at_ms=self._observed_at_ms,
                received_at_ms=self._received_at_ms,
                server_time_ms=self._server_time_ms,
                epoch=self._epoch,
                connected=self._connected,
                history_complete=self._history_complete,
                financial_complete=self._financial_complete,
                cutoff=self._cutoff,
                account=self._account,
                positions=positions,
                orders=orders,
                executions=executions,
                market_marks=marks,
                native_account_id=self.client.config.expected_demo_uid,
                native_account_source="bybit_v5_user_query_api",
                uncertainty_reasons=tuple(self._uncertainty_reasons),
            )
