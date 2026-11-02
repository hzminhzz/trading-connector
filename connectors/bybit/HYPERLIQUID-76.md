# Hyperliquid 76-market OHLCV to Bybit — research bridge

Status: **ENGINEERING / SHADOW ONLY**. No Bybit challenge execution authorized, account verified, or live service deployed.

## Ownership and scope

- Canonical 1-minute Parquet is owned by `ml4t-data`, at `/home/quant/dev/quant/ml4t-data/.data/store`; this connector does not copy, rewrite, or update it.
- `hyperliquid_76.json` freezes the 2026-10-30 CHD cohort: **50 default perps + 26 `xyz:` builder perps**. Existing additional stores are deliberately excluded.
- `hyperliquid_portfolio.py`: `load_registry`, `inventory`, `read_historical_minute`, `coverage`, `shadow_target_preview`.
- Example research input: `read_historical_minute(store_root, "BTC", minute, decision_at=...)`; the returned `observed_at=None` is deliberate. Archived market timestamps **cannot** be promoted to live first-receipt timestamps. A completed bar becomes theoretically available after its minute closes, but Parquet does not prove actual feed latency.
- Bybit mapping is **not a ticker-string alias**. Standard names are candidates only when exact active Bybit USDT linear perpetual contract metadata, trading status, lot/price precision, and minimum notional are present. All 26 `xyz:` instruments are blocked because HIP-3 builder exposures are not economically interchangeable with similarly named Bybit symbols.
- `shadow_target_preview` accepts **already sized, externally reconciled** manager-target USD notionals keyed by exact source instrument. It requires **Bybit reference marks**, rounds sizes down, preserves gross caps, skips unlisted instruments, and emits **no broker orders**. It does not reconstruct wallet NAVs, execute the portfolio, or approve a challenge.

## Operator read-only checks

```bash
cd /home/quant/dev/trading-connector
python -m pytest -q connectors/bybit/tests/test_hyperliquid_portfolio.py

# In the tested ML4T venv, from an isolated worktree or installed connector:
PYTHONPATH=. /home/quant/dev/quant/quant-research/.venv/bin/python -c 'from pathlib import Path; from connectors.bybit.hyperliquid_portfolio import inventory; print(inventory(Path("/home/quant/dev/quant/ml4t-data/.data/store")))'
```

The above inventory checks store directories. It does **not** assert continuous 1m history, end-to-end live freshness, independent bar acceptance, Bybit listing, or Bybit challenge permission. The single-minute reader independently validates path, provenance status, UTC timestamps, symbol, and OHLCV bounds. Historical bars use native **Hyperliquid** prices, not simulated Bybit execution quotes.

A prospective Bybit bridge requires a genuine as-of `copytrade-pm` portfolio target authority; the original PM-03/Combo2 ledger is research/shadow-only. Validate full Hyperliquid wallet account and subaccounts, source NAV, signed positions, T+300 batching, and per-sleeve attribution before translating any target. Hold the original position-intent logic in `copytrade-pm` and provider-specific broker protocol in this connector.

Before **challenge-account** integration, obtain the actual venue/platform challenge contract, authenticated account UID and environment, tradable symbol allowlist, leverage/margin/rule thresholds, fee/funding/session terms, and eligibility of API/automated trading. A standard Bybit Demo or Testnet account is **not** by itself evidence of access to a particular third-party Bybit-linked prop challenge. Use read-only account preflight and simulated order transport before considering any real mutation. Existing Bybit connector default `READ_ONLY` remains unchanged.

## Verified 2026-11-03 on the VPS

- `inventory`: 76/76 registered store directories exist (50 standard, 26 builder).
- Historical BTC bar `2026-11-02 14:59 UTC`: parsed from canonical Parquet with CHD `source.json` provenance; no invented observed/received time.
- Connector unit tests were run in the installed `quant-research` Python environment. No authenticated Bybit API call, order submission, or challenge-account verification performed.

**Classification**: DATA PATH verified for sampled historical BTC; MAPPING CONTRACT and SHADOW TARGET PREVIEW engineering tested; FULL DATA QUALITY not re-audited; CHALLENGE ELIGIBILITY unknown; PROSPECTIVE SIGNAL→BYBIT FILL not verified; SCIENTIFICALLY QUALIFIED no; DEPLOYED no.
