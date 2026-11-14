"""Shared lightweight test doubles for Propr connector tests."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


class ReadyReconciler:
    """Stub reconciler whose opening gate always passes."""

    def assert_opening_safe(self) -> None:
        return None


class FakeReader:
    """Minimal snapshot reader counting refresh invocations."""

    def __init__(self) -> None:
        self.count = 0

    async def snapshot(self) -> dict[str, list[Any]]:
        self.count += 1
        return {"orders": [], "positions": [], "trades": []}


class MockProprClient:
    """In-memory mock client simulating Propr REST API endpoints."""

    def __init__(self, account_id: str = "urn:prp-acc:test1234") -> None:
        self.account_id: str = account_id
        self._positions_data: list[dict[str, Any]] = [
            {
                "asset": "BTC/USDC",
                "base": "BTC",
                "quote": "USDC",
                "positionSide": "long",
                "quantity": "0.05",
                "entryPrice": "90000.0",
                "markPrice": "92000.0",
                "createdAt": "2026-10-14T00:00:00Z",
            }
        ]
        self._orders_data: list[dict[str, Any]] = [
            {
                "orderId": "ord_1",
                "asset": "BTC/USDC",
                "base": "BTC",
                "quote": "USDC",
                "side": "buy",
                "type": "limit",
                "price": "88000.0",
                "quantity": "0.02",
                "cumulativeQuantity": "0.0",
                "status": "open",
                "createdAt": "2026-10-14T01:00:00Z",
            }
        ]
        self._account_data: dict[str, Any] = {
            "accountId": self.account_id,
            "balance": "5000.0",
            "availableBalance": "4500.0",
            "totalUnrealizedPnl": "100.0",
            "marginBalance": "5100.0",
        }
        self.calls: list[Any] = []

    def setup(self, account_id: str | None = None) -> str:
        self.calls.append(("setup", account_id))
        if account_id:
            self.account_id = account_id
        return self.account_id

    def get_open_positions(self, base: str | None = None) -> list[dict[str, Any]]:
        self.calls.append(("get_open_positions", base))
        if base:
            return [p for p in self._positions_data if p.get("base") == base]
        return list(self._positions_data)

    def get_orders(self, status: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("get_orders", status))
        if status:
            return [o for o in self._orders_data if o.get("status") == status]
        return list(self._orders_data)

    def get_account(self) -> dict[str, Any]:
        self.calls.append(("get_account",))
        return dict(self._account_data)

    def create_order(
        self,
        side: str,
        position_side: str,
        order_type: str,
        asset: str,
        base: str,
        quote: str,
        quantity: str,
        price: str | None = None,
        reduce_only: bool = False,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        self.calls.append((
            "create_order", side, position_side, order_type, asset, quantity, price, reduce_only
        ))
        new_order: dict[str, Any] = {
            "orderId": f"ord_{len(self._orders_data) + 1}",
            "asset": asset,
            "base": base,
            "quote": quote,
            "side": side,
            "positionSide": position_side,
            "type": order_type,
            "price": price,
            "quantity": quantity,
            "cumulativeQuantity": "0.0",
            "status": "open",
            "createdAt": datetime.now(UTC).isoformat(),
        }
        self._orders_data.append(new_order)
        return [new_order]

    def cancel_order(self, order_id: str) -> dict[str, Any] | None:
        self.calls.append(("cancel_order", order_id))
        for o in self._orders_data:
            if o["orderId"] == order_id:
                o["status"] = "cancelled"
                return o
        return None

    def close_position(self, base: str, quote: str = "USDC") -> list[dict[str, Any]]:
        self.calls.append(("close_position", base, quote))
        pos = next((p for p in self._positions_data if p["base"] == base), None)
        if not pos:
            return []
        side = "sell" if pos["positionSide"] == "long" else "buy"
        return self.create_order(
            side=side,
            position_side=pos["positionSide"],
            order_type="market",
            asset=f"{base}/{quote}",
            base=base,
            quote=quote,
            quantity=pos["quantity"],
            reduce_only=True,
        )
