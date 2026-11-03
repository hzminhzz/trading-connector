# Bybit Demo PM canary — genuine V5 order/fill diagnostic

Date: **2026-11-03 UTC**. Tested against **Bybit Demo Trading** (`api-demo.bybit.com`), not Testnet and not a third-party prop challenge.

## Purpose and scope

This is an explicitly opted-in, **single-run diagnostic** from the real `copytrade-pm` Combo 2 **sizing engine** through the guarded Bybit instrument bridge and `BybitLiveBroker` / installed `ml4t-live` `SafeBroker` to actual Bybit **Demo** market orders. The source input is **SYNTHETIC**, not a real W028 wallet receipt; the 300-second synthetic PM chronology is not a real elapsed first-known wait. No wallet/venue economics, eligibility, P1/P14, or live automation is proven.

The runner operates only with an explicitly configured, authenticated Demo UID and dedicated Demo key. It refuses Testnet/mainnet, nonempty positions, nonempty open orders, insufficient equity, invalid quotes (and stale exchange timestamps when available), unlisted XRP linear perpetual, mismatched authenticated key or UID, a read-only API key, and unsupported PM or broker target. Bybit's public XRP ticker did not expose an exchange timestamp in the verified run; the runner refreshes its quote before submission but cannot independently prove quote age without that timestamp. Only one account-bound claim is allowed per UTC day, by atomic private claim file. The existing `bybit-demo.env` file stays in `read_only` mode; the `--execute` path selects execution **only in process** after independently confirming Demo identity.

## Runbook

No local key copies are required. Use the existing private credentials at `~/.config/trading-connector/bybit-demo.env`. The optional `copytrade_pm` package must be importable, and the installed `ml4t-live` runtime is required.

```bash
# Read-only plan and account preflight; no order
PYTHONPATH=/path/to/copytrade-pm/src:. python -m connectors.bybit.examples.demo_pm_canary

# Explicit ONE-SHOT authorized Demo buy + reduce-only close
PYTHONPATH=/path/to/copytrade-pm/src:. python -m connectors.bybit.examples.demo_pm_canary --execute

# Separate follow-up: read-only exchange queries and local SQLite settlement
PYTHONPATH=/path/to/copytrade-pm/src:. python -m connectors.bybit.examples.demo_pm_canary \
  --verify-audit ~/.local/share/copytrade/bybit-demo-pm-canary/DATE-HASH-attempt
```

Every successful `--execute` produces an audit under `~/.local/share/copytrade/bybit-demo-pm-canary/` with a private report JSON, an owner-private intent SQLite WAL, and an installed `SafeBroker` risk state plus journal. Do not commit or paste credentials, UID, raw account snapshots, or these files. There is no auto retry for an unknown order outcome. If a session HALTs, independently reconcile V5 positions, executions and open orders before any future order. Never allow the diagnostic to flatten someone else's position or submit an uncapped order.

## Verified one-shot execution (2026-11-03)

- Authenticated Demo account: endpoint, UID/key match and unified account independently verified; key is writable, but checked-in configuration remains read-only. Opening inventory: **0 positions, 0 pending orders**.
- Synthetic W028 leader book passed through the **actual** `Combo2PaperTracker` capital scaling and gross caps, then `shadow_target_preview`. Per-manager gross and market-step rounding stayed bounded.
- Venue: `XRP/USDT:USDT`, target **12 XRP** at about **$14.99** in reference notional, under the hard **$25** risk cap.
- Opening order: **12 XRP filled**. Confirmation came from the exchange's separate V5 execution endpoint (one execution with a real execution ID), not only the returned order acknowledgement.
- Reduce-only close: **12 XRP filled**, independently observed through V5 executions.
- Final authenticated account read: **0 open positions, 0 pending orders**.
- Fresh independent process: read-only reconnection re-queried both order IDs and verified 12 XRP each. **2** journal intents reconciled to `filled` from previously `submitted`; **0** new remote mutations on this verification pass.
- The local audit path is outside Git and persists with a single-used claim preventing unintended repeated test orders.
- Focused runtime tests validate the synthetic PM contract, bounded Bybit target, exchange preflight, duplicate claim, fill-accounting matching, and environment isolation.
- PR review hardening: V5 execution reconciliation now follows all bounded cursor pages and fails closed on repeated cursors, duplicate/missing execution IDs, or incomplete pagination. The previously recorded Demo fills were revalidated via a fresh read-only connection with the improved pagination logic; the account remained flat and both journal intents remained `filled`. The bridge also retains explicit zero/flat manager targets rather than silently dropping them. The compatible full connector test suite passed **288 tests** with three non-blocking warnings.

**Qualification:** ENGINEERING VERIFIED — genuine Bybit Demo transport/open/fill/close and reconciliation. **NOT verified**: a new genuine wallet event, point-in-time source equity, a real elapsed T+300 batch, deployment, automated strategy execution, Bybit-linked prop challenge account rules, sustained fills, net-execution economics, or scientific alpha.

Test contract is intentionally narrower than production: the synthetic source report contains fields required by the PM fixture but must never be ingested as a real Hyperliquid wallet receipt or funding/equity evidence.
