# Pre-ship verification (2026-10-14)

Repository: `hzminhzz/trading-connector`  
Branch: `feat/propr-connector`  
Pull request: [#13](https://github.com/hzminhzz/trading-connector/pull/13)  
Milestone: [Propr Connector](https://github.com/hzminhzz/trading-connector/milestone/1)

## Verified evidence

- Local working tree was clean at the start of this audit; branch head was `6a99d95236ab776ed918be0b53d056baf1c82c53`.
- `python -m pytest -q connectors/propr/tests`: **69 passed, 16 skipped** with the baseline Python environment on the VPS.
- `uv run --no-sync ruff check .`: **all checks passed**.
- GitHub reported three successful checks for the branch head: two connector test jobs and GitGuardian.
- The existing PR report records an additional ML4T-enabled run with 89 passing tests; that ML4T run was **not independently repeated** in this verification.
- `uv run --no-sync pytest -q` did not work in a newly created uninstalled virtualenv (imports failed); `uv run --no-sync python -m pytest -q` failed because pytest was not installed in that environment. The repository's CI explicitly uses `python -m pip install -e ".[test]"` then `python -m pytest`. Do not interpret the uninstalled environment as an underlying connector regression.

## Readiness

| Gate | Verified state |
| --- | --- |
| #1 Contract and identity baseline | Source/fixture-level implementation exists; no authenticated provider schema verification |
| #2 Beta environment | Not documented as definitely simulated; implementation remains safely fail-closed |
| #3-#4 Read-only Beta account/inventory | Mock tests only; authenticated account evidence missing |
| #5-#7 Remote order lifecycle and WebSocket | Local simulation/fixtures only |
| #8-#9 Shadow and ML4T | Local integration reported; no sustained authenticated live-data operation |
| #10 Remote Beta order tests | BLOCKED by authoritative simulation proof, selected account, and scoped test approval |
| #11 Live prop-rule and emergency controls | Synthetic drills, not live-qualified |
| #12 Four-week paper/live release gate | NOT MET |

## Ship decision

**NO-GO.** Preserve PR #13 as draft and leave milestone open. Do not add `Closes` footers to tickets whose acceptance criteria depend on unavailable provider evidence. Do not submit Beta/production orders or change leverage to force qualification.

## Next actions

1. Establish the Beta account ID through authenticated read-only discovery without leaking credentials; preserve all account data privately.
2. Obtain a clear authoritative statement of Beta account simulation and financial consequences.
3. Perform account and WebSocket read-only checks, then verify market/feed continuity and sustained shadow sessions.
4. Request new, *scoped* order-test permission only after Beta simulation is proven.
5. Accumulate the paper duration required by the plan; then rerun all issue criteria and ship gates.

This file records what was measured, not a claim that the release is complete.
