# Bybit Trading Connector & ML4T Live Broker

Dedicated connector integrating Bybit V5 Unified Trading Account via `ccxt.async_support` and `ccxt.pro` with `ml4t-live`.

## Capabilities

1. **Async REST Transport:** `BybitClient` wrapping `ccxt.async_support.bybit` with rate limiting, unified margin support, and error translation.
2. **ML4T AsyncBrokerProtocol Adapter:** `BybitLiveBroker` implementing full order lifecycle (market, limit, stop), atomic order amendment (`replace_order_async`), position tracking, and account value queries.
3. **Real-time Market Data Feed:** `BybitKlineFeed` conforming to `DataFeedProtocol`, streaming completed 1m bars via WebSocket (`watch_ohlcv`).
4. **Pre-Trade Risk Integration:** Fully verified with `ml4t.live.safety.SafeBroker` and `LiveRiskConfig` for shadow mode, drawdown limits, and max position size gates.

## Quickstart

### Configuration (.env)
```bash
BYBIT_API_KEY=your_key_here
BYBIT_API_SECRET=your_secret_here
BYBIT_ENVIRONMENT=testnet  # mainnet | testnet | demo
BYBIT_MODE=read_only
```

### Bybit Demo Trading (distinct from Testnet)

Use dedicated API keys created in Bybit Demo Trading. The connector uses the existing CCXT native `enable_demo_trading(True)` for Demo REST (`api-demo.bybit.com`); it does not select sandbox/Testnet. Use distinct credentials:

```bash
BYBIT_ENVIRONMENT=demo
BYBIT_DEMO_API_KEY=your_demo_key
BYBIT_DEMO_API_SECRET=your_demo_secret
BYBIT_DEMO_EXPECTED_UID=the_uid_seen_in_demo_trading_account
BYBIT_MODE=read_only
```

For simulated orders, `BYBIT_MODE=execution` must be configured on a verified Demo account with ML4T `LiveRiskConfig(execution_mode="paper")` and `SafeBroker`. No orders are placed by this example. The legacy `BYBIT_TESTNET` flag remains supported when `BYBIT_ENVIRONMENT` is unset; an explicit environment takes precedence.

Demo market data comes from Bybit mainnet. `BybitKlineFeed` builds a separate public mainnet CCXT Pro feed. Private Demo WebSocket uses `stream-demo.bybit.com`; Demo WebSocket order entry is unsupported, so order commands remain REST.

### Native Demo Account Preflight (Read-Only, Issue #44)

The operator command belongs to the research runtime, not this provider library:
`/home/quant/dev/quant/quant-research/docs/bybit_demo_preflight.md`
(on the current VPS checkouts). Use the installed `ml4t-live==0.1.2` environment
of `quant-research`. The operator must supply dedicated Demo credentials and
an independently observed Demo UID; without them the result is `BLOCKED`.
The preflight calls native `SafeBroker.preflight_async()`, makes only
Demo-authenticated GET requests, never submits or cancels orders, reports
redacted identity and reconciliation evidence, and refuses mismatched native
risk/persistence state. All account evidence stays outside source repositories.

Demo UNIFIED equity uses the V5 account-wide USD fields, not a coin balance
fallback. All supported linear USDT/USDC swap positions/orders are inspected;
unsupported active instrument categories, hedge/conditional orders or broken
V5 fields fail closed. Demo private REST is `api-demo.bybit.com`; the public
market feed is separate Mainnet. Testnet cannot substitute for Demo in this
operator command.

### Inspection (Read-Only)
```bash
python -m connectors.bybit.examples.inspect_account
```

### Shadow Mode Drill
```bash
python -m connectors.bybit.examples.shadow_demo
```
