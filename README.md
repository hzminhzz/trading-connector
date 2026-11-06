# Trading Connectors

Provider transports, native data normalization and optional framework bridges.
Strategies, portfolio selection and account finance belong to their consuming
applications. This remains a multi-provider library.

## Providers and authority

| Provider | Scope | Guide |
| --- | --- | --- |
| Propr | Native read-only evidence and synthetic shadow; remote mutations denied | [Provider](connectors/propr/README.md), [native evidence](connectors/propr/NATIVE_EVIDENCE.md), [operator runbook](connectors/propr/OPERATOR_RUNBOOK.md) |
| Bybit | Separate Demo read-only account observer, provider transport and optional ML4T bridge | [Provider](connectors/bybit/README.md), [order boundary](connectors/bybit/PLAN.md) |
| Hyperliquid | Independent provider and market-data integration | [Provider](connectors/hyperliquid/README.md) |

Existing explicit order-capable diagnostics do not grant permission to run them.
Reconciliation work defaults to SHADOW / NO_TRADE; do not submit orders, cancel
positions, change leverage or use account-wide actions without separate authority.
A named Beta or Demo environment is not sufficient proof of account permissions.

The official Propr SDK owns its endpoints. `propr_native_evidence_v1` preserves
provider identity, equity provenance and explicit UNKNOWN cash/history cutoff.
Do not manufacture cash as equity minus unrealized PnL or infer coherent history
from two matching REST snapshots.

Guardian in prop-production owns protected follower finance, risk permits and
durable production intents. It consumes native clients rather than connector-owned
ML4T journals. Standalone ML4T bridges remain separate optional consumers of the
installed `ml4t-live==0.1.2` protocol.

## Sizing boundary

Default copying uses signed `FIXED_K_NATIVE_QUANTITY`: k × selected leader
quantity × source mark gives notional, converted at the follower quote.
Leader whole-account NAV is optional. Follower equity/headroom, selected source
scope, causal clocks, reconciliation, provider constraints and the owning risk
authority remain mandatory. Explicit legacy NAV-relative sizing belongs to the
planner/research policy. A transport cannot pick k or grant risk authorization.

See [architecture](docs/architecture.md) and [agent instructions](AGENTS.md).

## Development and verification

The reviewed source baseline is GitHub `main`; inspect the current default branch,
remote SHA, dirty files and active worktrees before changes. Preserve other sessions.
Private credentials, account responses, datasets and journals remain outside Git.
This source repository is public; the program's private data is not publishable.

The authoritative installed ML4T integration is in the operator's quant-research
environment. Inspect its actual protocol before modifying an adapter. Copies under
`docs/ml4t-live/` are reference material, not runtime source.

The base package installs with `pip install -e ".[test]"`. Optional ML4T and
provider dependencies must match the consuming application's verified environment.

```bash
python -m pytest -q connectors
python -m ruff check connectors propr_sdk.py
uv build --offline
```

Default tests use synthetic responses. Credential-dependent integration tests
remain skipped unless their separately authorized environment is supplied.
GitHub Actions is optional; record exact commit, dependencies, pass/skip counts
and build results. Cross-repository pins are owned by
`prop-production/src/prop_production/compatibility.json` and checked by its
`scripts/verify-local.sh all`; do not introduce another independent pin list.

Passing unit tests establishes deterministic engineering behavior. It does not
qualify provider semantics, executable follower PnL or trading authorization.
