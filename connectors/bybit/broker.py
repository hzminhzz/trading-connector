"""Bybit ML4T AsyncBrokerProtocol adapter backed by BybitClient and CCXTIntentJournal."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from connectors.bybit.client import BybitClient
from connectors.bybit.config import BybitConfig, Environment
from connectors.bybit.journal import BybitIntentJournal
from connectors.bybit.models import (
    BybitInsufficientFundsError,
    BybitInvalidOrderError,
)
from connectors.ccxt_support.broker import BaseCCXTLiveBroker


class BybitLiveBroker(BaseCCXTLiveBroker):
    """ML4T AsyncBrokerProtocol adapter for Bybit V5 perpetual futures."""

    _default_client_cls = BybitClient
    _provider_label = "Bybit"
    _quote_currency = "USDT"
    _terminal_rejection_errors = (
        BybitInvalidOrderError,
        BybitInsufficientFundsError,
    )

    def __init__(
        self,
        client: BybitClient | None = None,
        config: BybitConfig | None = None,
        journal: BybitIntentJournal | None = None,
    ) -> None:
        super().__init__(client=client, config=config, journal=journal)

    def assert_paper_trading(self) -> None:
        if self.config.environment not in (Environment.TESTNET, Environment.DEMO):
            raise PermissionError(
                f"Paper trading requires Bybit TESTNET or DEMO environment, got {self.config.environment.value}"
            )

    def _submit_params(self, kwargs: dict[str, Any], intent_id: str) -> dict[str, Any]:
        params = dict(kwargs)
        if intent_id:
            params["orderLinkId"] = intent_id
        return params

    async def _sync_state(self) -> None:
        if self.client.config.environment is Environment.DEMO:
            # Fail on the first unsupported authenticated snapshot; do not leave
            # concurrent background inventory reads after a failed connect.
            await self._sync_balance()
            await self._sync_positions()
            await self._sync_orders()
        else:
            await super()._sync_state()

    async def _sync_balance(self) -> None:
        if self.client.config.environment is Environment.DEMO:
            account = await self.client.fetch_demo_account_summary()
            self._account_value = float(account.equity_usd)
            # V5 available-to-withdraw/available-to-trade is not ledger cash.
            self._cash = None
            return
        balances = await self.client.fetch_balance()
        usdt_bal = balances.get("USDT") or balances.get("USD")
        if usdt_bal:
            self._account_value = float(usdt_bal.total)
            self._cash = float(usdt_bal.free)
        else:
            raise ValueError("Bybit quote-currency account balance unverified")

    def _position_entry_time(self, p: Any) -> datetime:
        if self.client.config.environment is Environment.DEMO:
            # V5 createdTime (ms) is required by the Demo snapshot parser.
            return datetime.fromtimestamp(int(p.raw["createdTime"]) / 1000, UTC)
        return super()._position_entry_time(p)
