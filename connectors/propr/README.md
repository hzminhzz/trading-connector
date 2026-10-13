# Propr connector

**Status: planned; no implementation, no live orders.**

This is one provider module in the wider [Trading Connectors](../../README.md) library, not the repository's overarching architecture.

## Purpose

Implement a safe, reproducible gateway to the Propr trading API, including REST commands, account/order/position snapshots and WebSocket events. Offer an **optional** bridge to `ml4t-live`'s installed `AsyncBrokerProtocol` and keep a distinct `DataFeedProtocol` adapter for live market data.

See [implementation plan](PLAN.md) for scope, API evidence, unresolved details, acceptance gates and delivery sequence.

## Documented endpoints (not execution-safety certification)

| Environment | REST | WebSocket |
| --- | --- | --- |
| Production | `https://api.propr.xyz/v1` | `wss://api.propr.xyz/ws` |
| Beta | `https://api.beta.propr.xyz/v1` | `wss://api.beta.propr.xyz/ws` |

Beta examples show a `pk_beta_` key and the developer page recommends switching environments to test bots. **This does not establish that Beta is guaranteed paper-only or free of monetary consequences.** Confirm account semantics before placing even a test order.

## Official sources

- [Python SDK developer documentation](https://www.propr.xyz/developers/python-sdk)
- [Main API developer docs](https://www.propr.xyz/developers)
- [API reference](https://github.com/XBorgLabs/propr-docs/blob/main/docs/api.md)
- [WebSocket reference](https://github.com/XBorgLabs/propr-docs/blob/main/docs/websocket.md)
- [Python SDK source](https://github.com/XBorgLabs/propr-docs/blob/main/python/propr_sdk.py)
- [Python examples](https://github.com/XBorgLabs/propr-docs/tree/main/python/examples)
