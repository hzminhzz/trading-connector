# Propr connector compatibility record

**Document source pinned:** [XBorgLabs/propr-docs at `fee880982ce3cf36f330fb07a3a9be57ec4dd904`](https://github.com/XBorgLabs/propr-docs/tree/fee880982ce3cf36f330fb07a3a9be57ec4dd904). These are *documented contracts*, not proof of authenticated Beta compatibility.

| Component | Inspected version/source | Implemented boundary |
| --- | --- | --- |
| Propr Python SDK | official `python/propr_sdk.py` `__version__=0.1.0` | Avoid SDK's first-active-account implicit selection and fresh intent IDs on each retry |
| Propr REST | official `docs/api.md` at SHA above | HTTPS, `X-API-Key`, `limit/offset` GET pagination; POST/PUT/PATCH/DELETE blocked |
| Propr authenticated WS | official `docs/websocket.md` at SHA above | WSS, `X-API-Key`, `connected.data.userId` handshake, server ping 20s |
| ml4t-live | **0.1.2** installed at `/home/quant/dev/quant/quant-research/.venv` | Actual `AsyncBrokerProtocol`, `DataFeedProtocol`, `LiveEngine` signatures inspected; integration tests |
| Independent reference bars | Binance spot WSS closed-kline `<symbol>@kline_1m` | Typed ML4T `MarketEvent` with timestamps, sequence and gap/staleness checks |

## Provider schema (as published)

* Account: `GET /accounts/{accountId}`, `balance`, `availableBalance`, `totalUnrealizedPnl`, `isolatedPositionMargin`, `crossPositionMargin`, `crossOrderMargin`, `isolatedOrderMargin`, `totalInitialMargin`, `totalMaintenanceMargin`. SDK defines equity as balance + unrealized PnL + isolated position margin. The connector does not call available balance spot cash.
* Orders: `orderId`, `intentId`, `accountId`, `asset`, `side`, `positionSide`, `status`, `quantity`, `cumulativeQuantity`, `averageFillPrice`, `createdAt`, `updatedAt`; status `partially_filled` is distinct from `filled`.
* Positions: `positionId`, `asset`, `positionSide`, `quantity`, `leverage`, `marginMode`, `entryPrice`, `markPrice`, `marginUsed`, `liquidationPrice`, `createdAt`.
* Trades: `tradeId`, `orderId`, `positionId`, `executedAt`, `quantity`, `price`, `fee`.
* Native symbol format: BTC not BTC/USDC; case-sensitive HIP-3 `xyz:AAPL`. Explicit registry with Decimal size/tick metadata; never infer lot metadata from names.
* Conditional orders: native `positionId` or grouped entry; batch groups require ULID `orderGroupId`. These are **not exposed** for real mutations.
* Order create accepts ULID `intentId`; 400 cancel is not proof of absence, and 201 is a documented cancel success. Real remote mutations remain off.
* Socket events carry `type`, `userId`, `data`, `timestamp`; no authenticated sequence replay guarantee is documented. Reconnect forces two matching complete REST inventories and unresolved-intent checks.

## Installed ML4T 0.1.2 protocol

`AsyncBrokerProtocol`: `positions`, `pending_orders`, `is_connected`, `execution_capabilities`; `connect`, `disconnect`, `is_connected_async`, `get_positions_async`, `get_pending_orders_async`, `get_position_async`, `get_account_value_async`, `get_cash_async`, `submit_order_async(asset,quantity,side,order_type,limit_price,stop_price,**kwargs)`, `cancel_order_async`, `replace_order_async`, `close_position_async`, `assert_paper_trading`, `assert_live_trading`. `LiveEngine(strategy, broker, feed, halt_on_unhealthy=True)` is tested with the same Strategy class and a shadow order.

`DataFeedProtocol`: `start`, `stop`, `__aiter__`, `__anext__`, with validated `MarketEvent`; independent Binance spot bars are *reference-only* and are not equivalent to Hyperliquid execution prices. No extrapolation to CFD/perp fill prices is permitted.

## Unresolved provider attestations

1. **Beta is not proven paper/simulation-only.** Neither a Beta URL nor a key prefix guarantees no monetary or evaluation consequences. See [BETA_SAFETY.md](BETA_SAFETY.md).
2. No Beta account ID or key is available in the active VPS workspace environment as of the verification attempt. The prior shared key was not transferred into this process. Only anonymous `GET /health` was tested successfully.
3. Authenticated schemas, order/position reconciliations, challenge reset timezone and rule field mapping, and WebSocket event sequencing need real read-only Beta evidence.
4. No verified point-in-time instrument min step/tick rules, no production market-data/quote parity, no remote stop/TP or order mutation qualification.
5. No 4-week paper record. **Production = NO-GO.**
