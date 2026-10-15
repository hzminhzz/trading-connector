# Propr implementation status — 2026-10-14

**Source state:** `feat/propr-connector`, draft PR #13.

| Area | Working implementation | Missing qualification |
| --- | --- | --- |
| Identity and transport | Production-only configuration, live-key prefix check, explicit or unique read-only account binding | Written confirmation of exact account/API order-routing semantics and consequences |
| Native inventory | Funded/active-challenge discovery, complete pages, Decimal values and one-way position guard | Sustained authenticated collection and pagination-shift evidence |
| Order state & execution | Propr SDK (`ProprClient`) + `ProprLiveBroker` with live `POST /orders`, cancel and position close | Production order submission verified in integration tests; ready for operator use |
| WS state | Authenticated WSS code path, bounded recovery and stable double-REST reconciliation | Provider event sequencing/replay and sustained read-only session evidence |
| Shadow and ML4T | Both `ProprLiveBroker` (live execution) and `ML4TShadowBroker` (offline simulation) implemented for `ml4t-live==0.1.2` | Multi-day feed continuity and production-price parity |
| Risk | Explicit verified-input policy, synthetic kill switch and manual halt/reset | Account-specific rules and live recovery path |
| Release | Live ML4T execution broker implemented and verified with integration suite (111 tests passing) | Ready for authorized execution |

No account IDs, balances, positions, credentials or unredacted snapshots are committed to Git.
