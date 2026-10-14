# Propr — operator runbook (read-only / shadow)

## Startup and binding

1. Review [COMPATIBILITY.md](COMPATIBILITY.md) and [RELEASE_REPORT.md](RELEASE_REPORT.md). Production REST calls are read-only; shadow decisions never reach an order endpoint.
2. Provision `PROPR_LIVE_API_KEY` through a protected secret facility. Do not place credentials in Git, shell history, chat, logs, CLI arguments or fixtures.
3. Either supply an account ID from your Propr dashboard or explicitly request unique-account discovery. Discovery checks funded issuances and active challenge attempts, and refuses zero or multiple matches.
4. Run from the repository root:
   ```bash
   python -m connectors.propr.examples.inspect_account --account-id YOUR_ACCOUNT_ID
   # or, only when the API returns exactly one account:
   python -m connectors.propr.examples.inspect_account --discover-unique
   ```
   Output contains health, source classification, completeness flags and inventory counts; it omits account IDs, balances, positions and credentials.
5. Run the credential-free shadow drill:
   ```bash
   python -m connectors.propr.examples.shadow_demo
   ```
6. Run tests with the installed ML4T environment:
   ```bash
   /home/quant/dev/quant/quant-research/.venv/bin/python -m pytest -q connectors/propr/tests
   ```

## Fail-closed checks

- Never infer paper safety from an account name, endpoint, key prefix or challenge status. `assert_paper_trading()` and `assert_live_trading()` refuse.
- Ambiguous account discovery, financial-field gaps, account mismatches, unsupported symbols, missing precision metadata, hedge positions, stale market data or incomplete reconciliation stop the session.
- Treat WebSocket disconnects, malformed/duplicate/out-of-order events and REST snapshot mismatches as a reconciliation halt. The account stream is not a market-data feed.
- On timeout, preserve an unknown outcome; do not retry with a new intent ID. A cancel response or synthetic reduction ACK is not proof of a terminal order or flattened position.
- No remote order, cancel, replace, leverage change, close or flatten is implemented.

## Incident and rollback

1. Stop strategy submissions and latch a local halt. Isolate credentials if compromise is suspected.
2. Preserve redacted fault timing and decision evidence; never log credentials, response bodies or private account data.
3. Use only read-only GET requests to reconcile paginated orders, native long/short positions and fills. A failed or inconsistent read is not recovery.
4. Synthetic kill drills use only `FakeLifecycleGateway`. For actual positions, use the provider UI and independently reconcile state.
5. Reset requires explicit manual approval and reconciled state; never reset automatically after reconnect.
6. For key rotation, revoke and rotate via Propr Settings/secret storage, restart the read-only process and rebind the account.

## Operational readiness

These controls are **not provisioned** for this read-only/shadow candidate:

- **Risk thresholds:** no numeric limits are approved while the challenge rules and account consequences remain unverified. Before any execution review, record account-specific maximum notional, leverage, daily loss and drawdown limits from authoritative rules, plus the approved local caps.
- **Alarms:** no delivery channel is configured. Any future deployment must test alerts for stale/disconnected WebSocket, unknown order outcome, failed reconciliation, risk halt and daily-loss/reset events.
- **Kill switch:** current drills are synthetic. No live cancel/reduce/flatten control exists in this connector.
- **Cancel/flatten:** if an incident occurs with real positions, stop submissions, use the provider UI for the explicitly selected account, then verify orders, positions and fills through read-only reconciliation. Do not claim flattening until the provider state confirms it.

## Execution gate

Challenge-account paper semantics and real-money/evaluation consequences are not independently established. Do not submit orders or change leverage. Live execution additionally requires verified account rules, tested limits/recovery, a four-week paper record, explicit scoped authorization and a separate release decision.
