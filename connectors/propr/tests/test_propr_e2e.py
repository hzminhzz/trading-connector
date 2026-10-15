# pyright: reportMissingImports=false
# type: ignore
"""End-to-End (E2E) integration and reliability tests for ProprLiveBroker with LiveEngine."""
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from ml4t.backtest.strategy import Strategy
from ml4t.backtest.types import OrderSide, OrderType
from ml4t.live.engine import LiveEngine
from ml4t.live.safety import LiveRiskConfig, SafeBroker
from ml4t.specs.lifecycle import (
    BarPayload,
    EventCompletion,
    LifecycleVersion,
    MarketEvent,
    MarketEventKind,
)

from connectors.propr.feed import ValidatedExternalFeed
from connectors.propr.ml4t_bridge import ProprLiveBroker
from connectors.propr.state import UnknownOutcome


class ProprSmokeStrategy(Strategy):
    """Verifies strategy callbacks and order placement through LiveEngine."""

    def __init__(self) -> None:
        super().__init__()
        self.received_bars: list[dict] = []
        self.submitted_orders: list[object] = []

    def on_data(self, timestamp: datetime, data: dict, context: dict, broker: object) -> None:
        self.received_bars.append(data)
        if len(self.submitted_orders) == 0:
            order = broker.submit_order(
                asset="BTC/USDC",
                quantity=0.01,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
            )
            self.submitted_orders.append(order)


def test_propr_live_engine_shadow_e2e(tmp_path: Path):
    """Tests complete LiveEngine streaming lifecycle with ProprLiveBroker and SafeBroker."""
    now = datetime.now(UTC)

    # Synthetic validated market events
    event_1 = MarketEvent(
        version=LifecycleVersion.V1,
        event_time=now - timedelta(seconds=60),
        receipt_time=now,
        kind=MarketEventKind.BAR,
        completion=EventCompletion.COMPLETE,
        source="binance-spot-reference",
        asset="BTC/USDC",
        payload=BarPayload(80000.0, 80200.0, 79900.0, 80100.0, 10.0),
        provider_sequence="BTCUSDT:1m:1",
        metadata={"reference_only": True},
    )
    event_2 = MarketEvent(
        version=LifecycleVersion.V1,
        event_time=now,
        receipt_time=now,
        kind=MarketEventKind.BAR,
        completion=EventCompletion.COMPLETE,
        source="binance-spot-reference",
        asset="BTC/USDC",
        payload=BarPayload(80100.0, 80300.0, 80050.0, 80250.0, 15.0),
        provider_sequence="BTCUSDT:1m:2",
        metadata={"reference_only": True},
    )

    async def stream_source():
        yield event_1
        yield event_2

    feed = ValidatedExternalFeed(stream_source, max_age_seconds=300.0)

    # Mock client simulating Propr API
    mock_client = MagicMock()
    mock_client.account_id = "urn:prp-acc:test1234"
    mock_client.setup = MagicMock(return_value="urn:prp-acc:test1234")
    mock_client.get_open_positions = MagicMock(return_value=[])
    mock_client.get_orders = MagicMock(return_value=[])
    mock_client.get_account = MagicMock(return_value={
        "accountId": "urn:prp-acc:test1234",
        "balance": "5000.0",
        "availableBalance": "4500.0",
        "totalUnrealizedPnl": "0.0",
    })
    mock_client.create_order = MagicMock(return_value=[{
        "orderId": "ord_prp_e2e_1",
        "asset": "BTC",
        "base": "BTC",
        "quote": "USDC",
        "side": "buy",
        "type": "market",
        "quantity": "0.01",
        "status": "filled",
        "createdAt": now.isoformat(),
    }])

    journal_path = tmp_path / "propr_e2e_journal.db"
    raw_broker = ProprLiveBroker(client=mock_client, journal_path=journal_path)

    risk_config = LiveRiskConfig(
        execution_mode="shadow",
        max_order_value=5000.0,
        max_position_value=10000.0,
        max_total_exposure=20000.0,
        allowed_assets={"BTC/USDC"},
        state_file=tmp_path / "propr_risk_state.json",
    )
    safe_broker = SafeBroker(raw_broker, risk_config)
    safe_broker.record_market_snapshot("BTC/USDC", 80100.0)

    strategy = ProprSmokeStrategy()
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

    # Strategy verification
    assert len(strategy.received_bars) >= 1
    assert "BTC/USDC" in strategy.received_bars[0]
    assert strategy.received_bars[0]["BTC/USDC"]["close"] == 80100.0

    # Order verification (routed through SafeBroker in shadow mode)
    assert len(strategy.submitted_orders) == 1
    order = strategy.submitted_orders[0]
    assert order.order_id.startswith("SHADOW-")
    assert order.quantity == 0.01

    raw_broker.journal.close()


def test_propr_live_broker_intent_failure_and_reconciliation_gate(tmp_path: Path):
    """Reliability test: API timeout marks intent unknown and fails closed on new openings."""
    mock_client = MagicMock()
    mock_client.account_id = "urn:prp-acc:test1234"
    mock_client.setup = MagicMock(return_value="urn:prp-acc:test1234")
    mock_client.get_open_positions = MagicMock(return_value=[])
    mock_client.get_orders = MagicMock(return_value=[])
    mock_client.get_account = MagicMock(return_value={"balance": "5000.0"})

    mock_client.create_order = MagicMock(side_effect=TimeoutError("Connection timed out to Propr API"))

    journal_path = tmp_path / "propr_reliability.db"
    broker = ProprLiveBroker(client=mock_client, journal_path=journal_path)

    async def run():
        await broker.connect()

        # 1. Order submission failure raises UnknownOutcome and records unknown in journal
        with pytest.raises(UnknownOutcome, match="Order submission outcome ambiguous"):
            await broker.submit_order_async("BTC/USDC", 0.01, side=OrderSide.BUY)

        assert broker.journal.has_unknown() is True

        # 2. Subsequent position opening fails closed immediately before network I/O
        with pytest.raises(UnknownOutcome, match="Unreconciled intent: opening halted"):
            await broker.submit_order_async("BTC/USDC", 0.01, side=OrderSide.BUY)

        # 3. But a reduce-only exit order (risk reducing) is permitted through
        mock_client.get_open_positions = MagicMock(return_value=[{
            "asset": "BTC/USDC",
            "base": "BTC",
            "quote": "USDC",
            "positionSide": "long",
            "quantity": "0.01",
            "positionId": "pos-btc-1",
        }])
        mock_client.create_order = MagicMock(return_value=[{
            "orderId": "ord_exit_1",
            "asset": "BTC",
            "side": "sell",
            "quantity": "0.01",
            "status": "filled",
            "createdAt": datetime.now(UTC).isoformat(),
        }])
        exit_order = await broker.submit_order_async("BTC/USDC", 0.01, side=OrderSide.SELL)
        assert exit_order.order_id == "ord_exit_1"

        await broker.disconnect()

    asyncio.run(run())
    broker.journal.close()


def test_propr_broker_restart_retains_unknown_intent_gate(tmp_path: Path):
    """Restart durability: ambiguous order in run 1 halts openings upon broker 2 recreation."""
    journal_path = tmp_path / "propr_restart_gate.db"

    mock_client_1 = MagicMock()
    mock_client_1.account_id = "urn:prp-acc:restart-test"
    mock_client_1.setup = MagicMock(return_value="urn:prp-acc:restart-test")
    mock_client_1.get_open_positions = MagicMock(return_value=[])
    mock_client_1.get_orders = MagicMock(return_value=[])
    mock_client_1.get_account = MagicMock(return_value={"balance": "5000.0"})
    mock_client_1.create_order = MagicMock(side_effect=TimeoutError("API drop"))

    broker_1 = ProprLiveBroker(client=mock_client_1, journal_path=journal_path)

    async def run_broker_1():
        await broker_1.connect()
        with pytest.raises(UnknownOutcome):
            await broker_1.submit_order_async("BTC/USDC", 0.01, side=OrderSide.BUY)
        await broker_1.disconnect()

    asyncio.run(run_broker_1())
    broker_1.journal.close()

    # Recreate fresh broker on same account/journal path (simulating process restart)
    mock_client_2 = MagicMock()
    mock_client_2.account_id = "urn:prp-acc:restart-test"
    mock_client_2.setup = MagicMock(return_value="urn:prp-acc:restart-test")
    mock_client_2.get_open_positions = MagicMock(return_value=[])
    mock_client_2.get_orders = MagicMock(return_value=[])
    mock_client_2.get_account = MagicMock(return_value={"balance": "5000.0"})
    mock_client_2.create_order = MagicMock()

    broker_2 = ProprLiveBroker(client=mock_client_2, journal_path=journal_path)

    async def run_broker_2():
        await broker_2.connect()
        assert broker_2.journal.has_unknown() is True

        # Opening must be rejected immediately before client.create_order is even invoked
        with pytest.raises(UnknownOutcome, match="Unreconciled intent: opening halted"):
            await broker_2.submit_order_async("BTC/USDC", 0.01, side=OrderSide.BUY)

        mock_client_2.create_order.assert_not_called()
        await broker_2.disconnect()

    asyncio.run(run_broker_2())
    broker_2.journal.close()


def test_propr_broker_empty_order_response_marked_unknown(tmp_path: Path):
    """Validates that empty order response is marked unknown, not accepted."""
    mock_client = MagicMock()
    mock_client.account_id = "urn:prp-acc:test1234"
    mock_client.setup = MagicMock(return_value="urn:prp-acc:test1234")
    mock_client.get_open_positions = MagicMock(return_value=[])
    mock_client.get_orders = MagicMock(return_value=[])
    mock_client.get_account = MagicMock(return_value={"balance": "5000.0"})
    # Propr API returns an empty list
    mock_client.create_order = MagicMock(return_value=[])

    journal_path = tmp_path / "propr_empty_resp.db"
    broker = ProprLiveBroker(client=mock_client, journal_path=journal_path)

    async def run():
        await broker.connect()
        with pytest.raises(UnknownOutcome, match="Empty order acknowledgement"):
            await broker.submit_order_async("BTC/USDC", 0.01, side=OrderSide.BUY)

        # Must be marked unknown in the journal
        assert broker.journal.has_unknown() is True
        cur = broker.journal.connection.execute("SELECT state FROM intents")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "unknown"

        await broker.disconnect()

    asyncio.run(run())
    broker.journal.close()
