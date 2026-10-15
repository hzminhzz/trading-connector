# type: ignore
"""Bybit real-time market data feed conforming to ML4T DataFeedProtocol."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

try:
    from ml4t.specs.lifecycle import (
        BarPayload,
        EventCompletion,
        LifecycleVersion,
        MarketEvent,
        MarketEventKind,
    )
except ImportError:
    BarPayload = EventCompletion = LifecycleVersion = MarketEvent = MarketEventKind = None

from connectors.bybit.journal import UnknownOutcome

logger = logging.getLogger(__name__)


def _require_ml4t() -> None:
    if MarketEvent is None:
        raise ImportError("Optional dependency ml4t-live is required for BybitKlineFeed")


class BybitKlineFeed:
    """Streams real-time completed OHLCV bars from Bybit into ML4T MarketEvents."""

    def __init__(
        self,
        asset: str,
        symbol: str = "BTC/USDT:USDT",
        timeframe: str = "1m",
        exchange: Any = None,
        max_age_seconds: float = 120.0,
        silence_seconds: float = 130.0,
        now_fn: Any = None,
    ) -> None:
        _require_ml4t()
        self.asset = asset
        self.symbol = symbol
        self.timeframe = timeframe
        self.max_age_seconds = max_age_seconds
        self.silence_seconds = silence_seconds
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self._previous_event_time: datetime | None = None
        self._exchange = exchange
        self._running = False
        self._error: Exception | None = None
        self._queue: asyncio.Queue[MarketEvent | None] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._last_emitted_timestamp: int | None = None
        self._active_candle: list[float] | None = None
        self._step_ms = self._timeframe_to_ms(self.timeframe)
    async def start(self) -> None:
        """Starts real-time stream subscription and background collector."""
        if self._running:
            return
        self._running = True

        if self._exchange is None:
            import ccxt.pro as ccxtpro
            self._exchange = ccxtpro.bybit({
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            })

        self._worker_task = asyncio.create_task(self._stream_loop())

    def stop(self) -> None:
        """Gracefully stops the market data stream."""
        self._running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()

    def __aiter__(self) -> AsyncIterator[MarketEvent]:
        return self

    async def __anext__(self) -> MarketEvent:
        if self._error is not None:
            err = self._error
            self._error = None
            raise err

        if not self._running and self._queue.empty():
            raise StopAsyncIteration

        try:
            item = await asyncio.wait_for(self._queue.get(), timeout=self.silence_seconds)
        except asyncio.TimeoutError as exc:
            self.stop()
            raise UnknownOutcome(f"Market feed for {self.symbol} went silent") from exc
        except asyncio.CancelledError:
            raise StopAsyncIteration

        if self._error is not None:
            err = self._error
            self._error = None
            raise err

        if item is None:
            raise StopAsyncIteration

        event = item
        if not isinstance(event, MarketEvent):
            self.stop()
            raise UnknownOutcome("Feed item must be a typed MarketEvent")

        if event.metadata and event.metadata.get("gap_detected"):
            self.stop()
            raise UnknownOutcome(f"Market feed sequence gap detected on {self.symbol}")

        if self._previous_event_time is not None and event.event_time <= self._previous_event_time:
            self.stop()
            raise UnknownOutcome(f"Duplicate or out-of-order market event on {self.symbol}")

        self._previous_event_time = event.event_time
        return event

    async def _stream_loop(self) -> None:
        """Continually watches OHLCV and enqueues completed bars."""
        try:
            while self._running:
                try:
                    ohlcv = await self._exchange.watch_ohlcv(
                        self.symbol, timeframe=self.timeframe
                    )
                    if not ohlcv:
                        continue

                    if len(ohlcv) > 1:
                        completed_batch = ohlcv[:-1]
                        in_progress = ohlcv[-1]
                        self._active_candle = in_progress

                        for candle in completed_batch:
                            c_time = int(candle[0])
                            if self._last_emitted_timestamp is not None:
                                if c_time <= self._last_emitted_timestamp:
                                    continue
                                gap = c_time - self._last_emitted_timestamp
                                gap_detected = gap > self._step_ms
                                if gap_detected:
                                    logger.warning(
                                        "Feed sequence gap on %s: expected %d ms, got %d ms",
                                        self.symbol, self._step_ms, gap,
                                    )
                            else:
                                gap_detected = False

                            event = self._create_market_event(candle, gap_detected=gap_detected)
                            self._last_emitted_timestamp = c_time
                            await self._queue.put(event)

                    else:
                        singleton = ohlcv[0]
                        s_time = int(singleton[0])

                        if self._active_candle is None:
                            self._active_candle = singleton
                        elif s_time == int(self._active_candle[0]):
                            self._active_candle = singleton
                        elif s_time > int(self._active_candle[0]):
                            closed_candle = self._active_candle
                            c_time = int(closed_candle[0])

                            gap_detected = False
                            if self._last_emitted_timestamp is not None:
                                if (c_time - self._last_emitted_timestamp) > self._step_ms:
                                    gap_detected = True
                            if (s_time - c_time) > self._step_ms:
                                gap_detected = True
                            if gap_detected:
                                logger.warning(
                                    "Feed sequence gap on %s: interval skipped around %d",
                                    self.symbol, c_time,
                                )
                            event = self._create_market_event(closed_candle, gap_detected=gap_detected)
                            self._last_emitted_timestamp = c_time
                            self._active_candle = singleton
                            await self._queue.put(event)
                except asyncio.CancelledError:
                    break
                except UnknownOutcome as exc:
                    self._error = exc
                    self.stop()
                    self._queue.put_nowait(None)
                    break
                except Exception as exc:
                    if not self._running:
                        break
                    logger.warning("Error in Bybit kline stream loop: %s", exc)
                    await asyncio.sleep(1.0)
        finally:
            self._running = False
            self._queue.put_nowait(None)
            if self._exchange and hasattr(self._exchange, "close"):
                try:
                    await self._exchange.close()
                except Exception:
                    pass
    def _timeframe_to_ms(self, tf: str) -> int:
        unit = tf[-1]
        val = int(tf[:-1]) if len(tf) > 1 and tf[:-1].isdigit() else 1
        if unit == "m":
            return val * 60_000
        if unit == "h":
            return val * 3_600_000
        if unit == "d":
            return val * 86_400_000
        return 60_000

    def _create_market_event(self, candle: list[float], gap_detected: bool = False) -> MarketEvent:
        """Constructs a validated MarketEvent from a candle [timestamp, o, h, l, c, v]."""
        timestamp_ms = int(candle[0])
        event_time = datetime.fromtimestamp((timestamp_ms + self._step_ms) / 1000.0, UTC)
        now = self.now_fn()
        receipt_time = max(now, event_time)
        now = self.now_fn()
        age = (now - event_time).total_seconds()
        if age < -2.0:
            raise UnknownOutcome(f"Market event is future-dated (age={age:.2f}s)")
        if age > self.max_age_seconds:
            raise UnknownOutcome(f"Market event is stale (age={age:.2f}s > max={self.max_age_seconds}s)")

        payload = BarPayload(
            open=float(candle[1]),
            high=float(candle[2]),
            low=float(candle[3]),
            close=float(candle[4]),
            volume=float(candle[5]),
        )

        return MarketEvent(
            version=LifecycleVersion.V1,
            event_time=event_time,
            receipt_time=receipt_time,
            kind=MarketEventKind.BAR,
            completion=EventCompletion.COMPLETE,
            source="bybit-v5-swap",
            asset=self.asset,
            payload=payload,
            provider_sequence=f"{self.symbol}:{self.timeframe}:{timestamp_ms}",
            metadata={"source_market": "bybit", "symbol": self.symbol, "gap_detected": gap_detected},
        )
