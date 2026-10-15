# Trading Connectors

A home for reusable, independently testable connectors to trading venues, brokers, and prop-trading platforms.

**Scope:** this is a multi-provider integration library, not a strategy repository or a Propr-specific application. Providers live in their own directories; strategies, datasets, and research results belong in their owning projects.

**ML4T ownership:** `/home/quant/dev/quant/quant-research` is the authoritative trading/research environment for our `ml4t-live` integration. Verify the installed framework package and protocol in that environment before changing provider adapters. This repository owns provider-specific connectors and optional ML4T bridges, **not** `ml4t-live` itself. A separate `/home/quant/dev/quant/ml4t-live` checkout and the documentation copies under `docs/ml4t-live/` must not be treated as authoritative runtime source.

## Connectors

| Provider | Status | Documentation | Implementation |
| --- | --- | --- | --- |
| [Propr](connectors/propr/) | Read-only and synthetic-shadow candidate; remote execution **NO-GO** | [Runbook](connectors/propr/OPERATOR_RUNBOOK.md), [Release report](connectors/propr/RELEASE_REPORT.md), [Plan](connectors/propr/PLAN.md) | [Provider code](connectors/propr/README.md) |

Additional providers are added under `connectors/<provider>/` without coupling their logic to Propr or another provider.

## Repository conventions

- **Shared contracts:** [docs/architecture.md](docs/architecture.md) describes reusable order, execution, account, position, market-data, and lifecycle boundaries. It is an architecture target, not an implemented API.
- **Provider isolation:** each `connectors/<provider>/` owns its API transport, mappings, configuration, tests, examples, and plan.
- **Downstream integrations:** adapters for consumers such as `ml4t-live` should be thin, optional bridges, not the foundation of the transport.
- **Environment isolation:** production credentials/endpoints are never substituted for test credentials/endpoints. A named `beta` environment is not presumed to mean risk-free paper execution.
- **Safety:** no trading actions from repository setup, import, test discovery, or read-only diagnostics. All mutation-capable tests are explicitly gated.
- **Secrets:** credentials belong in the environment or a secret manager; do not commit `.env`, API keys, positions, account identifiers, or raw authenticated responses.

## Build and test

The base package installs with `pip install -e ".[test]"`. ML4T is optional, pinned to the locally inspected `ml4t-live==0.1.2` adapter boundary. The default GitHub Actions workflow uses only credential-free fixtures.

```bash
python -m pytest -q connectors/propr/tests
python -m ruff check connectors/propr
uv build --offline
```

## Development status

Propr has a production-only read-only REST gateway, conservative WebSocket reconciliation, synthetic order/kill-switch drills, shadow decisions and an optional ML4T LiveEngine integration. **No remote order calls are implemented or authorized.** The release remains **NO-GO for execution** until account-class safety, four weeks of paper evidence and separate approval are established. See the [qualification report](connectors/propr/RELEASE_REPORT.md).
