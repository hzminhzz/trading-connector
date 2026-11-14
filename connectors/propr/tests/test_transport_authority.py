"""Regressions for the real SDK's ultimate HTTP and intent identity boundaries."""
import json

import pytest
import requests

from propr_sdk import ProprClient, SyntheticTransport


def order():
    return dict(side="buy", position_side="long", order_type="market", asset="BTC",
                base="BTC", quote="USDC", quantity="0.001", client_intent_id="persisted-0001")


def test_default_sdk_denies_direct_mutations_before_adapter_send(monkeypatch):
    calls = []
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send",
                        lambda *a, **kw: calls.append(a))
    client = ProprClient(api_key="fixture", base_url="https://api.propr.xyz/v1")
    client.account_id = "fixture-account"
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(PermissionError):
            client._request(method, "/accounts/fixture-account/orders", json={})
        with pytest.raises(PermissionError):
            client._session.request(method, "https://api.propr.xyz/v1/orders", json={})
    assert calls == []


def test_persisted_id_is_transmitted_exactly_and_input_is_not_modified():
    from propr_sdk import SyntheticTransport

    recorded = []

    def response(method, url, **kwargs):
        recorded.append(kwargs["json"])
        r = requests.Response()
        r.status_code = 200
        r._content = json.dumps({"data": [{"orderId": "fixture-order"}]}).encode()
        return r

    client = ProprClient(api_key="fixture", base_url="https://fixture.invalid/v1",
                         synthetic_transport=SyntheticTransport(response))
    client.account_id = "fixture-account"
    client.create_order(**order())
    assert recorded[0]["orders"][0]["intentId"] == "persisted-0001"
    original = {"intentId": "persisted-0002", "quantity": "0.01"}
    client.create_orders([original])
    assert original == {"intentId": "persisted-0002", "quantity": "0.01"}
    with pytest.raises(ValueError, match="intent"):
        client.create_orders([{"quantity": "0.01"}])


def test_unknown_submit_is_never_retried():
    from propr_sdk import SyntheticTransport

    calls = []

    def timeout(*args, **kwargs):
        calls.append(kwargs["json"])
        raise requests.Timeout("accepted then timeout")

    client = ProprClient(api_key="fixture", base_url="https://fixture.invalid/v1",
                         synthetic_transport=SyntheticTransport(timeout))
    client.account_id = "fixture-account"
    with pytest.raises(requests.Timeout):
        client.create_order(**order())
    assert len(calls) == 1


def test_ambiguous_discovery_is_rejected(monkeypatch):
    client = ProprClient(api_key="fixture")
    monkeypatch.setattr(client, "get_challenge_attempts",
                        lambda **kw: [{"accountId": "one"}, {"accountId": "two"}])
    with pytest.raises(ValueError, match="ambiguous"):
        client.setup()


def test_convenience_methods_forward_intent_id_and_are_denied_in_default_readonly(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("network transport called for read-only client")

    monkeypatch.setattr(requests.Session, "request", blocked)
    ro_client = ProprClient(api_key="pk_live_test")
    ro_client.account_id = "fixture-account"
    for call in (
        lambda: ro_client.market_buy("BTC", "0.01", client_intent_id="cid-mb"),
        lambda: ro_client.market_sell("BTC", "0.01", client_intent_id="cid-ms"),
        lambda: ro_client.limit_buy("BTC", "0.01", "80000", client_intent_id="cid-lb"),
        lambda: ro_client.limit_sell("BTC", "0.01", "90000", client_intent_id="cid-ls"),
    ):
        with pytest.raises(PermissionError, match="NO-GO"):
            call()

    recorded = []

    def response(method, url, json=None, **_kwargs):
        recorded.append((method, url, json))
        res = requests.Response()
        res.status_code = 200
        if method == "GET":
            res._content = b'{"data":[{"positionSide":"long","quantity":"0.01"}]}'
        else:
            res._content = b'{"data":[{"orderId":"o1"}]}'
        return res

    syn_client = ProprClient(
        api_key="fixture",
        base_url="https://fixture.invalid/v1",
        synthetic_transport=SyntheticTransport(response),
    )
    syn_client.account_id = "fixture-account"
    syn_client.market_buy("BTC", "0.01", client_intent_id="cid-1")
    syn_client.market_sell("BTC", "0.02", client_intent_id="cid-ms")
    syn_client.limit_buy("BTC", "0.03", "79000", client_intent_id="cid-lb")
    syn_client.limit_sell("BTC", "0.01", "90000", client_intent_id="cid-2")
    syn_client.close_position("BTC", client_intent_id="cid-3")
    post_payloads = [body for method, _url, body in recorded if method == "POST"]
    assert [p["orders"][0]["intentId"] for p in post_payloads] == [
        "cid-1", "cid-ms", "cid-lb", "cid-2", "cid-3",
    ]
    assert post_payloads[1]["orders"][0]["side"] == "sell"
    assert post_payloads[2]["orders"][0]["type"] == "limit"


def test_close_position_empty_and_short_set_leverage_and_batch_guards():
    calls = []
    positions_queue = [[], [{"positionSide": "short", "quantity": "0.05"}]]

    def response(method, url, json=None, **_kwargs):
        calls.append((method, url, json))
        res = requests.Response()
        res.status_code = 200
        if method == "GET" and "/positions" in url:
            res._content = b_json({"data": positions_queue.pop(0)})
        elif method == "GET" and "/margin-config/" in url:
            res._content = b'{"configId":"cfg-1"}'
        else:
            res._content = b'{"data":{"configId":"cfg-1","leverage":3}}'
        return res

    def b_json(obj):
        return json.dumps(obj).encode()

    client = ProprClient(
        api_key="fixture",
        base_url="https://fixture.invalid/v1",
        synthetic_transport=SyntheticTransport(response),
    )
    client.account_id = "fixture-account"
    assert client.close_position("BTC", client_intent_id="cid-empty") == []
    assert not any(m == "POST" for m, _u, _j in calls)

    client.close_position("BTC", client_intent_id="cid-short")
    short_close = calls[-1][2]["orders"][0]
    assert short_close["side"] == "buy"
    assert short_close["positionSide"] == "short"
    assert short_close["reduceOnly"] is True

    client.set_leverage("BTC", 3, margin_mode="isolated")
    put_method, put_url, put_body = calls[-1]
    assert put_method == "PUT"
    assert put_url.endswith("/accounts/fixture-account/margin-config/cfg-1")
    assert put_body["leverage"] == 3
    assert put_body["marginMode"] == "isolated"

    with pytest.raises(ValueError, match="Cross-account"):
        client.create_orders([{"accountId": "other", "intentId": "i-1", "quantity": "0.1"}])
    with pytest.raises(ValueError, match="Duplicate"):
        client.create_orders([
            {"intentId": "same", "quantity": "0.1"},
            {"intentId": "same", "quantity": "0.2"},
        ])


def test_request_path_and_synthetic_transport_guards():
    with pytest.raises(PermissionError, match="fixture-only"):
        ProprClient(
            api_key="pk_live_secret",
            base_url="https://fixture.invalid/v1",
            synthetic_transport=SyntheticTransport(lambda *_a, **_kw: None),
        )
    with pytest.raises(PermissionError, match="fixture-only"):
        ProprClient(
            api_key="fixture",
            base_url="https://api.propr.xyz/v1",
            synthetic_transport=SyntheticTransport(lambda *_a, **_kw: None),
        )

    client = ProprClient(api_key="pk_live_test", base_url="https://api.propr.xyz/v1")
    for bad_path in ("https://evil.com/x", "//evil.com/x", "/accounts/../etc/passwd"):
        with pytest.raises(ValueError, match="Relative bound API path required"):
            client._request("GET", bad_path)
