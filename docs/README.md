# Quantitative Research & Trading Connector Documentation

This documentation hub provides local, searchable reference materials for coding agents and researchers across the entire quantitative pipeline:

```text
ml4t-data → ml4t-engineer → ml4t-diagnostic → ml4t-backtest → ml4t-live → trading-connector
```

---

## 1. Feature Engineering (`docs/ml4t-engineer/`)
- [`index.md`](ml4t-engineer/index.md): Polars/Numba engine overview and 120 indicator catalog.
- [`features.md`](ml4t-engineer/features.md): `compute_features()` API, fractional differencing (`ffdiff`), and feature catalog discovery.
- [`dataset-builder.md`](ml4t-engineer/dataset-builder.md): `create_dataset_builder`, leak-free scaling, and cross-validation folding.

## 2. Statistical Diagnostics & Validation (`docs/ml4t-diagnostic/`)
- [`index.md`](ml4t-diagnostic/index.md): Four-tier validation framework overview.
- [`statistical-tests.md`](ml4t-diagnostic/statistical-tests.md): Deflated Sharpe Ratio (DSR), Probability of Backtest Overfitting (PBO), Benjamini-Hochberg FDR, and HAC-adjusted IC.
- [`backtest-tearsheets.md`](ml4t-diagnostic/backtest-tearsheets.md): Standalone Plotly HTML tearsheet generation (`quant_trader`, `hedge_fund`, `risk_manager`, `full`).

## 3. Simulation & Execution Backtesting (`docs/ml4t-backtest/`)
- [`index.md`](ml4t-backtest/index.md): Event-driven engine overview, VectorBT/Backtrader/Zipline/LEAN parity validation.
- [`strategies.md`](ml4t-backtest/strategies.md): `Strategy` callback interface (`on_start`, `on_data`, `on_end`), broker methods, bracket orders, rebalancing.
- [`risk-management.md`](ml4t-backtest/risk-management.md): Position rules (`StopLoss`, `TakeProfit`, `TrailingStop`, `RuleChain`) and portfolio limits (`MaxDrawdownLimit`, `DailyLossLimit`).

## 4. Live Trading & Pre-Trade Risk (`docs/ml4t-live/`)
- [`INDEX.md`](ml4t-live/INDEX.md): Index of live runtime documentation.
- [`user-guide/risk.md`](ml4t-live/user-guide/risk.md): `SafeBroker` and `LiveRiskConfig` pre-trade controls, kill switch persistence, rate limiting.
- [`user-guide/backtest-to-live.md`](ml4t-live/user-guide/backtest-to-live.md): Migration from backtest to live with `ThreadSafeBrokerWrapper`.
- [`user-guide/brokers.md`](ml4t-live/user-guide/brokers.md): `AsyncBrokerProtocol` contract specification.
- [`user-guide/feeds.md`](ml4t-live/user-guide/feeds.md): `DataFeedProtocol` and `MarketEvent` streaming.
- [`DESIGN.md`](ml4t-live/DESIGN.md): Internal architecture and lifecycle state invariants.

## 5. Connector Architecture (`docs/architecture.md`)
- Multi-provider gateway architecture, provider isolation boundaries, and fail-closed safety policy.
