"""Async CCXT client wrapper for Hyperliquid DEX."""
from __future__ import annotations

from typing import Any

import ccxt.async_support as ccxta

from connectors.ccxt_support.client import BaseCCXTClient
from connectors.ccxt_support.models import CCXTErrorSpec
from connectors.common.validation import strict_decimal
from connectors.hyperliquid.config import Environment, HyperliquidConfig
from connectors.hyperliquid.models import (
    HyperliquidAuthenticationError,
    HyperliquidBalance,
    HyperliquidError,
    HyperliquidInsufficientFundsError,
    HyperliquidInvalidOrderError,
    HyperliquidNetworkError,
    HyperliquidOrderData,
    HyperliquidOrderNotFoundError,
    HyperliquidPositionData,
    HyperliquidRateLimitError,
)

_ERROR_SPEC = CCXTErrorSpec(
    provider_label="Hyperliquid",
    base_error=HyperliquidError,
    auth_error=HyperliquidAuthenticationError,
    rate_limit_error=HyperliquidRateLimitError,
    insufficient_funds_error=HyperliquidInsufficientFundsError,
    order_not_found_error=HyperliquidOrderNotFoundError,
    invalid_order_error=HyperliquidInvalidOrderError,
    network_error=HyperliquidNetworkError,
)


class HyperliquidClient(BaseCCXTClient):
    """Fail-closed async Hyperliquid CCXT client wrapper."""

    _balance_cls = HyperliquidBalance
    _position_cls = HyperliquidPositionData
    _order_cls = HyperliquidOrderData
    _cancel_symbol_required_msg = "Hyperliquid DEX requires a symbol to cancel an order"

    def __init__(
        self,
        config: HyperliquidConfig,
        exchange: ccxta.hyperliquid | None = None,
    ) -> None:
        super().__init__(config=config, error_spec=_ERROR_SPEC, exchange=exchange)

    def _build_exchange(self) -> ccxta.hyperliquid:
        options: dict[str, Any] = {
            "enableRateLimit": self.config.enable_rate_limit,
            "timeout": self.config.timeout_ms,
            "options": {"defaultType": self.config.default_type},
        }
        if self.config.wallet_address:
            options["walletAddress"] = self.config.wallet_address
        if self.config.private_key:
            options["privateKey"] = self.config.private_key
        exchange = ccxta.hyperliquid(options)
        if self.config.environment is Environment.TESTNET:
            exchange.set_sandbox_mode(True)
        return exchange

    async def _resolve_order_price(
        self,
        symbol: str,
        order_type: str,
        price: float | None,
    ) -> float | None:
        # Hyperliquid DEX requires a reference price even for market orders to compute slippage bounds.
        if order_type == "market" and price is None:
            ticker = await self.exchange.fetch_ticker(symbol)
            last = ticker.get("last") if isinstance(ticker, dict) else None
            return float(strict_decimal(last, "ticker.last", error_cls=HyperliquidError))
        return price
