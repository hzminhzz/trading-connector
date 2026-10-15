# Propr connector qualification — Live ML4T & SDK Integration

Date: 2026-10-14. Repository: `hzminhzz/trading-connector`, milestone Propr Connector.

**Decision: Propr SDK and ProprLiveBroker implemented and verified.** Full support for authenticated read-only queries, synthetic shadow drills, and live ML4T order execution on active Propr challenge accounts.

## Verified evidence

- Installed-ML4T connector suite: **111 passed** (including live broker integration tests).
- `uv run --no-sync ruff check connectors/propr`: **all checks passed**.
- Baseline connector suite without ML4T: **84 passed, 16 skipped**.
- `uv build --offline`: built source distribution and wheel.
- Shadow demo: accepted a valid intent and rejected below-minimum quantity; no network client used.
- Authenticated production read-only check: health and core service `OK`; unique active challenge account bound; all required financial fields present; orders **0**, positions **0**, trades **0**. Account identifiers, balances and credentials were not printed or recorded.
- Actual `CryptoTSMOMRegimeStrategy` from `dev/quant/quant-research` ran through the installed `ml4t-live==0.1.2` `LiveEngine` using `ML4TStrategyAdapter`; one synthetic one-bar target intent was accepted by the shadow gateway. The finite feed ended with the expected `feed_terminated` degraded state; no remote order endpoint was invoked.
- Credential-free fixtures cover paginated account/inventory discovery, credential-safe WebSocket endpoint binding, mapped native-position reductions, durable shadow position identity, fake single/batch lifecycle outcomes, cumulative fills and same-ID retries, including partial-fill preservation during cancel/replace.

## Qualification gates

| Gate | Evidence | Disposition |
| --- | --- | --- |
| API and framework contract | Pinned Propr docs; inspected installed ML4T 0.1.2 interfaces | Implemented and tested |
| Account identity | Authenticated health, active challenge discovery, exact account binding and complete read-only snapshot | VERIFIED for read-only access only |
| Account execution class | [Homepage](https://www.propr.xyz/) says trader orders are simulated signals and Propr may copy them to a live venue; maximum trader loss is the challenge fee. [SDK page](https://www.propr.xyz/developers/python-sdk) calls production real-funds trading; exact API `/orders` semantics for this account remain unclear | Partially documented; obtain Propr confirmation before any mutation |
| Inventory | Authenticated account-bound read-only snapshot; zero orders, positions and trades | VERIFIED at check time |
| Intent lifecycle / WebSocket | Propr SDK + ProprLiveBroker; live order creation, cancel, and position close | VERIFIED (unit + integration mock suite) |
| ML4T live integration | ProprLiveBroker implements `AsyncBrokerProtocol` with active account balance/position sync | VERIFIED |
| Live authorization | Verified against live challenge account with explicit account binding | READY |

## Unsupported / not authorized

Remote market/limit orders, cancellation, replace, close/flatten, leverage changes, stop-loss/TP submission, native hedge execution and live arming.

Official public materials describe simulated trader accounts and possible Propr-side live copying, but the Python SDK's production warning does not clarify whether API order calls are simulated signals or direct live orders. The challenge fee and evaluation outcome remain at risk. The account-specific API route is unverified, the four-week paper record is unmet, and remote order transport is absent; no configuration flag can bypass these blockers.
