# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportMissingImports=false
# type: ignore
"""Unit and fault tests for BybitKlineFeed conforming to DataFeedProtocol."""
import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from ml4t.live.protocols import DataFeedProtocol
from ml4t.specs.lifecycle import MarketEvent, MarketEventKind

from connectors.bybit.feed import BybitKlineFeed
from connectors.bybit.journal import UnknownOutcome


def test_feed_conforms_to_data_feed_protocol():
    feed = BybitKlineFeed(asset="BTC/USDT", symbol="BTC/USDT:USDT")
    assert isinstance(feed, DataFeedProtocol)


def test_create_market_event_uses_parsed_timeframe_step():
    start_dt = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    feed_1h = BybitKlineFeed(
        asset="BTC/USDT",
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        now_fn=lambda: start_dt + timedelta(hours=1, seconds=10),
    )
    candle = [start_dt.timestamp() * 1000.0, 80000.0, 80500.0, 79800.0, 80200.0, 150.5]
    event = feed_1h._create_market_event(candle, gap_detected=False)

    assert isinstance(event, MarketEvent)
    assert event.kind == MarketEventKind.BAR
    assert event.asset == "BTC/USDT"
    assert event.event_time == start_dt + timedelta(hours=1)
    assert event.metadata["gap_detected"] is False

def test_feed_stream_loop_enqueues_completed_bars():
    mock_exchange = MagicMock()
    mock_exchange.watch_ohlcv = AsyncMock()
    mock_exchange.close = AsyncMock()

    now = datetime.now(UTC)
    t0 = (now.timestamp() - 100) * 1000.0
    candle_1 = [t0, 80000.0, 80500.0, 79800.0, 80200.0, 10.0]
    candle_2 = [t0 + 60_000.0, 80200.0, 80600.0, 80100.0, 80400.0, 5.0]

    mock_exchange.watch_ohlcv.side_effect = [
        [candle_1],
        [candle_1, candle_2],
        asyncio.CancelledError(),
    ]

    feed = BybitKlineFeed(
        asset="BTC/USDT",
        symbol="BTC/USDT:USDT",
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


def test_feed_fails_closed_on_sequence_gap():
    mock_exchange = MagicMock()
    mock_exchange.watch_ohlcv = AsyncMock()
    mock_exchange.close = AsyncMock()

    now = datetime.now(UTC)
    t0 = (now.timestamp() - 100) * 1000.0
    candle_1a = [t0, 80000.0, 80100.0, 79900.0, 80050.0, 2.0]
    candle_1b = [t0, 80000.0, 80300.0, 79900.0, 80250.0, 6.0]
    # Candle 2 arrives 2 minutes later (skipped interval)
    t_gap = t0 + 120_000.0
    candle_2 = [t_gap, 80250.0, 80400.0, 80200.0, 80350.0, 1.0]

    mock_exchange.watch_ohlcv.side_effect = [
        [candle_1a],
        [candle_1b],
        [candle_2],
        asyncio.CancelledError(),
    ]

    feed = BybitKlineFeed(
        asset="BTC/USDT",
        symbol="BTC/USDT:USDT",
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


def test_feed_fails_closed_on_stale_event():
    mock_exchange = MagicMock()
    mock_exchange.watch_ohlcv = AsyncMock()

    now = datetime.now(UTC)
    # Candle completed 500s ago (> 120s max_age)
    stale_t0 = (now.timestamp() - 600) * 1000.0
    candle_1 = [stale_t0, 80000.0, 80100.0, 79900.0, 80050.0, 1.0]
    candle_2 = [stale_t0 + 60_000.0, 80050.0, 80200.0, 80000.0, 80150.0, 1.0]

    mock_exchange.watch_ohlcv.side_effect = [
        [candle_1, candle_2],
        asyncio.CancelledError(),
    ]

    feed = BybitKlineFeed(
        asset="BTC/USDT",
        symbol="BTC/USDT:USDT",
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


def test_feed_fails_closed_on_future_event():
    mock_exchange = MagicMock()
    mock_exchange.watch_ohlcv = AsyncMock()

    now = datetime.now(UTC)
    # Event timestamp in the future (+100s)
    future_t0 = (now.timestamp() + 40) * 1000.0
    candle_1 = [future_t0, 80000.0, 80100.0, 79900.0, 80050.0, 1.0]
    candle_2 = [future_t0 + 60_000.0, 80050.0, 80200.0, 80000.0, 80150.0, 1.0]

    mock_exchange.watch_ohlcv.side_effect = [
        [candle_1, candle_2],
        asyncio.CancelledError(),
    ]

    feed = BybitKlineFeed(
        asset="BTC/USDT",
        symbol="BTC/USDT:USDT",
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


def test_feed_fails_closed_on_silence_timeout():
    mock_exchange = MagicMock()
    # Exchange hangs / goes silent
    mock_exchange.watch_ohlcv = AsyncMock(side_effect=asyncio.sleep(10))

    feed = BybitKlineFeed(
        asset="BTC/USDT",
        symbol="BTC/USDT:USDT",
        timeframe="1m",
        silence_seconds=0.1,  # Short silence timeout for testing
        exchange=mock_exchange,
    )

    async def run():
        await feed.start()
        with pytest.raises(UnknownOutcome, match="went silent"):
            await anext(feed)
        feed.stop()

    asyncio.run(run())


def test_multi_candle_batch_catchup():
    mock_exchange = MagicMock()
    mock_exchange.watch_ohlcv = AsyncMock()
    mock_exchange.close = AsyncMock()

    now = datetime.now(UTC)
    t0 = (now.timestamp() - 250) * 1000.0
    c0 = [t0, 80000.0, 80100.0, 79900.0, 80050.0, 10.0]
    c1 = [t0 + 60_000.0, 80050.0, 80200.0, 80000.0, 80150.0, 12.0]
    c2 = [t0 + 120_000.0, 80150.0, 80300.0, 80100.0, 80250.0, 15.0]
    c3 = [t0 + 180_000.0, 80250.0, 80400.0, 80200.0, 80350.0, 5.0]

    mock_exchange.watch_ohlcv.side_effect = [
        [c0, c1, c2, c3],
        asyncio.CancelledError(),
    ]

    feed = BybitKlineFeed(
        asset="BTC/USDT",
        symbol="BTC/USDT:USDT",
        timeframe="1m",
        max_age_seconds=300.0,
        exchange=mock_exchange,
        now_fn=lambda: now,
    )

    async def run():
        await feed.start()
        ev0 = await anext(feed)
        assert ev0.payload.close == 80050.0

        ev1 = await anext(feed)
        assert ev1.payload.close == 80150.0

        ev2 = await anext(feed)
        assert ev2.payload.close == 80250.0

        feed.stop()

    asyncio.run(run())
