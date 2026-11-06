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
- The Guardian-native read-only evidence contract is `connectors/propr/native_evidence.py` (`propr_native_evidence_v1`). Read `connectors/propr/NATIVE_EVIDENCE.md`. Its missing provider cutoff and cash semantics must remain UNKNOWN; never derive cash from equity or mark two REST reads history-complete.
- `prop-production` Guardian alone owns production SQLite intent IDs and risk; never route Guardian orders through connector-owned ML4T/legacy journals. `copytrade-pm` stays research-only.
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
5. Challenge / Live execution is **NO-GO**. `ProprLiveBroker` retains optional ML4T 0.1.2 methods for independent compatibility only; `assert_live_trading()` rejects, and real SDK HTTP mutation requests are denied even when called directly. No tests or connected session grant execution authorization.

Never infer that an API labeled Beta is paper-only. Never auto-select an ambiguous account when multiple are active. Do not silently flatten or cancel positions on account-wide scopes. Keep the same strategy class between backtest and live when integrating ML4T.
