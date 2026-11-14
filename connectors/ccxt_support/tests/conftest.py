"""Shared provider fixtures and test specs for CCXT-backed connectors."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from connectors.bybit.broker import BybitLiveBroker
from connectors.bybit.client import BybitClient
from connectors.bybit.config import BybitConfig, Environment as BybitEnv, Mode as BybitMode
from connectors.bybit.feed import BybitKlineFeed
from connectors.bybit.journal import BybitIntentJournal
from connectors.bybit.models import (
    BybitAuthenticationError,
    BybitBalance,
    BybitError,
    BybitInsufficientFundsError,
    BybitInvalidOrderError,
    BybitNetworkError,
    BybitOrderData,
    BybitOrderNotFoundError,
    BybitPositionData,
    BybitRateLimitError,
)
from connectors.hyperliquid.broker import HyperliquidLiveBroker
from connectors.hyperliquid.client import HyperliquidClient
from connectors.hyperliquid.config import (
    Environment as HLEnv,
    HyperliquidConfig,
    Mode as HLMode,
)
from connectors.hyperliquid.feed import HyperliquidKlineFeed
from connectors.hyperliquid.journal import HyperliquidIntentJournal
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


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    asset: str
    symbol: str
    currency: str
    config_cls: type[Any]
    env_cls: type[Any]
    mode_cls: type[Any]
    client_cls: type[Any]
    broker_cls: type[Any]
    feed_cls: type[Any]
    journal_cls: type[Any]
    balance_cls: type[Any]
    position_cls: type[Any]
    order_cls: type[Any]
    base_err: type[Exception]
    auth_err: type[Exception]
    rate_err: type[Exception]
    funds_err: type[Exception]
    not_found_err: type[Exception]
    invalid_order_err: type[Exception]
    net_err: type[Exception]
    paper_error_match: str

    def make_config(self, *, mainnet: bool = False, mode: str = "read_only", authenticated: bool = True) -> Any:
        env = self.env_cls.MAINNET if mainnet else self.env_cls.TESTNET
        resolved_mode = self.mode_cls(mode)
        if self.name == "bybit":
            return self.config_cls(
                env,
                resolved_mode,
                api_key="k" if authenticated else None,
                api_secret="s" if authenticated else None,
            )
        return self.config_cls(
            env,
            resolved_mode,
            wallet_address="0x1" if authenticated else None,
            private_key="0x2" if authenticated else None,
        )


BYBIT_SPEC = ProviderSpec(
    name="bybit",
    asset="BTC/USDT",
    symbol="BTC/USDT:USDT",
    currency="USDT",
    config_cls=BybitConfig,
    env_cls=BybitEnv,
    mode_cls=BybitMode,
    client_cls=BybitClient,
    broker_cls=BybitLiveBroker,
    feed_cls=BybitKlineFeed,
    journal_cls=BybitIntentJournal,
    balance_cls=BybitBalance,
    position_cls=BybitPositionData,
    order_cls=BybitOrderData,
    base_err=BybitError,
    auth_err=BybitAuthenticationError,
    rate_err=BybitRateLimitError,
    funds_err=BybitInsufficientFundsError,
    not_found_err=BybitOrderNotFoundError,
    invalid_order_err=BybitInvalidOrderError,
    net_err=BybitNetworkError,
    paper_error_match="Paper trading requires Bybit TESTNET or DEMO environment",
)

HYPERLIQUID_SPEC = ProviderSpec(
    name="hyperliquid",
    asset="BTC/USDC",
    symbol="BTC/USDC:USDC",
    currency="USDC",
    config_cls=HyperliquidConfig,
    env_cls=HLEnv,
    mode_cls=HLMode,
    client_cls=HyperliquidClient,
    broker_cls=HyperliquidLiveBroker,
    feed_cls=HyperliquidKlineFeed,
    journal_cls=HyperliquidIntentJournal,
    balance_cls=HyperliquidBalance,
    position_cls=HyperliquidPositionData,
    order_cls=HyperliquidOrderData,
    base_err=HyperliquidError,
    auth_err=HyperliquidAuthenticationError,
    rate_err=HyperliquidRateLimitError,
    funds_err=HyperliquidInsufficientFundsError,
    not_found_err=HyperliquidOrderNotFoundError,
    invalid_order_err=HyperliquidInvalidOrderError,
    net_err=HyperliquidNetworkError,
    paper_error_match="Paper trading requires Hyperliquid TESTNET environment",
)

PROVIDER_SPECS = (BYBIT_SPEC, HYPERLIQUID_SPEC)


def make_mock_ccxt_exchange(spec: ProviderSpec) -> MagicMock:
    mock = MagicMock()
    mock.load_markets = AsyncMock()
    mock.close = AsyncMock()
    mock.fetch_balance = AsyncMock()
    mock.fetch_positions = AsyncMock()
    mock.fetch_open_orders = AsyncMock()
    mock.create_order = AsyncMock()
    mock.cancel_order = AsyncMock()
    mock.edit_order = AsyncMock()
    mock.fetch_ohlcv = AsyncMock()
    if spec.name == "hyperliquid":
        mock.fetch_ticker = AsyncMock(return_value={"last": "82500.0"})
    return mock


def make_mock_provider_client(spec: ProviderSpec, *, mode: str = "execution") -> MagicMock:
    client = MagicMock()
    client.config = spec.make_config(mode=mode)
    client.exchange = make_mock_ccxt_exchange(spec)
    client.connect = AsyncMock()
    client.close = AsyncMock()
    client.fetch_balance = AsyncMock(
        return_value={
            spec.currency: spec.balance_cls(
                spec.currency, Decimal_5000 := _d("5000.0"), _d("4500.0"), Decimal_5000 - _d("4500.0"), {}
            )
        }
    )
    client.fetch_positions = AsyncMock(return_value=[])
    client.fetch_open_orders = AsyncMock(return_value=[])
    client.create_order = AsyncMock()
    client.cancel_order = AsyncMock()
    client.edit_order = AsyncMock()
    return client


def _d(val: str):
    from decimal import Decimal

    return Decimal(val)


@pytest.fixture(params=PROVIDER_SPECS, ids=[s.name for s in PROVIDER_SPECS])
def provider_spec(request) -> ProviderSpec:
    return request.param
