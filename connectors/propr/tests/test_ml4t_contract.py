"""Optional ML4T protocol contract pin against installed 0.1.2."""
import inspect
import pytest

@pytest.mark.parametrize("method", [
    "connect", "disconnect", "is_connected_async", "get_positions_async",
    "get_pending_orders_async", "get_position_async", "get_account_value_async",
    "get_cash_async", "submit_order_async", "cancel_order_async",
    "replace_order_async", "close_position_async", "assert_paper_trading",
    "assert_live_trading",
])
def test_ml4t_broker_protocol_has_expected_method(method):
    protocols = pytest.importorskip("ml4t.live.protocols")
    assert hasattr(protocols.AsyncBrokerProtocol, method)

def test_ml4t_datafeed_contract():
    protocols = pytest.importorskip("ml4t.live.protocols")
    for name in ("start", "stop", "__aiter__", "__anext__"):
        assert hasattr(protocols.DataFeedProtocol, name)
    assert "asset" in inspect.signature(protocols.AsyncBrokerProtocol.submit_order_async).parameters
