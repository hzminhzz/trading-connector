"""Bybit Demo V5 snapshot contract and installed ML4T 0.1.2 read-only seam."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from ml4t.live import LiveRiskConfig, SafeBroker
from ml4t.live.protocols import AsyncBrokerProtocol

from connectors.bybit.broker import BybitLiveBroker
from connectors.bybit.client import BybitClient
from connectors.bybit.config import BybitConfig, Environment, Mode
from connectors.bybit.journal import BybitIntentJournal
from connectors.bybit.models import BybitSnapshotError


def v5(category=None, rows=None, cursor=""):
    result = {"list": rows if rows is not None else [], "nextPageCursor": cursor}
    if category is not None:
        result["category"] = category
    return {"retCode": 0, "result": result}


@pytest.fixture
def exchange():
    mock = MagicMock()
    mock.hostname = "bybit.com"
    mock.urls = {"api": {"private": "https://api-demo.{hostname}"}}
    mock.isSandboxModeEnabled = False
    mock.options = {"enableDemoTrading": True}
    mock.load_markets = AsyncMock()
    mock.close = AsyncMock()
    mock.private_get_v5_user_query_api = AsyncMock(return_value={
        "retCode": 0,
        "result": {"apiKey": "demo-synthetic-key", "userID": 24680, "uta": 1, "readOnly": 1},
    })
    mock.private_get_v5_account_info = AsyncMock(return_value={
        "retCode": 0, "result": {"unifiedMarginStatus": 5},
    })
    mock.private_get_v5_account_wallet_balance = AsyncMock(return_value={
        "retCode": 0, "result": {"list": [{
            "accountType": "UNIFIED",
            "totalEquity": "5000.10",
            "totalAvailableBalance": "4800.25",
            "coin": [{"coin": "BTC", "walletBalance": "1.0"}],
        }]},
    })
    mock.private_get_v5_position_list = AsyncMock(
        side_effect=lambda params: v5(params["category"])
    )
    mock.private_get_v5_order_realtime = AsyncMock(
        side_effect=lambda params: v5(params["category"])
    )
    mock.market.return_value = {
        "id": "BTCUSDT", "symbol": "BTC/USDT:USDT", "linear": True,
        "swap": True, "settle": "USDT",
    }
    mock.create_order = AsyncMock()
    mock.cancel_order = AsyncMock()
    mock.edit_order = AsyncMock()
    return mock


@pytest.fixture
def demo_config():
    return BybitConfig(
        environment=Environment.DEMO, mode=Mode.READ_ONLY,
        api_key="demo-synthetic-key", api_secret="demo-synthetic-secret",
        expected_demo_uid="24680",
    )


def test_authenticated_demo_contract_and_account_wide_usd_totals(exchange, demo_config):
    client = BybitClient(demo_config, exchange=exchange)
    async def run():
        await client.connect()
        assert client.demo_identity_verified is True
        assert client.demo_identity_evidence["authenticated_uid_matches_expected"] is True
        assert client.demo_identity_evidence["private_rest"] == "api-demo.bybit.com"
        summary = await client.fetch_demo_account_summary()
        assert str(summary.equity_usd) == "5000.10"
        assert str(summary.available_usd) == "4800.25"
        await client.close()
        assert client.demo_identity_verified is False
        assert client.preflight_identity_evidence["dedicated_demo_key_matches"] is True

    asyncio.run(run())
    assert exchange.private_get_v5_user_query_api.await_count == 1
    assert exchange.private_get_v5_account_info.await_count == 1
    exchange.private_get_v5_account_wallet_balance.assert_awaited_with(
        {"accountType": "UNIFIED"}
    )
    exchange.create_order.assert_not_awaited()
    exchange.cancel_order.assert_not_awaited()
    exchange.edit_order.assert_not_awaited()


@pytest.mark.parametrize("change", ["uid", "api_key", "endpoint", "sandbox", "demo_flag", "no_uid"])
def test_demo_identity_fails_closed(exchange, demo_config, change):
    if change == "uid":
        exchange.private_get_v5_user_query_api.return_value["result"]["userID"] = 12
    elif change == "api_key":
        exchange.private_get_v5_user_query_api.return_value["result"]["apiKey"] = "wrong"
    elif change == "endpoint":
        exchange.urls["api"]["private"] = "https://api.bybit.com"
    elif change == "sandbox":
        exchange.isSandboxModeEnabled = True
    elif change == "demo_flag":
        exchange.options["enableDemoTrading"] = False
    elif change == "no_uid":
        demo_config = BybitConfig(
            environment=Environment.DEMO, mode=Mode.READ_ONLY,
            api_key="demo-synthetic-key", api_secret="demo-synthetic-secret",
        )
    client = BybitClient(demo_config, exchange=exchange)
    with pytest.raises(BybitSnapshotError):
        asyncio.run(client.connect())
    assert client.demo_identity_verified is False
    exchange.create_order.assert_not_awaited()


def test_demo_wallet_missing_usd_field_is_blocked(exchange, demo_config):
    exchange.private_get_v5_account_wallet_balance.return_value["result"]["list"][0][
        "totalAvailableBalance"
    ] = ""
    client = BybitClient(demo_config, exchange=exchange)
    async def run():
        await client.connect()
        with pytest.raises(BybitSnapshotError, match="missing or invalid USD"):
            await client.fetch_demo_account_summary()
        await client.close()
    asyncio.run(run())


def test_native_read_only_preflight_and_no_mutations(exchange, demo_config, tmp_path):
    client = BybitClient(demo_config, exchange=exchange)
    broker = BybitLiveBroker(client=client, journal=BybitIntentJournal(":memory:"))
    assert isinstance(broker, AsyncBrokerProtocol)
    config = LiveRiskConfig(
        execution_mode="paper", fail_on_reconciliation_mismatch=True,
        state_file=tmp_path / "native_risk.json",
    )
    safe = SafeBroker(broker, config)
    try:
        native = asyncio.run(safe.preflight_async())
        assert native["passed"] is True
        assert native["broker_connected"] is True
        assert native["account_value"] == 5000.10
        assert native["cash"] == 4800.25
        assert native["reconciliation"]["clean"] is True
        assert native["reconciliation"]["live_positions"] == {}
        assert native["reconciliation"]["live_pending_orders"] == []
        assert client.preflight_identity_evidence["authenticated_uid_matches_expected"]
        assert safe.persistence_status["state_error"] is None
        assert safe.persistence_status["journal_error"] is None
    finally:
        safe.close_persistence()
        broker.journal.close()
    exchange.create_order.assert_not_awaited()
    exchange.cancel_order.assert_not_awaited()
    exchange.edit_order.assert_not_awaited()


def test_native_preflight_reports_unexpected_pending_order_without_mutating(exchange, demo_config, tmp_path):
    order = {
        "orderId": "synthetic-order", "orderLinkId": "client-ref",
        "symbol": "BTCUSDT", "side": "Buy", "orderType": "Limit",
        "orderStatus": "PartiallyFilled", "positionIdx": 0,
        "qty": "0.020", "cumExecQty": "0.005",
        "price": "65000", "createdTime": "1730000000000",
        "stopOrderType": "",
    }
    exchange.private_get_v5_order_realtime.side_effect = lambda params: v5(
        params["category"], [order] if params.get("settleCoin") == "USDT" else []
    )
    client = BybitClient(demo_config, exchange=exchange)
    broker = BybitLiveBroker(client=client, journal=BybitIntentJournal(":memory:"))
    safe = SafeBroker(broker, LiveRiskConfig(
        execution_mode="paper", fail_on_reconciliation_mismatch=True,
        state_file=tmp_path / "native_risk.json",
    ))
    try:
        native = asyncio.run(safe.preflight_async())
        assert native["passed"] is False
        assert native["reconciliation"]["clean"] is False
        assert len(native["reconciliation"]["unexpected_pending_orders"]) == 1
        assert native["reconciliation"]["live_pending_orders"][0]["quantity"] == 0.02
    finally:
        safe.close_persistence()
        broker.journal.close()
    exchange.create_order.assert_not_awaited()
    exchange.cancel_order.assert_not_awaited()
    exchange.edit_order.assert_not_awaited()


def test_demo_positions_all_pages_and_scope_validation(exchange, demo_config):
    long_position = {
        "symbol": "BTCUSDT", "side": "Buy", "size": "0.020",
        "positionIdx": 0, "avgPrice": "63000", "markPrice": "64000",
        "createdTime": "1730000000000",
    }
    short_position = {**long_position, "positionIdx": 2, "side": "Sell"}
    calls = []
    def position_response(params):
        calls.append(dict(params))
        if params.get("settleCoin") == "USDT":
            if "cursor" not in params:
                return v5("linear", [], "page2")
            return v5("linear", [long_position])
        return v5(params["category"])
    exchange.private_get_v5_position_list.side_effect = position_response
    client = BybitClient(demo_config, exchange=exchange)

    async def run():
        await client.connect()
        result = await client.fetch_positions()
        assert len(result) == 1
        assert str(result[0].size) == "0.020"
        assert result[0].symbol == "BTC/USDT:USDT"
        await client.close()
    asyncio.run(run())
    assert any(c.get("cursor") == "page2" for c in calls)

    exchange.private_get_v5_position_list.side_effect = lambda params: v5(
        params["category"], [short_position] if params.get("settleCoin") == "USDT" else []
    )
    client = BybitClient(demo_config, exchange=exchange)
    async def reject():
        await client.connect()
        with pytest.raises(BybitSnapshotError, match="hedge position"):
            await client.fetch_positions()
        await client.close()
    asyncio.run(reject())


def test_native_preflight_sees_nonempty_demo_position_without_duplicate_aliases(
    exchange, demo_config, tmp_path,
):
    position = {
        "symbol": "BTCUSDT", "side": "Buy", "size": "0.02",
        "positionIdx": 0, "avgPrice": "63000", "markPrice": "64000",
        "createdTime": "1730000000000",
    }
    exchange.private_get_v5_position_list.side_effect = lambda params: v5(
        params["category"], [position] if params.get("settleCoin") == "USDT" else []
    )
    broker = BybitLiveBroker(
        client=BybitClient(demo_config, exchange=exchange),
        journal=BybitIntentJournal(":memory:"),
    )
    safe = SafeBroker(broker, LiveRiskConfig(
        execution_mode="paper", fail_on_reconciliation_mismatch=True,
        state_file=tmp_path / "native_risk.json",
    ))
    try:
        report = asyncio.run(safe.preflight_async())
        assert report["passed"] is False  # fresh native state cannot explain broker exposure
        assert report["reconciliation"]["clean"] is False
        assert report["reconciliation"]["live_positions"] == {"BTC/USDT:USDT": 0.02}
        assert report["reconciliation"]["unexpected_positions"] == {"BTC/USDT:USDT": 0.02}
    finally:
        safe.close_persistence()
        broker.journal.close()
    exchange.create_order.assert_not_awaited()


def test_demo_order_contract_rejects_unrepresentable_active_orders(exchange, demo_config):
    order = {
        "orderId": "synthetic-order", "symbol": "BTCUSDT",
        "side": "Buy", "orderType": "Market", "orderStatus": "Untriggered",
        "positionIdx": 0, "qty": "1", "cumExecQty": "0", "price": "",
        "stopOrderType": "Stop",
    }
    exchange.private_get_v5_order_realtime.side_effect = lambda params: v5(
        params["category"], [order] if params.get("settleCoin") == "USDT" else []
    )
    client = BybitClient(demo_config, exchange=exchange)
    async def run():
        await client.connect()
        with pytest.raises(BybitSnapshotError, match="conditional order"):
            await client.fetch_open_orders()
        await client.close()
    asyncio.run(run())
    exchange.create_order.assert_not_awaited()
