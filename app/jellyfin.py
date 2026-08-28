from __future__ import annotations
import httpx
from .connections import get_connection

_HTTP: httpx.AsyncClient | None = None

def _client() -> httpx.AsyncClient:
    global _HTTP
    if _HTTP is None or _HTTP.is_closed:
        _HTTP = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=6.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=30.0),
        )
    return _HTTP


async def search_jellyfin(term: str, limit: int = 5) -> dict:
    conn = get_connection("jellyfin")
    if not conn.api_key:
        return {"configured": False, "found": False, "items": []}
    try:
        headers = {"X-Emby-Token": conn.api_key}
        params = {"searchTerm": term, "recursive": "true", "limit": limit, "includeItemTypes": "Movie,Series,MusicArtist,MusicAlbum"}
        r = await _client().get(f"{conn.url.rstrip('/')}/Items", headers=headers, params=params)
        r.raise_for_status()
        items = (r.json().get("Items") or [])
        return {"configured": True, "found": bool(items), "items": items}
    except Exception as exc:
        return {"configured": True, "found": False, "items": [], "error": str(exc)}


async def jellyfin_status() -> dict:
    conn = get_connection("jellyfin")
    if not conn.api_key:
        raise RuntimeError("Jellyfin API key is not configured")
    headers={"X-Emby-Token":conn.api_key,"Accept":"application/json"}
    r=await _client().get(f"{conn.url.rstrip('/')}/System/Info",headers=headers)
    r.raise_for_status()
    return r.json()
