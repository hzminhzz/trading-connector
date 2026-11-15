import base64
import hashlib
import hmac

from connectors.vanta import VantaConfig
from connectors.vanta.client import canonical_string, signed_headers
from connectors.vanta.tests.fixtures import ACCOUNT, KEY_ID, SECRET

EMPTY_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
GET_PATH = "/api/v1/trading/account"
GET_TS = "1717718400000"
GET_NONCE = "9f8c1a2b3d4e5f60718293a4b5c6d7e8"
GET_SIGNATURE = "v1=ARXQ6aQCU4GUl47DNcQOJVW0miSfhgWdlfufJHWHRdE="

POST_PATH = "/api/v1/trading/orders"
POST_TS = "1717718400000"
POST_NONCE = "3c1d5e7f90a2b4c6d8e0f2a4b6c8d0e2"
POST_BODY = (
    '{"accountId":"6f1c2e34-9a4b-4c1d-8e2f-1a2b3c4d5e6f","trade":'
    '{"execution_type":"MARKET","trade_pair":"BTCUSDC","order_type":"LONG","value":1000}}'
)
POST_SIGNATURE = "v1=1/Oq1TQAeJehKvyAoHCq8DIE+8dAqkXYHIyvO3/17Q4="


def test_official_get_vector():
    canonical = canonical_string("GET", GET_PATH, GET_TS, GET_NONCE, b"")
    assert canonical == "\n".join(
        ["v1", "GET", GET_PATH, GET_TS, GET_NONCE, EMPTY_HASH]
    )
    headers = signed_headers(
        KEY_ID, SECRET, "GET", GET_PATH, timestamp=GET_TS, nonce=GET_NONCE
    )
    assert headers == {
        "X-Vanta-Key-Id": KEY_ID,
        "X-Vanta-Timestamp": GET_TS,
        "X-Vanta-Nonce": GET_NONCE,
        "X-Vanta-Signature": GET_SIGNATURE,
    }


def test_official_post_vector_hashes_exact_body_bytes():
    canonical = canonical_string(
        "POST", POST_PATH, POST_TS, POST_NONCE, POST_BODY.encode("utf-8")
    )
    assert canonical == "\n".join(
        [
            "v1",
            "POST",
            POST_PATH,
            POST_TS,
            POST_NONCE,
            hashlib.sha256(POST_BODY.encode("utf-8")).hexdigest(),
        ]
    )
    headers = signed_headers(
        KEY_ID,
        SECRET,
        "POST",
        POST_PATH,
        POST_BODY.encode("utf-8"),
        timestamp=POST_TS,
        nonce=POST_NONCE,
    )
    assert headers["X-Vanta-Signature"] == POST_SIGNATURE


def test_query_string_is_signed_verbatim_including_percent_encoding():
    path = "/api/v1/trading/payouts?limit=50&cursor=a%2Fb%2Bc%3D"
    canonical = canonical_string("GET", path, GET_TS, GET_NONCE, b"")
    assert path in canonical.split("\n")
    headers = signed_headers(
        KEY_ID, SECRET, "GET", path, timestamp=GET_TS, nonce=GET_NONCE
    )
    expected = base64.b64encode(
        hmac.new(
            SECRET.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
        ).digest()
    ).decode("ascii")
    assert headers["X-Vanta-Signature"] == f"v1={expected}"


def test_signed_headers_generate_fresh_timestamp_and_nonce():
    first = signed_headers(KEY_ID, SECRET, "GET", GET_PATH)
    second = signed_headers(KEY_ID, SECRET, "GET", GET_PATH)
    assert first["X-Vanta-Nonce"] != second["X-Vanta-Nonce"]
    assert first["X-Vanta-Nonce"].isalnum()
    assert first["X-Vanta-Timestamp"].isdigit()
    assert first["X-Vanta-Signature"].startswith("v1=")


def test_config_does_not_leak_into_signing_headers():
    config = VantaConfig(ACCOUNT, KEY_ID, SECRET)
    headers = signed_headers(
        config.api_key, config.api_secret, "GET", GET_PATH, timestamp=GET_TS
    )
    assert headers["X-Vanta-Key-Id"] == KEY_ID
    assert SECRET not in repr(headers)
