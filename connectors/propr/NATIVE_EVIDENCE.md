# Propr native evidence v1 — Guardian integration contract

**Schema:** `propr_native_evidence_v1` in `connectors.propr.native_evidence`.
**Entry point:** `await inspect_native_evidence(rest, account_id=..., endpoint=..., known_at_ms=..., stream_connected=False)`.
**Status:** Read-only diagnostic interface. **SHADOW / NO_TRADE**. Never opening-ready with currently documented Propr REST/WS capabilities.

## Ownership

- `trading-connector`: Propr native REST/WS/SDK, native account/attempt identity, provider-specific financial semantics, Decimal values, orders, trades, positions, field provenance, observation certainty.
- `prop-production`: Guardian, Planner, risk floors, immutable SQLite financial journal and committed order outbox, reservations, HALT/restart recovery, independent Outside Judge. Sole authority for production intents; connector cannot mint or authorize Guardian instructions.
- `copytrade-pm`: Research, wallet and portfolio SQLite authority, signed portfolio handoff. No live broker credentials or execution authority.
- `ml4t-live==0.1.2`: Optional independent consumer of adapter classes. Never needed to import this native interface; no ML4T engine in Guardian.

## Observations and invariants

`NativeEvidence` is frozen and returns explicit account/attempt ID, account source, endpoint, provider update timestamp when present, caller's locally known millisecond timestamp, native `Inventory` and independent certainty fields.

`financial` contains `FinancialField(value, provider_field, provenance)` for balance, equity, buying power, cash, unrealized PnL, isolated position margin, initial margin and maintenance margin. Amounts are exact `Decimal` or `None`; provenance is `PROVIDER`, `DERIVED_SDK_FORMULA` or `UNKNOWN`. Provider-derived equity follows SDK: `balance + totalUnrealizedPnl + isolatedPositionMargin` only when all three components exist. It is **not** provider-proven cash. `availableBalance` is buying power, not cash/equity. Cash is **UNKNOWN**, and the application does **not** substitute `equity - unrealized`. Missing/stale/invalid provider time or financial values produces explicit blockers. The cash field requires provider-accounting verification before financial completeness can be established.

`Inventory` preserves native order/intent IDs, position ID and side, native asset namespace, quantities/prices, partial fills, fees and timestamps. `Trade.funding=None` explicitly means a per-fill funding record was not established by the documented reader; it does **not** mean zero funding. A completed paginated REST read is `inventory_read_complete=True`, but that is **not** a history-complete or verified-empty account. Cross-account/duplicate IDs invalidate inventory; missing row-level account identity or unsupported one-way hedge mapping is a blocker. Changed pages with no detectable duplicate still cannot establish a coherent provider cutoff.

**Propr does not establish a coherent replay sequence, offset-pagination snapshot boundary or authoritative history cutoff.** The native evidence returns `history_complete=False`, `cutoff=None`, `opening_ready=False`, `RECONCILIATION_UNCERTAIN`. Matching repeated REST reads do not override that result. AccountReconciler stores `rest_consistent=True` for diagnostics only, and continues to deny openings. WebSocket connection/heartbeat is not historical replay proof; disconnect and restart revoke continuity.

Provider-read failures return a non-ready evidence value with no partial inventory promoted as confirmed flat. No read-only operation sends a provider mutation. Ultimate SDK HTTP transport, including direct raw `Session.send` and `ProprClient`, rejects all remote POST, PUT, PATCH and DELETE. Synthetic transports require the reserved fixture origin. No automatic retry of unknown sends; provider deduplication/query-by-ID is unverified. The optional ML4T broker's `assert_live_trading()` deliberately rejects while remote execution is disabled.

## Consumer contract

`prop-production.gateway.inspect_propr_readonly()` delegates to the native evidence entry point, enforces exact evidence version/account/endpoint, preserves provider balance/buying power, provenance, account and attempt identity, timestamps, native order and position diagnostics, and uncertainty reasons. It leaves `BrokerSnapshot.cash=None` and `history_complete=False` while broker cash and cutoff remain unverified. Its `ReadOnlyGateway.submit/cancel` are denied; the Guardian reserve/dispatch path remains synthetic-only and uses the **Guardian-created, committed SQLite intent ID**. No second connector journal may serve Guardian instructions.

### Additional external evidence required

1. Authenticated provider field/account classification, including spending/cash-equivalent semantics and opening balance.
2. Explicit provider-supported coherent event replay/snapshot cutoff, partial execution fee/funding semantics, and venue idempotency/query-by-client-ID guarantees.
3. Independently proven account-specific challenge/API rulebook and trading permissions.
4. Complete forward paper, independent execution economics and scoped G0–G4 authorization.

**Tests and successful builds do not establish these external facts.** No Propr mutation or deployment is approved by this contract.
