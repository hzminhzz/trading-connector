# Propr connector & ML4T Live Broker

Official Propr integration supporting both direct SDK trading and `ml4t-live` async execution.

The connector includes:

- **Propr SDK (`propr_sdk.py` / `connectors.propr.sdk`):** Official client for Propr REST and WebSocket APIs (`/health`, `/accounts`, `/orders`, `/positions`, `/trades`, `/margin-config`).
- **`ProprLiveBroker` (`connectors.propr.ml4t_bridge`):** Live execution broker implementing `ml4t.live.protocols.AsyncBrokerProtocol` for running ML4T strategies directly against active Propr accounts.
- **`ML4TShadowBroker`:** Safe offline/shadow adapter for validating strategy signals without remote execution.
- **Account inspection:** Safe read-only inspection (`inspect_account.py`) with support for `.env` credentials (`PROPR_API_KEY` / `PROPR_LIVE_API_KEY`).
- **Reconciliation & WebSocket ingestion:** Authenticated state streaming and double-REST reconciliation.
- **Symbol & Precision mapping:** Lossless decimal handling for perpetual contracts on Hyperliquid.
## Combo 2 paper-only, no-broker-intent projection

`connectors.propr.combo2_paper_projection.Combo2PaperProjection` is a **local-only** adapter for the independently verified `copytrade-pm` paper event chain. It requires a pre-existing private SQLite `IntentJournal` and writes only separate `combo2_paper_decisions` and `combo2_paper_actions` tables. It does **not** stage real broker orders, mark an actual order filled or affect the live Propr account. Exact repeated projection is idempotent; a conflicting replay or a partially committed transaction fails atomically.

The `connectors.propr.examples.project_combo2_paper` CLI imports the optional installed `copytrade_pm` module, verifies the immutable manifest and each full journal post-state, then projects locally into the SQLite journal. This command imports no Propr SDK/REST client and does no networking. Actual Propr account health remains in the separate read-only `ReadOnlyAccount` API.

**Release gate:** this is not a deployed paper tracker. Propr leverage-limit GET and account margin-configuration GET returned records for selected symbols, but account-specific instrument/DEX ID, lot/tick/minimum/session, independent executable price/fees and challenge rules remain **unattested**. Do not guess them from display symbols. All live order mutations remain unauthorized. See copytrade-pm Issue #22.

## Tests and use

From the repository root:

```bash
/home/quant/dev/quant/quant-research/.venv/bin/python -m pytest -q connectors/propr/tests
/home/quant/dev/quant/quant-research/.venv/bin/python -m connectors.propr.examples.shadow_demo
```

For a read-only account check, provision `PROPR_LIVE_API_KEY` privately and select identity explicitly:

```bash
python -m connectors.propr.examples.inspect_account --account-id YOUR_ACCOUNT_ID
python -m connectors.propr.examples.inspect_account --discover-unique
```

The second command proceeds only if the API yields exactly one funded or active challenge account. Output is redacted and no orders are submitted.

See [OPERATOR_RUNBOOK.md](OPERATOR_RUNBOOK.md), [COMPATIBILITY.md](COMPATIBILITY.md) and [RELEASE_REPORT.md](RELEASE_REPORT.md). Merging this code does not authorize trading.
