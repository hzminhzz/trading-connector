"""Independent venue reconciliation rejects missing fills, false flatness and fees."""

from copy import deepcopy

import pytest

from connectors.propr.examples.verify_free_trial_wallet_canary import evaluate


ACCOUNT = "urn:prp-account:exampleJhW9"


def sample():
    audit = {
        "schema": "combo2_propr_free_trial_canary_v1",
        "signal_kind": "INITIAL_HOLDING_CANARY_NOT_NEW_FILL",
        "source_receipt_sha256": "a" * 64,
        "live_funded_account": False,
        "production_copytrade_qualified": False,
        "preflight": {"balance": "4999.241315"},
        "opening": {"id": "start-intent", "order_id": "start-order"},
        "closing": {"id": "stop-intent", "order_id": "stop-order"},
    }
    account = {
        "accountId": ACCOUNT,
        "type": "paper",
        "balance": "4999.225661",
    }
    orders = [
        {
            "orderId": "start-order",
            "accountId": ACCOUNT,
            "intentId": "start-intent",
            "side": "buy",
            "reduceOnly": False,
            "asset": "HYPE",
            "status": "filled",
            "cumulativeQuantity": "0.2",
            "averageFillPrice": "85.857",
        },
        {
            "orderId": "stop-order",
            "accountId": ACCOUNT,
            "intentId": "stop-intent",
            "side": "sell",
            "reduceOnly": True,
            "asset": "HYPE",
            "status": "filled",
            "cumulativeQuantity": "0.2",
            "averageFillPrice": "85.856",
        },
    ]
    fills = [
        {
            "orderId": "start-order",
            "asset": "HYPE",
            "quantity": "0.2",
            "fee": "0.007727",
            "realizedPnl": "0",
        },
        {
            "orderId": "stop-order",
            "asset": "HYPE",
            "quantity": "0.2",
            "fee": "0.007727",
            "realizedPnl": "-0.0002",
        },
    ]
    journal = {"start-intent": "filled", "stop-intent": "filled"}
    return audit, account, orders, fills, [], [], journal


def test_one_shot_fill_cash_and_fee_parity():
    result = evaluate(*sample())
    assert result["balance_before"] == "4999.241315"
    assert result["balance_after"] == "4999.225661"
    assert result["net_pnl"] == "-0.015654"
    assert result["trading_fees"] == "0.015454"
    assert result["venue_confirmed_fills"] == 2
    assert result["network_order_mutations_during_reconciliation"] == 0


@pytest.mark.parametrize(
    "column,field",
    [
        (0, "status"),
        (0, "reduceOnly"),
        (0, "cumulativeQuantity"),
        (1, "intentId"),
    ],
)
def test_inconsistent_venue_order_rejected(column, field):
    data = list(sample())
    data[2] = deepcopy(data[2])
    row = data[2][column]
    row[field] = {
        "status": "open",
        "reduceOnly": True,
        "cumulativeQuantity": "0.1",
        "intentId": "wrong",
    }[field]
    with pytest.raises(ValueError, match="BROKER_ORDER_STATUS"):
        evaluate(*data)


def test_missing_fill_and_unclosed_exposure_fail():
    rows = list(sample())
    rows[3] = rows[3][:1]
    with pytest.raises(ValueError, match="BROKER_FILLS_INCOMPLETE"):
        evaluate(*rows)
    rows = list(sample())
    rows[4] = [{"asset": "HYPE", "quantity": "0.2"}]
    with pytest.raises(ValueError, match="CANARY_ACCOUNT_NOT_FLAT"):
        evaluate(*rows)
    rows = list(sample())
    rows[5] = [{"asset": "HYPE", "status": "open"}]
    with pytest.raises(ValueError, match="CANARY_ACCOUNT_NOT_FLAT"):
        evaluate(*rows)


def test_forged_balance_and_journal_state_fail():
    rows = list(sample())
    rows[1] = dict(rows[1], balance="5000")
    with pytest.raises(ValueError, match="BALANCE_PNL_RECONCILIATION"):
        evaluate(*rows)
    rows = list(sample())
    rows[6] = {"start-intent": "unknown", "stop-intent": "filled"}
    with pytest.raises(ValueError, match="BROKER_ORDER_STATUS"):
        evaluate(*rows)
