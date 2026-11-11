# Account-bound execution capability evidence

`connectors.execution_capabilities.ExecutionCapabilities` is a versioned, immutable evidence contract. It binds account, endpoint, instrument, observation/knowledge/expiry clocks and explicit position mode to separately proven net-order, reduce-only and cross-zero semantics. Evidence status is UNKNOWN, UNSUPPORTED, VERIFIED or SYNTHETIC. Proven facts retain the original evidence SHA and provenance. No field grants trading authorization.

A consumer must check exact account/endpoint/instrument and the original expiry at use. Equality at expiry fails. Synthetic facts are accepted only for the reserved fixture account and fake broker endpoint, and cannot satisfy a native check. Hedge or unknown mode cannot authorize a one-way net adjustment.

The current Propr native evidence reader returns an empty capability tuple. Absence means UNKNOWN. A one-sided current position, matching REST reads, a healthy connection or a provider's generic API documentation does not prove account-specific position mode or safe cross-zero behavior. Native cash/history-cutoff and remote mutation restrictions remain unchanged. Bybit transport support is also not an account-specific Guardian capability attestation.

The Guardian in prop-production consumes this evidence through its existing BrokerGateway. It owns risk, signed mandates, reservations, attribution and the only protected financial journal. This contract creates no order journal or financial authority.

Verification uses synthetic facts, wrong bindings, expiry equality, unknown/hedge mode, malformed evidence and authority-escalation attempts. These tests do not establish native execution qualification. `uv sync --frozen --extra test --extra ml4t` reproduces the committed development dependency lock; optional bridges remain independent consumers.
