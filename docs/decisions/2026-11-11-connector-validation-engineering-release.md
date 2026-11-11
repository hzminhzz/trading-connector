# Connector validation program — engineering release decision

Date: 2026-11-11. Parent: [trading-connector #22](https://github.com/hzminhzz/trading-connector/issues/22). Scope: provider-boundary engineering only, no live trading authority.

## Decision and exact revisions

**Accepted provider seams: ENGINEERING_VERIFIED and merged.** Connector PR [#32](https://github.com/hzminhzz/trading-connector/pull/32) merged at `129697e547e06056e2f6fc977d91d3358077c915` (replaces `5f7664b51988e9f48b3ff3a07876a136c2d97de8` for this program). Propr, Bybit and Hyperliquid only.

**Guardian compatibility pin: ENGINEERING_VERIFIED and merged.** Guardian PR [#46](https://github.com/hzminhzz/prop-production/pull/46) merged to `feat/account-guardian-program` at `2cfb70e282bfdf5f7a874db8028003480c970d3b` (parent `ffbd3a1a44ed4c41938df7089f68f226b91eac5d`). All three pin surfaces (`compatibility.json`, `pyproject.toml`, `uv.lock`) resolve connector `129697e5`, previously `2db29b50af301741821781dedf64e4d8194fc125`. The original copytrade-pm research pin and Guardian v1 evidence schemas are unchanged.

**Optional provider drafts: DRAFT_VERIFIED_LOCALLY, NOT INTEGRATED.** The MyFundedPerps/Vanta implementations and comparison tests remain in a separate, untracked DevSpace worktree; they were not imported into the installed wheel, published to an accepted branch, pinned by Guardian or promoted to executable provider access. These local tests are developmental evidence, not provider qualification. Original dirty/untracked user work was not changed.

**Pydantic decision: KEEP STANDARD LIBRARY** for the corrected policy seam, **DEFER** any separate future seam until it demonstrates materially better correctness and exact-numeric/provenance handling. Do not introduce Pydantic as a mandatory runtime dependency.

**Deployment: NOT DONE. Running revision: NOT OBSERVED. Trading authorization: NO-GO.** Synthetic tests do not authorize remote mutations, establish financial readiness or validate net trading economics.

## Ticket ledger

| Issue | Outcome | Evidence | Unresolved |
| --- | --- | --- | --- |
| #23 Propr evidence | MERGED_ENGINEERING_VERIFIED | PR #32; strict native enums, side dataclass invariants, reduceOnly, account echo and uncertainty | External provider historical cutoff remains UNKNOWN by design |
| #24 Propr exact decimals | MERGED_ENGINEERING_VERIFIED | PR #32; Decimal-first JSON token parse and high-precision account arithmetic; malformed number tests | Financial interpretation still provider-specific |
| #25 Bybit | MERGED_ENGINEERING_VERIFIED | PR #32; missing finance/positions/order states reject, no inferred cash; strict DEMO path retained | Generic ML4T DEMO cash preflight must remain blocked until a separately attested cash contract exists |
| #26 Hyperliquid | MERGED_ENGINEERING_VERIFIED | PR #32; native symbols, no duplicate alias inventory, no mixed-currency cash, strict state | No venue-authenticated live reconciliation authorized |
| #27 MyFundedPerps | DRAFT_VERIFIED_LOCAL | 117 non-Pydantic draft tests under Python 3.13, strict policy flags and pagination | Draft unpublished; independent provider docs/credentialed proof not completed |
| #28 Vanta | DRAFT_VERIFIED_LOCAL | 86 synthetic draft tests under Python 3.13; signed GET/POST fixture HMAC, exact Decimal JSON numbers, Unicode nested encoding, dispatch-denial | Independent live documentation verification and credentialed read-only validation unavailable; dispatch stays denied |
| #29 Pydantic pilot | PILOT_VERIFIED_LOCAL — KEEP STDLIB | 48 pilot tests under Python 3.13, pinned Pydantic 2.12.5 / pydantic-core 2.41.5 | Pilot deliberately not a package dependency or public API |
| #30 Guardian | MERGED_ENGINEERING_VERIFIED | PR #46; independent exact pinned-package 3.13/3.14 tests and lockfile consistency | Operational rollout not performed |
| #31 Final integration | ACCEPTED_SEAMS_VERIFIED; OTHER GAPS OPEN | This record; merged code and tested pin, draft isolation, rollback below | Final provider-draft scientific/operational qualification remains explicitly out of scope |

## Executable test receipts

Connector accepted revision `129697e5`, clean checkout and synthetic mocks:

```sh
uv run --python 3.13 --no-project --with ccxt==4.5.74 --with pytest==8.4.2 --with httpx --with python-dotenv --with ml4t-live==0.1.2 python -m pytest -q connectors
uv run --python 3.14 --no-project --with ccxt==4.5.74 --with pytest==8.4.2 --with httpx --with python-dotenv --with ml4t-live==0.1.2 python -m pytest -q connectors
uv run --no-project --with ruff==0.14.3 ruff check connectors propr_sdk.py
uv build --offline
```

Connector tests: **390 passed / 4 skipped** on each Python 3.13 and 3.14. Ruff passed and wheel/sdist build succeeded. Skips were reported rather than treated as passes.

Guardian new lockfile with `uv --frozen --extra connector` in the isolated pin worktree:

```sh
uv run --python 3.13 --frozen --extra connector --group dev pytest -q
uv run --python 3.14 --frozen --extra connector --group dev pytest -q
uv run --python 3.13 --frozen --group dev ruff check src tests
uv run --python 3.13 --frozen --group dev mypy
uv run --python 3.14 --frozen --group dev ruff check src tests
uv run --python 3.14 --frozen --group dev mypy
uv lock --check
```

Guardian tests: **199 passed / 6 skipped** per version. Ruff, mypy, locked resolution and `compatibility.py --check-project` passed. CCXT 4.5.74 is pinned by Guardian.

Isolated draft execution:

```sh
uv run --python 3.13 --no-project --with pytest==8.4.2 --with httpx --with websockets --with python-dotenv --with pydantic==2.12.5 python -m pytest -q connectors/myfundedperpetuals/tests connectors/vanta/tests
```

**251 passed** per Python version on 3.11, 3.12, 3.13 and 3.14. Separately on 3.13: MyFundedPerps standard-library tests 117 pass, Vanta tests 86 pass, Pydantic pilot 48 pass. Draft Ruff passes. Results are synthetics; exact vendor signatures are only established relative to existing checked-in fixture vectors, not independently verified provider network authority.

## Pydantic v2 comparison (one synthetic representative seam)

Pydantic 2.12.5; pydantic-core 2.41.5; 3000 measured policy validations in Python 3.13 with tracemalloc. One run produced corrected stdlib **18.772 µs/call, 1152 peak measured bytes** vs Pydantic **7.613 µs/call, 1280 peak bytes**; other same-machine runs varied. Cold import example: stdlib policy module 145.585 ms / 3,095,694 bytes peak vs Pydantic module 201.127 ms / 3,666,761 bytes peak. These are *not* production performance benchmarks.

On the exact long JSON fractional-number fixture, `PilotNumeric.model_validate_json` did **not** preserve all source digits, while `json.loads(..., parse_float=Decimal)` followed by `model_validate` **did**. Pydantic `model_dump_json()` serialized Decimal as a **quoted string**, unsuitable as a provider contract requiring JSON numbers without custom serialization. Correctness and existing public dataclass interfaces gain no necessary Pydantic improvement, despite faster validation in the sampled microbenchmark.

## Safety and rollback contract

- No live/demo orders or cancellations, account-wide flattening, leverage mutation, provider credential reads or account changes were executed by this program.
- Guardian alone owns risk permits, protected finance, fixed-k sizing, intent IDs, durable journal and history. Revisions to native connector parsing cannot grant trading authorization.
- Preserve unknown Propr ledger cash, provider history cutoff and non-authoritative paginated inventory; Bybit Demo `available_usd` is *buying power*, never a substitute for ledger cash in a generic ML4T `get_cash_async()` result. Consequently its generic preflight can refuse to run; do not bypass that refusal.
- Provider IDs are opaque and case-preserved; unknown statuses and malformed values reject rather than becoming PENDING, flat, safe zero or trusted account identity.
- To roll back **without rewinding journal state**, pin Guardian's connector SHA in all three surfaces back to `2db29b50af301741821781dedf64e4d8194fc125`, run `uv lock`, verify clean tests and release gates, and redeploy only after separate runtime change authorization. The previous Guardian source revision was `ffbd3a1`; its historical manifest/lock is the rollback reference. Do **not** reset Guardian SQLite or production journals, change risk trust thresholds, or activate trading as part of rollback.
- No deployment receipt, running process revision, venue replay, real credentials, full provider documentation or OOS trading evidence was obtained in this mission. All claims are engineering/synthetic only.

## Remaining boundaries

Independent Propr and Vanta provider attestation, live contract interpretation, order-outcome recovery beyond mocked fixtures, account-level cash/equity proof, all missing/pagination provider-history semantics and operational release are separate evidence gates. Maintain NO-GO until explicitly verified. Research comparisons and draft prototype microbenchmarks cannot establish a production SLA or actual executable follower PnL.
