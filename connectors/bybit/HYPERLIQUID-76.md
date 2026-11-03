# Hyperliquid 76-market OHLCV to Bybit — research bridge

Status: **ENGINEERING / SHADOW ONLY**. Bybit Demo execution has been separately verified with a synthetic PM test, but no Bybit challenge account or unattended trading deployment has been verified.

## Ownership and scope

- Canonical 1-minute Parquet is owned by `ml4t-data`, at `/home/quant/dev/quant/ml4t-data/.data/store`; this connector does not copy, rewrite, or update it.
- `hyperliquid_76.json` freezes the 2026-10-30 CHD cohort: **50 default perps + 26 `xyz:` builder perps**. Existing additional stores are deliberately excluded.
- `hyperliquid_portfolio.py`: `load_registry`, `inventory`, `read_historical_minute`, `coverage`, `shadow_target_preview`.
- Example research input: `read_historical_minute(store_root, "BTC", minute, decision_at=...)`; the returned `observed_at=None` is deliberate. Archived market timestamps **cannot** be promoted to live first-receipt timestamps. A completed bar becomes theoretically available after its minute closes, but Parquet does not prove actual feed latency.
- Bybit mapping is **not a ticker-string alias**. Standard names are candidates only when exact active Bybit USDT linear perpetual contract metadata, trading status, lot/price precision, and minimum notional are present. All 26 `xyz:` instruments are blocked because HIP-3 builder exposures are not economically interchangeable with similarly named Bybit symbols.
- `shadow_target_preview` accepts the **original Combo 2 `PAPER_REBALANCE` report**, specifically `allowed[manager][source_instrument]` **signed Hyperliquid base quantities** produced by `copytrade-pm` after leader-equity sizing, the $1,250 coin cap and the $5,000 portfolio cap. It rejects flattened net-only inputs; the current contract covers exactly W028, CA-008 and W015, **not** the later P1/P14 research rosters. It requires the **same Hyperliquid source marks used by the PM** to verify gross notional **per manager before netting**, then converts signed **net USD notionals** to Bybit target quantities using separate Bybit marks and step sizes. The result is explicitly **target positions, NOT order deltas**: it cannot infer broker inventory or make opening/closing decisions. An explicitly zeroed manager position remains an explicit **flat target**, rather than disappearing from the target book. It blocks any unsupported/missing mapped legs (including HIP-3), any gross-cap violation, partial PM book, or PM risk freeze instead of silently proposing a subset. It emits **no broker orders** and does not approve a challenge.

## Operator read-only checks

```bash
cd /home/quant/dev/trading-connector
python -m pytest -q connectors/bybit/tests/test_hyperliquid_portfolio.py

# With copytrade-pm's clean, current checked-out source available:
PYTHONPATH="$COPYTRADE_PM_ROOT/src:." python -m pytest -q connectors/bybit/tests/test_hyperliquid_pm_sizing_integration.py

# In the tested ML4T venv, from an isolated worktree or installed connector:
PYTHONPATH=. /home/quant/dev/quant/quant-research/.venv/bin/python -c 'from pathlib import Path; from connectors.bybit.hyperliquid_portfolio import inventory; print(inventory(Path("/home/quant/dev/quant/ml4t-data/.data/store")))'
```

The above inventory checks store directories. It does **not** assert continuous 1m history, end-to-end live freshness, independent bar acceptance, Bybit listing, or Bybit challenge permission. The single-minute reader independently validates path, provenance status, UTC timestamps, symbol, and OHLCV bounds. Historical bars use native **Hyperliquid** prices, not simulated Bybit execution quotes. The `source_marks` argument to the portfolio preview must reproduce the **frozen marks from that PM report**; this shadow function does not independently authenticate them or replay the ledger.

A prospective Bybit bridge requires a genuine as-of `copytrade-pm` portfolio target authority; the original PM-03/Combo2 ledger is research/shadow-only. Validate full Hyperliquid wallet account and subaccounts, source NAV, signed positions, T+300 batching, and per-sleeve attribution before translating any target. Hold the original position-intent logic in `copytrade-pm` and provider-specific broker protocol in this connector.

Before **challenge-account** integration, obtain the actual venue/platform challenge contract, authenticated account UID and environment, tradable symbol allowlist, leverage/margin/rule thresholds, fee/funding/session terms, and eligibility of API/automated trading. A standard Bybit Demo or Testnet account is **not** by itself evidence of access to a particular third-party Bybit-linked prop challenge. Use read-only account preflight and simulated order transport before considering any real mutation. Existing Bybit connector default `READ_ONLY` remains unchanged.

## Verified 2026-11-03 on the VPS

- `inventory`: 76/76 registered store directories exist (50 standard, 26 builder).
- Historical BTC bar `2026-11-02 14:59 UTC`: parsed from canonical Parquet with CHD `source.json` provenance; no invented observed/received time.
- Independent sizing review compares the actual `copytrade-pm` `Combo2PaperTracker` outputs to Bybit shadow targets: with $100k leader equity and $300k signed BTC exposure, PM gives $3,000 desired, clamps to $1,250, and Bybit $80k mark + 0.001 lot maps to 0.015 BTC/$1,200 target (the $50 rounding residual is explicit). Opposing $625 long/$625 short produces $1,250 **pre-net** gross and a zero-position target. No Bybit order is inferred from either result.
- VPS review: **89 Bybit tests passed** (including **2 real PM sizing integration cases**), and the separate clean `copytrade-pm/main` tree passed **483 core tests**, including its **15 focused Combo 2 tests**. Changed-file Ruff lint and format checks passed. The base PM main has fewer tests than its still-unmerged PR #26 branch; these counts must not be confused with the previously reported 510.
- This bridge module makes **no authenticated Bybit API calls** itself. The separate bounded synthetic-PM Demo diagnostic **did** verify real order submission, execution reconciliation, and flattening; see [DEMO-PM-E2E.md](DEMO-PM-E2E.md). A fresh review using the compatible `copytrade-pm` branch verified **288 connector tests passed**, including all Bybit and Propr connector tests. Challenge-account verification remains outstanding.

**Classification**: DATA PATH verified for sampled historical BTC; MAPPING CONTRACT and SHADOW TARGET PREVIEW engineering tested; FULL DATA QUALITY not re-audited; CHALLENGE ELIGIBILITY unknown; PROSPECTIVE SIGNAL→BYBIT FILL not verified; SCIENTIFICALLY QUALIFIED no; DEPLOYED no.
