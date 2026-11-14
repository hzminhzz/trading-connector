"""Tests for CCXT UNIFIED reconciliation and ccxt.pro WS streaming on Bybit Demo."""
from __future__ import annotations

import asyncio
from collections import deque
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest

from connectors.bybit.client import BybitClient
from connectors.bybit.config import BybitConfig, Environment, Mode
from connectors.bybit.models import BybitInvalidOrderError, BybitSnapshotError
from connectors.bybit.reconcile import DEMO_PRIVATE_WS_ENDPOINT, BybitDemoReconciler


def _demo_config(*, mode: Mode = Mode.READ_ONLY) -> BybitConfig:
    return BybitConfig(
        api_key="demo-key",
        api_secret="demo-secret",
        environment=Environment.DEMO,
        mode=mode,
        expected_demo_uid="778899",
    )


def _demo_rest_exchange() -> AsyncMock:
    ex = AsyncMock()
    ex.hostname = "bybit.com"
    ex.urls = {"api": {"private": "https://api-demo.{hostname}"}}
    ex.isSandboxModeEnabled = False
    ex.options = {"enableDemoTrading": True}
    ex.enable_demo_trading = lambda enabled=True: None
    ex.load_markets = AsyncMock(return_value={})
    ex.close = AsyncMock(return_value=None)
    ex.fetch_time = AsyncMock(return_value=1_700_000_005_000)

    def _market(native_symbol: str) -> dict[str, Any]:
        mapping = {
            "BTCUSDT": {"id": "BTCUSDT", "symbol": "BTC/USDT:USDT", "linear": True, "swap": True, "settle": "USDT"},
            "ETHUSDT": {"id": "ETHUSDT", "symbol": "ETH/USDT:USDT", "linear": True, "swap": True, "settle": "USDT"},
        }
        return mapping[native_symbol]

    ex.market = _market
    ex.private_get_v5_user_query_api = AsyncMock(
        return_value={"retCode": 0, "result": {"userID": "778899", "apiKey": "demo-key", "uta": "1", "readOnly": "0"}}
    )
    ex.private_get_v5_account_info = AsyncMock(
        return_value={"retCode": 0, "result": {"unifiedMarginStatus": 6}}
    )
    ex.fetch_balance = AsyncMock(
        return_value={
            "info": {
                "result": {
                    "list": [
                        {
                            "accountType": "UNIFIED",
                            "totalEquity": "10025.50",
                            "totalAvailableBalance": "9000.50",
                            "totalWalletBalance": "10000.00",
                            "totalPerpUPL": "25.50",
                            "totalInitialMargin": "1000.00",
                            "totalMaintenanceMargin": "50.00",
                        }
                    ]
                }
            }
        }
    )

    async def _fetch_positions(*, params: dict[str, Any]) -> list[dict[str, Any]]:
        if params.get("subType") == "linear" and params.get("settleCoin") == "USDT":
            return [
                {
                    "symbol": "BTC/USDT:USDT",
                    "contracts": 0.1,
                    "side": "long",
                    "entryPrice": 50000.0,
                    "markPrice": 50255.0,
                    "unrealizedPnl": 25.5,
                    "info": {
                        "symbol": "BTCUSDT",
                        "side": "Buy",
                        "size": "0.1",
                        "avgPrice": "50000.0",
                        "markPrice": "50255.0",
                        "unrealisedPnl": "25.50",
                        "positionIdx": "0",
                        "createdTime": "1700000001000",
                    },
                }
            ]
        return []

    ex.fetch_positions = AsyncMock(side_effect=_fetch_positions)

    async def _fetch_open_orders(*, params: dict[str, Any]) -> list[dict[str, Any]]:
        if params.get("subType") == "linear" and params.get("settleCoin") == "USDT":
            return [
                {
                    "id": "ord-open-1",
                    "clientOrderId": "intent-open-1",
                    "symbol": "BTC/USDT:USDT",
                    "side": "sell",
                    "type": "limit",
                    "price": 52000.0,
                    "amount": 0.1,
                    "filled": 0.0,
                    "status": "open",
                    "reduceOnly": True,
                    "timestamp": 1_700_000_002_000,
                    "info": {
                        "orderId": "ord-open-1",
                        "orderLinkId": "intent-open-1",
                        "symbol": "BTCUSDT",
                        "side": "Sell",
                        "orderType": "Limit",
                        "price": "52000.00",
                        "qty": "0.1",
                        "cumExecQty": "0",
                        "orderStatus": "New",
                        "reduceOnly": True,
                        "positionIdx": "0",
                        "stopOrderType": "",
                        "createdTime": "1700000002000",
                    },
                }
            ]
        return []

    async def _fetch_closed_orders(*, since: int | None = None, params: dict[str, Any]) -> list[dict[str, Any]]:
        if params.get("subType") == "linear" and params.get("settleCoin") == "USDT":
            return [
                {
                    "id": "ord-closed-1",
                    "clientOrderId": "intent-entry-1",
                    "symbol": "BTC/USDT:USDT",
                    "side": "buy",
                    "type": "market",
                    "price": None,
                    "amount": 0.1,
                    "filled": 0.1,
                    "status": "closed",
                    "reduceOnly": False,
                    "timestamp": 1_700_000_001_000,
                    "info": {
                        "orderId": "ord-closed-1",
                        "orderLinkId": "intent-entry-1",
                        "symbol": "BTCUSDT",
                        "side": "Buy",
                        "orderType": "Market",
                        "price": "0",
                        "qty": "0.1",
                        "cumExecQty": "0.1",
                        "orderStatus": "Filled",
                        "reduceOnly": False,
                        "positionIdx": "0",
                        "stopOrderType": "UNKNOWN",
                        "createdTime": "1700000001000",
                    },
                }
            ]
        return []

    ex.fetch_open_orders = AsyncMock(side_effect=_fetch_open_orders)
    ex.fetch_canceled_and_closed_orders = AsyncMock(side_effect=_fetch_closed_orders)

    async def _fetch_my_trades(*, symbol: str | None = None, since: int | None = None, params: dict[str, Any]) -> list[dict[str, Any]]:
        if params.get("settleCoin") == "USDT":
            return [
                {
                    "id": "exec-1",
                    "order": "ord-closed-1",
                    "symbol": "BTC/USDT:USDT",
                    "side": "buy",
                    "amount": 0.1,
                    "price": 50000.0,
                    "fee": {"cost": 2.75, "currency": "USDT"},
                    "timestamp": 1_700_000_001_100,
                    "info": {
                        "execId": "exec-1",
                        "orderId": "ord-closed-1",
                        "orderLinkId": "intent-entry-1",
                        "symbol": "BTCUSDT",
                        "side": "Buy",
                        "execQty": "0.1",
                        "execPrice": "50000.0",
                        "execFee": "2.75",
                        "feeCurrency": "USDT",
                        "execTime": "1700000001100",
                    },
                },
                {
                    "id": "exec-1",
                    "order": "ord-closed-1",
                    "symbol": "BTC/USDT:USDT",
                    "side": "buy",
                    "amount": 0.1,
                    "price": 50000.0,
                    "fee": {"cost": 2.75, "currency": "USDT"},
                    "timestamp": 1_700_000_001_100,
                    "info": {
                        "execId": "exec-1",
                        "orderId": "ord-closed-1",
                        "orderLinkId": "intent-entry-1",
                        "symbol": "BTCUSDT",
                        "side": "Buy",
                        "execQty": "0.1",
                        "execPrice": "50000.0",
                        "execFee": "2.75",
                        "feeCurrency": "USDT",
                        "execTime": "1700000001100",
                    },
                },
            ]
        return []

    ex.fetch_my_trades = AsyncMock(side_effect=_fetch_my_trades)
    ex.fetch_ticker = AsyncMock(
        return_value={"symbol": "ETH/USDT:USDT", "last": 3000.0, "info": {"markPrice": "3001.25"}, "timestamp": 1_700_000_005_000}
    )
    return ex


def _demo_ws_exchange() -> AsyncMock:
    ws = AsyncMock()
    ws.hostname = "bybit.com"
    ws.urls = {"api": {"ws": {"private": {"contract": "wss://stream-demo.{hostname}/v5/private"}}}}
    ws.isSandboxModeEnabled = False
    ws.options = {"enableDemoTrading": True}
    ws.close = AsyncMock(return_value=None)
    return ws


def test_ccxt_unified_account_positions_orders_executions() -> None:
    async def _run() -> None:
        ex = _demo_rest_exchange()
        client = BybitClient(_demo_config(), exchange=ex)
        await client.connect()

        summary = await client.fetch_demo_account_summary_ccxt()
        assert summary.equity_usd == Decimal("10025.50")
        assert summary.wallet_balance_usd == Decimal("10000.00")
        assert summary.unrealized_pnl_usd == Decimal("25.50")
        assert summary.initial_margin_usd == Decimal("1000.00")
        assert summary.maintenance_margin_usd == Decimal("50.00")
        assert summary.equity_usd == summary.wallet_balance_usd + summary.unrealized_pnl_usd

        positions = await client.fetch_demo_positions_ccxt()
        assert len(positions) == 1
        assert positions[0].symbol == "BTC/USDT:USDT"
        assert positions[0].size == Decimal("0.1")
        assert positions[0].entry_price == Decimal("50000.0")
        assert positions[0].mark_price == Decimal("50255.0")

        orders = await client.fetch_demo_orders_ccxt()
        by_id = {o.order_id: o for o in orders}
        assert set(by_id) == {"ord-open-1", "ord-closed-1"}
        assert by_id["ord-open-1"].reduce_only is True
        assert by_id["ord-open-1"].client_order_id == "intent-open-1"
        assert by_id["ord-closed-1"].status == "filled"

        execs = await client.fetch_demo_executions_ccxt()
        assert len(execs) == 1
        assert execs[0].execution_id == "exec-1"
        assert execs[0].fee == Decimal("2.75")
        assert execs[0].client_order_id == "intent-entry-1"

    asyncio.run(_run())


def test_ccxt_rejects_active_hedge_or_non_unified_account() -> None:
    async def _run() -> None:
        ex = _demo_rest_exchange()
        client = BybitClient(_demo_config(), exchange=ex)
        await client.connect()

        ex.fetch_balance.return_value = {"info": {"result": {"list": [{"accountType": "CONTRACT", "totalEquity": "10"}]}}}
        with pytest.raises(BybitSnapshotError):
            await client.fetch_demo_account_summary_ccxt()

        ex.fetch_positions.side_effect = None
        ex.fetch_positions.return_value = [
            {
                "symbol": "BTC/USDT:USDT",
                "side": "long",
                "contracts": 0.1,
                "entryPrice": 50000,
                "markPrice": 50100,
                "info": {"symbol": "BTCUSDT", "side": "Buy", "size": "0.1", "avgPrice": "50000", "markPrice": "50100", "positionIdx": "1"},
            }
        ]
        with pytest.raises(BybitSnapshotError, match="hedge"):
            await client.fetch_demo_positions_ccxt()

    asyncio.run(_run())


def test_create_order_validates_client_order_id_and_reduce_only() -> None:
    async def _run() -> None:
        ex = _demo_rest_exchange()
        ex.create_order = AsyncMock(
            return_value={
                "id": "ord-new-1",
                "clientOrderId": "intent-123",
                "symbol": "BTC/USDT:USDT",
                "side": "buy",
                "type": "limit",
                "price": 50000.0,
                "amount": 0.01,
                "filled": 0.0,
                "status": "open",
                "reduceOnly": True,
                "timestamp": 1_700_000_010_000,
            }
        )
        client = BybitClient(_demo_config(mode=Mode.EXECUTION), exchange=ex)
        order = await client.create_order(
            symbol="BTC/USDT:USDT",
            side="buy",
            order_type="limit",
            amount=0.01,
            price=50000.0,
            reduce_only=True,
            client_order_id="intent-123",
        )
        assert order.client_order_id == "intent-123"
        assert order.reduce_only is True
        assert ex.create_order.call_args.kwargs["params"] == {"orderLinkId": "intent-123", "reduceOnly": True}

        with pytest.raises(BybitInvalidOrderError):
            await client.create_order("BTC/USDT:USDT", "buy", "limit", 0.01, 50000.0, client_order_id="x" * 37)

        with pytest.raises(BybitInvalidOrderError):
            await client.create_order(
                "BTC/USDT:USDT", "buy", "limit", 0.01, 50000.0, client_order_id="a", params={"orderLinkId": "b"}
            )

    asyncio.run(_run())


def test_reconciler_ws_endpoint_guard_catch_up_stream_and_fail_closed() -> None:
    async def _run() -> None:
        ex = _demo_rest_exchange()
        bad_ws = _demo_ws_exchange()
        bad_ws.isSandboxModeEnabled = True
        client = BybitClient(_demo_config(), exchange=ex)
        with pytest.raises(BybitSnapshotError, match="Bybit Demo"):
            BybitDemoReconciler(client, ws_exchange=bad_ws)

        ws = _demo_ws_exchange()
        assert DEMO_PRIVATE_WS_ENDPOINT in ws.urls["api"]["ws"]["private"]["contract"].replace("{hostname}", ws.hostname)
        clock = deque([1_700_000_005_100, 1_700_000_006_000, 1_700_000_006_050, 1_700_000_006_100, 1_700_000_006_150, 1_700_000_006_200])
        reconciler = BybitDemoReconciler(
            client,
            ws_exchange=ws,
            symbols=("BTC/USDT:USDT", "ETH/USDT:USDT"),
            now_ms_fn=lambda: clock.popleft() if clock else 1_700_000_006_200,
        )

        snap1 = await reconciler.catch_up()
        assert snap1.connected is True
        assert snap1.history_complete is True
        assert snap1.financial_complete is True
        assert snap1.epoch == 1
        assert snap1.cutoff == "bybit-demo:1700000005000:e1"
        assert dict((sym, price) for sym, price, _ in snap1.market_marks) == {
            "BTC/USDT:USDT": Decimal("50255.0"),
            "ETH/USDT:USDT": Decimal("3001.25"),
        }

        ws.watch_balance = AsyncMock(
            return_value={
                "timestamp": 1_700_000_006_000,
                "info": {
                    "data": [
                        {
                            "accountType": "UNIFIED",
                            "totalEquity": "10250.00",
                            "totalAvailableBalance": "10250.00",
                            "totalWalletBalance": "10250.00",
                            "totalPerpUPL": "0",
                            "totalInitialMargin": "0",
                            "totalMaintenanceMargin": "0",
                        }
                    ]
                },
            }
        )
        ws.watch_positions = AsyncMock(
            return_value=[{"symbol": "BTC/USDT:USDT", "info": {"symbol": "BTCUSDT", "size": "0", "positionIdx": "0"}}]
        )
        ws.watch_orders = AsyncMock(
            return_value=[
                {
                    "id": "ord-open-1",
                    "clientOrderId": "intent-open-1",
                    "symbol": "BTC/USDT:USDT",
                    "side": "sell",
                    "type": "limit",
                    "price": 52500.0,
                    "amount": 0.1,
                    "filled": 0.1,
                    "status": "closed",
                    "reduceOnly": True,
                    "timestamp": 1_700_000_005_900,
                    "info": {
                        "orderId": "ord-open-1",
                        "orderLinkId": "intent-open-1",
                        "symbol": "BTCUSDT",
                        "side": "Sell",
                        "orderType": "Limit",
                        "price": "52500.0",
                        "qty": "0.1",
                        "cumExecQty": "0.1",
                        "orderStatus": "Filled",
                        "reduceOnly": True,
                        "positionIdx": "0",
                    },
                }
            ]
        )
        ws.watch_my_trades = AsyncMock(
            return_value=[
                {
                    "id": "exec-2",
                    "order": "ord-open-1",
                    "symbol": "BTC/USDT:USDT",
                    "side": "sell",
                    "amount": 0.1,
                    "price": 52500.0,
                    "fee": {"cost": 1.05, "currency": "USDT"},
                    "timestamp": 1_700_000_005_950,
                    "info": {
                        "execId": "exec-2",
                        "orderId": "ord-open-1",
                        "orderLinkId": "intent-open-1",
                        "symbol": "BTCUSDT",
                        "side": "Sell",
                        "execQty": "0.1",
                        "execPrice": "52500.0",
                        "execFee": "1.05",
                        "feeCurrency": "USDT",
                        "execTime": "1700000005950",
                    },
                }
            ]
        )
        ws.watch_ticker = AsyncMock(
            return_value={"last": 52510.0, "info": {"markPrice": "52505.0"}, "timestamp": 1_700_000_006_100}
        )

        await reconciler.stream_once()
        snap2 = reconciler.snapshot()
        assert snap2.positions == ()
        assert snap2.account is not None and snap2.account.equity_usd == Decimal("10250.00")
        assert [e.execution_id for e in snap2.executions] == ["exec-1", "exec-2"]

        ws.watch_balance = AsyncMock(side_effect=RuntimeError("ws dropped"))
        with pytest.raises(RuntimeError):
            await reconciler.stream_once()
        snap3 = reconciler.snapshot()
        assert snap3.connected is False
        assert any("WS_STREAM_ERROR" in r for r in snap3.uncertainty_reasons)

    asyncio.run(_run())
