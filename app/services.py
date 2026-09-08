from __future__ import annotations

import asyncio
import copy
import time
from datetime import datetime, timezone
from typing import Any

from .arr import LidarrClient, ProwlarrClient, RadarrClient, SonarrClient
from .connections import get_connection
from .jellyfin import JellyfinClient
from .seerr import SeerrClient

_SERVICE_SPECS = (
    ("radarr", "Radarr", RadarrClient),
    ("sonarr", "Sonarr", SonarrClient),
    ("lidarr", "Lidarr", LidarrClient),
    ("prowlarr", "Prowlarr", ProwlarrClient),
    ("jellyfin", "Jellyfin", JellyfinClient),
    ("seerr", "Seerr", SeerrClient),
)

_CACHE: dict[str, Any] = {
    "updated_monotonic": 0.0,
    "updated_at": "",
    "rows": [],
    "error": "",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _placeholder_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, label, _ in _SERVICE_SPECS:
        conn = get_connection(key)
        rows.append({
            "key": key,
            "label": label,
            "ok": False,
            "configured": bool(conn.url and conn.api_key),
            "url": conn.url,
            "error": "Health check warming up",
            "version": "",
            "checking": True,
        })
    return rows


async def _one_status(key: str, label: str, factory, timeout: float) -> dict[str, Any]:
    conn = get_connection(key)
    try:
        result = await asyncio.wait_for(factory().status(), timeout=timeout)
    except Exception as exc:
        return {
            "key": key,
            "label": label,
            "ok": False,
            "configured": bool(conn.url and conn.api_key),
            "url": conn.url,
            "error": str(exc),
            "version": "",
            "checking": False,
        }

    version = ""
    if isinstance(result, dict):
        version = str(result.get("version") or result.get("Version") or result.get("commitTag") or "")
    return {
        "key": key,
        "label": label,
        "ok": True,
        "configured": bool(conn.url and conn.api_key),
        "url": conn.url,
        "error": "",
        "version": version,
        "checking": False,
    }


async def status_rows(timeout: float = 7.0) -> list[dict[str, Any]]:
    return await asyncio.gather(*(
        _one_status(key, label, factory, timeout)
        for key, label, factory in _SERVICE_SPECS
    ))


async def refresh_status() -> list[dict[str, Any]]:
    rows = await status_rows()
    _CACHE.update({
        "updated_monotonic": time.monotonic(),
        "updated_at": _now_iso(),
        "rows": rows,
        "error": "",
    })
    return rows


def cached_status_rows() -> list[dict[str, Any]]:
    rows = _CACHE.get("rows") or []
    return copy.deepcopy(rows if rows else _placeholder_rows())


def cache_state() -> dict[str, Any]:
    updated = float(_CACHE.get("updated_monotonic") or 0)
    return {
        "updated_at": _CACHE.get("updated_at") or "",
        "age_seconds": max(0.0, time.monotonic() - updated) if updated else None,
        "error": _CACHE.get("error") or "",
        "ready": bool(_CACHE.get("rows")),
    }


async def status_loop(interval: float = 30.0) -> None:
    while True:
        try:
            await refresh_status()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _CACHE["error"] = str(exc)
        await asyncio.sleep(interval)
