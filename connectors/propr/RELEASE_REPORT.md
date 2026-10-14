# Propr connector qualification — NO-GO for execution

Date: 2026-10-14. Repository: `hzminhzz/trading-connector`, milestone Propr Connector, PR #13.

**Decision: remote order mutation and live deployment remain NO-GO.** The connector is limited to authenticated read-only inspection and synthetic shadow decisions. No order, cancel, leverage change or flatten request was sent.

## Verified evidence

- Installed-ML4T connector suite: **107 passed**.
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
| Account execution class | Public wording does not establish risk-free routing, fees or evaluation/funded-balance effects for this challenge account | UNVERIFIED; mutation gate remains closed |
| Inventory | Authenticated account-bound read-only snapshot; zero orders, positions and trades | VERIFIED at check time |
| Intent lifecycle / WebSocket | Deterministic synthetic tests; remote order transport absent | Fixture-qualified only |
| ML4T shadow integration | Actual project strategy plus target-weight conversion produced one audited shadow decision | Smoke passed; no production price parity claim |
| Account rules / kill switch | Fail-closed verified-input checks and fake-only kill drills | Synthetic only; live recovery unqualified |
| Four-week paper record | No verified duration, fill or performance evidence | NOT MET |
| Live authorization | No separate release authorization or reviewed operational recovery | NOT AUTHORIZED |

## Unsupported / not authorized

Remote market/limit orders, cancellation, replace, close/flatten, leverage changes, stop-loss/TP submission, native hedge execution and live arming.

The deliverable is a **read-only/synthetic-shadow connector candidate**, not a trading bot. The unresolved account-class evidence and four-week paper requirement remain release blockers for execution; no configuration flag can bypass them.
