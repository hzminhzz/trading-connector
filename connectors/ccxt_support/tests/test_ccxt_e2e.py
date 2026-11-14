"""Shared LiveEngine E2E shadow tests for CCXT feeds and brokers."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from ml4t.backtest.strategy import Strategy
from ml4t.backtest.types import OrderSide, OrderType
from ml4t.live.engine import LiveEngine
from ml4t.live.safety import LiveRiskConfig, SafeBroker

from connectors.ccxt_support.tests.conftest import make_mock_provider_client


class SmokeStrategy(Strategy):
    def __init__(self, asset: str) -> None:
        super().__init__()
        self.asset = asset
        self.received_bars: list[dict] = []
        self.submitted_orders: list[object] = []

    def on_data(self, timestamp: datetime, data: dict, context: dict, broker: object) -> None:
        self.received_bars.append(data)
        if not self.submitted_orders:
            self.submitted_orders.append(
                broker.submit_order(
                    asset=self.asset,
                    quantity=0.005,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                )
            )


def test_ccxt_live_engine_shadow_e2e(provider_spec, tmp_path):
    now = datetime.now(UTC)
    t0 = (now.timestamp() - 150) * 1000.0
    c0 = [t0, 80000.0, 80100.0, 79900.0, 80050.0, 10.0]
    c1 = [t0 + 60_000.0, 80050.0, 80200.0, 80000.0, 80150.0, 15.0]
    c2 = [t0 + 120_000.0, 80150.0, 80300.0, 80100.0, 80250.0, 5.0]

    mock_exchange = MagicMock()
    mock_exchange.watch_ohlcv = AsyncMock(side_effect=[[c0, c1], [c0, c1, c2], asyncio.CancelledError()])
    mock_exchange.close = AsyncMock()

    feed = provider_spec.feed_cls(
        asset=provider_spec.asset,
        symbol=provider_spec.symbol,
        timeframe="1m",
        max_age_seconds=300.0,
        silence_seconds=5.0,
        exchange=mock_exchange,
        now_fn=lambda: now,
    )

    mock_client = make_mock_provider_client(provider_spec, mode="shadow")
    journal = provider_spec.journal_cls(tmp_path / f"{provider_spec.name}_e2e.db")
    raw_broker = provider_spec.broker_cls(client=mock_client, journal=journal)

    safe_broker = SafeBroker(
        raw_broker,
        LiveRiskConfig(
            execution_mode="shadow",
            max_order_value=5000.0,
            max_position_value=10000.0,
            max_total_exposure=20000.0,
            allowed_assets={provider_spec.asset, provider_spec.symbol},
            state_file=tmp_path / f"{provider_spec.name}_risk_state.json",
        ),
    )
    safe_broker.record_market_snapshot(provider_spec.asset, 80050.0)

    strategy = SmokeStrategy(provider_spec.asset)
    engine = LiveEngine(
        strategy=strategy,
        broker=safe_broker,
        feed=feed,
        halt_on_unhealthy=True,
        auto_recover=False,
    )

    async def run():
        await engine.connect()
        await engine.run()
        await engine.stop()

    asyncio.run(run())

    assert strategy.received_bars
    assert strategy.received_bars[0][provider_spec.asset]["close"] == 80050.0
    assert len(strategy.submitted_orders) == 1
    order = strategy.submitted_orders[0]
    assert order.order_id.startswith("SHADOW-")
    assert order.quantity == 0.005
    assert order.status.value in ("pending", "filled")
    mock_client.create_order.assert_not_called()
