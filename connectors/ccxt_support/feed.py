"""Shared CCXT Pro WebSocket completed-bar kline feed implementing ML4T DataFeedProtocol."""
from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import datetime, timezone
from typing import Any

import ccxt.pro as ccxtpro

from connectors.common.intents import UnknownOutcome

try:
    from ml4t.specs.lifecycle import (
        BarPayload,
        EventCompletion,
        GapEvidence,
        LifecycleVersion,
        MarketEvent,
        MarketEventKind,
    )
except ImportError:
    BarPayload = None  # type: ignore[assignment,misc]
    EventCompletion = None  # type: ignore[assignment,misc]
    GapEvidence = None  # type: ignore[assignment,misc]
    LifecycleVersion = None  # type: ignore[assignment,misc]
    MarketEvent = None  # type: ignore[assignment,misc]
    MarketEventKind = None  # type: ignore[assignment,misc]

_UNITS_MS = {
    "m": 60_000,
    "h": 3_600_000,
    "d": 86_400_000,
    "w": 604_800_000,
}


def _require_ml4t() -> None:
    if MarketEvent is None:
        raise ImportError(
            "ml4t-specs / ml4t.live is required for CCXTKlineFeed; install with `pip install ml4t-live`."
        )


def parse_timeframe_ms(tf: str) -> int:
    if not tf or len(tf) < 2:
        raise ValueError(f"Unsupported timeframe: {tf!r}")
    unit = tf[-1].lower()
    if unit not in _UNITS_MS:
        raise ValueError(f"Unsupported timeframe unit: {unit!r} in {tf!r}")
    try:
        amount = int(tf[:-1])
    except ValueError as exc:
        raise ValueError(f"Invalid timeframe multiplier in {tf!r}") from exc
    if amount <= 0:
        raise ValueError(f"Timeframe multiplier must be positive: {tf!r}")
    return amount * _UNITS_MS[unit]


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


class CCXTKlineFeed:
    """Async CCXT Pro OHLCV kline feed emitting fail-closed completed-bar MarketEvents."""

    def __init__(
        self,
        asset: str,
        symbol: str | None,
        timeframe: str = "1m",
        exchange: Any | None = None,
        max_age_seconds: float = 120.0,
        silence_seconds: float = 120.0,
        now_fn: Callable[[], datetime | float] = _default_now,
        *,
        default_symbol: str,
        exchange_factory: Callable[[], Any],
        source: str,
        source_market: str,
    ) -> None:
        _require_ml4t()
        self.asset = asset
        self.symbol = symbol or default_symbol
        self.timeframe = timeframe
        self._exchange = exchange
        self._exchange_factory = exchange_factory
        self._source = source
        self._source_market = source_market
        self.max_age_seconds = max_age_seconds
        self.silence_seconds = silence_seconds
        self.now_fn = now_fn
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._last_emitted_ts: int | None = None
        self._fatal_error: BaseException | None = None
        self._tf_ms = parse_timeframe_ms(timeframe)

    def _now_dt(self) -> datetime:
        val = self.now_fn()
        if isinstance(val, datetime):
            return val if val.tzinfo is not None else val.replace(tzinfo=timezone.utc)
        return datetime.fromtimestamp(float(val), tz=timezone.utc)

    async def start(self) -> None:
        if self._running:
            return
        if self._exchange is None:
            self._exchange = self._exchange_factory()
        self._running = True
        self._fatal_error = None
        self._task = asyncio.create_task(self._stream_loop())

    def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def __aiter__(self) -> AsyncIterator[Any]:
        return self

    async def __anext__(self) -> Any:
        while True:
            if self._fatal_error is not None and self._queue.empty():
                raise self._fatal_error
            if not self._running and self._queue.empty():
                raise StopAsyncIteration
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=0.2)
                if isinstance(item, BaseException):
                    raise item
                return item
            except asyncio.TimeoutError:
                if self._fatal_error is not None:
                    raise self._fatal_error
                if not self._running and self._queue.empty():
                    raise StopAsyncIteration

    async def _fail(self, exc: BaseException) -> None:
        self._fatal_error = exc
        self._running = False
        await self._queue.put(exc)

    async def _stream_loop(self) -> None:
        assert self._exchange is not None
        current_candle: Sequence[Any] | None = None
        last_activity = self._now_dt()

        try:
            while self._running:
                try:
                    ohlcv_list = await asyncio.wait_for(
                        self._exchange.watch_ohlcv(self.symbol, self.timeframe),
                        timeout=self.silence_seconds,
                    )
                    last_activity = self._now_dt()
                except asyncio.TimeoutError:
                    elapsed = (self._now_dt() - last_activity).total_seconds()
                    await self._fail(
                        UnknownOutcome(
                            f"Feed for {self.symbol} ({self.timeframe}) went silent for {elapsed:.1f}s"
                        )
                    )
                    break
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    elapsed = (self._now_dt() - last_activity).total_seconds()
                    if elapsed >= self.silence_seconds:
                        await self._fail(
                            UnknownOutcome(
                                f"Feed for {self.symbol} ({self.timeframe}) failed for {elapsed:.1f}s: "
                                f"{type(exc).__name__}"
                            )
                        )
                        break
                    await asyncio.sleep(1.0)
                    continue

                if not ohlcv_list:
                    continue

                for candle in ohlcv_list:
                    ts = int(candle[0])
                    if self._last_emitted_ts is not None and ts <= self._last_emitted_ts:
                        continue

                    if current_candle is None:
                        current_candle = candle
                        continue

                    prev_ts = int(current_candle[0])
                    if ts > prev_ts:
                        gap = (ts - prev_ts) > self._tf_ms
                        try:
                            event = self.parse_candle(current_candle, gap_detected=gap)
                        except UnknownOutcome as exc:
                            await self._fail(exc)
                            return
                        await self._queue.put(event)
                        self._last_emitted_ts = prev_ts
                        current_candle = candle
                    elif ts == prev_ts:
                        current_candle = candle
        finally:
            self._running = False
            if self._exchange is not None and hasattr(self._exchange, "close"):
                with contextlib.suppress(Exception):
                    await self._exchange.close()

    def parse_candle(self, candle: Sequence[Any], *, gap_detected: bool = False) -> Any:
        """Validate chronology/staleness and build a completed-bar MarketEvent."""
        if gap_detected:
            raise UnknownOutcome(
                f"Kline sequence gap detected before candle {candle[0]} for {self.symbol}"
            )
        ts_ms = int(candle[0])
        now_dt = self._now_dt()
        event_dt = datetime.fromtimestamp((ts_ms + self._tf_ms) / 1000.0, tz=timezone.utc)
        age_seconds = (now_dt - event_dt).total_seconds()
        if age_seconds < -30.0:
            raise UnknownOutcome(
                f"Market event is future-dated by {-age_seconds:.1f}s for {self.symbol}"
            )
        if age_seconds > self.max_age_seconds:
            raise UnknownOutcome(
                f"Market event is stale ({age_seconds:.1f}s old) for {self.symbol}"
            )

        open_px, high_px, low_px, close_px, volume = (
            float(candle[1]),
            float(candle[2]),
            float(candle[3]),
            float(candle[4]),
            float(candle[5]),
        )
        if not all(math.isfinite(x) for x in (open_px, high_px, low_px, close_px, volume)):
            raise UnknownOutcome(f"Non-finite OHLCV values in candle {ts_ms} for {self.symbol}")
        if min(open_px, high_px, low_px, close_px) <= 0 or volume < 0:
            raise UnknownOutcome(f"Invalid OHLCV values in candle {ts_ms} for {self.symbol}")

        return MarketEvent(  # type: ignore[misc]
            version=LifecycleVersion.V1,
            event_time=event_dt,
            receipt_time=now_dt,
            kind=MarketEventKind.BAR,
            completion=EventCompletion.COMPLETE,
            source=self._source,
            asset=self.asset,
            payload=BarPayload(
                open=open_px,
                high=high_px,
                low=low_px,
                close=close_px,
                volume=volume,
            ),
            provider_sequence=ts_ms,
            gap=GapEvidence(
                detected=False,
                reason="contiguous",
            ),
            metadata={
                "timeframe": self.timeframe,
                "source_market": self._source_market,
                "gap_detected": gap_detected,
            },
        )


def make_ccxtpro_factory(exchange_name: str, *, new_updates: bool = True) -> Callable[[], Any]:
    """Create a default ccxt.pro exchange constructor configured for swaps."""
    cls = getattr(ccxtpro, exchange_name)
    return lambda: cls({"enableRateLimit": True, "newUpdates": new_updates})
