"""Shared contract and fault tests for CCXT Kline feeds (Bybit & Hyperliquid)."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from ml4t.live.protocols import DataFeedProtocol
from ml4t.specs.lifecycle import MarketEvent, MarketEventKind

from connectors.common.intents import UnknownOutcome


def test_feed_conforms_to_data_feed_protocol(provider_spec):
    feed = provider_spec.feed_cls(asset=provider_spec.asset, symbol=provider_spec.symbol)
    assert isinstance(feed, DataFeedProtocol)


def test_parse_candle_uses_parsed_timeframe_step(provider_spec):
    start_dt = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    feed_1h = provider_spec.feed_cls(
        asset=provider_spec.asset,
        symbol=provider_spec.symbol,
        timeframe="1h",
        now_fn=lambda: start_dt + timedelta(hours=1, seconds=10),
    )
    candle = [start_dt.timestamp() * 1000.0, 80000.0, 80500.0, 79800.0, 80200.0, 150.5]
    event = feed_1h.parse_candle(candle, gap_detected=False)

    assert isinstance(event, MarketEvent)
    assert event.kind == MarketEventKind.BAR
    assert event.asset == provider_spec.asset
    assert event.event_time == start_dt + timedelta(hours=1)
    assert event.metadata["gap_detected"] is False


def test_feed_stream_loop_enqueues_completed_bars(provider_spec):
    mock_exchange = MagicMock()
    mock_exchange.watch_ohlcv = AsyncMock()
    mock_exchange.close = AsyncMock()

    now = datetime.now(UTC)
    t0 = (now.timestamp() - 100) * 1000.0
    candle_1 = [t0, 80000.0, 80500.0, 79800.0, 80200.0, 10.0]
    candle_2 = [t0 + 60_000.0, 80200.0, 80600.0, 80100.0, 80400.0, 5.0]
    mock_exchange.watch_ohlcv.side_effect = [[candle_1], [candle_1, candle_2], asyncio.CancelledError()]

    feed = provider_spec.feed_cls(
        asset=provider_spec.asset,
        symbol=provider_spec.symbol,
        exchange=mock_exchange,
        now_fn=lambda: now,
    )

    async def run():
        await feed.start()
        event = await anext(feed)
        assert event.payload.close == 80200.0
        feed.stop()
        mock_exchange.close.assert_called()

    asyncio.run(run())


def test_feed_fails_closed_on_sequence_gap(provider_spec):
    mock_exchange = MagicMock()
    mock_exchange.watch_ohlcv = AsyncMock()
    mock_exchange.close = AsyncMock()

    now = datetime.now(UTC)
    t0 = (now.timestamp() - 100) * 1000.0
    c1a = [t0, 80000.0, 80100.0, 79900.0, 80050.0, 2.0]
    c1b = [t0, 80000.0, 80300.0, 79900.0, 80250.0, 6.0]
    c2 = [t0 + 120_000.0, 80250.0, 80400.0, 80200.0, 80350.0, 1.0]
    mock_exchange.watch_ohlcv.side_effect = [[c1a], [c1b], [c2], asyncio.CancelledError()]

    feed = provider_spec.feed_cls(
        asset=provider_spec.asset,
        symbol=provider_spec.symbol,
        timeframe="1m",
        exchange=mock_exchange,
        now_fn=lambda: now,
    )

    async def run():
        await feed.start()
        with pytest.raises(UnknownOutcome, match="sequence gap detected"):
            await anext(feed)
        feed.stop()

    asyncio.run(run())


def test_feed_fails_closed_on_stale_event(provider_spec):
    mock_exchange = MagicMock()
    now = datetime.now(UTC)
    stale_t0 = (now.timestamp() - 600) * 1000.0
    c1 = [stale_t0, 80000.0, 80100.0, 79900.0, 80050.0, 1.0]
    c2 = [stale_t0 + 60_000.0, 80050.0, 80200.0, 80000.0, 80150.0, 1.0]
    mock_exchange.watch_ohlcv = AsyncMock(side_effect=[[c1, c2], asyncio.CancelledError()])

    feed = provider_spec.feed_cls(
        asset=provider_spec.asset,
        symbol=provider_spec.symbol,
        timeframe="1m",
        max_age_seconds=120.0,
        exchange=mock_exchange,
        now_fn=lambda: now,
    )

    async def run():
        await feed.start()
        with pytest.raises(UnknownOutcome, match="Market event is stale"):
            await anext(feed)
        feed.stop()

    asyncio.run(run())


def test_feed_fails_closed_on_future_event(provider_spec):
    mock_exchange = MagicMock()
    now = datetime.now(UTC)
    future_t0 = (now.timestamp() + 40) * 1000.0
    c1 = [future_t0, 80000.0, 80100.0, 79900.0, 80050.0, 1.0]
    c2 = [future_t0 + 60_000.0, 80050.0, 80200.0, 80000.0, 80150.0, 1.0]
    mock_exchange.watch_ohlcv = AsyncMock(side_effect=[[c1, c2], asyncio.CancelledError()])

    feed = provider_spec.feed_cls(
        asset=provider_spec.asset,
        symbol=provider_spec.symbol,
        timeframe="1m",
        exchange=mock_exchange,
        now_fn=lambda: now,
    )

    async def run():
        await feed.start()
        with pytest.raises(UnknownOutcome, match="Market event is future-dated"):
            await anext(feed)
        feed.stop()

    asyncio.run(run())


def test_feed_fails_closed_on_silence_timeout(provider_spec):
    mock_exchange = MagicMock()

    async def silent_exchange(*args, **kwargs):
        await asyncio.sleep(10)

    mock_exchange.watch_ohlcv = AsyncMock(side_effect=silent_exchange)
    feed = provider_spec.feed_cls(
        asset=provider_spec.asset,
        symbol=provider_spec.symbol,
        timeframe="1m",
        silence_seconds=0.1,
        exchange=mock_exchange,
    )

    async def run():
        await feed.start()
        with pytest.raises(UnknownOutcome, match="went silent"):
            await anext(feed)
        feed.stop()

    asyncio.run(run())


def test_multi_candle_batch_catchup(provider_spec):
    mock_exchange = MagicMock()
    mock_exchange.close = AsyncMock()
    now = datetime.now(UTC)
    t0 = (now.timestamp() - 250) * 1000.0
    candles = [
        [t0, 80000.0, 80100.0, 79900.0, 80050.0, 10.0],
        [t0 + 60_000.0, 80050.0, 80200.0, 80000.0, 80150.0, 12.0],
        [t0 + 120_000.0, 80150.0, 80300.0, 80100.0, 80250.0, 15.0],
        [t0 + 180_000.0, 80250.0, 80400.0, 80200.0, 80350.0, 5.0],
    ]
    mock_exchange.watch_ohlcv = AsyncMock(side_effect=[candles, asyncio.CancelledError()])

    feed = provider_spec.feed_cls(
        asset=provider_spec.asset,
        symbol=provider_spec.symbol,
        timeframe="1m",
        max_age_seconds=300.0,
        exchange=mock_exchange,
        now_fn=lambda: now,
    )

    async def run():
        await feed.start()
        for expected_close in (80050.0, 80150.0, 80250.0):
            ev = await anext(feed)
            assert ev.payload.close == expected_close
        feed.stop()

    asyncio.run(run())


def test_feed_fails_closed_when_repeated_errors_exceed_silence_deadline(provider_spec):
    mock_exchange = MagicMock()
    mock_exchange.close = AsyncMock()
    mock_exchange.watch_ohlcv = AsyncMock(side_effect=RuntimeError("ws flap"))

    feed = provider_spec.feed_cls(
        asset=provider_spec.asset,
        symbol=provider_spec.symbol,
        timeframe="1m",
        silence_seconds=0.05,
        exchange=mock_exchange,
    )

    async def run():
        await feed.start()
        with pytest.raises(UnknownOutcome, match="failed for"):
            await anext(feed)
        assert mock_exchange.watch_ohlcv.await_count >= 1
        feed.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    "bad_candle",
    [
        [1700000000000.0, float("nan"), 100.0, 99.0, 100.0, 1.0],
        [1700000000000.0, 100.0, 101.0, 99.0, 0.0, 1.0],
        [1700000000000.0, 100.0, 101.0, 99.0, 100.0, -1.0],
    ],
)
def test_parse_candle_rejects_nonfinite_or_out_of_range_ohlcv(provider_spec, bad_candle):
    now_dt = datetime.fromtimestamp((1700000000000 + 60_000) / 1000.0, tz=UTC)
    feed = provider_spec.feed_cls(
        asset=provider_spec.asset,
        symbol=provider_spec.symbol,
        now_fn=lambda: now_dt,
    )
    with pytest.raises(UnknownOutcome, match="OHLCV"):
        feed.parse_candle(bad_candle, gap_detected=False)
