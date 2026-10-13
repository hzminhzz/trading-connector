# Propr Connector — Implementation Plan

**Status:** planned, not implemented. **Last reviewed:** 2026-10-13.
**Ownership:** `connectors/propr/`; reusable provider library with an optional `ml4t-live` bridge. **Out of scope:** strategies, backtesting research, general framework replacement or hosting other providers' code.

## 1. Outcome and bounded MVP

Build a custom Propr connector with independently testable REST and WebSocket transports, explicit environment/account binding, robust order/position mapping, deterministic recovery, and fail-closed risk semantics. The initial release supports **read-only and shadow**. Add simulated-order qualification only after Beta safety is verified. Production trading remains a separate acceptance milestone.

**Initial allowed order scope:** linear Hyperliquid perpetuals, one-way position model per canonical symbol, MARKET and LIMIT with explicit side, positionSide and reduceOnly, cancel-by-order-ID, and close/reduce position by native position ID/side. Implement clear capability rejection for unsupported native types. Do not claim hedge-mode support in the `ml4t-live` bridge unless its single-position-per-symbol model is extended.

## 2. Evidence base and current compatibility

Propr [website Python SDK](https://www.propr.xyz/developers/python-sdk) exposes `ProprClient` v0.1.0, synchronous `requests`, account setup, orders, positions, trades, leverage, health and an async WebSocket example. Source: [GitHub SDK](https://github.com/XBorgLabs/propr-docs/blob/main/python/propr_sdk.py), [API reference](https://github.com/XBorgLabs/propr-docs/blob/main/docs/api.md), [WebSocket docs](https://github.com/XBorgLabs/propr-docs/blob/main/docs/websocket.md).

Previous direct local inspection of `/home/quant/dev/quant/quant-research/.venv/lib/python3.14/site-packages/ml4t/live/` found `ml4t-live==0.1.2` installed in the project virtualenv, including:
- `protocols.py`: `AsyncBrokerProtocol`, `DataFeedProtocol`;
- `orders.py`: `CanonicalOrderRequest`, execution capability validation;
- `engine.py`: `LiveEngine`, reconnect/watchdog/reconciliation settings;
- `safety.py`: `SafeBroker`, `LiveRiskConfig`;
- `brokers/alpaca.py`, `brokers/ib.py`: current examples;
- `wrappers.py`: sync-to-async bridge for strategy callbacks.

**Reinspect installed versions/source before implementing.** `ml4t-live` is an optional consumer, not a base dependency or the owner of transport abstractions.

### Protocol mapping

| Required ML4T broker capability | Propr API | Decision |
| --- | --- | --- |
| `connect`, `disconnect`, `is_connected` | REST health + WS | Implement transport readiness; health is not order readiness |
| cached `positions` and `get_positions_async` | paginated `GET /accounts/{id}/positions` | Map signed positions only in one-way mode; detect conflicting long+short |
| cached `pending_orders` and `get_pending_orders_async` | paginated `GET /accounts/{id}/orders` | Preserve partial/open statuses and reconcile |
| `get_position_async` | positions filtered by instrument/side | Fail if multiple native positions cannot be represented |
| `get_account_value_async` | account snapshot | Use verified equity definition incl. unrealized PnL; do not equate with available balance |
| `get_cash_async` | available balance / account balance | Publish chosen semantics and distinguish buying power from cash |
| `submit_order_async` | `POST /accounts/{id}/orders` | Stable `intentId`, Decimal conversion, no blind retries |
| `cancel_order_async` | `POST /accounts/{id}/orders/{orderId}/cancel` | Query final state on ambiguous 400/timeouts |
| `replace_order_async` | no native atomic replace documented | Cancel + reconcile + submit with explicit gap/race and hard fail on ambiguity |
| `close_position_async` | reduce-only order / `closePosition` | Target verified specific native side/quantity, not SDK's first matching position |
| `assert_paper_trading`, `assert_live_trading` | Beta/production environment & account metadata | Paper predicate FAILS until simulated semantics proven; require explicit production arming |
| `execution_capabilities` | server's order type/TIF/instrument rules | Advertise only verified types |
| `DataFeedProtocol` | account WebSocket not a market feed | Separate OHLCV/trade/quote source; detect stale/gapped feed |

`LiveEngine` requires `await engine.connect()` then `await engine.run()`. Configure `halt_on_unhealthy=True` and reject startup on reconciliation mismatch in safety wrapper; do not rely on default relaxed behavior.

## 3. Beta versus production gate

The Propr website dynamically switches to Beta with:
- `PROPR_API_KEY=pk_beta_...`
- `PROPR_API_URL=https://api.beta.propr.xyz/v1`
- `PROPR_WS_URL=wss://api.beta.propr.xyz/ws`
- Beta account settings `https://app.beta.propr.xyz/settings`

Its main developer page recommends switching environment to test bots. Yet retrieved UI/extractor wording did **not conclusively prove** simulated-only execution; some text still describes real markets/funds. Therefore:
1. Obtain authoritative environment/account documentation confirming where Beta orders execute, whether they can incur costs, and whether they can affect funded/evaluation accounts.
2. Validate read-only beta identity, account IDs, and health. Require explicit account selection; never `setup()`'s first-active-account selection for order-capable use.
3. Encode environment + credential prefix + account ID into a non-overridable execution identity. Block production endpoint with beta credentials and vice versa.
4. Reject `assert_paper_trading()` until verified and tested. Beta tests are not automatically paper tests.
5. Send **no order** until this gate is satisfied and specifically approved.

## 4. Execution semantics to resolve

### Instruments and precision
Use a registry with native `asset`, `base`, `quote`, product, tick/lot/min order; both `BTC` and HIP-3 symbols such as `xyz:AAPL` must round-trip. The website convenience SDK sometimes produces `asset=\"BTC/USDC\"` while the GitHub API docs prescribe bare assets (`BTC`). Confirm with read-only metadata or provider clarification; do not guess. `CanonicalOrderRequest` uppercases asset strings: introduce an explicit reversible symbol map. Store Decimal quantities/prices; float only at ML4T's unavoidable API boundary and validate quantization and requested notional before sending.

### Sides, fills, lifecycle
Propr order payload requires `side=buy|sell` **and** `positionSide=long|short`; `reduceOnly`/ `closePosition` matter independently. Opposite trade without reduceOnly can open the other side instead of closing. Map `pending/open/partially_filled/filled/cancelled/rejected/expired`, cumulative quantity and average fill; represent partial fills without treating as fully complete. `get_positions()` can return distinct long/short, while ML4T snapshot is `dict[symbol, Position]`; guard against accidental netting.

### Idempotency and cancellation
Propr supports a client `intentId` (ULID). Persist it **before** POST and reuse exactly on uncertain retry; the convenience `create_order()` generates a fresh intent each call, so do not use it directly as a retry loop. A cancel 400 is not proof the order is gone. Poll/reconcile order and trades, with bounded retries and frozen opening flow on unknown outcome. Paginate all pages—SDK `cancel_all_orders()` only fetches its default first page. Replace is non-atomic without a native replace endpoint.

### Conditional orders and leverage
The API reference describes `stop_market`, `stop_limit`, TP variants and group/position-ID requirements that the website SDK stop-loss example may not satisfy. Capability-gate stop and TP until verified with isolated safe tests. Fetch per-account margin, leverage constraints, minimums, liquidation data and prop-rule limits; do not assume `SafeBroker`'s spot-like notional calculations correctly model levered exposure.

### WebSocket and data feeds
Authenticated `wss://api.beta.propr.xyz/ws` publishes account/order/position/trade events. No verified sequence replay or atomic REST/WS snapshot boundary is documented. Connect, ingest, debounce/deduplicate, heartbeat, reconnect with bounded backoff, fetch complete paginated REST snapshots and reconcile before accepting new openings. Model out-of-order, duplicated or missing `order.filled`/`trade.created` events. Use independent fresh timestamped market data for strategy signals and safe price deviations; don't treat position `markPrice` as a full real-time feed.

## 5. Proposed package layout (not yet created)

```text
connectors/propr/
├── README.md
├── PLAN.md
├── config.py           # explicit endpoint, account, mode, credential validation
├── instruments.py      # reversible native symbol + Decimal precision
├── rest.py             # async REST and typed errors
├── ws.py               # WS client + gap/heartbeat/reconnect
├── models.py           # provider-native orders/positions/accounts/events
├── state.py            # durable intents, snapshots, reconciliation
├── broker.py           # provider-neutral gateway
├── ml4t_bridge.py      # optional ml4t-live 0.1.2+ protocol adapter, version pinned/tested
├── risk.py             # prop/leverage/execution identity checks
├── tests/              # deterministic fixtures, simulated responses, faults
└── examples/           # read-only and shadow only initially
```

Exact module boundaries can be refined to keep APIs deep/cohesive and avoid boilerplate, but provider code must stay isolated.

## 6. Work packages and acceptance gates

| Gate | Deliverables | Pass evidence |
| --- | --- | --- |
| P0 — contracts and safety discovery | Source-version lock, documented Beta behavior, execution identity, order/position field fixtures, instrument rules | No unresolved question about whether test orders can affect real funds; otherwise stop at read-only |
| P1 — core read-only | Async HTTP, pagination, models, account/position/order/trade retrieval, typed errors and symbol codec | Unit tests + read-only Beta snapshots with redaction; precise account binding |
| P2 — state/recovery | Durable intent journal, WebSocket, reconciliation, restart, unknown outcomes, cancel races | Fault injection: disconnect, dropped/duplicate event, partial fill, timeout and crash; fail-closed behavior |
| P3 — shadow and ML4T bridge | Strict no-mutation shadow mode, `AsyncBrokerProtocol` adapter, separate feed integration, capability matrix | Against the actual installed ML4T library; test strategy unchanged from backtest |
| P4 — safe simulated qualification | Explicitly verified paper/Beta identity, market/limit/cancel/reduce-only/conditional tests only as permitted | Independent account snapshots, exact reconciliation and reports; no unintended exposure |
| P5 — production readiness (separate approval) | Live arming, prop-specific limits, alerts, kill switch, incident playbook, rollback and operational tests | At least 4 weeks paper testing, end-to-end audited results and explicit human approval |

### Required automated tests
- Canonical ↔ native symbol round-trip, HIP-3 case preservation, step size, price tick, invalid minimums.
- BUY/SELL × LONG/SHORT × reduceOnly and close semantics, with conflicting hedge positions rejected at bridge.
- Open/partial/filled/rejected/expired order state mapping, trade aggregation and snapshot consistency.
- Same persisted intent ID on timeout retry; never submit duplicate order from an unknown result.
- Cancel-while-fill, cancel-400, cancel/replace gap, batch partial acceptance, pagination >20.
- Stale account/market feed, WS disconnect/replay/gaps, duplicate messages, restart mid-order.
- No mutation from constructor, import, CI, read-only or shadow mode.
- Live/Paper identity assertions and proper refusal when Beta safety is unproven.
- Account equity/day-reset/drawdown calculations for the actual Propr challenge type, margins/leverage caps, and forced-reduction behavior.

## 7. Operational rules

- Start with `EXECUTION_MODE=read_only` and explicit `PROPR_ENV=beta`; use separate `PROPR_BETA_API_KEY` and `PROPR_LIVE_API_KEY` variables, never an ambiguous shared fallback.
- Logging must redact keys, user identifiers, sensitive account data and order tokens; preserve opaque order/intent IDs in restricted audit storage.
- The environment validator must reject accidental endpoint changes, credential mismatches, non-allowlisted accounts and unapproved instruments.
- `SafeBroker` does not replace provider-native checks for liquidation, max leverage, prop equity floors, drawdown windows, or independent close-side semantics.
- During uncertain state, halt new exposure and reconcile. Kill-switch can request cancel/reduce/flatten but must report whether requests **actually succeeded**, including when connectivity fails.
- Prove recovery behavior and monitoring under injected outages before unattended operation.

## 8. Deliverable definition

Completion means an independently runnable, documented Propr gateway with reproducible CI tests, optional ML4T integration, provider-specific read-only and (when proven safe) test execution paths, and production activation guarded by explicit policy. It does **not** mean all connectors, strategies, trading venues or data feeds share a single implementation.

### Open blockers

1. Official assurance of Beta simulated execution and its effects on funded/evaluation balances.
2. Verified native `asset` format, HIP-3 mapping and quantity/tick metadata.
3. Conditional-order `positionId` / `orderGroupId` requirements.
4. Account-equity, balance, leverage, position-hedging and prop-rule interpretations.
5. WebSocket ordering/replay guarantees and behavior after restart.
6. Market-data source, stale-feed thresholds and exchange/venue timing behavior.

## Source references

- [Propr Python SDK, website](https://www.propr.xyz/developers/python-sdk)
- [Propr API documentation](https://github.com/XBorgLabs/propr-docs/blob/main/docs/api.md)
- [Propr SDK source](https://github.com/XBorgLabs/propr-docs/blob/main/python/propr_sdk.py)
- [Propr WebSocket documentation](https://github.com/XBorgLabs/propr-docs/blob/main/docs/websocket.md)
- [Propr Python examples](https://github.com/XBorgLabs/propr-docs/tree/main/python/examples)
- [ML4T documentation](https://ml4trading.io/libraries/)
