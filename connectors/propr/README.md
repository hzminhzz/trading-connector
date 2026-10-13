# Propr connector (read-only and synthetic shadow candidate)

**Current classification: NO-GO for any remote order execution.** This module is one provider in the [multi-provider trading connector library](../../README.md); it does not contain strategies.

The connector includes:

- Async HTTPS read-only REST with explicit Beta/production credentials, account binding and pagination.
- Propr account equity/margin parsing, native order/position/trade data, Decimal and reversible HIP-3 symbol mapping.
- Durable local intent journal, fail-closed unknown outcomes, fake-only cancel/replace/reduce and kill drills.
- Authenticated account WebSocket client and conservative double REST reconciliation, heartbeat/reconnect detection.
- Credential-free shadow intent gateway and optional native `ml4t-live==0.1.2` `AsyncBrokerProtocol` bridge.
- Optional independent Binance spot completed-kline reference feed using ML4T `MarketEvent` (not equivalent to Propr/Hyperliquid execution prices).
- Explicit blocked Beta remote order qualification. No operational code enables real POST/PUT order mutations.

## Tests and use

From the repo root run:

```bash
/home/quant/dev/quant/quant-research/.venv/bin/python -m pytest -q connectors/propr/tests
/home/quant/dev/quant/quant-research/.venv/bin/python -m connectors.propr.examples.shadow_demo
```

For a read-only Beta account check, provision the secret key privately as `PROPR_BETA_API_KEY` and provide the explicit account ID:

```bash
python -m connectors.propr.examples.beta_readonly_smoke --account-id YOUR_ACCOUNT_ID
```

This command does not submit orders and only prints redacted status/counts.

Read [OPERATOR_RUNBOOK.md](OPERATOR_RUNBOOK.md), [COMPATIBILITY.md](COMPATIBILITY.md), [BETA_SAFETY.md](BETA_SAFETY.md) and [RELEASE_REPORT.md](RELEASE_REPORT.md). Publishing an issue or merging a PR does not authorize trading.
