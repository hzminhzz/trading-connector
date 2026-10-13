# Propr implementation status

Date: 2026-10-13

## Verified locally
- Python safety configuration: Beta/production endpoint and credential segregation; all remote execution disabled.
- Explicit native/canonical symbol mapping and Decimal tick/lot checks.
- Native order and position models with rejection of ambiguous one-way position views.
- SQLite intent journal with crash recovery of staged commands to unknown state.
- Basic manually resettable risk gate.
- Async HTTP client with enforced read-only transport, timeout/error classification, and preliminary page/limit pagination.
- 20 credential-free unit tests passed using python -m pytest -q connectors/propr/tests.

## NOT qualified / must not be represented as completed issues
- No authoritative Beta simulation-only guarantee.
- Propr REST/WS schemas, pagination and SDK contract not yet independently verified.
- No read-only authenticated Beta account snapshot.
- No safe order execution gateway, cancel/replace/reduce-only integration, WS reconciliation or LiveEngine adapter.
- No real provider-verified order lifecycle tests or four weeks of paper operation.
- No production authorization. Production readiness: NO-GO.

## Next implementation sequence
1. Verify exact Propr SDK/REST/WS and installed ml4t-live signatures against source; update compatibility matrix and HTTP parsing.
2. Complete issue #1 and #2 acceptance tests and source evidence.
3. Add account health and inventory mapping, mocked contract fixtures and read-only Beta diagnostics.
4. Implement and test idempotent gateway, cancel/replace, WS state reconciliation, shadow, optional ML4T bridge, risk and release assessment.
5. Run complete tests, independent review, commit/PR only on satisfied acceptance criteria.
