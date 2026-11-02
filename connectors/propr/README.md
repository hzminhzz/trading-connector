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

**Release gate:** this is not a deployed paper tracker. Propr leverage-limit GET and account margin-configuration GET returned records for selected symbols, but account-specific instrument/DEX ID, lot/tick/minimum/session, independent executable price/fees and challenge rules remain **unattested**. Do not guess them from display symbols. General and unattended remote order mutations remain unauthorized. See the one-shot Free Trial diagnostic below and copytrade-pm Issue #22.

## One-shot real Free Trial execution canary (November 2, 2026)

With **explicit user authorization**, the `free_trial_wallet_canary` command executed exactly one **0.20 HYPE buy** and one **0.20 HYPE reduce-only sell** on authenticated Propr trial **#JhW9**. The challenge API identified the account as **Free Trial**, account `type=paper`, and active. This genuinely submitted two orders through Propr's production REST API to the **simulated** account, not an offline mock or funded-account execution.

The source was an actual private SHA-verified W028 parent-account HYPE long **position snapshot**, observed at first-local receipt and reconfirmed on Hyperliquid after the five-minute delay. **No new wallet position change or fill had arrived.** The diagnostic is explicitly `INITIAL_HOLDING_CANARY_NOT_NEW_FILL`; it does not qualify faithful Combo 2 position sizing, first-known trade detection or unattended operations. Its small fixed 0.20 HYPE size was deliberately **not** derived from W028's unified-account NAV (still unverified).

Authenticated independent read-only Propr reconciliation (verified venue order IDs, `/trades` and `/positions`) confirmed both fills: buy **85.857**, sell **85.856**, fees **0.007727** each, realized P&L **−0.000200**, net **−0.015654**. Trial balance moved from **4999.241315** to **4999.225661** exactly; **zero open positions and zero pending/open/partial orders**. Private immutable evidence, local intent journal, report and venue reconciliation are stored under `/home/quant/.local/share/copytrade/reports/combo2-real-free-trial-canary-20261102/` (not in Git).

The one-shot CLI requires an explicit source generation, exact account binding, `--authorize-free-trial-canary`, existing private credentials and a *new, non-reused* private audit directory. It refuses anything except active Free Trial `paper` accounts, enforces a **$10–$30 reference notional**, stages provider ULIDs durably before each POST, and never retries unknown outcomes. A matching filled opening is closed by exactly one reduce-only market order; partial/unknown results halt rather than becoming fabricated fills. The companion **read-only** `verify_free_trial_wallet_canary` independently checks local intent IDs against Propr orders, trades, exact account balance P&L and flatness. Do not rerun this diagnostic on the same journal.

**Narrow scope:** This one-time Free Trial canary was the only authorized remote order mutation. Neither `Combo2PaperTracker` nor the main `ProprRest` read-only interface acquired a mutation capability. **No funded-account/live-money authorization, continuous auto-copying, or production deployment** follows from this test. The full three-wallet source-equity and executable-instrument contracts remain blocked under copytrade-pm Issue #22.

## Free Trial diagnostic, November 2, 2026

A user-authorized one-shot test on verified Propr Free Trial account #JhW9 (`type=paper`) submitted a 0.20 HYPE opening order and a 0.20 HYPE reduce-only closing order. The Propr order and trade APIs independently confirmed both fills and an empty position book. The balance reconciled from $4,999.241315 to $4,999.225661 (net -$0.015654 including fees). Private evidence is under `/home/quant/.local/share/copytrade/reports/combo2-real-free-trial-canary-20261102/`.

The source was an actual W028 HYPE holding snapshot, **not a newly observed wallet trade**. It was an execution diagnostic rather than faithful Combo 2 follower sizing. The Free Trial test does not authorize any future automated or funded-account orders. The existing normal Propr read-only transport and paper-ledger projection remain unchanged.

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
