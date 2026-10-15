# Hyperliquid Trading Connector & ML4T Live Broker

Dedicated connector integrating Hyperliquid DEX via `ccxt.async_support` and `ccxt.pro` with `ml4t-live`.

## Capabilities

1. **Async REST Transport:** `HyperliquidClient` wrapping `ccxt.async_support.hyperliquid` with cross-margin support, rate limiting, and structured error mapping.
2. **ML4T AsyncBrokerProtocol Adapter:** `HyperliquidLiveBroker` implementing full order lifecycle (market, limit, stop), atomic order amendment (`replace_order_async`), position tracking, and account value queries.
3. **Durable Intent Journaling & Reconciliation:** `HyperliquidIntentJournal` staging orders in SQLite WAL before network transmission, failing closed on ambiguity.
4. **Real-time Market Data Feed:** `HyperliquidKlineFeed` conforming to `DataFeedProtocol`, streaming completed 1m bars via WebSocket (`watch_ohlcv`).
5. **Pre-Trade Risk Integration:** Fully verified with `ml4t.live.safety.SafeBroker` and `LiveRiskConfig` for shadow mode, drawdown limits, and max position size gates.

## Quickstart

### Configuration (.env)
```bash
HYPERLIQUID_WALLET_ADDRESS=0xYourWalletAddress
HYPERLIQUID_PRIVATE_KEY=0xYourPrivateKeyOrApiWalletKey
HYPERLIQUID_TESTNET=true
HYPERLIQUID_MODE=read_only
```

### Inspection (Read-Only)
```bash
python -m connectors.hyperliquid.examples.inspect_account
```

### Shadow Mode Drill
```bash
python -m connectors.hyperliquid.examples.shadow_demo
```
