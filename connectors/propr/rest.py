"""Async read-only Propr REST transport. All network mutations fail before I/O."""
from dataclasses import dataclass
from typing import Any
import httpx


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
        if not isinstance(path, str) or not path.startswith("/") or "//" in path or ".." in path:
            raise ValueError("Relative API path required")
        if "headers" in kwargs or "auth" in kwargs or "json" in kwargs or "data" in kwargs:
            raise PermissionError("Read-only request cannot override credentials or send a body")
        try:
            response = await self.client.request(method.upper(), path, **kwargs)
            response.raise_for_status()
            if method.upper() == "HEAD" or response.status_code == 204:
                return {}
            return response.json()
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
