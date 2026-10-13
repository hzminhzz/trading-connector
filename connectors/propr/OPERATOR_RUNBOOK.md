# Propr — operator runbook (read-only / shadow)

## Startup and binding

1. Review [BETA_SAFETY.md](BETA_SAFETY.md), [COMPATIBILITY.md](COMPATIBILITY.md), and [RELEASE_REPORT.md](RELEASE_REPORT.md). The only authorized remote API calls are GET/HEAD/OPTIONS. No deployment can enable remote order placement.
2. Select `PROPR_ENV=beta`; set `PROPR_BETA_API_KEY` through a protected VPS secret facility or process environment (not Git, shell history, chat, logs, `--api-key` CLI argument, or fixture). Use a dedicated `pk_beta_` credential. Keep `PROPR_LIVE_API_KEY` out of the Beta process.
3. Supply an explicit account ID from the Propr dashboard/your own account record. Never default to the first challenge attempt, and never allow a historical account to silently replace a selected one.
4. Run from repository root with the project's Python environment:
   `python -m connectors.propr.examples.beta_readonly_smoke --account-id YOUR_ACCOUNT_ID`
   The command prints only health, account-binding boolean, completeness flags, and inventory counts. No account IDs, PnL, positions or credentials.
5. Run credential-free shadow drill:
   `python -m connectors.propr.examples.shadow_demo`.
6. Run tests with the current installed ML4T environment:
   `/home/quant/dev/quant/quant-research/.venv/bin/python -m pytest -q connectors/propr/tests`.

## Fail-closed checks

* No trade if the user-supplied account ID cannot be uniquely matched to a challenge attempt or if the account's verified financial fields are incomplete.
* Neither Beta nor production has `assert_paper_trading()` or `assert_live_trading()` enabled. Shadow intents cannot invoke a mutation-capable client.
* Treat any WebSocket disconnect, malformed event, heartbeat timeout, duplicate/out-of-order update, or mismatch between REST inventories as a reconciliation halt. The WS account stream is not an independent market-data feed.
* On timeout, write unknown intent state; never generate a fresh order ID and retry blindly. On restart, `staged` is recovered as `unknown`. Check provider order and trade identity before any manual clearing.
* A cancel HTTP 400 does not confirm cancellation. A fake kill-drill reduction ACK never means the position has been flattened.
* Refuse unknown native symbols, missing tick/lot metadata, hedge positions collapsed to one-way, absent prop challenge rules, stale independent prices, unverified margin mode, unavailable maintenance margin, and breached risk floors.

## Incident / kill switch

1. Latch a local halt; stop strategy submissions first. Isolate credentials if compromise is suspected.
2. Record fault class and redacted timing/decision evidence, no API response bodies or headers.
3. Use read-only GET to reconcile *all* paginated orders, native long/short positions and fills against persisted intent IDs. A failed read or inconsistent snapshot is not a successful recovery.
4. For a **synthetic** drill only, `SyntheticKillDrill` can request cancellation/reduction through an isolated `FakeLifecycleGateway`; it records confirmed terminal statuses and unconfirmed reduction ACKs accurately. **There is currently no remote automatic flatten implementation or permission.** For any real position, manual action in the provider UI is required.
5. Operator confirms the account, equity, daily reset, margin/leverage limits and that the cause of the halt is addressed. Reset requires explicit manual approval **and** reconciled state; never reset automatically on network reconnect.
6. If key rotation is needed: revoke at Propr Settings, rotate in the secret store, restart the read-only process, rebind the account and verify snapshots. Never reuse a production key for Beta.

## Rollback and production gate

* Stop the read-only/shadow session, revoke or rotate the Beta credential if necessary; code checkout rollback to last reviewed SHA is independent of provider state. There are no automatic live orders to reverse.
* Production requires documented Beta isolation, separately approved remote Beta qualification, full WS/reconciliation testing, verified challenge daily-loss/reset rules, quote-versus-execution parity, a tested live-capable safe flatten/recovery path, at least **four weeks of paper results**, and fresh explicit human authorization. Until every gate is proven: **NO-GO**.
