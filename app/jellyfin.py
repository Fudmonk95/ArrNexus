from __future__ import annotations
import httpx
from .connections import get_connection


async def search_jellyfin(term: str, limit: int = 5) -> dict:
    conn = get_connection("jellyfin")
    if not conn.api_key:
        return {"configured": False, "found": False, "items": []}
    try:
        headers = {"X-Emby-Token": conn.api_key}
        params = {"searchTerm": term, "recursive": "true", "limit": limit, "includeItemTypes": "Movie,Series,MusicArtist,MusicAlbum"}
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(f"{conn.url.rstrip('/')}/Items", headers=headers, params=params)
        r.raise_for_status()
        items = (r.json().get("Items") or [])
        return {"configured": True, "found": bool(items), "items": items}
    except Exception as exc:
        return {"configured": True, "found": False, "items": [], "error": str(exc)}
