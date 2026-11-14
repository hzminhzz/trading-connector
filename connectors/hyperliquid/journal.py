"""Hyperliquid durable SQLite WAL order-intent journal."""
from connectors.ccxt_support.journal import CCXTIntentJournal, IntentRecord
from connectors.common.intents import UnknownOutcome, new_ulid

HyperliquidIntentJournal = CCXTIntentJournal
new_intent_id = new_ulid

__all__ = [
    "HyperliquidIntentJournal",
    "IntentRecord",
    "UnknownOutcome",
    "new_intent_id",
]
