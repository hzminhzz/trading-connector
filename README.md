# Trading Connectors

A home for reusable, independently testable connectors to trading venues, brokers, and prop-trading platforms.

**Scope:** this is a multi-provider integration library, not a strategy repository or a Propr-specific application. Providers live in their own directories; strategies, datasets, and research results belong in their owning projects.

## Connectors

| Provider | Status | Documentation | Implementation |
| --- | --- | --- | --- |
| [Propr](connectors/propr/) | Planned; API and safety semantics require qualification | [Plan](connectors/propr/PLAN.md) | Not implemented |

Additional providers are added under `connectors/<provider>/` without coupling their logic to Propr or another provider.

## Repository conventions

- **Shared contracts:** [docs/architecture.md](docs/architecture.md) describes reusable order, execution, account, position, market-data, and lifecycle boundaries. It is an architecture target, not an implemented API.
- **Provider isolation:** each `connectors/<provider>/` owns its API transport, mappings, configuration, tests, examples, and plan.
- **Downstream integrations:** adapters for consumers such as `ml4t-live` should be thin, optional bridges, not the foundation of the transport.
- **Environment isolation:** production credentials/endpoints are never substituted for test credentials/endpoints. A named `beta` environment is not presumed to mean risk-free paper execution.
- **Safety:** no trading actions from repository setup, import, test discovery, or read-only diagnostics. All mutation-capable tests are explicitly gated.
- **Secrets:** credentials belong in the environment or a secret manager; do not commit `.env`, API keys, positions, account identifiers, or raw authenticated responses.

## Proposed structure

```text
trading-connector/
├── README.md
├── AGENTS.md
├── docs/
│   └── architecture.md
└── connectors/
    └── propr/
        ├── README.md
        └── PLAN.md
```

The files listed above are documentation only; implementation packages and tests will be added per provider after its plan is approved.

## Development status

This repository was initialized with documentation and the Propr connector plan. **No trading connector code has been deployed and no orders have been placed through this repository.**
