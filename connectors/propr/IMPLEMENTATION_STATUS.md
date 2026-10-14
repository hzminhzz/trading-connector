# Propr implementation status — 2026-10-14

**Source state:** `feat/propr-connector`, draft PR #13.

| Area | Working implementation | Missing qualification |
| --- | --- | --- |
| Identity and transport | Production-only configuration, live-key prefix check, explicit or unique read-only account binding | Provider account-class and financial-consequence guarantee |
| Native inventory | Funded/active-challenge discovery, complete pages, Decimal values and one-way position guard | Sustained authenticated collection and pagination-shift evidence |
| Order state | SQLite WAL intent journal, unknown-on-crash and fake-only lifecycle | Remote order transport is intentionally absent |
| WS state | Authenticated WSS code path, bounded recovery and stable double-REST reconciliation | Provider event sequencing/replay and sustained read-only session evidence |
| Shadow and ML4T | Same ML4T Strategy class through installed `LiveEngine`; target-weight shim produces audited shadow intents | Multi-day feed continuity and production-price parity |
| Risk | Explicit verified-input policy, synthetic kill switch and manual halt/reset | Account-specific rules and live recovery path |
| Release | Redacted read-only account/inventory inspection and synthetic-shadow runbook | Authoritative simulated-account proof, four-week paper record and separate live approval |

No account IDs, balances, positions, credentials or unredacted snapshots are committed. The connector does not enable remote order placement.
