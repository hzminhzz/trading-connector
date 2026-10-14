# Propr connector compatibility record

**Document source pinned:** [XBorgLabs/propr-docs at `fee880982ce3cf36f330fb07a3a9be57ec4dd904`](https://github.com/XBorgLabs/propr-docs/tree/fee880982ce3cf36f330fb07a3a9be57ec4dd904). These are documented contracts, not proof of paper execution.

| Component | Inspected version/source | Implemented boundary |
| --- | --- | --- |
| Propr REST | Official `docs/api.md` at the pinned revision | Production HTTPS, `X-API-Key`, complete GET pagination; mutations blocked |
| Account discovery | `/book-account-issuances`, `/challenge-attempts?status=active` | Explicit account ID or exactly one unique API result; ambiguous discovery fails closed |
| Propr authenticated WS | Official `docs/websocket.md` at the pinned revision | WSS, `X-API-Key`, `connected.data.userId` handshake, bounded recovery |
| ml4t-live | **0.1.2** installed in `/home/quant/dev/quant/quant-research/.venv` | Actual broker/feed/LiveEngine signatures inspected; same Strategy class adapted and exercised in shadow |
| Independent reference bars | Binance spot WSS closed-kline `<symbol>@kline_1m` | Typed ML4T `MarketEvent` with timestamp, sequence and gap/staleness checks |

## Provider schema (as published)

- Account: `GET /accounts/{accountId}`; financial values are decimal strings. The connector keeps equity, balance, available balance, margin and unrealized PnL distinct.
- Account sources: funded accounts are listed by `/book-account-issuances`; challenge attempts are listed by `/challenge-attempts`. Challenge accounts are not assumed to be paper accounts.
- Orders, positions and trades use provider-native IDs and preserve partial-fill, side, timestamp and Decimal evidence.
- Native symbols are case-sensitive, including HIP-3 symbols such as `xyz:AAPL`; tick, lot and minimum metadata must come from an explicit registry.
- Conditional order creation, cancel/replace, leverage changes and position closing are not available through the production REST transport.
- WebSocket events do not document sequence replay or an atomic REST/WS snapshot boundary. Reconnect requires complete REST reconciliation; unresolved outcomes remain halted.

## Installed ML4T 0.1.2 protocol

`AsyncBrokerProtocol` and `DataFeedProtocol` signatures were inspected in the installed package. `LiveEngine(strategy, broker, feed, halt_on_unhealthy=True)` runs the unchanged strategy through `ML4TStrategyAdapter`; legacy target-weight calls become bounded shadow intents. `ML4TShadowBroker` exposes no remote mutation methods.

`DataFeedProtocol` receives validated `MarketEvent` values. Binance spot bars are reference-only and are not equivalent to Propr/Hyperliquid execution prices.

## Unresolved release qualification

1. Propr's homepage FAQ now says trader orders run on simulated accounts and are submitted as signals; Propr may copy signals to a live venue. It says the one-time challenge fee is the trader's maximum loss. [Homepage FAQ](https://www.propr.xyz/)
2. The [Python SDK page](https://www.propr.xyz/developers/python-sdk) labels production as actual trading with real funds and exposes order/margin mutation methods. It does not clarify whether this account's API `POST /accounts/{id}/orders` is only a simulated signal or a live order. Provider confirmation for that exact API path/account is still required.
3. Challenge fees and account evaluation/failure are real consequences even when trader-facing orders are simulated. Do not call this account risk-free. `assert_paper_trading()` and all remote mutations remain blocked pending API-path clarification and scoped approval.
4. WebSocket sequencing, restart replay, verified tick/lot metadata, provider-specific challenge rules, and quote-to-execution parity remain unqualified. No four-week paper record or live-trading authorization exists. **Production execution remains NO-GO.**
