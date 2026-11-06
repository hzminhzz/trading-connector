# Transport hardening for prop-production

## Verified code boundary

The SDK now defaults to a read-only Session.send boundary. Direct create, batch,
cancel, close and raw HTTP mutation calls cannot reach an HTTP adapter.
No live or demo capability is enabled. ProprConfig bypass does not bypass this
guard. SDK import no longer reads dotenv files.

create_order accepts client_intent_id and transmits it unchanged. create_orders
requires caller-persisted IDs, rejects duplicates and cross-account rows, and
does not mutate input dictionaries. The ML4T bridge passes its durable journal ID.
The reserved https://fixture.invalid/v1 callback path is synthetic only.

A timeout remains unknown. There is no automatic HTTP retry and server-side
deduplication/query-by-client-ID remain UNVERIFIED. Cancel HTTP 400 is an error,
not proof of cancellation. Cancel/replace refuses the second opening until a
coherent fill cutoff is independently proven. Default account discovery rejects
multiple active attempts.

## Compatibility and limits

The root propr_sdk module is now packaged. requests is a declared dependency;
dotenv and ULID are unnecessary for this SDK. ML4T 0.1.2 requires Python 3.12+,
while provider-only library use retains Python 3.11 compatibility.

The existing four-week paper policy remains required. Complete account-specific
rulebook, margin tiers, real routing classification and provider idempotency
evidence remain unavailable. Fixture tests do not change NO-GO execution status.
No credentialed mutations were used in this patch.

## Validation

.venv/bin/python -m pytest -q connectors/propr/tests
.venv/bin/python -m ruff check connectors/propr

Observed: 138 passed, 2 optional tests skipped. Tests include SDK transport spying,
persisted ID equality, accepted-after-timeout, ambiguous account discovery, and
blocked replacement after cancellation. The installed optional bridge signature
was inspected against ml4t-live 0.1.2.
