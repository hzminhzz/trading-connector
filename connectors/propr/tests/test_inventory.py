import asyncio
from decimal import Decimal as D
import httpx
import pytest
from connectors.propr.inventory import InventoryReader, parse_order, parse_position
from connectors.propr.models import OrderStatus, one_way_positions
from connectors.propr.rest import ProprRest, RemoteAPIError

def test_inventory_paginates_and_preserves_hedges():
    calls = []
    def handler(req):
        calls.append(req.method)
        offset = int(req.url.params["offset"])
        if req.url.path.endswith("/orders"):
            rows = [{"id": str(i), "asset": "xyz:AAPL", "side": "buy",
                     "positionSide": "long", "status": "partially_filled",
                     "quantity": "1.25", "filledQuantity": "0.25"} for i in range(43)]
        elif req.url.path.endswith("/positions"):
            rows = [{"id": "long", "asset": "xyz:AAPL", "positionSide": "long", "quantity": "1"},
                    {"id": "short", "asset": "xyz:AAPL", "positionSide": "short", "quantity": "2"}]
        else:
            rows = [{"id": str(i), "orderId": str(i), "asset": "xyz:AAPL",
                     "quantity": ".25", "price": "123.45", "timestamp": "2026-01-01T00:00:00Z"} for i in range(25)]
        return httpx.Response(200, json={"data": rows[offset:offset+20]})
    async def run():
        rest = ProprRest("https://api.beta.propr.xyz/v1", "dummy", transport=httpx.MockTransport(handler))
        try:
            inv = await InventoryReader(rest, "selected").snapshot()
            assert inv.summary() == {"orders": 43, "positions": 2, "trades": 25, "has_hedges": True}
            assert inv.orders[20].status is OrderStatus.PARTIAL
            assert inv.orders[20].filled == D(".25")
            assert inv.positions[0].asset == "xyz:AAPL"
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

def test_expired_status_and_margin():
    order = parse_order({"id": "a", "asset": "BTC", "side": "sell", "positionSide": "short",
                         "status": "expired", "quantity": "2", "filledQuantity": "0"})
    position = parse_position({"id": "p", "asset": "BTC", "positionSide": "short",
                               "quantity": "2", "leverage": "3.5", "marginMode": "cross"})
    assert order.status is OrderStatus.EXPIRED
    assert position.leverage == D("3.5")
    assert position.margin_mode == "cross"
