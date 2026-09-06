from __future__ import annotations

from typing import Any

from .arr import RadarrClient, SonarrClient


async def discover_lookup(term: str, media_type: str) -> list[dict]:
    client = RadarrClient() if media_type == "movie" else SonarrClient()
    return list(await client.lookup(term) or [])[:30]


async def _default_root(client) -> str:
    roots = await client.roots()
    if not roots:
        raise RuntimeError(f"{client.name} has no root folders configured")
    # Prefer a root that is accessible and has the most free space when the API provides it.
    roots = sorted(roots, key=lambda x: int(x.get("freeSpace") or 0), reverse=True)
    return str(roots[0].get("path") or "")


async def existing(media_type: str, *, tmdb_id: int | None = None, tvdb_id: int | None = None, imdb_id: str = "") -> dict | None:
    if media_type == "movie":
        rows = await RadarrClient().movies()
        for row in rows or []:
            if tmdb_id and int(row.get("tmdbId") or 0) == int(tmdb_id): return row
            if imdb_id and str(row.get("imdbId") or "") == imdb_id: return row
    else:
        rows = await SonarrClient().series()
        for row in rows or []:
            if tvdb_id and int(row.get("tvdbId") or 0) == int(tvdb_id): return row
            if tmdb_id and int(row.get("tmdbId") or 0) == int(tmdb_id): return row
            if imdb_id and str(row.get("imdbId") or "") == imdb_id: return row
    return None


async def add_candidate(candidate: dict, media_type: str, root: str = "auto", search: bool = True, monitored: bool = True) -> dict[str, Any]:
    if media_type == "movie":
        client = RadarrClient()
        ext = int(candidate.get("tmdbId") or 0)
        owned = await existing("movie", tmdb_id=ext or None, imdb_id=str(candidate.get("imdbId") or ""))
        if owned:
            return {"item": owned, "existing": True, "root": owned.get("path", "")}
        actual_root = await _default_root(client) if not root or root == "auto" else root
        item = await client.add_movie(candidate, actual_root, search=search, monitored=monitored)
        return {"item": item, "existing": False, "root": actual_root}

    client = SonarrClient()
    ext = int(candidate.get("tvdbId") or 0)
    owned = await existing("tv", tvdb_id=ext or None, tmdb_id=int(candidate.get("tmdbId") or 0) or None, imdb_id=str(candidate.get("imdbId") or ""))
    if owned:
        return {"item": owned, "existing": True, "root": owned.get("path", "")}
    actual_root = await _default_root(client) if not root or root == "auto" else root
    item = await client.add_series(candidate, actual_root, search=search, monitored=monitored)
    return {"item": item, "existing": False, "root": actual_root}
