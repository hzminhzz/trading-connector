# Shared Connector Architecture

**Status: design target; not implemented.**

## Goals

A cohesive, extensible library for custom trading broker/exchange integrations. A connector is a provider-specific execution and account gateway, while execution-policy, strategy, and research code remain outside the provider module.

```text
Strategy / automation / ML4T live
             |
     optional framework bridge
             |
    shared execution contracts
             |
    provider connector module
      /           |           \
  REST/API    WS account     market-data
  commands      events        adapter
      \           |           /
       reconcile + state store
             |
    safety, telemetry, incident controls
```

## Provider boundaries

Each `connectors/<provider>/` owns a minimal configuration schema, credential discovery, symbol metadata and normalization, client transport, order/position/account mapping, event-stream reconciliation, error classification, contract tests and operator runbook. Do not import another provider's implementation.

An integration for `ml4t-live` should implement the **installed version** of its broker protocol as an optional dependency. Keep strategy objects, market datasets and brokerage credentials out of the shared package.

## Canonical concepts

- **Environment identity:** `provider`, endpoint, credential type, explicit account ID, and independently verified simulation/live classification.
- **Instrument:** lossless native symbol, canonical symbol, quote, product kind, quantity increment, price tick and precision. Never uppercase or strip HIP-3 namespaces without reversible mapping.
- **Order intent:** stable persisted client intent ID, account, instrument, buy/sell, position side (where supported), reduce-only, type, quantity Decimal, prices Decimal, TIF and capability constraints.
- **Order state:** distinguish accepted, pending/open, partial, filled, cancelled, rejected and expired. Keep raw venue state, cumulative fill and average price; no assumption of atomic fills.
- **Position:** per-provider native position ID, instrument, long/short side, quantity, margin mode/leverage, mark, unrealized PnL and liquidation information; support hedge-mode at native layer even if downstream interfaces cannot.
- **Account:** equity, buying power, collateral, cash-like balance and margin are different fields. Preserve provider-specific prop-rule thresholds and reset rules.
- **Events:** REST snapshots and WS deltas merged with deduplication and gap detection; after reconnect, reconcile against paginated REST, not guessed replay guarantees.
- **Market data:** separate feed contract with timestamp, source, continuity and freshness guarantees; account WebSocket updates are not inherently price data.

## Failure semantics

Every command returns a known outcome or enters an explicit unknown-outcome state. Network timeouts are **not proof an order failed**: retry only with the same persistently recorded idempotency key where provider behavior is verified. Cancel/replace must account for fills between actions and cannot promise atomicity without an explicit venue guarantee. Duplicate or missing events trigger reconciliation and halt opening orders until certainty is restored.

## Safety policy

Read-only and shadow behavior must be enforced below strategy level; no network mutation should be possible in those modes. Production arming requires account binding, approved allowlist, max order/notional/exposure/leverage, broker-specific margin and prop-rule limits, stale-feed checks, reconciliation, durable journals, alerts, manual stop and restart recovery. Risk-reducing actions may be allowed under a latched halt, but cannot assume connectivity or guaranteed execution.

## Tests and documentation

Each provider should have `README.md`, `PLAN.md`, `tests/`, `examples/read_only.py` and `examples/shadow.py` when implemented. Destructive smoke tests must be in separately gated locations. Contract tests should target both mapping correctness and failure/recovery behavior. Never require real credentials to run default CI.
