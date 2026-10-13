# Agent Instructions

This repository is a **multi-provider custom trading connector library**. Do not turn it into a Propr-only system or put trading strategies here.

## Before changes
1. Read `README.md`, `docs/architecture.md`, and the specific `connectors/<provider>/PLAN.md`.
2. Inspect the actual installed source and versions of any target trading framework before writing an adapter. Do not implement from guessed protocol signatures.
3. Identify the target provider environment, explicit account identity, trading permissions and rollback path.
4. Do not commit secrets, credentials, unredacted account snapshots, or private research data.

## Design boundaries
- Place each provider under `connectors/<provider>/`; keep providers independent.
- Prefer a provider-neutral internal execution representation, with a strictly isolated translation boundary for each provider.
- Downstream libraries such as `ml4t-live` are optional integration adapters.
- Model order intent, exact side/position-side, reduce-only, quantity precision, partial fills, idempotency, errors, and reconciliation explicitly.
- Use Decimal for quantities and prices at provider boundaries; preserve raw IDs and timestamp evidence.
- Keep REST command transport separate from WebSocket state-event ingestion and market-data feeds.
- Persist intent IDs and reconciliation state before order submission; fail closed on ambiguity.

## Verification stages
1. Unit tests with synthetic API responses.
2. Stateful fault tests: timeouts, retries, disconnects, duplicate/out-of-order events, partial fills, cancel races and restart.
3. Read-only queries in an explicitly selected environment.
4. Shadow-mode with no order mutation.
5. Paper/simulated execution **only after documented verification** that the chosen account/environment is risk-free.
6. Live only with explicit user authorization, reviewed risk limits, reconciliation, kill switch, logging, and recovery drills.

Never infer that an API labeled Beta is paper-only. Never run an order-creating example as a smoke test. Never auto-select the first account for trading. Do not silently flatten or cancel positions on account-wide scopes. Keep the same strategy class between backtest and live when integrating ML4T.
