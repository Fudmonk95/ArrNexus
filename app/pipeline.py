from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from .arr import RadarrClient, SonarrClient
from .db import pipeline_upsert
from .seerr import SeerrClient
from . import zurg

_STAGE_ORDER = {
    "requested": 10,
    "approved": 20,
    "searching": 30,
    "grabbed": 40,
    "scraping": 50,
    "mounted": 60,
    "imported": 70,
    "complete": 80,
    "failed": 90,
    "declined": 90,
}

REQUEST_STATUS = {1: "pending", 2: "approved", 3: "declined", 4: "failed", 5: "completed"}
MEDIA_STATUS = {1: "unknown", 2: "pending", 3: "processing", 4: "partially_available", 5: "available", 6: "blocklisted", 7: "deleted"}

_CACHE: tuple[float, dict[str, Any]] = (0.0, {})
_CACHE_TTL = 2.0


def _rows(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        return list(payload.get("records") or payload.get("results") or [])
    return list(payload or [])


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _request_type(request: dict) -> str:
    return str(request.get("type") or request.get("mediaType") or (request.get("media") or {}).get("mediaType") or "movie").lower()


def _external_ids(request: dict) -> tuple[int, int]:
    media = request.get("media") or {}
    return _int(media.get("tmdbId") or request.get("mediaId")), _int(media.get("tvdbId") or request.get("tvdbId"))


def _queue_match(queue_rows: list[dict], media_type: str, arr_id: int) -> dict | None:
    if not arr_id:
        return None
    key = "movieId" if media_type == "movie" else "seriesId"
    return next((row for row in queue_rows if _int(row.get(key)) == arr_id), None)


def _history_match(history_rows: list[dict], media_type: str, arr_id: int) -> dict | None:
    if not arr_id:
        return None
    key = "movieId" if media_type == "movie" else "seriesId"
    return next((row for row in history_rows if _int(row.get(key)) == arr_id), None)


def _progress(queue: dict) -> str:
    size = _int(queue.get("size")); left = _int(queue.get("sizeleft") or queue.get("sizeLeft"))
    if size > 0:
        pct = max(0, min(100, round((size - left) / size * 100)))
        return f"{pct}% · {queue.get('status') or queue.get('trackedDownloadStatus') or 'queued'}"
    return str(queue.get("status") or queue.get("trackedDownloadStatus") or "queued")


def _stage_label(stage: str) -> str:
    return {
        "requested": "Requested",
        "approved": "Approved",
        "searching": "Searching",
        "grabbed": "Grabbed",
        "scraping": "Zurg scraping",
        "mounted": "Mounted in Zurg",
        "imported": "Imported",
        "complete": "Finished",
        "failed": "Failed",
        "declined": "Declined",
    }.get(stage, stage.title())


async def live_snapshot(force: bool = False) -> dict[str, Any]:
    global _CACHE
    if not force and time.monotonic() - _CACHE[0] < _CACHE_TTL:
        return _CACHE[1]

    seerr = SeerrClient(); radarr = RadarrClient(); sonarr = SonarrClient()
    results = await asyncio.gather(
        seerr.recent_requests(60),
        radarr.movies(), radarr.queue(), radarr.history(),
        sonarr.series(), sonarr.queue(), sonarr.history(),
        return_exceptions=True,
    )
    if isinstance(results[0], Exception):
        raise results[0]

    requests = results[0] or []
    movies = [] if isinstance(results[1], Exception) else (results[1] or [])
    rad_queue = [] if isinstance(results[2], Exception) else _rows(results[2])
    rad_history = [] if isinstance(results[3], Exception) else _rows(results[3])
    series = [] if isinstance(results[4], Exception) else (results[4] or [])
    son_queue = [] if isinstance(results[5], Exception) else _rows(results[5])
    son_history = [] if isinstance(results[6], Exception) else _rows(results[6])

    movie_by_tmdb = {str(x.get("tmdbId")): x for x in movies if x.get("tmdbId")}
    tv_by_tvdb = {str(x.get("tvdbId")): x for x in series if x.get("tvdbId")}
    tv_by_tmdb = {str(x.get("tmdbId")): x for x in series if x.get("tmdbId")}

    rows: list[dict[str, Any]] = []
    for request in requests:
        media_type = _request_type(request)
        tmdb_id, tvdb_id = _external_ids(request)
        media = request.get("media") or {}
        req_status_num = _int(request.get("status"))
        media_status_num = _int(media.get("status4k") if request.get("is4k") else media.get("status"))
        req_status = REQUEST_STATUS.get(req_status_num, str(req_status_num or "unknown"))
        media_status = MEDIA_STATUS.get(media_status_num, str(media_status_num or "unknown"))

        arr_item = movie_by_tmdb.get(str(tmdb_id)) if media_type == "movie" else (tv_by_tvdb.get(str(tvdb_id)) or tv_by_tmdb.get(str(tmdb_id)))
        arr_id = _int((arr_item or {}).get("id"))
        title = str((arr_item or {}).get("title") or request.get("title") or media.get("title") or f"{media_type.title()} request #{request.get('id')}")
        year = _int((arr_item or {}).get("year")) or None
        queue = _queue_match(rad_queue if media_type == "movie" else son_queue, media_type, arr_id)
        history = _history_match(rad_history if media_type == "movie" else son_history, media_type, arr_id)
        matches = await asyncio.to_thread(zurg.title_matches, title, year, 3) if arr_item else {"movies": [], "shows": [], "working": []}
        mounted_paths = matches["movies"] if media_type == "movie" else matches["shows"]
        download_paths = matches["working"]

        stage = "requested"; detail = "Waiting for approval"
        has_file = False
        if arr_item:
            has_file = bool(arr_item.get("hasFile")) if media_type == "movie" else _int((arr_item.get("statistics") or {}).get("episodeFileCount")) > 0
        if req_status == "declined":
            stage, detail = "declined", "Request declined in Seerr"
        elif req_status == "failed" or media_status == "blocklisted":
            stage, detail = "failed", "Seerr reported a failed or blocked request"
        elif media_status == "available" or req_status == "completed":
            stage, detail = "complete", "Seerr reports the media as available"
        elif has_file:
            stage, detail = "imported", "The Arr reports media files present; waiting for Seerr to confirm availability"
        elif mounted_paths:
            stage, detail = "mounted", "Media is visible in the Zurg library"
        elif download_paths:
            stage, detail = "scraping", "Zurg is exposing the request in its working views"
        elif queue:
            stage, detail = "grabbed", _progress(queue)
        elif arr_item:
            if history and str(history.get("eventType") or "").lower() == "grabbed":
                stage, detail = "grabbed", str(history.get("sourceTitle") or "Release grabbed")
            elif media_status == "processing":
                stage, detail = "searching", "Seerr is processing the approved request through the Arr stack"
            else:
                stage, detail = "searching", "Added to the Arr; waiting for a release"
        elif req_status == "approved":
            stage, detail = "approved", "Approved in Seerr; waiting for the Arr entry"

        external_id = str(tmdb_id or tvdb_id or "")
        item_key = f"seerr:{request.get('id')}"
        zurg_path = (mounted_paths or download_paths or [""])[0]
        row = {
            "key": item_key,
            "request_id": _int(request.get("id")),
            "media_type": media_type,
            "title": title,
            "year": year,
            "external_id": external_id,
            "service": "Radarr" if media_type == "movie" else "Sonarr",
            "arr_id": arr_id or None,
            "stage": stage,
            "stage_label": _stage_label(stage),
            "stage_order": _STAGE_ORDER.get(stage, 0),
            "detail": detail,
            "request_status": req_status,
            "media_status": media_status,
            "zurg_path": zurg_path,
            "updated_at": request.get("updatedAt") or request.get("createdAt") or "",
            "requested_by": ((request.get("requestedBy") or {}).get("displayName") or (request.get("requestedBy") or {}).get("username") or ""),
        }
        rows.append(row)
        pipeline_upsert(row)

    summary = {
        "total": len(rows),
        "active": sum(1 for x in rows if x["stage"] not in {"complete", "failed", "declined"}),
        "finished": sum(1 for x in rows if x["stage"] == "complete"),
        "failed": sum(1 for x in rows if x["stage"] == "failed"),
    }
    snapshot = {"ok": True, "summary": summary, "rows": rows}
    _CACHE = (time.monotonic(), snapshot)
    return snapshot
