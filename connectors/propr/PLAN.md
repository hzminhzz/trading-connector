# Propr Connector — current scope and release plan

**Status:** read-only and synthetic-shadow implementation; remote order execution remains NO-GO.  
**Ownership:** `connectors/propr/`; optional integration with the installed `ml4t-live` package. Strategies remain in their owning project.

## Supported behavior

- Production HTTPS and authenticated WSS are configured separately. REST transport permits only GET, HEAD and OPTIONS.
- Read-only account lookup supports funded-account issuances and active challenge attempts. Operators either bind an exact account ID or explicitly request unique discovery; zero/multiple matches fail closed.
- Account and inventory data preserve provider IDs, timestamps, Decimal values, partial fills and long/short position sides.
- A durable local intent journal records unknown outcomes. Synthetic lifecycle and kill-switch drills use fake transports only.
- `ML4TShadowBroker` implements the inspected `ml4t-live==0.1.2` async broker contract without remote mutations. `ML4TStrategyAdapter` adapts legacy target-weight calls so the unchanged backtest strategy can run through `LiveEngine`.
- Binance spot closed-kline events are an independent reference feed, not Propr/Hyperliquid execution prices.

## Account identity and safety

The account source is read from `/book-account-issuances` or `/challenge-attempts?status=active`; the returned `accountId` is then checked against `GET /accounts/{accountId}` and inventory records. No account is inferred from local stale IDs or selected from an arbitrary first result.

Official Propr materials are now clearer about the platform model: the homepage says trader orders are simulated signals that Propr may copy to a live venue, and that the one-time challenge fee is the trader's maximum loss. However, the [Python SDK documentation](https://www.propr.xyz/developers/python-sdk) labels production trading as real-funds execution and exposes order mutations without stating whether this exact account's API `POST /orders` is signal-only. Do not treat the challenge as risk-free or infer the API route from marketing language. `assert_paper_trading()` and `assert_live_trading()` refuse; all remote mutations remain absent until Propr confirms the exact route/account semantics and the separately scoped risks are approved.

## Important boundaries

- Native symbols and Decimal lot/tick values require an explicit registry; do not infer precision from symbol names.
- The ML4T one-position-per-symbol view rejects ambiguous native hedge positions.
- REST/WS reconnect does not assume replay or an atomic snapshot boundary; new openings stay blocked until complete REST reconciliation succeeds.
- Account WebSocket events are not market data. Fresh independent prices and Propr execution prices are not proven equivalent.
- Synthetic acknowledgements do not establish fill, cancellation or flattening.

## Release gates

| Gate | Evidence required | Current decision |
| --- | --- | --- |
| Read-only account | Authenticated health, explicit/unique binding, complete financial fields and reconciled inventory | Can be run with the operator command; do not commit private results |
| Challenge/API classification | Written account-specific confirmation of `/orders` routing, challenge fee and evaluation consequences | Official public materials describe simulated signals and possible live copying, but conflict with SDK production wording; exact API semantics unverified |
| Shadow | Actual installed ML4T strategy and feed through `LiveEngine`; no mutation; fault/reconciliation checks | Smoke passed |
| Paper record | At least four weeks of documented operation under explicitly scoped account, fee, evaluation and risk limits | Not met |
| Live operation | Separate authorization, reviewed limits, reconciliation, kill switch and recovery drills | NO-GO |

If any account-safety evidence is ambiguous, keep the mutation gate closed and report the missing evidence; do not change configuration to bypass it.

## Source references

- [Propr developer documentation](https://www.propr.xyz/developers)
- [Pinned Propr API documentation](https://github.com/XBorgLabs/propr-docs/blob/fee880982ce3cf36f330fb07a3a9be57ec4dd904/docs/api.md)
- [Pinned Propr WebSocket documentation](https://github.com/XBorgLabs/propr-docs/blob/fee880982ce3cf36f330fb07a3a9be57ec4dd904/docs/websocket.md)
- [ML4T documentation](https://ml4trading.io/libraries/)
