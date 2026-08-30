from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Any

from .arr import RadarrClient, SonarrClient, LidarrClient, poster_url, ArrError
from .config import settings
from .instances import discover_instances, get_instance, ArrInstance
from .scanner import ScanItem, inspect_item, normalize_title
from .routing import decide_movie, decide_tv, RouteDecision
from .importer import import_movie_source, import_tv_source, ImportErrorSafe
from .db import log_import, add_activity, learn_exact_route, track_request, request_map


def _client_for_instance(inst: ArrInstance):
    if inst.service == "radarr":
        return RadarrClient(inst.url, inst.api_key, f"Radarr/{inst.instance}")
    if inst.service == "sonarr":
        return SonarrClient(inst.url, inst.api_key, f"Sonarr/{inst.instance}")
    if inst.service == "lidarr":
        return LidarrClient(inst.url, inst.api_key, f"Lidarr/{inst.instance}")
    raise ArrError(f"Unsupported service {inst.service}")


def primary_client(service: str):
    if service == "radarr":
        return RadarrClient()
    if service == "sonarr":
        return SonarrClient()
    if service == "lidarr":
        return LidarrClient()
    raise ArrError(service)


def client_for_destination(service: str, destination_key: str):
    inst = get_instance(service, destination_key)
    if inst and inst.api_key:
        return _client_for_instance(inst), inst
    return primary_client(service), None


async def _all_instances(service: str):
    discovered = [i for i in discover_instances() if i.service == service and i.api_key]
    if discovered:
        return [(_client_for_instance(i), i) for i in discovered]
    return [(primary_client(service), None)]


async def existing_match_any(item: ScanItem) -> tuple[dict | None, ArrInstance | None, Any | None]:
    service = "radarr" if item.media_type == "movie" else "sonarr"
    target = normalize_title(item.title_guess)
    for client, inst in await _all_instances(service):
        try:
            entries = await (client.movies() if service == "radarr" else client.series())
        except Exception:
            continue
        for entry in entries or []:
            if normalize_title(entry.get("title", "")) != target:
                continue
            if item.year_guess and entry.get("year") and int(entry.get("year")) != int(item.year_guess):
                continue
            return entry, inst, client
    return None, None, None


async def lookup_item(item: ScanItem) -> list[dict]:
    client = RadarrClient() if item.media_type == "movie" else SonarrClient()
    term = f"{item.title_guess} {item.year_guess or ''}".strip() if item.media_type == "movie" else item.title_guess
    try:
        results = await client.lookup(term)
    except Exception:
        results = []
    return (results or [])[:10]


def existing_resolution(entry: dict | None) -> int:
    if not entry:
        return 0
    file_obj = entry.get("movieFile") or entry.get("episodeFile") or {}
    quality = (file_obj.get("quality") or {}).get("quality") or {}
    try:
        return int(quality.get("resolution") or 0)
    except Exception:
        return 0


async def route_item(item: ScanItem) -> dict:
    existing, inst, _ = await existing_match_any(item)
    lookup = []
    metadata = existing or {}
    if not existing:
        lookup = await lookup_item(item)
        metadata = lookup[0] if lookup else {}
    if existing and inst and inst.destination_key:
        roots = settings.movie_roots if item.media_type == "movie" else settings.tv_roots
        decision = RouteDecision(inst.destination_key, roots.get(inst.destination_key, roots["default"]), f"Already owned by {inst.service}/{inst.instance}", 100)
    else:
        decision = decide_movie(item.title_guess, metadata) if item.media_type == "movie" else decide_tv(item.title_guess, metadata)
    return {
        "existing": existing,
        "existing_instance": inst,
        "lookup": lookup,
        "metadata": metadata,
        "decision": decision,
        "poster": poster_url(metadata),
        "existing_resolution": existing_resolution(existing),
        "upgrade": bool(item.quality and existing_resolution(existing) and item.quality > existing_resolution(existing)),
    }


async def import_one(source_path: str, destination_key: str | None = None, candidate_index: int = -1) -> dict:
    item = inspect_item(source_path)
    routed = await route_item(item)
    recommended: RouteDecision = routed["decision"]
    chosen = destination_key or recommended.key
    service = "radarr" if item.media_type == "movie" else "sonarr"
    roots = settings.movie_roots if item.media_type == "movie" else settings.tv_roots
    if chosen not in roots:
        raise ImportErrorSafe(f"Invalid {item.media_type} destination: {chosen}")
    root = roots[chosen]

    existing = routed["existing"]
    existing_inst = routed["existing_instance"]
    target_client, target_inst = client_for_destination(service, chosen)

    # Existing Arr ownership wins. We never create a duplicate in another specialist Arr.
    if existing:
        client = _client_for_instance(existing_inst) if existing_inst and existing_inst.api_key else target_client
        arr_item = existing
        actual_destination_key = existing_inst.destination_key if existing_inst and existing_inst.destination_key else chosen
        if existing_inst and existing_inst.root:
            root = existing_inst.root
    else:
        lookup = routed["lookup"] or await lookup_item(item)
        if not lookup:
            raise ImportErrorSafe(f"No {service.title()} match found for {item.title_guess}")
        idx = candidate_index if 0 <= candidate_index < len(lookup) else 0
        candidate = lookup[idx]
        client = target_client
        arr_item = await (client.add_movie(candidate, root, search=False) if service == "radarr" else client.add_series(candidate, root, search=False))
        actual_destination_key = chosen

    if service == "radarr":
        dest_dir = arr_item.get("path") or f"{root}/{arr_item['title']} ({arr_item.get('year', '')})"
        created = import_movie_source(source_path, dest_dir, arr_item.get("title", item.title_guess), arr_item.get("year") or item.year_guess)
        await client.rescan(int(arr_item["id"]))
    else:
        dest_dir = arr_item.get("path") or f"{root}/{arr_item['title']}"
        created = import_tv_source(source_path, dest_dir, arr_item.get("title", item.title_guess))
        await client.rescan(int(arr_item["id"]))

    # User overrides teach the router an exact-title preference.
    if destination_key and destination_key != recommended.key:
        learn_exact_route(item.media_type, item.title_guess, destination_key)

    arr_instance_name = existing_inst.instance if existing_inst else (target_inst.instance if target_inst else "configured-main")
    import_id = log_import(
        source_path=source_path,
        source_name=item.name,
        media_type=item.media_type,
        destination_key=actual_destination_key,
        destination_path=dest_dir,
        arr_name=service,
        arr_instance=arr_instance_name,
        arr_id=arr_item.get("id"),
        status="complete",
        note=f"Created/verified {len(created)} symlink(s)",
        created_paths=created,
        source_fingerprint=item.fingerprint,
        source_quality=item.quality,
    )
    add_activity("import", item.title_guess, f"{service}/{arr_instance_name} → {actual_destination_key} ({len(created)} links)", source_path)
    return {
        "ok": True,
        "import_id": import_id,
        "item": item.dict(),
        "destination_key": actual_destination_key,
        "destination_path": dest_dir,
        "created": created,
        "arr": service,
        "arr_instance": arr_instance_name,
        "arr_id": arr_item.get("id"),
    }


async def discover_lookup(term: str, media_type: str) -> list[dict]:
    if media_type == "movie":
        results = (await RadarrClient().lookup(term))[:30]
        id_key = "tmdbId"
    else:
        results = (await SonarrClient().lookup(term))[:30]
        id_key = "tvdbId"

    # Mark anything already owned by any discovered Arr instance. This keeps
    # Discover useful after a request instead of presenting the same Add button.
    owned = {}
    service = "radarr" if media_type == "movie" else "sonarr"
    for client, inst in await _all_instances(service):
        try:
            rows = await (client.movies() if service == "radarr" else client.series())
        except Exception:
            continue
        for row in rows or []:
            ext = row.get(id_key)
            if ext:
                owned[str(ext)] = {
                    "instance": inst.instance if inst else "main",
                    "destination": inst.destination_key if inst else "default",
                    "arr_id": row.get("id"),
                    "has_file": bool(row.get("hasFile") or row.get("statistics", {}).get("episodeFileCount")),
                }
    tracked = request_map(media_type)
    for candidate in results:
        ext = candidate.get(id_key)
        state = owned.get(str(ext)) if ext else None
        if not state and ext:
            state = tracked.get(str(ext))
        candidate["arrnexus_request"] = state or None
    return results


async def discover_add(candidate: dict, media_type: str, destination_key: str = "auto", search: bool = True) -> dict:
    if media_type == "movie":
        decision = decide_movie(candidate.get("title", ""), candidate)
        key = decision.key if destination_key == "auto" else destination_key
        root = settings.movie_roots[key]
        client, inst = client_for_destination("radarr", key)
        existing = [x for x in await client.movies() if x.get("tmdbId") and x.get("tmdbId") == candidate.get("tmdbId")]
        movie = existing[0] if existing else await client.add_movie(candidate, root, search=False)
        if search:
            await client.search(int(movie["id"]))
        inst_name = inst.instance if inst else "main"
        track_request("movie", str(movie.get("tmdbId") or candidate.get("tmdbId") or ""), movie.get("title", candidate.get("title", "Movie")), movie.get("year"), key, inst_name, movie.get("id"), "requested")
        add_activity("discover", movie.get("title", "Movie"), f"Added to Radarr/{inst_name} and search queued")
        return {"item": movie, "destination": key, "instance": inst_name}
    decision = decide_tv(candidate.get("title", ""), candidate)
    key = decision.key if destination_key == "auto" else destination_key
    root = settings.tv_roots[key]
    client, inst = client_for_destination("sonarr", key)
    tvdb = candidate.get("tvdbId")
    existing = [x for x in await client.series() if tvdb and x.get("tvdbId") == tvdb]
    series = existing[0] if existing else await client.add_series(candidate, root, search=False)
    if search:
        await client.search(int(series["id"]))
    inst_name = inst.instance if inst else "main"
    track_request("tv", str(series.get("tvdbId") or tvdb or ""), series.get("title", candidate.get("title", "Series")), series.get("year"), key, inst_name, series.get("id"), "requested")
    add_activity("discover", series.get("title", "Series"), f"Added to Sonarr/{inst_name} and search queued")
    return {"item": series, "destination": key, "instance": inst_name}

# Public alias used by dashboards/queue aggregation.
client_for_instance = _client_for_instance
