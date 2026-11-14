"""Hyperliquid ML4T AsyncBrokerProtocol adapter backed by HyperliquidClient and CCXTIntentJournal."""
from __future__ import annotations

from connectors.ccxt_support.broker import BaseCCXTLiveBroker
from connectors.hyperliquid.client import HyperliquidClient
from connectors.hyperliquid.config import Environment, HyperliquidConfig
from connectors.hyperliquid.journal import HyperliquidIntentJournal
from connectors.hyperliquid.models import (
    HyperliquidInsufficientFundsError,
    HyperliquidInvalidOrderError,
)


class HyperliquidLiveBroker(BaseCCXTLiveBroker):
    """ML4T AsyncBrokerProtocol adapter for Hyperliquid DEX perpetual futures."""

    _default_client_cls = HyperliquidClient
    _provider_label = "Hyperliquid"
    _quote_currency = "USDC"
    _terminal_rejection_errors = (
        HyperliquidInvalidOrderError,
        HyperliquidInsufficientFundsError,
    )

    def __init__(
        self,
        client: HyperliquidClient | None = None,
        config: HyperliquidConfig | None = None,
        journal: HyperliquidIntentJournal | None = None,
    ) -> None:
        super().__init__(client=client, config=config, journal=journal)

    def assert_paper_trading(self) -> None:
        if self.config.environment is not Environment.TESTNET:
            raise PermissionError(
                f"Paper trading requires Hyperliquid TESTNET environment, got {self.config.environment.value}"
            )
