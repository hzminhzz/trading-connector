"""Async Vanta Trading REST client. Reads are account-bound; mutations never dispatch."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import string
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, NoReturn
from urllib.parse import urlencode

import httpx

from .config import VantaConfig

BASE_URL = "https://app.vantatrading.io"
MUTATION_DENIED = "Vanta mutations are disabled at the transport boundary; no request was sent"
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_PRINTABLE = frozenset(string.printable) - frozenset("\t\n\r\x0b\x0c")
_RATE_LIMIT_HEADERS = ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset")
_MAX_PAYOUT_PAGES = 10_000


class VantaError(RuntimeError):
    """Base provider error; messages never include credentials."""


class VantaTransportError(VantaError):
    """Network or timeout failure. No outcome is inferred."""


class VantaResponseError(VantaError):
    """A response did not match the documented envelope or shape."""


class VantaAPIError(VantaError):
    def __init__(
        self,
        status_code: int,
        *,
        code: str | None = None,
        required_scope: str | None = None,
        retry_after: int | str | None = None,
        rate_limit: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.required_scope = required_scope
        self.retry_after = retry_after
        self.rate_limit = dict(rate_limit or {})
        self.payload = payload
        detail = f": {code}" if code else ""
        super().__init__(f"Vanta HTTP {status_code}{detail}")


class VantaValidationError(VantaAPIError):
    """HTTP 400."""


class VantaAuthenticationError(VantaAPIError):
    """HTTP 401."""


class VantaPermissionError(VantaAPIError):
    """HTTP 403."""


class VantaNotFoundError(VantaAPIError):
    """HTTP 404."""


class VantaConflictError(VantaAPIError):
    """HTTP 409, normally a replayed nonce."""


class VantaRateLimitError(VantaAPIError):
    """HTTP 429."""


class VantaServerError(VantaAPIError):
    """HTTP 5xx."""


class VantaAccountScopeError(VantaError):
    """The key or response belongs to a different account than configured."""


class MutationDenied(PermissionError):
    def __init__(self, message: str, prepared: PreparedOrder | None = None) -> None:
        super().__init__(message)
        self.prepared = prepared


@dataclass(frozen=True)
class PreparedOrder:
    """Exact wire request for POST /api/v1/trading/orders; never dispatched here."""

    method: str
    path: str
    body: dict[str, Any]
    body_json: str


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON number {value}")


def _json_value(value: Any) -> str:
    """Exact JSON token serialization; Decimal never round-trips through float."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite Decimal")
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ",".join(_json_value(item) for item in value) + "]"
    if isinstance(value, Mapping):
        return "{" + ",".join(
            json.dumps(str(key)) + ":" + _json_value(item) for key, item in value.items()
        ) + "}"
    raise TypeError(f"cannot serialize {type(value).__name__} as JSON")


def canonical_string(
    method: str, path: str, timestamp: str, nonce: str, body: bytes | str = b""
) -> str:
    """Official six-field canonical string, no trailing newline."""
    if not isinstance(method, str) or method.upper() not in {"GET", "POST"}:
        raise ValueError("method must be GET or POST")
    if not isinstance(path, str) or not path:
        raise ValueError("request path required")
    pathname = path.split("?", 1)[0]
    if not pathname.startswith("/") or "//" in pathname or ".." in pathname:
        raise ValueError("request path must be a relative API path")
    if not isinstance(timestamp, str) or not timestamp.isdigit():
        raise ValueError("timestamp must be whole milliseconds as a decimal string")
    if not isinstance(nonce, str) or not nonce:
        raise ValueError("nonce required")
    body_bytes = body.encode("utf-8") if isinstance(body, str) else body
    if not isinstance(body_bytes, (bytes, bytearray)):
        raise ValueError("body must be bytes or str")
    return "\n".join(
        (
            "v1",
            method.upper(),
            path,
            timestamp,
            nonce,
            hashlib.sha256(body_bytes).hexdigest(),
        )
    )


def signed_headers(
    api_key: str,
    api_secret: str,
    method: str,
    path: str,
    body: bytes | str = b"",
    *,
    timestamp: str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Build the four required headers for one request."""
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("api_key required")
    if not isinstance(api_secret, str) or not api_secret:
        raise ValueError("api_secret required")
    stamp = timestamp if timestamp is not None else str(int(time.time() * 1000))
    value = nonce if nonce is not None else secrets.token_hex(16)
    canonical = canonical_string(method, path, stamp, value, body)
    digest = hmac.new(api_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).digest()
    return {
        "X-Vanta-Key-Id": api_key,
        "X-Vanta-Timestamp": stamp,
        "X-Vanta-Nonce": value,
        "X-Vanta-Signature": "v1=" + base64.b64encode(digest).decode("ascii"),
    }


def _error_from_response(response: httpx.Response) -> VantaAPIError:
    payload: Mapping[str, Any] | None = None
    code: str | None = None
    required_scope: str | None = None
    try:
        parsed = json.loads(
            response.content, parse_float=Decimal, parse_constant=_reject_constant
        )
    except (ValueError, UnicodeDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        payload = parsed
        raw_code = parsed.get("error")
        if isinstance(raw_code, str):
            code = raw_code
        raw_scope = parsed.get("required_scope")
        if isinstance(raw_scope, str):
            required_scope = raw_scope
    retry_after: int | str | None = response.headers.get("Retry-After")
    if isinstance(retry_after, str) and retry_after.isdigit():
        retry_after = int(retry_after)
    rate_limit = {
        name: response.headers[name] for name in _RATE_LIMIT_HEADERS if name in response.headers
    }
    status = response.status_code
    error_class: type[VantaAPIError] = {
        400: VantaValidationError,
        401: VantaAuthenticationError,
        403: VantaPermissionError,
        404: VantaNotFoundError,
        409: VantaConflictError,
        429: VantaRateLimitError,
    }.get(status, VantaServerError if status >= 500 else VantaAPIError)
    return error_class(
        status,
        code=code,
        required_scope=required_scope,
        retry_after=retry_after,
        rate_limit=rate_limit,
        payload=payload,
    )


class _GuardedTransport(httpx.AsyncBaseTransport):
    """Last HTTP boundary: credentials only to the official host, reads only."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method.upper() not in _READ_METHODS:
            raise MutationDenied(MUTATION_DENIED)
        if request.url.scheme != "https" or request.url.host != "app.vantatrading.io":
            raise PermissionError("Vanta credentials may only be sent to https://app.vantatrading.io")
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


def _identifier(value: Any, name: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"{name} must be 1-{limit} characters")
    if any(character not in _PRINTABLE for character in value):
        raise ValueError(f"{name} must be printable ASCII")
    return value


def _trade_pair(value: Any) -> str | dict[str, str]:
    if isinstance(value, str):
        return _identifier(value, "trade_pair")
    if isinstance(value, Mapping):
        if set(value) != {"trade_pair_id"}:
            raise ValueError("trade_pair object must contain only trade_pair_id")
        return {"trade_pair_id": _identifier(value["trade_pair_id"], "trade_pair_id")}
    raise ValueError("trade_pair must be a string or {trade_pair_id: string}")


def _positive(value: Any, name: str) -> int | Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ValueError(f"{name} must be an int or Decimal, never a float")
    if isinstance(value, Decimal):
        if not value.is_finite() or value <= 0:
            raise ValueError(f"{name} must be positive and finite")
    elif value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


_BRACKET_FIELDS = frozenset({"stop_loss", "take_profit", "value", "quantity", "order_uuid"})
_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "MARKET": frozenset(
        {"execution_type", "trade_pair", "order_type", "value", "quantity", "leverage",
         "stop_loss", "take_profit"}
    ),
    "LIMIT": frozenset(
        {"execution_type", "trade_pair", "order_type", "value", "quantity", "leverage",
         "stop_loss", "take_profit", "limit_price"}
    ),
    "BRACKET": frozenset(
        {"execution_type", "trade_pair", "order_type", "position_uuid", "bracket_orders"}
    ),
    "LIMIT_EDIT": frozenset(
        {"execution_type", "trade_pair", "order_type", "quantity", "position_uuid",
         "bracket_orders"}
    ),
    "LIMIT_CANCEL": frozenset({"execution_type", "order_uuid"}),
    "FLAT_ALL": frozenset({"execution_type", "order_uuid"}),
}
_SIZE_FIELDS = ("value", "quantity", "leverage")
_ORDER_TYPES = frozenset({"LONG", "SHORT", "FLAT"})


def _validate_bracket_orders(legs: Any, *, require_price_leg: bool) -> None:
    if not isinstance(legs, list) or not legs:
        raise ValueError("bracket_orders must be a non-empty list")
    price_leg = False
    for index, leg in enumerate(legs):
        if not isinstance(leg, Mapping):
            raise ValueError(f"bracket_orders[{index}] must be an object")
        unknown = sorted(str(key) for key in leg if key not in _BRACKET_FIELDS)
        if unknown:
            raise ValueError(f"unsupported bracket_orders[{index}] fields: {unknown}")
        if not leg:
            raise ValueError(f"bracket_orders[{index}] must not be empty")
        if "value" in leg and "quantity" in leg:
            raise ValueError(f"bracket_orders[{index}] takes at most one of value or quantity")
        for field_name in ("stop_loss", "take_profit", "value", "quantity"):
            if field_name in leg:
                _positive(leg[field_name], f"bracket_orders[{index}].{field_name}")
        if "order_uuid" in leg:
            _identifier(leg["order_uuid"], f"bracket_orders[{index}].order_uuid")
        if not ({"stop_loss", "take_profit", "order_uuid"} & set(leg)):
            raise ValueError(
                f"bracket_orders[{index}] needs stop_loss, take_profit or order_uuid"
            )
        if {"stop_loss", "take_profit"} & set(leg):
            price_leg = True
    if require_price_leg and not price_leg:
        raise ValueError("bracket_orders must attach at least one stop_loss or take_profit")


class VantaClient:
    """Account-bound Vanta REST client. Reads only; writes are prepared and denied."""

    def __init__(
        self,
        config: VantaConfig,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not isinstance(config, VantaConfig):
            raise TypeError("config must be a VantaConfig")
        self.config = config
        self._identity: dict[str, Any] | None = None
        self._identity_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(
            base_url=config.base_url,
            transport=_GuardedTransport(transport or httpx.AsyncHTTPTransport()),
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )

    async def __aenter__(self) -> VantaClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    # --- Transport ---

    async def _request(
        self, method: str, path: str, *, params: Mapping[str, Any] | None = None
    ) -> Any:
        method = method.upper()
        if method not in _READ_METHODS:
            raise MutationDenied(MUTATION_DENIED)
        target = path
        if params:
            query = urlencode([(key, value) for key, value in params.items() if value is not None])
            if query:
                target = f"{path}?{query}"
        headers = signed_headers(
            self.config.api_key, self.config.api_secret, method, target
        )
        try:
            response = await self._http.request(method, target, headers=headers)
        except httpx.TimeoutException as exc:
            raise VantaTransportError("Vanta request timed out; outcome not inferred") from exc
        except httpx.HTTPError as exc:
            raise VantaTransportError("Vanta transport failure") from exc
        if response.status_code >= 400:
            raise _error_from_response(response)
        try:
            payload = json.loads(
                response.content, parse_float=Decimal, parse_constant=_reject_constant
            )
        except (ValueError, UnicodeDecodeError) as exc:
            raise VantaResponseError("Vanta response was not valid JSON") from exc
        if not isinstance(payload, dict) or payload.get("success") is not True or "data" not in payload:
            raise VantaResponseError("Vanta response envelope was not successful")
        return payload["data"]

    # --- Identity and account binding ---

    def _require_account(self, data: Any, *, require_id: bool = False) -> None:
        if isinstance(data, dict):
            if require_id and "accountId" not in data:
                raise VantaResponseError("Vanta response omitted accountId")
            rows: list[Any] = [data]
        elif isinstance(data, list):
            rows = data
            if any(not isinstance(row, dict) for row in rows):
                raise VantaResponseError("Vanta response row was not an object")
        else:
            raise VantaResponseError("Vanta response data had an unexpected shape")
        for row in rows:
            if isinstance(row, dict):
                account_id = row.get("accountId")
                if account_id is not None and account_id != self.config.account_id:
                    raise VantaAccountScopeError(
                        "Vanta response belongs to a different account than configured"
                    )

    async def get_key(self) -> dict[str, Any]:
        """The signing key's own details; always refetched and account-verified."""
        data = await self._request("GET", "/api/v1/keys/self")
        if not isinstance(data, dict):
            raise VantaResponseError("Vanta key response was not an object")
        self._require_account(data, require_id=True)
        self._identity = data
        return data

    async def _ensure_identity(self) -> None:
        if self._identity is not None:
            return
        async with self._identity_lock:
            if self._identity is None:
                await self.get_key()

    # --- Reads ---

    async def get_account(self) -> dict[str, Any]:
        await self._ensure_identity()
        data = await self._request("GET", "/api/v1/trading/account")
        self._require_account(data, require_id=True)
        if not isinstance(data, dict):
            raise VantaResponseError("Vanta account response was not an object")
        return data

    async def get_positions(self) -> list[dict[str, Any]]:
        await self._ensure_identity()
        data = await self._request("GET", "/api/v1/trading/positions")
        self._require_account(data)
        if not isinstance(data, list):
            raise VantaResponseError("Vanta positions response was not an array")
        return data

    async def get_orders(self) -> list[dict[str, Any]]:
        await self._ensure_identity()
        data = await self._request("GET", "/api/v1/trading/orders")
        self._require_account(data)
        if not isinstance(data, list):
            raise VantaResponseError("Vanta orders response was not an array")
        return data

    async def get_trades(self) -> list[dict[str, Any]]:
        await self._ensure_identity()
        data = await self._request("GET", "/api/v1/trading/trades")
        self._require_account(data)
        if not isinstance(data, list):
            raise VantaResponseError("Vanta trades response was not an array")
        return data

    async def get_payouts(self, limit: int = 50) -> dict[str, Any]:
        """All payout rows, following nextCursor; accrual comes from the first page."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer from 1 to 200")
        await self._ensure_identity()
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        cursor: str | None = None
        first: dict[str, Any] | None = None
        for _page in range(_MAX_PAYOUT_PAGES):
            params: dict[str, Any] = {"limit": limit}
            if cursor is not None:
                params["cursor"] = cursor
            data = await self._request("GET", "/api/v1/trading/payouts", params=params)
            self._require_account(data)
            if not isinstance(data, dict):
                raise VantaResponseError("Vanta payout response was not an object")
            page_items = data.get("items")
            if not isinstance(page_items, list):
                raise VantaResponseError("Vanta payout response had no items array")
            for item in page_items:
                if not isinstance(item, dict):
                    raise VantaResponseError("Vanta payout item was not an object")
                items.append(item)
            if first is None:
                first = data
            next_cursor = data.get("nextCursor")
            if next_cursor is None:
                return {
                    "items": items,
                    "accrualStatus": first.get("accrualStatus"),
                    "accrual": first.get("accrual"),
                }
            if not isinstance(next_cursor, str) or not next_cursor:
                raise VantaResponseError("Vanta payout cursor was malformed")
            if next_cursor in seen:
                raise VantaResponseError("Vanta payout cursor repeated")
            seen.add(next_cursor)
            cursor = next_cursor
        raise VantaResponseError("Vanta payout pagination exceeded its safety limit")

    async def get_trade_pairs(self) -> dict[str, Any]:
        await self._ensure_identity()
        data = await self._request("GET", "/api/v1/trading/trade-pairs")
        self._require_account(data, require_id=True)
        if not isinstance(data, dict):
            raise VantaResponseError("Vanta trade-pairs response was not an object")
        return data

    # --- Order preparation; dispatch is denied ---

    def prepare_order(self, payload: Mapping[str, Any]) -> PreparedOrder:
        """Validate one documented order variant and return the exact signed body."""
        return _prepare_order_payload(self.config.account_id, payload)

    async def place_order(self, payload: Mapping[str, Any]) -> NoReturn:
        """Prepare the request, then refuse locally; no network request is made."""
        raise MutationDenied(MUTATION_DENIED, self.prepare_order(payload))


def _prepare_order_payload(account_id: str, payload: Mapping[str, Any]) -> PreparedOrder:
    """Validate one documented order variant and return the exact signed body."""
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping of trade fields")
    if "accountId" in payload or "trade" in payload:
        raise ValueError("payload must be the trade object; accountId is bound by the client")
    trade = dict(payload)
    execution = trade.get("execution_type")
    if execution not in _ALLOWED_FIELDS:
        raise ValueError(
            "execution_type must be one of MARKET, LIMIT, BRACKET, LIMIT_EDIT, "
            "LIMIT_CANCEL, FLAT_ALL"
        )
    unknown = sorted(str(key) for key in trade if key not in _ALLOWED_FIELDS[execution])
    if unknown:
        raise ValueError(f"unsupported fields for {execution}: {unknown}")

    if execution in ("MARKET", "LIMIT"):
        _trade_pair(trade.get("trade_pair"))
        if trade.get("order_type") not in _ORDER_TYPES:
            raise ValueError("order_type must be LONG, SHORT or FLAT")
        sizes = [name for name in _SIZE_FIELDS if name in trade]
        if len(sizes) != 1:
            raise ValueError("exactly one of value, quantity or leverage is required")
        _positive(trade[sizes[0]], sizes[0])
        for level in ("stop_loss", "take_profit"):
            if level in trade:
                _positive(trade[level], level)
        if execution == "LIMIT":
            if "limit_price" not in trade:
                raise ValueError("limit_price is required for LIMIT orders")
            _positive(trade["limit_price"], "limit_price")
    elif execution == "BRACKET":
        if "trade_pair" in trade:
            _trade_pair(trade["trade_pair"])
        if "order_type" in trade and trade["order_type"] not in _ORDER_TYPES:
            raise ValueError("order_type must be LONG, SHORT or FLAT")
        if "position_uuid" in trade:
            _identifier(trade["position_uuid"], "position_uuid")
        _validate_bracket_orders(trade.get("bracket_orders"), require_price_leg=True)
    elif execution == "LIMIT_EDIT":
        _trade_pair(trade.get("trade_pair"))
        if trade.get("order_type") not in ("LONG", "SHORT"):
            raise ValueError("LIMIT_EDIT order_type must be LONG or SHORT")
        if "quantity" not in trade:
            raise ValueError("LIMIT_EDIT requires quantity")
        _positive(trade["quantity"], "quantity")
        if "position_uuid" in trade:
            _identifier(trade["position_uuid"], "position_uuid")
        _validate_bracket_orders(trade.get("bracket_orders"), require_price_leg=False)
    elif execution == "LIMIT_CANCEL":
        _identifier(trade.get("order_uuid"), "order_uuid")
    else:  # FLAT_ALL
        _identifier(trade.get("order_uuid"), "order_uuid")

    body = {"accountId": account_id, "trade": trade}
    return PreparedOrder("POST", "/api/v1/trading/orders", body, _json_value(body))


class _OrderTransport(httpx.AsyncBaseTransport):
    """Restricts POST execution strictly to /api/v1/trading/orders on the official host."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method.upper() != "POST" or request.url.path != "/api/v1/trading/orders":
            raise PermissionError("VantaOrderClient only permits POST /api/v1/trading/orders")
        if request.url.scheme != "https" or request.url.host != "app.vantatrading.io":
            raise PermissionError("Vanta credentials may only be sent to https://app.vantatrading.io")
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


class VantaOrderClient:
    """Explicitly armed single-order POST client for Vanta.

    Isolated from read-only VantaClient so GET clients remain transport-denied for
    mutations. Never retries POSTs (Vanta has no order idempotency key).
    """

    def __init__(
        self,
        config: VantaConfig,
        *,
        confirm_account_id: str,
        allow_execution: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not isinstance(config, VantaConfig):
            raise TypeError("config must be a VantaConfig")
        if allow_execution is not True:
            raise PermissionError("VANTA_EXECUTION_NOT_ENABLED")
        if confirm_account_id != config.account_id:
            raise PermissionError("VANTA_ACCOUNT_CONFIRMATION_MISMATCH")
        self.config = config
        self._http = httpx.AsyncClient(
            base_url=config.base_url,
            transport=_OrderTransport(transport or httpx.AsyncHTTPTransport()),
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )

    async def __aenter__(self) -> VantaOrderClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    async def submit_order(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Validate, sign, and submit one order to POST /api/v1/trading/orders; never retries."""
        prepared = _prepare_order_payload(self.config.account_id, payload)
        body_bytes = prepared.body_json.encode("utf-8")
        headers = signed_headers(
            self.config.api_key,
            self.config.api_secret,
            "POST",
            prepared.path,
            body_bytes,
        )
        try:
            response = await self._http.post(prepared.path, content=body_bytes, headers=headers)
        except httpx.TimeoutException as exc:
            raise VantaTransportError("Vanta order POST timed out; outcome unknown (not retried)") from exc
        except httpx.HTTPError as exc:
            raise VantaTransportError("Vanta order transport failure; outcome unknown") from exc
        if response.status_code >= 400:
            raise _error_from_response(response)
        try:
            parsed = json.loads(
                response.content, parse_float=Decimal, parse_constant=_reject_constant
            )
        except (ValueError, UnicodeDecodeError) as exc:
            raise VantaResponseError("Vanta response was not valid JSON") from exc
        if not isinstance(parsed, dict) or parsed.get("success") is not True:
            raise VantaResponseError("Vanta order response envelope was not successful")
        data = parsed.get("data")
        if not isinstance(data, dict):
            raise VantaResponseError("Vanta order response data was not an object")
        return data
