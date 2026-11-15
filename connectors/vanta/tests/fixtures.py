"""Synthetic Vanta fixtures. No credentials, no network."""
from __future__ import annotations

from typing import Any

ACCOUNT = "6f1c2e34-9a4b-4c1d-8e2f-1a2b3c4d5e6f"
OTHER_ACCOUNT = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
KEY_ID = "vk_EXAMPLEKEYID000000"
SECRET = "vks_EXAMPLE_SECRET_DO_NOT_USE_IN_PRODUCTION"
POSITION_ID = "0f9d1a2b-3c4d-5e6f-7a8b-9c0d1e2f3a4b"
ORDER_ID = "a12b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
TRADE_ID = "c34d5e6f-7a8b-9c0d-1e2f-3a4b5c6d7e8f"


def key_payload(account_id: str = ACCOUNT, **overrides: Any) -> dict[str, Any]:
    payload = {
        "keyId": KEY_ID,
        "accountId": account_id,
        "scopes": ["trade:place", "trade:close", "trade:read"],
        "orderPlacementPausedUntil": None,
    }
    payload.update(overrides)
    return payload


def account_payload(account_id: str = ACCOUNT, **overrides: Any) -> dict[str, Any]:
    payload = {
        "accountId": account_id,
        "assetClass": "all_markets",
        "marketName": "All Markets",
        "accountSize": 25000,
        "formattedAccountSize": "25K",
        "evaluation": {
            "title": "Crypto 25K Evaluation",
            "status": "evaluation",
            "isEliminated": False,
            "accountSize": "$25,000",
            "activeAccounts": "1/1",
            "effectiveAccountSizeNumeric": 25000,
        },
        "account": {
            "currentBalance": 25120.5,
            "currentEquity": 25180.25,
            "balanceChange": 120.5,
            "balanceChangePercent": 0.48,
            "totalPnL": 180.25,
            "totalPnLPercent": 0.72,
            "openPnL": 59.75,
            "openPnLPercent": 0.24,
            "openPositions": 1,
            "portfolioBalance": 25120.5,
            "portfolioBalanceChangePercent": 0.48,
            "portfolioBalanceBreakdown": {
                "currentBalance": 25120.5,
                "marginLeverage": 5,
                "sumPositionValue": 7202.5,
            },
            "leverage": "Current - 0.2870x / Max - 10x",
            "capitalUsed": 7202.5,
            "totalRealizedPnl": 120.5,
            "isPassed": False,
        },
        "challenge": {
            "variant": "default",
            "bucket": "SUBACCOUNT_CHALLENGE",
            "drawdownCriteria": "trailing",
            "profitTarget": 2500,
            "profitTargetPercent": 10,
            "remaining": 2319.75,
            "maxLeverage": "10x",
            "trailingDrawdownPercent": 5,
            "maxDrawdown": -1250,
            "daysRemaining": 27,
            "totalDays": 90,
            "drawdownBreakdown": {
                "highWaterMark": 25180.25,
                "allowedDrawdown": 1259.01,
                "currentDrawdown": 0,
                "remainingDrawdown": 1259.01,
                "remainingDrawdownPercentHWM": 5,
            },
        },
        "performance": {
            "totalTrades": 4,
            "winRate": 75,
            "totalWins": 3,
            "avgTradePnL": 45.06,
            "tradeDuration": "63h 12m",
            "challengeStartMs": 1717200000000,
            "dailyReturns": [
                {"date": "2026-06-28", "value": 0.31},
                {"date": "2026-06-29", "value": 0.17},
            ],
        },
        "meta": {"generatedAtMs": 1717718400000, "stale": False},
        "proTrack": None,
        "drawdownRules": {
            "criteria": "trailing",
            "dailyLoss": {"usedPct": 0.16, "limitPct": 5, "dayStartEquity": 25221.1},
            "maxLoss": {
                "rule": "eod_trailing",
                "usedPct": 0.16,
                "limitPct": 5,
                "anchorEquity": 25261.4,
                "measuredEquity": 25221.1,
            },
            "currentBalance": 25120.5,
        },
        "elimination": None,
        "isPro": False,
        "tradingDays": None,
    }
    payload.update(overrides)
    return payload


def position_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "positionUuid": POSITION_ID,
        "tradePair": "BTCUSDC",
        "tradePairDisplay": "BTC/USDC",
        "positionType": "LONG",
        "netLeverage": 0.287,
        "netQuantity": 0.11533,
        "averageEntryPrice": 62450.12,
        "currentReturn": 0.0024,
        "openMs": 1717490000000,
        "unrealizedPnl": 59.75,
        "realizedPnl": 0,
        "netValue": 7262.25,
        "cumulativeEntryValue": 7202.5,
        "stopLoss": 61000,
        "takeProfit": 65000,
    }
    payload.update(overrides)
    return payload


def order_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "orderUuid": ORDER_ID,
        "tradePair": "ETHUSDC",
        "tradePairDisplay": "ETH/USDC",
        "orderType": "LONG",
        "executionType": "LIMIT",
        "processedMs": 1717500000000,
        "limitPrice": 3200,
        "leverage": 1,
        "value": 1500,
        "quantity": None,
        "stopLoss": 3100,
        "takeProfit": 3500,
        "bracketPct": None,
        "trailingPercent": None,
        "trailingValue": None,
    }
    payload.update(overrides)
    return payload


def trade_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "id": TRADE_ID,
        "tradePair": "BTCUSDC",
        "tradePairDisplay": "BTC/USDC",
        "positionType": "LONG",
        "leverage": "0.50x",
        "positionSize": "$1,000.00",
        "quantity": 0.01663,
        "entryPrice": "$60,120.00",
        "closePrice": "$61,540.00",
        "unrealizedPnl": 0,
        "realizedPnl": 23.62,
        "returnAtClose": 0.0236,
        "status": "Filled",
        "openTimeMs": 1717200000000,
        "closeTimeMs": 1717230000000,
        "stopLoss": 59000,
        "takeProfit": 62000,
        "totalFees": 1.18,
    }
    payload.update(overrides)
    return payload


def payout_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "id": "e56f7a8b-9c0d-4e1f-8a3b-4c5d6e7f8a9b",
        "periodStartMs": 1757894400000,
        "periodEndMs": 1758499200000,
        "amount": 412.5,
        "currency": "USD",
        "profitSplit": "100%",
        "status": "completed",
        "rail": "crypto",
        "provider": "rise",
        "alphaAmount": None,
        "alphaDestination": None,
        "requestedAtMs": 1758502800000,
        "paidAtMs": 1758585600000,
        "createdAtMs": 1758499260000,
    }
    payload.update(overrides)
    return payload


def payouts_payload(
    items: list[dict[str, Any]] | None = None,
    next_cursor: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    payload = {
        "items": items if items is not None else [payout_payload()],
        "nextCursor": next_cursor,
        "accrualStatus": "ok",
        "accrual": {
            "estimatedAmount": 88.25,
            "windowStartMs": 1758499200000,
            "asOfMs": 1758800000000,
            "priorUnpaidAmount": 0,
            "earliestUnpaidPeriodStartMs": None,
            "deferredBalance": None,
        },
    }
    payload.update(overrides)
    return payload


def trade_pair_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "tradePairId": "BTCUSDC",
        "tradePair": "BTC/USDC",
        "category": "crypto",
        "source": "hyperliquid",
        "isPro": True,
        "maxLeverage": 1,
        "minLeverage": 0.01,
        "lotSize": None,
        "hlCoin": "BTC",
    }
    payload.update(overrides)
    return payload


def trade_pairs_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "accountId": ACCOUNT,
        "assetClass": "all_markets",
        "tradePairsAssetClass": "all_markets",
        "isPro": False,
        "readAtMs": 1791920800000,
        "validatorTimestampMs": 1791920788376,
        "allowed": [trade_pair_payload()],
        "disabled": [
            trade_pair_payload(
                tradePairId="XAUUSD",
                tradePair="XAU/USD",
                category="forex",
                source="vanta",
                isPro=False,
                maxLeverage=2,
                minLeverage=0.05,
                hlCoin="XAU",
            )
        ],
        "totalAllowed": 1,
        "totalDisabled": 1,
        "meta": {"generatedAtMs": 1791920800000, "stale": False},
    }
    payload.update(overrides)
    return payload


def order_response_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "success": True,
        "order_uuid": "d45e6f7a-8b9c-0d1e-2f3a-4b5c6d7e8f90",
        "message": "Order successfully processed by Taoshi validator",
        "error_message": "",
        "processing_time": 1.23,
        "order_json": "{'trade_pair_id': 'BTCUSDC'}",
        "order": {
            "orderUuid": "d45e6f7a-8b9c-0d1e-2f3a-4b5c6d7e8f90",
            "tradePair": "BTCUSDC",
            "tradePairDisplay": "BTC/USDC",
            "orderType": "LONG",
            "executionType": "MARKET",
            "filled": True,
            "quantity": 0.01601,
            "value": 1000,
            "leverage": 0.04,
            "price": 62450.12,
            "slippage": 0.0001,
            "limitPrice": None,
            "stopPrice": None,
            "stopCondition": None,
            "stopLoss": None,
            "takeProfit": None,
            "bracketPct": None,
            "processedMs": 1717718400000,
            "realizedPnl": 0,
            "bindingCap": None,
            "bracketOrders": None,
        },
    }
    payload.update(overrides)
    return payload


def envelope(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data}
