# Provider/account draft disposition — 2026-11-07

This is a **scope decision**, not acceptance of unpublished local work. Inspect current Git and provider schemas before implementing or merging. The preserved working copy at `/home/quant/dev/trading-connector` is intentionally **not reset, deleted, staged, copied, or merged** by this decision; its state may change in later sessions.

## Canonical active contract

The accepted `prop-production` compatibility manifest pins `trading-connector` commit `2db29b50af301741821781dedf64e4d8194fc125`. Only its tested Propr native read-only evidence and independently authenticated Bybit Demo read-only paths are in the current protected integration. Release authority remains with the application; connectors provide transport/evidence, not broker order permission.

## Preserved unfinished changes — evidence-based triage

| Observed draft surface | Classification | Disposition |
| --- | --- | --- |
| Existing Propr native evidence, exact source/account identity, provider cash/equity provenance, reconciliation uncertainty | Required current baseline | Keep pinned version and its unknown/fail-closed semantics; do not substitute the draft |
| Existing Bybit Demo endpoint and expected UID isolation, no generic credential fallback | Required current baseline | Keep pinned version; verify with account-bound read-only tests before any change |
| Potential error handling, journaling and recovery improvements within unchanged contracts | Generic reliability candidate | Extract only after independent focused tests and backward-compatible API review; none accepted by this document |
| `connectors/backed/`, `ctrader/`, `matchtrader/`, `mt5/` working-copy directories | Optional future providers | Preserve outside the active integration path; do not merge or add required dependencies until a provider-specific spec, tests, source-of-truth and explicit acceptance |
| Draft `connectors/bybit/config.py` profile loader, new `BYBIT_PROP_*` credential sourcing and auto-loading `bybit-prop.env` | Execution/account authority change | **Do not merge into active Bybit Demo path.** Require explicit account selection, no credential fallback and tested endpoints |
| Draft Propr stop/stop-limit, `trigger_price`, `Intent` and journal schema changes | Execution-contract expansion | **Do not merge into the active order path** without native order semantics, migration/replay proof, cancel/fill race tests and explicit authority |
| Draft Propr ML4T `get_cash()`/account-value synchronous bridge | Potentially incompatible financial semantics | **Do not promote buying power to Guardian cash** or replace asynchronous protocol behavior without verified installed ML4T signatures and migration tests |
| Other local edits to examples and tests | Unqualified supporting work | Retain for separate review; do not infer production approval from their existence |

The classification above reflects the inspected **local diff**, not a complete behavioral or credential audit. It makes no claim that any draft is deployed or production safe.

## Acceptance and independent gates

- `OBSERVE`: selected source identity, exact market scope and causal read-only evidence. No Guardian heartbeat or broker credential requirement.
- `PAPER`: valid selected source observations plus quote-based simulated follower accounting, pending-order model and risk bounds; historical positive alpha is not a prerequisite for *starting collection*.
- `DEMO`: additionally independently proven Demo-account binding, broker reconciliation, idempotent intents, recovery, risk and explicit demo-order authority.
- `LIVE`: actual account and release authorization, trusted equity/headroom, venue restrictions and protected risk supervisor; research/observer success never grants trading authority.

Retain separate SHA-pinned compatibility from `prop-production/src/prop_production/compatibility.json`; never broaden provider ownership or financial order semantics as a side effect of onboarding optional providers.
