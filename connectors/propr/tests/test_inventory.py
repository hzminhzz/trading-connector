import asyncio
from decimal import Decimal as D
import httpx
import pytest

from connectors.propr.inventory import InventoryReader, parse_order, parse_position, parse_trade
from connectors.propr.models import OrderStatus, one_way_positions
from connectors.propr.rest import ProprRest, RemoteAPIError


def _order(i):
    return {
        "orderId": f"ord-{i}", "intentId": f"int-{i}", "accountId": "selected",
        "asset": "xyz:AAPL", "side": "buy", "positionSide": "long",
        "status": "partially_filled", "type": "limit", "quantity": "1.25",
        "cumulativeQuantity": "0.25", "averageFillPrice": "125.1",
        "cumulativeTradingFees": "0.02", "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:01:00Z",
    }


def _position(side):
    return {
        "positionId": side, "accountId": "selected", "asset": "xyz:AAPL",
        "positionSide": side, "status": "open", "quantity": "2",
        "leverage": "3.5", "marginMode": "cross", "markPrice": "123.45",
        "entryPrice": "120", "unrealizedPnl": "6.9", "marginUsed": "82",
        "updatedAt": "2026-01-01T00:02:00Z",
    }


def _trade(i):
    return {
        "tradeId": f"trade-{i}", "orderId": f"ord-{i}", "accountId": "selected",
        "asset": "xyz:AAPL", "quantity": ".25", "price": "123.45",
        "executedAt": "2026-01-01T00:01:00Z",
    }


def test_inventory_paginates_and_preserves_hedges():
    calls = []
    def handler(req):
        calls.append(req.method)
        offset = int(req.url.params["offset"])
        if req.url.path.endswith("/orders"):
            rows = [_order(i) for i in range(43)]
        elif req.url.path.endswith("/positions"):
            rows = [_position("long"), _position("short")]
        else:
            rows = [_trade(i) for i in range(25)]
        return httpx.Response(200, json={"data": rows[offset:offset + 20]})

    async def run():
        rest = ProprRest(
            "https://api.beta.propr.xyz/v1", "dummy",
            transport=httpx.MockTransport(handler),
        )
        try:
            inv = await InventoryReader(rest, "selected").snapshot()
            assert inv.summary() == {
                "orders": 43, "positions": 2, "trades": 25, "has_hedges": True,
            }
            assert inv.orders[20].status is OrderStatus.PARTIAL
            assert inv.orders[20].filled == D(".25")
            assert inv.orders[20].fee_paid == D(".02")
            assert inv.positions[0].asset == "xyz:AAPL"
            assert inv.trades[0].timestamp == "2026-01-01T00:01:00Z"
            with pytest.raises(ValueError):
                one_way_positions(inv.positions)
        finally:
            await rest.close()
    asyncio.run(run())
    assert calls and set(calls) == {"GET"}


def test_unknown_provider_fields_fail_closed():
    with pytest.raises(RemoteAPIError):
        parse_order({"id": "1"})
    with pytest.raises(RemoteAPIError):
        parse_position({"id": "1"})
    with pytest.raises(RemoteAPIError):
        parse_trade({"id": "1"})


def test_expired_status_and_margin():
    order = parse_order({**_order(1), "status": "expired"})
    position = parse_position(_position("short"))
    assert order.status is OrderStatus.EXPIRED
    assert position.leverage == D("3.5")
    assert position.margin_mode == "cross"


def test_nan_rejected():
    with pytest.raises(RemoteAPIError):
        parse_order({**_order(1), "cumulativeQuantity": "NaN"})


def test_wrong_account_rejected():
    async def run():
        def handler(req):
            if req.url.path.endswith("/orders"):
                rows = [{**_order(1), "accountId": "other"}]
            else:
                rows = []
            return httpx.Response(200, json={"data": rows})
        rest = ProprRest(
            "https://api.beta.propr.xyz/v1", "dummy",
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(RemoteAPIError, match="account identity"):
                await InventoryReader(rest, "selected").snapshot()
        finally:
            await rest.close()
    asyncio.run(run())
