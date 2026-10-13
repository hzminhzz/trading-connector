# Propr compatibility evidence (work in progress)

- Inspected local `ml4t-live` distribution in `/home/quant/dev/quant/quant-research/.venv`: version **0.1.2** (verified with `importlib.metadata`).
- Propr website SDK plan references **v0.1.0**, but current REST and WS response shapes remain **unverified**.
- Propr Beta environment is **not proven simulation-only**. `assert_paper_trading` fails closed and all remote mutations are disabled.
- REST `limit`/`offset` pagination and `X-API-Key` header are documented in the official API reference. Exact challenge-attempt account mapping and response shape still require authenticated verification.
- The implemented HTTP transport is read-only. `TransportPolicy(allow_mutations=True)` is an internal testing primitive only, not production arming; do not instantiate it with a real network client.
- No provider account snapshot or actual execution has been verified.

## Verified ML4T interface

Inspected installed `ml4t-live==0.1.2` `ml4t.live.protocols` directly. `AsyncBrokerProtocol` requires cached `positions`, `pending_orders`, `is_connected`, `execution_capabilities`; async lifecycle `connect`, `disconnect`, `is_connected_async`; account/position/order queries; `submit_order_async(asset, quantity, side=None, order_type=MARKET, limit_price=None, stop_price=None, **kwargs)`, `cancel_order_async`, `replace_order_async`, `close_position_async`; and `assert_paper_trading`/`assert_live_trading`. `DataFeedProtocol` requires `start`, `stop`, `__aiter__`, `__anext__` yielding `FeedItem` (prefer validated `MarketEvent`). Fifteen optional contract tests passed against the installed version.

## Outstanding issue #1 checks

Verify Propr official API and WS schema fields and the documented SDK version against primary sources, then complete compatibility tests. Until then #1 remains open.
