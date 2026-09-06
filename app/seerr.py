from __future__ import annotations

import httpx

from .connections import get_connection

_HTTP: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _HTTP
    if _HTTP is None or _HTTP.is_closed:
        _HTTP = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=6.0), follow_redirects=True)
    return _HTTP


class SeerrError(RuntimeError):
    pass


class SeerrClient:
    def __init__(self):
        c = get_connection("seerr")
        self.base_url = c.url.rstrip("/")
        self.api_key = c.api_key

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def request(self, path: str, params: dict | None = None):
        if not self.configured:
            raise SeerrError("Seerr is not configured")
        response = await _client().get(
            f"{self.base_url}/{path.lstrip('/')}",
            headers={"X-Api-Key": self.api_key, "Accept": "application/json"},
            params=params,
        )
        if response.status_code >= 400:
            raise SeerrError(f"Seerr: HTTP {response.status_code}: {response.text[:500]}")
        return response.json()

    async def status(self):
        return await self.request("/api/v1/status")

    async def requests(self, take: int = 50, skip: int = 0, filter_name: str = "all"):
        return await self.request(
            "/api/v1/request",
            {"take": take, "skip": skip, "filter": filter_name, "sort": "modified", "sortDirection": "desc", "mediaType": "all"},
        )

    async def recent_requests(self, limit: int = 60) -> list[dict]:
        payload = await self.requests(take=max(10, min(100, limit)), skip=0)
        return list(payload.get("results") or [])[:limit]
