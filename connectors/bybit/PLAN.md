# Bybit ATS7 boundary qualification

## Scope

Base: 6175a948e6d253ba6f4b610b97b34d8433662e77.
Branch: fix/ats7-bybit-order-boundary.
No package upgrade, credential changes, order submissions, cancellations,
position-mode changes, leverage changes, or account-wide flattening occurred.
Unrelated Propr and cTrader work in the original checkout is preserved.

The connector remains the provider transport owner. Native ML4T Live remains
the risk/position owner. ATS7 supplies a stable release-scoped client identity;
it does not create a second order engine.

## Corrected failure semantics

- Accept the native intent idempotency key in durable staging and validate the
  provider's 1–36 character client-ID format.
- Reject reuse before a network call. A changed payload or a terminal old entry
  does not authorize retransmission of the same client ID.
- Pass the staged ID as orderLinkId; callers cannot override it through raw params.
- Map native reducing_risk to provider reduceOnly.
- Classify task cancellation and ambiguous transport errors as unknown. Text
  containing “invalid”, “reject”, or “insufficient” is not authoritative rejection.
- Preserve an accepted provider order ID if subsequent order parsing or account
  refresh fails, and retain the unknown obligation.
- Keep pending state after cancellation acknowledgment. Only observed provider
  state can establish that the order no longer has a remaining obligation.

Typed insufficient-funds/invalid-order pre-effect errors remain explicit
refusals. A timeout is not proof of no fill. No retry or cancel acknowledgement
is converted into a fabricated terminal provider event.

## Verification

All 67 Bybit tests pass, including nine new boundary cases. Source lint and
git diff whitespace checks pass. The new tests exercise stable-ID propagation,
duplicate suppression before dispatch, lost acknowledgement, task
cancellation, post-acknowledgement snapshot failure, native reduce-only mapping,
cancellation races, and raw parameter override rejection.

These are deterministic connector tests, not new Demo execution evidence.
The existing real Demo order/fill/funding archive is independently reconciled
through ATS3 by the parent ATS7 qualification. No new Demo order was authorized.

## Provider contract references

- Demo environments and supported APIs:
  https://bybit-exchange.github.io/docs/v5/demo
- Asynchronous order acceptance, client ID, IOC market orders and reduce-only:
  https://bybit-exchange.github.io/docs/v5/order/create-order
- Duplicate Filled messages during cancellation races:
  https://bybit-exchange.github.io/docs/v5/websocket/private/order
- Order-history retention and delayed history:
  https://bybit-exchange.github.io/docs/v5/order/order-list
- Instruments, lot/price increments and leverage filters:
  https://bybit-exchange.github.io/docs/v5/market/instrument

Client ID uniqueness is not a promise of unlimited exactly-once delivery
across lost journals or provider retention expiry. Unknown outcomes remain
blocked until original execution, order, position and balance evidence agrees.

## Unqualified capabilities and rollout

No fresh external partial fill, cancel/late-fill race, reconnect with a live
pending order, or external protective reduction was executed by ATS7.
Provider Demo order qualification needs an explicitly authorized account,
instruments, risk envelope, action list, expiry and independently approved
release. Mainnet execution remains outside this mission.

The original connector journal continues to use its existing persistence
format. ATS7's account owner enforces one canonical native writer; this does
not qualify arbitrary concurrent standalone connector processes.
