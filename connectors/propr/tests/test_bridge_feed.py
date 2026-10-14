import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

pytest.importorskip("ml4t.live", reason="Optional ML4T integration not installed")

from connectors.propr.account import AccountSnapshot
from connectors.propr.feed import ValidatedExternalFeed, binance_completed_kline
from connectors.propr.inventory import Inventory
from connectors.propr.instruments import Instrument, InstrumentRegistry
from connectors.propr.ml4t_bridge import ML4TShadowBroker, ML4TStrategyAdapter
from connectors.propr.models import Position as NativePosition
from connectors.propr.shadow import ShadowGateway
from connectors.propr.state import UnknownOutcome


class Ready:
    def assert_opening_safe(self):
        pass


class FakeAccount:
    async def inspect(self):
        return {
            "account_bound": True, "financial_fields_verified": True,
            "account_source": "challenge",
            "snapshot": AccountSnapshot(
                "selected", "attempt", D("1000"), D("1000"), D("450"),
                D("100"), D("-10"), D("90"), D("50"),
            ),
        }


class FakeInventory:
    def __init__(self, positions=()):
        self.positions = positions

    async def snapshot(self, account_id=None):
        assert account_id == "selected"
        return Inventory((), tuple(self.positions), ())

def make_broker(positions=()):
    registry = InstrumentRegistry([Instrument("BTC", "BTC", D(".01"), D(".01"), D(".01"))])
    ready = Ready()
    shadow = ShadowGateway(registry, ready)
    broker = ML4TShadowBroker(FakeAccount(), FakeInventory(positions), shadow, registry, ready)
    return broker, shadow


def test_ml4t_protocol_shadow_intent_and_assertions():
    pytest.importorskip("ml4t.live.protocols")
    from ml4t.live.protocols import AsyncBrokerProtocol
    broker, shadow = make_broker()
    assert isinstance(broker, AsyncBrokerProtocol)

    async def run():
        await broker.connect()
        assert broker.is_connected
        assert await broker.get_cash_async() == 450.0
        assert await broker.get_account_value_async() == 1000.0
        order = await broker.submit_order_async("BTC", .10)
        assert order.order_id.startswith("shadow-")
        assert shadow.decisions[-1].allowed
        with pytest.raises(PermissionError):
            await broker.cancel_order_async("not-real")
        with pytest.raises(PermissionError):
            await broker.close_position_async("BTC")
        with pytest.raises(PermissionError):
            broker.assert_paper_trading()
        await broker.disconnect()
        assert not broker.is_connected

    asyncio.run(run())


def test_hedge_is_rejected_before_adapter_ready():
    created = datetime.now(UTC).isoformat()
    positions = (
        NativePosition("p1", "BTC", "long", D("1"), entry_price=D("100"), created_at=created),
        NativePosition("p2", "BTC", "short", D("1"), entry_price=D("100"), created_at=created),
    )
    broker, _ = make_broker(positions)
    async def run():
        with pytest.raises(ValueError, match="one-way"):
            await broker.connect()
    asyncio.run(run())
    assert not broker.is_connected


def test_stale_inventory_account_binding_fails_before_snapshot():
    broker, _ = make_broker()
    broker.inventory_reader.account_id = "old-challenge-account"

    async def run():
        with pytest.raises(UnknownOutcome, match="different account"):
            await broker.connect()

    asyncio.run(run())
    assert not broker.is_connected


def make_kline(*, age_seconds=60, complete=True):
    close = datetime.now(UTC) - timedelta(seconds=age_seconds)
    end_ms = int(close.timestamp() * 1000)
    start_ms = end_ms - 60_000
    return {
        "e": "kline", "s": "BTCUSDT",
        "k": {
            "s": "BTCUSDT", "i": "1m", "x": complete, "t": start_ms,
            "T": end_ms, "o": "100", "h": "105", "l": "99",
            "c": "102", "v": "50",
        },
    }


def test_completed_binance_kline_and_feed():
    pytest.importorskip("ml4t.specs.lifecycle")
    event = binance_completed_kline(make_kline(), asset="BTC", symbol="BTCUSDT")
    assert event is not None
    assert event.asset == "BTC"
    assert event.payload.close == 102.0
    assert binance_completed_kline(make_kline(complete=False), asset="BTC", symbol="BTCUSDT") is None

    async def source():
        yield event
    feed = ValidatedExternalFeed(source)
    async def run():
        await feed.start()
        assert (await feed.__anext__()).source == "binance-spot-reference"
        with pytest.raises(StopAsyncIteration):
            await feed.__anext__()
        feed.stop()
    asyncio.run(run())


def test_feed_rejects_stale_and_duplicate():
    event = binance_completed_kline(make_kline(age_seconds=600), asset="BTC", symbol="BTCUSDT")
    async def source():
        yield event
        yield event
    feed = ValidatedExternalFeed(source, max_age_seconds=100)
    async def run():
        await feed.start()
        with pytest.raises(UnknownOutcome, match="stale"):
            await feed.__anext__()
        feed.stop()
    asyncio.run(run())


def test_live_engine_runs_unchanged_strategy_in_shadow():
    pytest.importorskip("ml4t.live")
    from ml4t.live.engine import LiveEngine
    from ml4t.backtest.strategy import Strategy

    hit = []
    submitted = []
    class SameStrategy(Strategy):
        def on_data(self, timestamp, data, context, broker):
            hit.append(data)
            submitted.append(broker.submit_order("BTC", .10))

    event = binance_completed_kline(make_kline(age_seconds=20), asset="BTC", symbol="BTCUSDT")
    async def source():
        yield event
    broker, shadow = make_broker()
    feed = ValidatedExternalFeed(source)
    engine = LiveEngine(SameStrategy(), broker, feed, halt_on_unhealthy=True,
                        auto_recover=False, max_event_age_seconds=120)
    async def run():
        await engine.connect()
        await engine.run()
    asyncio.run(run())
    assert len(hit) == 1
    assert len(submitted) == 1
    assert submitted[0].order_id.startswith("shadow-")
    assert len(shadow.decisions) == 1 and shadow.decisions[0].allowed


def test_live_engine_adapts_target_weight_strategy_to_shadow_intents():
    from ml4t.backtest import Strategy
    from ml4t.live.engine import LiveEngine

    class TargetWeightStrategy(Strategy):
        def on_data(self, timestamp, data, context, broker):
            assert data["BTC"]["close"] == 102.0
            broker.order_target_percent("BTC", 0.102)

    event = binance_completed_kline(make_kline(age_seconds=20), asset="BTC", symbol="BTCUSDT")

    async def source():
        yield event

    broker, shadow = make_broker()
    engine = LiveEngine(
        ML4TStrategyAdapter(TargetWeightStrategy()), broker,
        ValidatedExternalFeed(source), halt_on_unhealthy=True,
        auto_recover=False, max_event_age_seconds=120,
    )

    async def run():
        await engine.connect()
        await engine.run()

    asyncio.run(run())
    assert len(shadow.decisions) == 1
    assert shadow.decisions[0].allowed
    assert shadow.decisions[0].quantity == "1.0"


def test_reduce_only_maps_canonical_symbol_to_exact_native_position():
    registry = InstrumentRegistry([
        Instrument("AAPL", "xyz:AAPL", D(".01"), D(".01"), D(".01")),
    ])
    created = datetime.now(UTC).isoformat()
    native = NativePosition(
        "native-aapl-1", "xyz:AAPL", "long", D("1"),
        entry_price=D("100"), created_at=created,
    )
    ready = Ready()
    shadow = ShadowGateway(registry, ready)
    broker = ML4TShadowBroker(FakeAccount(), FakeInventory([native]), shadow, registry, ready)
    requests = []
    submit = shadow.submit

    async def capture(request):
        requests.append(request)
        return await submit(request)

    shadow.submit = capture

    async def run():
        await broker.connect()
        await broker.submit_order_async("AAPL", -0.25)

    asyncio.run(run())
    assert len(requests) == 1
    assert requests[0].asset == "AAPL"
    assert requests[0].side == "sell"
    assert requests[0].position_side == "long"
    assert requests[0].reduce_only
    assert requests[0].position_id == "native-aapl-1"
