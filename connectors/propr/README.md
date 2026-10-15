# Propr connector & ML4T Live Broker

Official Propr integration supporting both direct SDK trading and `ml4t-live` async execution.

The connector includes:

- **Propr SDK (`propr_sdk.py` / `connectors.propr.sdk`):** Official client for Propr REST and WebSocket APIs (`/health`, `/accounts`, `/orders`, `/positions`, `/trades`, `/margin-config`).
- **`ProprLiveBroker` (`connectors.propr.ml4t_bridge`):** Live execution broker implementing `ml4t.live.protocols.AsyncBrokerProtocol` for running ML4T strategies directly against active Propr accounts.
- **`ML4TShadowBroker`:** Safe offline/shadow adapter for validating strategy signals without remote execution.
- **Account inspection:** Safe read-only inspection (`inspect_account.py`) with support for `.env` credentials (`PROPR_API_KEY` / `PROPR_LIVE_API_KEY`).
- **Reconciliation & WebSocket ingestion:** Authenticated state streaming and double-REST reconciliation.
- **Symbol & Precision mapping:** Lossless decimal handling for perpetual contracts on Hyperliquid.
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
