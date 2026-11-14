"""Shared CCXT async infrastructure for perpetual-futures connectors."""
from connectors.ccxt_support.client import BaseCCXTClient
from connectors.ccxt_support.feed import CCXTKlineFeed
from connectors.ccxt_support.journal import CCXTIntentJournal, IntentRecord
from connectors.ccxt_support.models import (
    CCXTBalance,
    CCXTError,
    CCXTErrorSpec,
    CCXTOrderData,
    CCXTPositionData,
    wrap_ccxt_exception,
)

__all__ = [
    "BaseCCXTClient",
    "CCXTBalance",
    "CCXTError",
    "CCXTErrorSpec",
    "CCXTIntentJournal",
    "CCXTKlineFeed",
    "CCXTOrderData",
    "CCXTPositionData",
    "IntentRecord",
    "wrap_ccxt_exception",
]
