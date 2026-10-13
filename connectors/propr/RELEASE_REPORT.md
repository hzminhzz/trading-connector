# Propr connector qualification — NO-GO

Date: 2026-10-13. Repository: `hzminhzz/trading-connector`, milestone Propr Connector, draft PR #13.

**Decision: NO-GO for any remote order mutation or live deployment.** No remote orders, cancels, leverage changes or flatten requests have been made.

| Gate | Evidence | Disposition |
| --- | --- | --- |
| P0 API contract | Official Propr SDK/docs pinned; ml4t-live 0.1.2 inspected; credential-free tests | Source-level evidence, still needs authenticated schema confirmation |
| Beta risk-free order environment | No definitive official provider assurance; beta policy refuses paper assertion | BLOCKED |
| Beta account | Previously shared key not found in active process environment or local workspace; no explicit selected account ID | BLOCKED |
| Beta health | Anonymous read-only `GET https://api.beta.propr.xyz/v1/health` returned HTTP 200 and `status=OK` from VPS | VERIFIED health only, **not** account or execution qualification |
| Account/inventory | Mocked authenticated `GET` response fixtures; no account secrets | Fixture-qualified only |
| Intent / cancel / replace | Durable fake-only journal; synthetic timeout/race/restart tests | Fixture-qualified only |
| WS / recovery | Authenticated-code path, bounded reconnect, dedup, two read-only snapshots, fail-closed unknown state | Fixture-qualified only; no Beta authenticated session |
| Shadow/ML4T | Same `Strategy` class via native `LiveEngine`; reference-only independent bar feed; synthetic order decisions | Fixture-qualified; no external live session |
| Prop rules / kill switch | Verified-input fail-closed pretrade risk, latched manual reset and fake cancellation/reduction drills | Synthetic only; actual challenge rule interpretation missing |
| Simulated order qualification | All remote mutations blocked by policy | BLOCKED by missing simulation proof and separate scoped approval |
| Four-week paper | No verified duration/performance/fill evidence | NOT MET |

## Unsupported / not authorized

Real market/limit orders, account cancellation or close, non-atomic remote replace, remote flatten, leverage updates, stop-loss and TP conditional submissions, verified native hedge execution, production execution, and live arming.

The signed-off deliverable is a **read-only/synthetic-shadow development candidate**, not a production trading bot. Re-run the release gates after credential/account binding and authoritative simulation proof; do not bypass this report by setting a config flag.
