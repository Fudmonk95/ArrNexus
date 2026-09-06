from __future__ import annotations

import asyncio
from typing import Any

from .arr import LidarrClient, ProwlarrClient, RadarrClient, SonarrClient
from .connections import get_connection
from .jellyfin import JellyfinClient
from .seerr import SeerrClient


async def status_rows() -> list[dict[str, Any]]:
    specs = [
        ("radarr", "Radarr", RadarrClient().status),
        ("sonarr", "Sonarr", SonarrClient().status),
        ("lidarr", "Lidarr", LidarrClient().status),
        ("prowlarr", "Prowlarr", ProwlarrClient().status),
        ("jellyfin", "Jellyfin", JellyfinClient().status),
        ("seerr", "Seerr", SeerrClient().status),
    ]
    results = await asyncio.gather(*(fn() for _, _, fn in specs), return_exceptions=True)
    rows: list[dict[str, Any]] = []
    for (key, label, _), result in zip(specs, results):
        conn = get_connection(key)
        if isinstance(result, Exception):
            rows.append({
                "key": key, "label": label, "ok": False,
                "configured": bool(conn.url and conn.api_key), "url": conn.url,
                "error": str(result), "version": "",
            })
            continue
        version = ""
        if isinstance(result, dict):
            version = str(result.get("version") or result.get("Version") or result.get("commitTag") or "")
        rows.append({
            "key": key, "label": label, "ok": True,
            "configured": bool(conn.url and conn.api_key), "url": conn.url,
            "error": "", "version": version,
        })
    return rows
