"""Regressions for the real SDK's ultimate HTTP and intent identity boundaries."""
import json

import pytest
import requests

from propr_sdk import ProprClient


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
