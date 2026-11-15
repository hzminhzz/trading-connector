# Vanta connector

Provider-isolated transport for the Vanta Trading API. It owns HMAC request
signing, account-bound REST reads and exact order-request preparation. It does
not own strategy, risk authorization, production intent IDs or copy-trading
configuration.

Official documentation: <https://app.vantatrading.io/docs>. OpenAPI 3.1:
<https://app.vantatrading.io/docs/openapi.json>.

## Third-party copy trading is prohibited

Vanta's FAQ states: **"Only your own strategy. Third-party copy trading is not
allowed and results in immediate disqualification from your evaluation or scaled
account."** This connector exposes no copy/follower/mirror feature and must not
be used to relay another trader's orders into a Vanta account. Provider-side
copying of Vanta traders is Vanta's own business, not a client feature.

## Scope

- Reads: account snapshot, open positions, pending orders, trade history,
  payouts (paginated), trade pairs and the signing key's own details.
- Preparation: exact `POST /api/v1/trading/orders` envelope and trade payload
  validation for the documented execution types.
- Mutation: `place_order` always raises locally before any network request.

There is no sandbox, no market-data endpoint and no streaming feed. Vanta
accounts are simulated prop accounts; API keys reach the live account bound at
creation. No test or sandbox credential exists.

## Authentication

Every request carries four headers:

| Header | Value |
| --- | --- |
| `X-Vanta-Key-Id` | key id (`vk_…`), not secret |
| `X-Vanta-Timestamp` | whole milliseconds since epoch, decimal string |
| `X-Vanta-Nonce` | fresh random value per request (16 random bytes hex) |
| `X-Vanta-Signature` | `v1=<base64 HMAC-SHA256>` |

The canonical string is six fields joined by newlines, with no trailing
newline:

```text
v1
<UPPERCASE METHOD>
<request path including query string exactly as sent>
<timestamp ms>
<nonce>
<lowercase hex SHA-256 of the exact request body bytes>
```

For a GET the body is empty, so its hash is
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
Serialize a JSON body once, sign those exact bytes and transmit the same bytes;
re-serialising after signing breaks the signature. The query string is part of
the signed target, including percent-encoding.

Official test vectors (sample credentials, not valid for authentication):

| Input | GET | POST |
| --- | --- | --- |
| secret | `vks_EXAMPLE_SECRET_DO_NOT_USE_IN_PRODUCTION` | same |
| method | `GET` | `POST` |
| path | `/api/v1/trading/account` | `/api/v1/trading/orders` |
| timestamp | `1717718400000` | `1717718400000` |
| nonce | `9f8c1a2b3d4e5f60718293a4b5c6d7e8` | `3c1d5e7f90a2b4c6d8e0f2a4b6c8d0e2` |
| body | empty | `{"accountId":"6f1c2e34-9a4b-4c1d-8e2f-1a2b3c4d5e6f","trade":{"execution_type":"MARKET","trade_pair":"BTCUSDC","order_type":"LONG","value":1000}}` |
| signature | `v1=ARXQ6aQCU4GUl47DNcQOJVW0miSfhgWdlfufJHWHRdE=` | `v1=1/Oq1TQAeJehKvyAoHCq8DIE+8dAqkXYHIyvO3/17Q4=` |

Timestamps must be within five minutes of server time. Nonces are single-use
for the life of the key; a replay returns `409`. A client therefore signs each
attempt immediately before sending and never re-uses a nonce.

## Account binding

A key is bound to exactly one prop account at creation and cannot be re-pointed.
`VantaConfig` requires an explicit `account_id`, `api_key` (`vk_…` key id) and
`api_secret` (`vks_…` secret); both prefixes are enforced. There is no ambient
credential fallback and no automatic account selection. Both credentials are
hidden from `repr`.

- Reads need no `accountId`; if supplied it must match the bound account or the
  API returns `403`.
- Writes must name the bound account in `accountId`.
- Before the first account read the client calls `GET /api/v1/keys/self` and
  requires the key's `accountId` to equal the configured account; the verified
  identity is cached. `get_key()` always refetches and re-verifies. Every
  response carrying a different `accountId` is rejected.
- `get_account()`, `get_trade_pairs()` and `get_key()` require the field and
  raise `VantaResponseError` when it is absent.
- One key per account: trading several accounts means several keys.

## Endpoints

| Method | Path | Scope | Client method |
| --- | --- | --- | --- |
| GET | `/api/v1/trading/account` | `trade:read` | `get_account()` |
| GET | `/api/v1/trading/positions` | `trade:read` | `get_positions()` |
| GET | `/api/v1/trading/orders` | `trade:read` | `get_orders()` |
| GET | `/api/v1/trading/trades` | `trade:read` | `get_trades()` |
| GET | `/api/v1/trading/payouts` | `trade:read` | `get_payouts()` |
| GET | `/api/v1/trading/trade-pairs` | `trade:read` | `get_trade_pairs()` |
| GET | `/api/v1/keys/self` | none | `get_key()` |
| POST | `/api/v1/trading/orders` | `trade:place` | `prepare_order()` / denied `place_order()` |

Success responses are `{ "success": true, "data": … }`. Failures are a 4xx/5xx
status with `{ "error": "…" }`; a missing scope adds `required_scope`.

Payouts are the only paginated read: `limit` is 1–200 (default 50) and
`nextCursor` is passed back unchanged as `cursor` until it is null. The accrual
estimate is computed on the first page only. `get_payouts(limit=50)`
auto-paginates and returns every item plus the first page's `accrualStatus` and
`accrual`; a malformed, empty or repeated cursor raises `VantaResponseError`.

## Orders

`prepare_order(payload)` validates and returns the exact JSON-ready envelope for
`POST /api/v1/trading/orders`. It does not send anything. The accepted
`execution_type` values are `MARKET`, `LIMIT`, `BRACKET`, `LIMIT_EDIT`,
`LIMIT_CANCEL` and `FLAT_ALL`; `order_type` is `LONG`, `SHORT` or `FLAT`.

- `MARKET` and `LIMIT` require exactly one of `value`, `quantity` or
  `leverage`; `LIMIT` also requires `limit_price`.
- `BRACKET` requires at least one `bracket_orders` leg.
- `LIMIT_EDIT` restates a position's bracket legs.
- `LIMIT_CANCEL` requires `order_uuid`.
- `FLAT_ALL` uses `order_uuid` as a selector: `ALL` or comma-separated
  `positionUuid` values. It is prepared for completeness but never dispatched;
  no bulk or account-wide flattening is authorized.
- Unrecognised fields are dropped by the provider, so `prepare_order` rejects
  them instead of silently losing a typo.
- `trailing_stop`, `bracket_pct`, `STOP_LIMIT`, `stop_price` and
  `stop_condition` are not accepted by the provider API.

`place_order(payload)` prepares the same request and then raises
`MutationDenied` before any network I/O. A wrapping HTTP transport rejects every
non-read method and every host other than `https://app.vantatrading.io`, so an
injected transport cannot bypass the denial. The guarded `httpx` client is
private (`_http`); even a direct call on it is denied.

Order placement is **not idempotent**: there is no idempotency key. A timeout or
transport failure is an unknown outcome, not a rejection. Before any later
authorized retry, read positions, orders and trades to determine whether the
order landed. A cancel acknowledgement is not proof that the order did not fill.

## Precision and unknowns

JSON numbers are parsed as `Decimal` without a float round-trip. Provider
identifiers, timestamps, nullable fields and unknown enum values are preserved.
`netQuantity` null means unknown, never zero. `meta.stale` is surfaced rather
than treated as fresh data. A `503` read is an outage, not a zeroed account.

## Errors and rate limits

| Status | Meaning | Action |
| --- | --- | --- |
| 400 | query/body validation, account not provisioned | fix request, retry with backoff |
| 401 | missing credentials, bad signature, invalid key/timestamp | fix signing or key |
| 403 | `insufficient_scope` or account mismatch | do not retry |
| 404 | bound account no longer resolves | do not retry |
| 409 | nonce already used | re-sign with a fresh nonce |
| 429 | `rate_limited` (120 reads/min/key) or `too_many_failed_requests` | honour `Retry-After` |
| 500/502/503 | platform or upstream failure; 503 fails closed | retry with backoff |

Only `429` responses carry `Retry-After`, `X-RateLimit-Limit`,
`X-RateLimit-Remaining` and `X-RateLimit-Reset`. Thirty rejected order requests
within ten minutes pause order placement for fifteen minutes.

## Verification

```bash
python -m pytest -q connectors/vanta/tests
```

Tests use synthetic HTTP fixtures only, with no credentials and no external
network. They prove deterministic signing, mapping and failure behavior; they do
not establish provider compatibility, account eligibility or trading
authorization.

## Official sources

- <https://app.vantatrading.io/docs>
- <https://app.vantatrading.io/docs/quickstart>
- <https://app.vantatrading.io/docs/authentication>
- <https://app.vantatrading.io/docs/endpoints>
- <https://app.vantatrading.io/docs/orders>
- <https://app.vantatrading.io/docs/errors>
- <https://app.vantatrading.io/docs/changelog>
- <https://app.vantatrading.io/docs/openapi.json>
- <https://www.vantatrading.io/faq> (copy-trading prohibition)
