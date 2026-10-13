"""Minimal async HTTP gateway with transport-enforced read-only safety."""
from dataclasses import dataclass
from typing import Any
import httpx

class RemoteAPIError(RuntimeError):
    pass

@dataclass(frozen=True)
class TransportPolicy:
    allow_mutations: bool = False

    def check(self, method: str):
        if method.upper() not in ("GET", "HEAD", "OPTIONS") and not self.allow_mutations:
            raise PermissionError("Mutation prohibited at HTTP transport boundary")

class ProprRest:
    def __init__(self, base_url: str, api_key: str, policy: TransportPolicy | None = None, transport=None):
        self.policy = policy or TransportPolicy()
        self.client = httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(10.0),
            headers={"X-API-Key": api_key},
            transport=transport,
        )

    async def request(self, method: str, path: str, **kwargs) -> Any:
        self.policy.check(method)
        try:
            response = await self.client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as exc:
            raise RemoteAPIError("Provider timeout; outcome not inferred") from exc
        except httpx.HTTPStatusError as exc:
            raise RemoteAPIError(f"Provider HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise RemoteAPIError("Provider transport failure") from exc

    async def pages(self, path: str, *, page_size: int = 20):
        """Fetch complete documented limit/offset pages."""
        if page_size < 1:
            raise ValueError("Invalid page size")
        result = []
        offset = 0
        while True:
            data = await self.request("GET", path, params={"offset": offset, "limit": page_size})
            if isinstance(data, dict):
                rows = data.get("data")
                if not isinstance(rows, list):
                    raise RemoteAPIError("Unexpected pagination response")
            elif isinstance(data, list):
                rows = data
            else:
                raise RemoteAPIError("Unexpected pagination response")
            result.extend(rows)
            if len(rows) < page_size:
                return result
            offset += len(rows)
            if offset > 200000:
                raise RemoteAPIError("Pagination safety limit")

    async def close(self):
        await self.client.aclose()
