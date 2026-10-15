# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportMissingImports=false
# type: ignore
"""End-to-End (E2E) integration test running LiveEngine with HyperliquidLiveBroker and HyperliquidKlineFeed."""
import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from ml4t.backtest.strategy import Strategy
from ml4t.backtest.types import OrderSide, OrderType
from ml4t.live.engine import LiveEngine
from ml4t.live.safety import LiveRiskConfig, SafeBroker

from connectors.hyperliquid.broker import HyperliquidLiveBroker
from connectors.hyperliquid.config import Environment, HyperliquidConfig, Mode
from connectors.hyperliquid.feed import HyperliquidKlineFeed
from connectors.hyperliquid.journal import HyperliquidIntentJournal
from connectors.hyperliquid.models import HyperliquidOrderData


class SmokeStrategy(Strategy):
    """Simple strategy that submits an order on first received bar."""

    def __init__(self) -> None:
        super().__init__()
        self.received_bars: list[dict] = []
        self.submitted_orders: list[object] = []

    def on_data(self, timestamp: datetime, data: dict, context: dict, broker: object) -> None:
        self.received_bars.append(data)
        if len(self.submitted_orders) == 0:
            order = broker.submit_order(
                asset="BTC/USDC",
                quantity=0.005,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
            )
            self.submitted_orders.append(order)


def test_hyperliquid_live_engine_shadow_e2e(tmp_path):
    now = datetime.now(UTC)
    t0 = (now.timestamp() - 150) * 1000.0
    c0 = [t0, 80000.0, 80100.0, 79900.0, 80050.0, 10.0]
    c1 = [t0 + 60_000.0, 80050.0, 80200.0, 80000.0, 80150.0, 15.0]
    c2 = [t0 + 120_000.0, 80150.0, 80300.0, 80100.0, 80250.0, 5.0]

    mock_exchange = MagicMock()
    mock_exchange.watch_ohlcv = AsyncMock(side_effect=[
        [c0, c1],
        [c0, c1, c2],
        asyncio.CancelledError(),
    ])
    mock_exchange.close = AsyncMock()

    feed = HyperliquidKlineFeed(
        asset="BTC/USDC",
        symbol="BTC/USDC:USDC",
        timeframe="1m",
        max_age_seconds=300.0,
        silence_seconds=5.0,
        exchange=mock_exchange,
        now_fn=lambda: now,
    )

    mock_client = MagicMock()
    mock_client.config = HyperliquidConfig(
        Environment.TESTNET, Mode.SHADOW, wallet_address="0x1", private_key="0x2"
    )
    mock_client.connect = AsyncMock()
    mock_client.close = AsyncMock()
    mock_client.fetch_balance = AsyncMock(return_value={})
    mock_client.fetch_positions = AsyncMock(return_value=[])
    mock_client.fetch_open_orders = AsyncMock(return_value=[])
    mock_client.create_order = AsyncMock(return_value=HyperliquidOrderData(
        order_id="hl_ord_e2e_1",
        client_order_id=None,
        symbol="BTC/USDC:USDC",
        side="buy",
        order_type="market",
        price=Decimal("80050.0"),
        quantity=Decimal("0.005"),
        filled_quantity=Decimal("0.005"),
        status="filled",
        timestamp=int(now.timestamp() * 1000),
        raw={},
    ))

    journal = HyperliquidIntentJournal(tmp_path / "hl_e2e_journal.db")
    raw_broker = HyperliquidLiveBroker(client=mock_client, journal=journal)

    risk_config = LiveRiskConfig(
        execution_mode="shadow",
        max_order_value=5000.0,
        max_position_value=10000.0,
        max_total_exposure=20000.0,
        allowed_assets={"BTC/USDC", "BTC/USDC:USDC"},
        state_file=tmp_path / "hl_risk_state.json",
    )
    safe_broker = SafeBroker(raw_broker, risk_config)
    safe_broker.record_market_snapshot("BTC/USDC", 80050.0)

    strategy = SmokeStrategy()
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

    assert len(strategy.received_bars) >= 1
    assert "BTC/USDC" in strategy.received_bars[0]
    assert strategy.received_bars[0]["BTC/USDC"]["close"] == 80050.0

    assert len(strategy.submitted_orders) == 1
    order = strategy.submitted_orders[0]
    assert order.order_id.startswith("SHADOW-")
    assert order.quantity == 0.005
    assert order.status.value in ("pending", "filled")
