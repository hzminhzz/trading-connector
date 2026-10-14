# Propr connector (read-only and synthetic-shadow candidate)

**Remote order execution: NO-GO.** This is one provider in the [multi-provider trading connector library](../../README.md); strategies remain in their owning project.

The connector includes:

- Production-only read-only REST with explicit account binding or opt-in unique account discovery.
- Funded and active-challenge account lookup; ambiguous results fail closed.
- Propr account equity/margin parsing, native inventory, Decimal values and reversible HIP-3 symbol mapping.
- Durable local intent journal, unknown-outcome handling, fake-only order lifecycle and kill drills.
- Authenticated account WebSocket ingestion and conservative double-REST reconciliation.
- Credential-free shadow gateway and optional `ml4t-live==0.1.2` `AsyncBrokerProtocol` integration.
- An adapter for running the unchanged ML4T backtest `Strategy` through `LiveEngine`; target-weight calls become audited shadow intents.
- Independent Binance spot completed-kline reference feed; not equivalent to Propr/Hyperliquid execution prices.

No operational code enables remote order, cancel, replace, leverage, close or flatten mutations.

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
