"""Async read-only Propr REST transport. All network mutations fail before I/O."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import quote
import httpx

from connectors.common.validation import finite_json_loads, strict_decimal, validate_relative_api_path


class RemoteAPIError(RuntimeError):
    """Redacted provider error; never include response bodies or auth headers."""


class ProviderHTTPError(RemoteAPIError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"Provider HTTP {status_code}")


class ProviderRateLimited(ProviderHTTPError):
    pass


@dataclass(frozen=True)
class TransportPolicy:
    """Non-overridable read-only policy for real provider HTTP clients."""

    def check(self, method: str):
        if method.upper() not in ("GET", "HEAD", "OPTIONS"):
            raise PermissionError("Mutation prohibited at HTTP transport boundary")


class _ReadOnlyTransport(httpx.AsyncBaseTransport):
    """Last HTTP boundary: even accidental raw client.post() is denied."""

    def __init__(self, inner):
        self.inner = inner

    async def handle_async_request(self, request):
        TransportPolicy().check(request.method)
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


class ProprRest:
    def __init__(self, base_url: str, api_key: str, policy: TransportPolicy | None = None, transport=None):
        if not base_url.startswith("https://"):
            raise ValueError("Propr REST requires HTTPS")
        if not api_key:
            raise ValueError("Propr API key required")
        if policy is not None and type(policy) is not TransportPolicy:
            raise PermissionError("Custom HTTP policies cannot override read-only safety")
        self.policy = TransportPolicy()
        self.client = httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(timeout=10.0, connect=5.0),
            follow_redirects=False,
            headers={"X-API-Key": api_key},
            transport=_ReadOnlyTransport(transport or httpx.AsyncHTTPTransport()),
        )

    async def request(self, method: str, path: str, **kwargs) -> Any:
        self.policy.check(method)
        validate_relative_api_path(path)
        if "headers" in kwargs or "auth" in kwargs or "json" in kwargs or "data" in kwargs:
            raise PermissionError("Read-only request cannot override credentials or send a body")
        try:
            response = await self.client.request(method.upper(), path, **kwargs)
            response.raise_for_status()
            if method.upper() == "HEAD" or response.status_code == 204:
                return {}
            # Preserve exact JSON numeric lexemes instead of rounding to binary floats.
            return finite_json_loads(response.content, parse_float=Decimal)
        except httpx.TimeoutException as exc:
            raise RemoteAPIError("Provider timeout; outcome not inferred") from exc
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code == 429:
                raise ProviderRateLimited(code) from None
            raise ProviderHTTPError(code) from None
        except httpx.HTTPError as exc:
            raise RemoteAPIError("Provider transport failure") from exc
        except (ValueError, UnicodeDecodeError) as exc:
            raise RemoteAPIError("Unexpected provider response encoding") from exc

    async def pages(self, path: str, *, page_size: int = 20):
        """Complete documented limit/offset pages, with a hard bounded count."""
        if not 1 <= page_size <= 100:
            raise ValueError("Invalid page size")
        result = []
        offset = 0
        while True:
            data = await self.request("GET", path, params={"offset": offset, "limit": page_size})
            if isinstance(data, dict):
                rows = data.get("data")
            elif isinstance(data, list):
                rows = data
            else:
                rows = None
            if not isinstance(rows, list):
                raise RemoteAPIError("Unexpected pagination response")
            result.extend(rows)
            if len(rows) < page_size:
                return result
            offset += len(rows)
            if offset >= 200000:
                raise RemoteAPIError("Pagination safety limit")

    async def close(self):
        await self.client.aclose()


_PINNED_PROPR_REST_URL = "https://api.propr.xyz/v1"
_VALID_TIFS = frozenset({"IOC", "FOK", "GTC", "GTX"})


class ProprOrderClient:
    """Explicitly armed single-order POST client for Propr.

    Isolated from read-only ProprRest so GET paths retain their non-overridable
    read-only TransportPolicy. Never batches or retries uncertain POST requests.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        account_id: str,
        confirm_account_id: str,
        allow_execution: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if allow_execution is not True:
            raise PermissionError("PROPR_EXECUTION_NOT_ENABLED")
        clean_url = base_url.rstrip("/")
        if clean_url != _PINNED_PROPR_REST_URL:
            raise ValueError("Propr order execution requires pinned https://api.propr.xyz/v1 endpoint")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("Propr API key required")
        if (
            not isinstance(account_id, str)
            or not account_id.strip()
            or "/" in account_id
            or ".." in account_id
        ):
            raise ValueError("Valid explicit account_id required")
        if confirm_account_id != account_id:
            raise PermissionError("PROPR_ACCOUNT_CONFIRMATION_MISMATCH")
        self.base_url = clean_url
        self.account_id = account_id
        self.client = httpx.AsyncClient(
            base_url=clean_url,
            timeout=httpx.Timeout(timeout=10.0, connect=5.0),
            follow_redirects=False,
            headers={"X-API-Key": api_key},
            transport=transport or httpx.AsyncHTTPTransport(),
        )

    async def _post_once(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        validate_relative_api_path(path)
        try:
            response = await self.client.post(path, json=payload)
            response.raise_for_status()
            return finite_json_loads(response.content, parse_float=Decimal)
        except httpx.TimeoutException as exc:
            raise RemoteAPIError("Provider POST timeout; outcome unknown (not retried)") from exc
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code == 429:
                raise ProviderRateLimited(code) from None
            raise ProviderHTTPError(code) from None
        except httpx.HTTPError as exc:
            raise RemoteAPIError("Provider transport failure; outcome unknown") from exc
        except (ValueError, UnicodeDecodeError) as exc:
            raise RemoteAPIError("Unexpected provider response encoding") from exc

    async def submit_market_order(
        self,
        *,
        intent_id: str,
        asset: str,
        side: str,
        position_side: str,
        quantity: Decimal,
        reduce_only: bool = False,
        time_in_force: str = "IOC",
        base: str | None = None,
        quote_asset: str = "USDC",
    ) -> dict[str, Any]:
        """Submit one single market order with pre-committed intentId; never retries."""
        if not isinstance(intent_id, str) or not intent_id.strip():
            raise ValueError("Pre-committed intent_id required")
        if not isinstance(asset, str) or not asset.strip():
            raise ValueError("Valid asset required")
        if side not in ("buy", "sell") or position_side not in ("long", "short"):
            raise ValueError("Invalid order side or position_side")
        if type(reduce_only) is not bool:
            raise ValueError("reduce_only must be bool")
        if time_in_force not in _VALID_TIFS:
            raise ValueError("Unsupported time_in_force")
        qty = strict_decimal(quantity, "quantity", error_cls=ValueError)
        if qty <= 0:
            raise ValueError("Order quantity must be positive")

        resolved_base = (base or asset.split("/")[-1]).strip()
        exchange_asset = asset.split("/")[0] if "/" in asset else asset
        order_payload: dict[str, Any] = {
            "accountId": self.account_id,
            "intentId": intent_id,
            "exchange": "hyperliquid",
            "type": "market",
            "side": side,
            "positionSide": position_side,
            "productType": "perp",
            "timeInForce": time_in_force,
            "asset": exchange_asset,
            "base": resolved_base,
            "quote": quote_asset,
            "quantity": str(qty),
            "reduceOnly": reduce_only,
            "closePosition": False,
        }
        path = f"/accounts/{quote(self.account_id, safe='')}/orders"
        raw = await self._post_once(path, {"orders": [order_payload]})
        rows = raw.get("data", raw) if isinstance(raw, dict) else raw
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise RemoteAPIError("Unexpected Propr order ack shape")
        ack = rows[0]
        if (
            ack.get("intentId") != intent_id
            or ack.get("accountId") != self.account_id
            or not isinstance(ack.get("orderId"), str)
            or not ack["orderId"]
        ):
            raise RemoteAPIError("Propr order ack identity mismatch")
        return ack

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel one order by broker orderId; never retries."""
        if not isinstance(order_id, str) or not order_id.strip() or "/" in order_id or ".." in order_id:
            raise ValueError("Valid order_id required")
        path = f"/accounts/{quote(self.account_id, safe='')}/orders/{quote(order_id, safe='')}/cancel"
        raw = await self._post_once(path)
        if not isinstance(raw, dict):
            raise RemoteAPIError("Unexpected Propr cancel response shape")
        return raw

    async def close(self) -> None:
        await self.client.aclose()
