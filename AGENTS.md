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
- The official Propr Python SDK (`propr_sdk.py` and `connectors/propr/sdk.py`) is the source of truth for all Propr REST/WebSocket API endpoints.
- `ProprLiveBroker` in `connectors/propr/ml4t_bridge.py` implements the installed `ml4t-live==0.1.2` `AsyncBrokerProtocol` for live and challenge-account trading via `ProprClient`.
- `ML4TShadowBroker` remains available for offline simulation and shadow intent evaluation.
- Model order intent, exact side/position-side, reduce-only, quantity precision, partial fills, idempotency, errors, and reconciliation explicitly.
- Use Decimal for quantities and prices at provider boundaries; preserve raw IDs and timestamp evidence.
- Keep REST command transport separate from WebSocket state-event ingestion and market-data feeds.

## Verification stages
1. Unit tests with synthetic API responses and mock clients (`pytest connectors/propr/tests`).
2. Protocol conformance: `ProprLiveBroker` conforms to `AsyncBrokerProtocol`.
3. Read-only queries in an explicitly selected environment (`inspect_account.py` or `ProprClient.health()`).
4. Shadow-mode with no order mutation (`shadow_demo.py` / `ML4TShadowBroker`).
5. Challenge / Live execution: `ProprLiveBroker` executes orders (`submit_order_async`, `cancel_order_async`, `close_position_async`) on the explicitly bound account.

Never infer that an API labeled Beta is paper-only. Never auto-select an ambiguous account when multiple are active. Do not silently flatten or cancel positions on account-wide scopes. Keep the same strategy class between backtest and live when integrating ML4T.
