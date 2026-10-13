# Propr implementation status — 2026-10-13

**Source state:** active milestone branch `feat/propr-connector`, draft PR #13.

| Area | Working implementation | Missing qualification |
| --- | --- | --- |
| Identity and transport | Explicit environment, account binding, read-only HTTPS | Authenticated Beta account inspection and response conformity |
| Native inventory | Documented `orderId/positionId/tradeId` fields, full pages, Decimal and one-way guard | Live account, pagination shift evidence |
| Order state | SQLite WAL with ULID intent, unknown-on-crash; fake-only lifecycle | Real transport deliberately unavailable, more partial-fill/replay evidence |
| WS state | Read-only WSS, authenticated handshake, bounded recovery; reconciler requires stable double REST snapshot | Read-only Beta WS smoke; provider sequencing/replay semantics not certified |
| Shadow | Pure shadow decisions, journal/audit, fake risk; ML4T 0.1.2 unchanged Strategy integration | Multi-day real reference feed/production parity |
| Risk | Explicit verified-input policy and fake kill switch | Account-specific verified challenge rules, daily reset behavior, live flatten path |
| Release | Read-only runbook and NO-GO report | Beta isolation certification, four-week paper, explicit live approval |

The previously shared Propr Beta credential is not set in the current VPS process environment and no project `.env` exists. An unauthenticated Beta health GET responded OK; that does not qualify trading. Never copy credentials into tracked files.
