# Beta execution-safety decision

**Decision: UNVERIFIED — no remote order mutation authorized.**

As of 2026-10-13, the developer SDK page and Beta endpoints indicate a testing environment, but the inspected material does **not** establish whether Beta orders can reach live venues, incur fees, or affect evaluation/funded balances. No authoritative assurance or account-class attestation has been obtained.

Required independent evidence before changing this decision:

1. Official Propr documentation or a verifiable provider response identifying Beta's actual order-routing destination.
2. Explicit statements covering real-money exposure, fees, funded/evaluation-account effects, and supported account classes.
3. Read-only evidence binding the selected account ID to a documented simulated-only account class.
4. A separate scoped human approval for simulated-order qualification.

Until then `BetaEvidence.assert_simulated()` and `ProprConfig.assert_paper_trading()` always raise. Real HTTP mutations remain blocked. **No test order, cancel, leverage change, or close was sent.**
