from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from pathlib import Path
import re
import time
from typing import Any

from .arr import LidarrClient, RadarrClient, SonarrClient
from .db import pipeline_upsert
from .seerr import SeerrClient
from . import zurg

_STAGE_ORDER = {
    "requested": 10,
    "approved": 20,
    "searching": 30,
    "grabbed": 40,
    "working": 50,
    "scraping": 55,
    "mounted": 60,
    "imported": 70,
    "available": 80,
    "complete": 80,  # compatibility with v11.0.0 history
    "failed": 90,
    "declined": 90,
}
_TERMINAL = {"available", "complete", "failed", "declined"}

REQUEST_STATUS = {1: "pending", 2: "approved", 3: "declined", 4: "failed", 5: "completed"}
MEDIA_STATUS = {1: "unknown", 2: "pending", 3: "processing", 4: "partially_available", 5: "available", 6: "blocklisted", 7: "deleted"}

_CACHE: dict[str, Any] = {
    "updated_monotonic": 0.0,
    "updated_at": "",
    "snapshot": {
        "ok": True,
        "stale": True,
        "errors": [],
        "summary": {"total": 0, "active": 0, "finished": 0, "failed": 0, "working": 0},
        "rows": [],
    },
    "error": "",
    "refreshing": False,
}
_CATALOG: dict[str, Any] = {
    "updated_monotonic": 0.0,
    "movies": [],
    "series": [],
}
_REFRESH_LOCK = asyncio.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rows(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        return list(payload.get("records") or payload.get("results") or [])
    return list(payload or [])


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _norm(text: str) -> str:
    text = str(text or "").casefold()
    text = re.sub(r"\b(19|20)\d{2}\b", " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


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
    size = _int(queue.get("size"))
    left = _int(queue.get("sizeleft") or queue.get("sizeLeft"))
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
        "working": "Zurg working",
        "scraping": "Zurg scraping",
        "mounted": "Mounted in Zurg",
        "imported": "Imported",
        "available": "Available",
        "complete": "Available",
        "failed": "Failed",
        "declined": "Declined",
    }.get(stage, stage.title())


def _milestone(stage: str, detail: str) -> dict[str, str]:
    return {"stage": stage, "label": _stage_label(stage), "detail": detail}


def _append_milestone(milestones: list[dict[str, str]], stage: str, detail: str) -> None:
    if not any(x.get("stage") == stage for x in milestones):
        milestones.append(_milestone(stage, detail))


def _best_stage(milestones: list[dict[str, str]], fallback: tuple[str, str]) -> tuple[str, str]:
    if not milestones:
        return fallback
    best = max(milestones, key=lambda x: _STAGE_ORDER.get(str(x.get("stage")), 0))
    return str(best.get("stage") or fallback[0]), str(best.get("detail") or fallback[1])


async def _catalog(radarr: RadarrClient, sonarr: SonarrClient) -> tuple[list[dict], list[dict], list[str]]:
    errors: list[str] = []
    if time.monotonic() - float(_CATALOG.get("updated_monotonic") or 0) < 60 and (_CATALOG.get("movies") or _CATALOG.get("series")):
        return list(_CATALOG.get("movies") or []), list(_CATALOG.get("series") or []), errors

    results = await asyncio.gather(
        asyncio.wait_for(radarr.movies(), timeout=12),
        asyncio.wait_for(sonarr.series(), timeout=12),
        return_exceptions=True,
    )
    movies = list(_CATALOG.get("movies") or [])
    series = list(_CATALOG.get("series") or [])
    if isinstance(results[0], Exception):
        errors.append(f"Radarr catalogue: {results[0]}")
    else:
        movies = list(results[0] or [])
    if isinstance(results[1], Exception):
        errors.append(f"Sonarr catalogue: {results[1]}")
    else:
        series = list(results[1] or [])
    if not isinstance(results[0], Exception) or not isinstance(results[1], Exception):
        _CATALOG.update({"updated_monotonic": time.monotonic(), "movies": movies, "series": series})
    return movies, series, errors


async def _fetch_runtime(radarr: RadarrClient, sonarr: SonarrClient, lidarr: LidarrClient, seerr: SeerrClient):
    calls = (
        ("Seerr requests", seerr.recent_requests(60)),
        ("Radarr queue", radarr.queue()),
        ("Radarr history", radarr.history()),
        ("Sonarr queue", sonarr.queue()),
        ("Sonarr history", sonarr.history()),
        ("Lidarr queue", lidarr.queue()),
        ("Lidarr history", lidarr.history()),
    )
    results = await asyncio.gather(
        *(asyncio.wait_for(coro, timeout=12) for _, coro in calls),
        return_exceptions=True,
    )
    errors: list[str] = []
    payloads: list[Any] = []
    for (label, _), result in zip(calls, results):
        if isinstance(result, Exception):
            errors.append(f"{label}: {result}")
            payloads.append([])
        else:
            payloads.append(result)
    requests = list(payloads[0] or [])
    return requests, _rows(payloads[1]), _rows(payloads[2]), _rows(payloads[3]), _rows(payloads[4]), _rows(payloads[5]), _rows(payloads[6]), errors


def _queue_title(row: dict) -> str:
    return str(
        row.get("title")
        or row.get("movieTitle")
        or row.get("seriesTitle")
        or row.get("episodeTitle")
        or row.get("albumTitle")
        or row.get("artistName")
        or row.get("downloadClient")
        or row.get("sourceTitle")
        or "Arr queue item"
    )


def _looks_like_existing(path_or_name: str, rows: list[dict[str, Any]]) -> bool:
    text = _norm(path_or_name)
    if not text:
        return False
    for row in rows:
        wanted = _norm(str(row.get("title") or ""))
        tokens = [x for x in wanted.split() if len(x) >= 3][:5]
        if tokens and all(token in text for token in tokens):
            return True
    return False


def _seerr_row(
    request: dict,
    movie_by_tmdb: dict[str, dict],
    tv_by_tvdb: dict[str, dict],
    tv_by_tmdb: dict[str, dict],
    rad_queue: list[dict],
    rad_history: list[dict],
    son_queue: list[dict],
    son_history: list[dict],
) -> dict[str, Any]:
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
    queue_rows = rad_queue if media_type == "movie" else son_queue
    history_rows = rad_history if media_type == "movie" else son_history
    queue = _queue_match(queue_rows, media_type, arr_id)
    history = _history_match(history_rows, media_type, arr_id)
    matches = zurg.title_matches(title, year, 4) if arr_item else {"movies": [], "shows": [], "downloads": [], "magic": [], "nzb": [], "working": []}

    mounted_paths = matches.get("movies", []) if media_type == "movie" else matches.get("shows", [])
    download_paths = matches.get("downloads", [])
    magic_paths = matches.get("magic", [])
    nzb_paths = matches.get("nzb", [])

    milestones: list[dict[str, str]] = []
    _append_milestone(milestones, "requested", "Request exists in Seerr")

    if req_status in {"approved", "completed"} or arr_item:
        _append_milestone(milestones, "approved", "Request approved in Seerr")
    if arr_item:
        _append_milestone(milestones, "searching", f"Added to {'Radarr' if media_type == 'movie' else 'Sonarr'}")
    if history and str(history.get("eventType") or "").lower() == "grabbed":
        _append_milestone(milestones, "grabbed", str(history.get("sourceTitle") or "Release grabbed"))
    elif queue:
        _append_milestone(milestones, "grabbed", _progress(queue))

    if download_paths:
        _append_milestone(milestones, "working", "Zurg is exposing the item in __downloads__")
    if magic_paths or nzb_paths:
        section = "__magic__" if magic_paths else "__nzb__"
        _append_milestone(milestones, "scraping", f"Zurg is working on the item in {section}")
    if mounted_paths:
        _append_milestone(milestones, "mounted", "Media is visible in the Zurg library")

    has_file = False
    if arr_item:
        has_file = bool(arr_item.get("hasFile")) if media_type == "movie" else _int((arr_item.get("statistics") or {}).get("episodeFileCount")) > 0
    if has_file:
        _append_milestone(milestones, "imported", "The Arr reports media files present")
    if media_status == "available" or req_status == "completed":
        _append_milestone(milestones, "available", "Seerr reports the media as available")

    if req_status == "declined":
        stage, detail = "declined", "Request declined in Seerr"
        _append_milestone(milestones, stage, detail)
    elif req_status == "failed" or media_status == "blocklisted":
        stage, detail = "failed", "Seerr reported a failed or blocked request"
        _append_milestone(milestones, stage, detail)
    else:
        stage, detail = _best_stage(milestones, ("requested", "Waiting for approval"))

    zurg_path = (mounted_paths or magic_paths or nzb_paths or download_paths or [""])[0]
    external_id = str(tmdb_id or tvdb_id or "")
    return {
        "key": f"seerr:{request.get('id')}",
        "request_id": _int(request.get("id")),
        "source": "Seerr",
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
        "milestones": milestones,
    }


def _append_unmatched_arr_queue(rows: list[dict[str, Any]], queue_rows: list[dict], media_type: str, service: str) -> None:
    existing_ids = {(str(x.get("service")), _int(x.get("arr_id"))) for x in rows if x.get("arr_id")}
    key_name = {"movie": "movieId", "tv": "seriesId", "music": "albumId"}.get(media_type, "seriesId")
    for q in queue_rows:
        arr_id = _int(q.get(key_name))
        if arr_id and (service, arr_id) in existing_ids:
            continue
        title = _queue_title(q)
        item_id = q.get("id") or q.get("downloadId") or f"{arr_id}:{_norm(title)[:40]}"
        detail = _progress(q)
        row = {
            "key": f"{service.lower()}:queue:{item_id}",
            "request_id": 0,
            "source": f"{service} queue",
            "media_type": media_type,
            "title": title,
            "year": None,
            "external_id": "",
            "service": service,
            "arr_id": arr_id or None,
            "stage": "grabbed",
            "stage_label": _stage_label("grabbed"),
            "stage_order": _STAGE_ORDER["grabbed"],
            "detail": detail,
            "request_status": "",
            "media_status": "",
            "zurg_path": "",
            "updated_at": q.get("added") or q.get("estimatedCompletionTime") or "",
            "requested_by": "—",
            "milestones": [
                _milestone("searching", f"Active in {service}"),
                _milestone("grabbed", detail),
            ],
        }
        rows.append(row)
        pipeline_upsert(row)


def _append_unmatched_zurg(rows: list[dict[str, Any]]) -> None:
    for item in zurg.working_entries(80):
        path = str(item.get("path") or "")
        name = str(item.get("name") or Path(path).name or "Zurg working item")
        if _looks_like_existing(path or name, rows):
            continue
        stage = str(item.get("stage") or "working")
        section = str(item.get("section") or "working")
        detail = f"Visible in Zurg {section} view"
        row = {
            "key": f"zurg:{section}:{_norm(path)[-120:]}",
            "request_id": 0,
            "source": "Zurg",
            "media_type": "unknown",
            "title": name,
            "year": None,
            "external_id": "",
            "service": "Zurg",
            "arr_id": None,
            "stage": stage,
            "stage_label": _stage_label(stage),
            "stage_order": _STAGE_ORDER.get(stage, 50),
            "detail": detail,
            "request_status": "",
            "media_status": "",
            "zurg_path": path,
            "updated_at": "",
            "requested_by": "—",
            "milestones": [_milestone(stage, detail)],
        }
        rows.append(row)
        pipeline_upsert(row)


async def refresh_snapshot() -> dict[str, Any]:
    if _REFRESH_LOCK.locked():
        return cached_snapshot()
    async with _REFRESH_LOCK:
        _CACHE["refreshing"] = True
        try:
            seerr = SeerrClient()
            radarr = RadarrClient()
            sonarr = SonarrClient()
            lidarr = LidarrClient()

            (movies, series, cat_errors), runtime = await asyncio.gather(
                _catalog(radarr, sonarr),
                _fetch_runtime(radarr, sonarr, lidarr, seerr),
            )
            requests, rad_queue, rad_history, son_queue, son_history, lid_queue, lid_history, runtime_errors = runtime
            errors = cat_errors + runtime_errors

            movie_by_tmdb = {str(x.get("tmdbId")): x for x in movies if x.get("tmdbId")}
            tv_by_tvdb = {str(x.get("tvdbId")): x for x in series if x.get("tvdbId")}
            tv_by_tmdb = {str(x.get("tmdbId")): x for x in series if x.get("tmdbId")}

            rows: list[dict[str, Any]] = []
            for request in requests:
                row = _seerr_row(
                    request, movie_by_tmdb, tv_by_tvdb, tv_by_tmdb,
                    rad_queue, rad_history, son_queue, son_history,
                )
                rows.append(row)
                pipeline_upsert(row)

            _append_unmatched_arr_queue(rows, rad_queue, "movie", "Radarr")
            _append_unmatched_arr_queue(rows, son_queue, "tv", "Sonarr")
            _append_unmatched_arr_queue(rows, lid_queue, "music", "Lidarr")
            _append_unmatched_zurg(rows)

            rows.sort(key=lambda x: (
                1 if x.get("stage") in _TERMINAL else 0,
                -int(x.get("stage_order") or 0),
                str(x.get("title") or "").casefold(),
            ))

            summary = {
                "total": len(rows),
                "active": sum(1 for x in rows if x.get("stage") not in _TERMINAL),
                "finished": sum(1 for x in rows if x.get("stage") in {"available", "complete"}),
                "failed": sum(1 for x in rows if x.get("stage") in {"failed", "declined"}),
                "working": sum(1 for x in rows if x.get("stage") in {"working", "scraping"}),
            }
            snapshot = {
                "ok": not bool(errors),
                "stale": False,
                "errors": errors,
                "summary": summary,
                "rows": rows,
                "updated_at": _now_iso(),
                "index": zurg.index_state(),
            }
            _CACHE.update({
                "updated_monotonic": time.monotonic(),
                "updated_at": snapshot["updated_at"],
                "snapshot": snapshot,
                "error": " · ".join(errors),
            })
            return copy.deepcopy(snapshot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _CACHE["error"] = str(exc)
            old = cached_snapshot()
            old["ok"] = False
            old["stale"] = True
            old["errors"] = list(dict.fromkeys(list(old.get("errors") or []) + [str(exc)]))
            return old
        finally:
            _CACHE["refreshing"] = False


def cached_snapshot() -> dict[str, Any]:
    snapshot = copy.deepcopy(_CACHE.get("snapshot") or {})
    updated = float(_CACHE.get("updated_monotonic") or 0)
    age = max(0.0, time.monotonic() - updated) if updated else None
    snapshot.setdefault("summary", {"total": 0, "active": 0, "finished": 0, "failed": 0, "working": 0})
    snapshot.setdefault("rows", [])
    snapshot.setdefault("errors", [])
    snapshot["age_seconds"] = age
    snapshot["stale"] = bool(age is None or age > 30)
    snapshot["refreshing"] = bool(_CACHE.get("refreshing"))
    snapshot["updated_at"] = _CACHE.get("updated_at") or snapshot.get("updated_at") or ""
    if _CACHE.get("error") and _CACHE.get("error") not in snapshot["errors"]:
        snapshot["errors"].append(_CACHE.get("error"))
    return snapshot


def tracker_state() -> dict[str, Any]:
    updated = float(_CACHE.get("updated_monotonic") or 0)
    return {
        "ready": bool(updated),
        "refreshing": bool(_CACHE.get("refreshing")),
        "updated_at": _CACHE.get("updated_at") or "",
        "age_seconds": max(0.0, time.monotonic() - updated) if updated else None,
        "error": _CACHE.get("error") or "",
        "index": zurg.index_state(),
    }


async def live_snapshot(force: bool = False) -> dict[str, Any]:
    if force:
        return await refresh_snapshot()
    return cached_snapshot()


async def tracker_loop(interval: float = 5.0) -> None:
    # Give the Zurg index loop a moment to warm before the first correlation.
    await asyncio.sleep(1.0)
    while True:
        try:
            await refresh_snapshot()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _CACHE["error"] = str(exc)
        await asyncio.sleep(interval)
