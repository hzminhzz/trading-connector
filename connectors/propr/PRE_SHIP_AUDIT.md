# Pre-ship verification (2026-10-14)

Repository: `hzminhzz/trading-connector`  
Branch: `feat/propr-connector`  
Pull request: [#13](https://github.com/hzminhzz/trading-connector/pull/13)  
Milestone: [Propr Connector](https://github.com/hzminhzz/trading-connector/milestone/1)

## Verified evidence

- `/home/quant/dev/quant/quant-research/.venv/bin/python -m pytest -q connectors/propr/tests`: **106 passed**.
- `uv run --no-sync ruff check connectors/propr`: **all checks passed**.
- Baseline connector suite without ML4T: **83 passed, 16 skipped**.
- `uv build --offline`: built source distribution and wheel.
- `connectors.propr.examples.shadow_demo`: valid synthetic intent accepted; below-minimum quantity rejected.
- Authenticated production read-only smoke: health/core `OK`, unique active challenge account bound, financial fields complete, orders **0**, positions **0**, trades **0**. No account ID, balance or credential was printed or committed.
- Actual `CryptoTSMOMRegimeStrategy` from `dev/quant/quant-research` ran through ML4T 0.1.2 `LiveEngine` using `ML4TStrategyAdapter`; one synthetic target intent was accepted by the shadow gateway. The finite smoke feed ended in `feed_terminated` degraded state. No remote order endpoint was invoked.
- Review findings fixed: WebSocket credentials require the exact Propr production endpoint; canonical reductions resolve exact native position IDs; shadow journals retain those IDs; fake lifecycle tests cover per-order batch outcomes, cumulative fills and same-ID restart retries.
- Public provider wording does not establish that this challenge account is simulated-only or free of monetary/evaluation consequences. The paper assertion and every remote mutation remain blocked.
- GitHub-hosted checks must be rechecked after the final push.

## Issue disposition

| Issues | Evidence / disposition |
| --- | --- |
| #1 | Pinned Propr REST/WS contracts and inspected installed ML4T interface; account identity is explicit or uniquely discovered |
| #2 | Account source is identified as an active challenge; paper safety remains **UNVERIFIED**, and mutation APIs are unavailable |
| #3–#4 | Authenticated read-only account and inventory checks passed; zero current orders, positions and trades |
| #5–#7 | Idempotency, race, reconciliation, WebSocket fault and restart behavior remain synthetic-test-only |
| #8–#9 | Shadow gateway and unchanged project strategy run through ML4T `LiveEngine`; no remote order mutation |
| #10 | Safety proof is unresolved; execution refuses, with mock/shadow qualification retained |
| #11 | Risk and kill-switch tests are synthetic; no live controls are claimed |
| #12 | Four-week paper evidence is **NOT MET**; report remains **NO-GO for execution** |

## Ship decision

Merge only as a read-only/synthetic-shadow connector candidate after final CI checks pass. This is not approval to place orders, change leverage or deploy a live trading process. Close issues only with their stated blocked/NO-GO dispositions where external safety or duration evidence is unavailable.

## External execution blockers

1. Obtain authoritative Propr account-class, order-routing, fee and evaluation/funded-balance consequences for this challenge.
2. Accumulate and verify the required four-week paper record.
3. Complete provider-specific rules, event-replay, market-price parity and live recovery qualification before any separate execution approval.

## Post-merge regression follow-up

- A post-merge review found cancel/replace could overlook a cumulative partial fill stored in the fake order result when the separate fill accumulator was empty.
- `FakeLifecycleGateway.replace()` now checks both fill sources and blocks replacement after any fill; a regression test exercises a partial result followed by cancellation.
- Follow-up verification: **107 passed** with ML4T; **84 passed, 16 skipped** without ML4T; Ruff and offline package build passed.
- Live execution remains **NO-GO** under the external blockers above.
