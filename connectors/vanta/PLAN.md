# Vanta connector — scope and release plan

## Scope

Provider-isolated transport for a single explicitly selected Vanta prop account.
Provider docs: <https://app.vantatrading.io/docs> and
<https://app.vantatrading.io/docs/openapi.json> (v1.4.0, 2026-10-17).

Implemented: HMAC-SHA256 request signing, account snapshot, open positions,
pending orders, trade history, paginated payouts, trade pairs, key details, and
exact preparation of `POST /api/v1/trading/orders` payloads.

Not implemented: order dispatch, sandbox environment, market data, streaming,
account administration, payouts requests, bulk flattening/cancellation,
copy/follower/mirror features, strategy, ML4T bridge, production journals or
intent IDs.

## Safety posture

- `place_order` raises `MutationDenied` before any network request, and the
  wrapping HTTP transport rejects every non-read method. No flag, environment
  variable or injected transport can enable dispatch.
- The configured account is mandatory. `get_account()` rejects a snapshot whose
  `accountId` differs; `get_key()` exposes the key's bound account and scopes.
- Order placement is not idempotent. Timeouts and transport failures are
  unknown outcomes; no automatic retry exists.
- `prepare_order` rejects unknown fields, because the provider silently drops
  them and would otherwise place a different order than requested.
- JSON numbers are `Decimal`; identifiers, timestamps and nullable/unknown
  fields are preserved. A 503 read is an outage, never a zeroed account.
- Third-party copy trading is prohibited by Vanta's FAQ and is not exposed.

## Verification stages

1. Synthetic unit tests: `python -m pytest -q connectors/vanta/tests`.
2. Read-only credentialed inspection with explicitly supplied key id, secret and
   account id, using only GET endpoints.
3. Order dispatch is NO-GO pending a separate reviewed change, explicit trading
   authority, account binding evidence and reconciliation ownership.

Deterministic tests do not qualify provider semantics, account eligibility or
trading authorization.

## Provider constraints and open questions

- No sandbox and no paper credentials: every key reaches the live bound account.
- No idempotency key; `409` is nonce replay protection, not order deduplication.
- Only payouts document pagination; other reads return their full current set.
- Response shapes are documented by example, not a strict schema; fields may be
  added without notice and unknown enum values must not crash a client.
- The provider ignores unrecognised request fields, so local rejection is the
  only reliable typo protection.
- `FLAT_ALL` request preparation is supported for contract inspection only;
  every dispatch, including account-wide flattening, is denied.

## Official sources

- <https://app.vantatrading.io/docs/quickstart>
- <https://app.vantatrading.io/docs/authentication>
- <https://app.vantatrading.io/docs/endpoints>
- <https://app.vantatrading.io/docs/orders>
- <https://app.vantatrading.io/docs/errors>
- <https://app.vantatrading.io/docs/changelog>
- <https://www.vantatrading.io/faq>
