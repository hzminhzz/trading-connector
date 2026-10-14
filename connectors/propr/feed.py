"""Independent, timestamp-validated ML4T market data for shadow research.

Binance spot BTC/ETH 1m bars are a reference feed, NOT Propr execution marks.
Trading and risk must not assume the two markets have identical prices.
"""
import asyncio
from datetime import UTC, datetime
import json

from .state import UnknownOutcome

try:
    from ml4t.specs.lifecycle import (
        BarPayload, EventCompletion, LifecycleVersion, MarketEvent, MarketEventKind,
    )
except ImportError:  # ML4T is an optional integration
    BarPayload = EventCompletion = LifecycleVersion = MarketEvent = MarketEventKind = None


def _require_ml4t():
    if MarketEvent is None:
        raise ImportError("Optional dependency ml4t-live==0.1.2 is required")


class ValidatedExternalFeed:
    """Adapt independently sourced typed MarketEvent items to DataFeedProtocol."""

    def __init__(self, source_factory, *, max_age_seconds=120.0, silence_seconds=130.0,
                 now=lambda: datetime.now(UTC)):
        _require_ml4t()
        if max_age_seconds <= 0 or silence_seconds <= 0:
            raise ValueError("Feed age limits must be positive")
        self.source_factory = source_factory
        self.max_age_seconds = max_age_seconds
        self.silence_seconds = silence_seconds
        self.now = now
        self.running = False
        self.iterator = None
        self.previous = {}

    async def start(self):
        if self.running:
            return
        self.iterator = self.source_factory().__aiter__()
        self.running = True

    def stop(self):
        self.running = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.running or self.iterator is None:
            raise StopAsyncIteration
        try:
            event = await asyncio.wait_for(self.iterator.__anext__(), timeout=self.silence_seconds)
        except asyncio.TimeoutError as exc:
            self.running = False
            raise UnknownOutcome("Independent market feed went silent") from exc
        if not isinstance(event, MarketEvent):
            self.running = False
            raise UnknownOutcome("Market feed must provide typed MarketEvent")
        if event.source.lower().startswith("propr"):
            self.running = False
            raise UnknownOutcome("Account stream is not independent market data")
        age = (self.now() - event.event_time).total_seconds()
        if age < -2 or age > self.max_age_seconds or event.gap is not None:
            self.running = False
            raise UnknownOutcome("Market feed stale, future-dated or gapped")
        key = (event.source, event.asset, event.kind)
        prev = self.previous.get(key)
        if prev is not None and event.event_time <= prev:
            self.running = False
            raise UnknownOutcome("Duplicate or out-of-order market event")
        self.previous[key] = event.event_time
        return event


def binance_completed_kline(message, *, asset, symbol, interval="1m", now=None):
    """Parse Binance's documented closed-kline WebSocket envelope."""
    _require_ml4t()
    if not isinstance(message, dict) or message.get("e") != "kline":
        raise ValueError("Invalid Binance kline envelope")
    k = message.get("k")
    if not isinstance(k, dict) or k.get("s") != symbol.upper() or k.get("i") != interval:
        raise ValueError("Unexpected Binance symbol or interval")
    if k.get("x") is not True:
        return None
    event_time = datetime.fromtimestamp((int(k["T"]) + 1) / 1000, UTC)
    receipt_time = now or datetime.now(UTC)
    data = BarPayload(
        open=float(k["o"]), high=float(k["h"]), low=float(k["l"]),
        close=float(k["c"]), volume=float(k["v"]),
    )
    return MarketEvent(
        version=LifecycleVersion.V1, event_time=event_time, receipt_time=receipt_time,
        kind=MarketEventKind.BAR, completion=EventCompletion.COMPLETE,
        source="binance-spot-reference", asset=asset, payload=data,
        provider_sequence=f"{symbol}:{interval}:{int(k['t'])}",
        metadata={"reference_only": True, "source_market": "binance-spot"},
    )


class BinanceCompletedKlineFeed(ValidatedExternalFeed):
    """Concrete 1m independent bar source; no API key and no order endpoints."""

    def __init__(self, *, asset, symbol, connector=None, **kwargs):
        _require_ml4t()
        if not symbol or not symbol.upper().endswith("USDT"):
            raise ValueError("Expected Binance USDT market symbol")
        self.symbol = symbol.upper()
        self.asset = asset
        if connector is None:
            import websockets
            connector = websockets.connect
        self.connector = connector
        super().__init__(self._source, **kwargs)

    async def _source(self):
        url = f"wss://stream.binance.com:9443/ws/{self.symbol.lower()}@kline_1m"
        last_start = None
        async with self.connector(url, ping_interval=20, ping_timeout=10, close_timeout=5) as ws:
            async for raw in ws:
                try:
                    message = json.loads(raw)
                    event = binance_completed_kline(
                        message, asset=self.asset, symbol=self.symbol,
                    )
                except (ValueError, TypeError, KeyError) as exc:
                    raise UnknownOutcome("Malformed independent market feed") from exc
                if event is None:
                    continue
                start = int(message["k"]["t"])
                if last_start is not None and start - last_start != 60_000:
                    raise UnknownOutcome("Independent kline sequence gap")
                last_start = start
                yield event
