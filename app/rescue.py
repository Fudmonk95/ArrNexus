from __future__ import annotations

"""ArrNexus wanted-media rescue helpers.

The normal Arr/Prowlarr automation remains authoritative.  Rescue is an
explicit, review-first fallback for monitored media that has remained missing.
Discovery uses the configured Prowlarr torrent indexers; ArrNexus then checks
Real-Debrid instant availability and can hand an explicitly chosen torrent to
Real-Debrid without asking Sonarr/Radarr to accept the release first.
"""

import asyncio
import hashlib
from typing import Any

from .acquisition import annotate_debrid_cache, rank_releases, normalize_protocol
from .arr import ProwlarrClient, ArrError
from .db import cache_get, cache_set, log_event, add_activity
from .instances import discover_instances
from .router_service import client_for_instance
from .tvpacks import classify_release
from .scanner import normalize_title
from . import realdebrid as rd


def _token(row: dict[str, Any], service: str, instance: str, arr_id: int) -> str:
    raw = "|".join([
        service,
        instance,
        str(arr_id),
        str(row.get("guid") or row.get("downloadUrl") or row.get("magnetUrl") or row.get("title") or ""),
        str(row.get("indexerId") or row.get("indexer") or ""),
    ])
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        rows = value.get("records") or value.get("items") or value.get("data") or []
        return [dict(x) for x in rows if isinstance(x, dict)]
    if isinstance(value, list):
        return [dict(x) for x in value if isinstance(x, dict)]
    return []


async def _queue_ids(client: Any, service: str) -> set[int]:
    try:
        rows = _records(await client.queue(500))
    except Exception:
        return set()
    key = "movieId" if service == "radarr" else "seriesId"
    out: set[int] = set()
    for row in rows:
        try:
            value = int(row.get(key) or 0)
        except Exception:
            continue
        if value > 0:
            out.add(value)
    return out


async def scan_missing_sonarr() -> list[dict[str, Any]]:
    instances = [i for i in discover_instances() if i.service == "sonarr" and i.api_key]

    async def one(inst):
        client = client_for_instance(inst)
        try:
            series, active_ids = await asyncio.gather(client.series(), _queue_ids(client, "sonarr"))
        except Exception as exc:
            return [{"service": "sonarr", "instance": inst.instance, "destination_key": inst.destination_key or "default", "error": str(exc)}]
        out = []
        for item in series or []:
            if item.get("monitored") is False:
                continue
            stats = item.get("statistics") or {}
            total = int(stats.get("episodeCount") or 0)
            have = int(stats.get("episodeFileCount") or 0)
            missing = max(0, total - have)
            if not missing:
                continue
            seasons = []
            for season in item.get("seasons") or []:
                try:
                    number = int(season.get("seasonNumber") or 0)
                except Exception:
                    continue
                if number <= 0 or season.get("monitored") is False:
                    continue
                ss = season.get("statistics") or {}
                stotal = int(ss.get("episodeCount") or 0)
                shave = int(ss.get("episodeFileCount") or 0)
                smissing = max(0, stotal - shave)
                if smissing:
                    seasons.append({"season": number, "missing": smissing, "have": shave, "total": stotal})
            series_id = int(item.get("id") or 0)
            out.append({
                "service": "sonarr",
                "instance": inst.instance,
                "destination_key": inst.destination_key or "default",
                "arr_id": series_id,
                "series_id": series_id,
                "title": str(item.get("title") or "Untitled"),
                "year": item.get("year"),
                "tvdb_id": item.get("tvdbId"),
                "missing": missing,
                "have": have,
                "total": total,
                "seasons": seasons,
                "actively_downloading": series_id in active_ids,
            })
        return out

    pages = await asyncio.gather(*(one(i) for i in instances), return_exceptions=True)
    rows: list[dict[str, Any]] = []
    for page in pages:
        if isinstance(page, Exception):
            continue
        rows.extend(page)
    rows.sort(key=lambda x: (bool(x.get("actively_downloading")), -int(x.get("missing") or 0), str(x.get("title") or "").lower()))
    return rows


async def scan_missing_radarr() -> list[dict[str, Any]]:
    instances = [i for i in discover_instances() if i.service == "radarr" and i.api_key]

    async def one(inst):
        client = client_for_instance(inst)
        try:
            movies, active_ids = await asyncio.gather(client.movies(), _queue_ids(client, "radarr"))
        except Exception as exc:
            return [{"service": "radarr", "instance": inst.instance, "destination_key": inst.destination_key or "default", "error": str(exc)}]
        out = []
        for item in movies or []:
            if item.get("monitored") is False or bool(item.get("hasFile")):
                continue
            movie_id = int(item.get("id") or 0)
            out.append({
                "service": "radarr",
                "instance": inst.instance,
                "destination_key": inst.destination_key or "default",
                "arr_id": movie_id,
                "movie_id": movie_id,
                "title": str(item.get("title") or "Untitled"),
                "year": item.get("year"),
                "tmdb_id": item.get("tmdbId"),
                "imdb_id": item.get("imdbId"),
                "status": item.get("status") or "",
                "missing": 1,
                "have": 0,
                "total": 1,
                "actively_downloading": movie_id in active_ids,
            })
        return out

    pages = await asyncio.gather(*(one(i) for i in instances), return_exceptions=True)
    rows: list[dict[str, Any]] = []
    for page in pages:
        if isinstance(page, Exception):
            continue
        rows.extend(page)
    rows.sort(key=lambda x: (bool(x.get("actively_downloading")), str(x.get("title") or "").lower(), int(x.get("year") or 0)))
    return rows


async def scan_missing(service: str) -> list[dict[str, Any]]:
    if service == "sonarr":
        return await scan_missing_sonarr()
    if service == "radarr":
        return await scan_missing_radarr()
    raise ValueError("Rescue supports Sonarr or Radarr")


def _instance(service: str, instance_name: str):
    for inst in discover_instances():
        if inst.service == service and inst.instance == instance_name and inst.api_key:
            return inst
    raise ArrError(f"{service.title()} instance '{instance_name}' is not available")


async def _identity(service: str, instance_name: str, arr_id: int) -> tuple[Any, dict[str, Any]]:
    inst = _instance(service, instance_name)
    client = client_for_instance(inst)
    if service == "sonarr":
        item = await client.series_by_id(int(arr_id))
    elif service == "radarr":
        item = await client.movie(int(arr_id))
    else:
        raise ValueError("Unsupported rescue service")
    if not isinstance(item, dict):
        raise ArrError(f"{service.title()} returned an invalid item")
    return inst, item


async def search_debrid_candidates(service: str, instance_name: str, arr_id: int, *, cached_only: bool = False, limit: int = 80) -> dict[str, Any]:
    """Search torrent discovery and annotate exact Real-Debrid cache state.

    Real-Debrid has no general title-search catalogue.  ArrNexus deliberately
    uses Prowlarr for discovery, then checks the returned torrent hashes against
    Real-Debrid and can hand a reviewed candidate directly to RD.  This bypasses
    Sonarr/Radarr release acceptance while keeping discovery explicit.
    """
    service = str(service or "").lower()
    inst, item = await _identity(service, instance_name, int(arr_id))
    title = str(item.get("title") or "").strip()
    if not title:
        raise ArrError("Arr item has no title")
    year = int(item.get("year") or 0)
    query = f"{title} {year}".strip() if service == "radarr" and year else title
    categories = [2000] if service == "radarr" else [5000]
    rows = await ProwlarrClient().search(query, categories=categories, limit=max(20, min(200, int(limit))))
    torrents = [dict(x) for x in (rows or []) if normalize_protocol(x.get("protocol")) == "torrent"]
    await annotate_debrid_cache(torrents, limit=min(80, len(torrents)))
    ranked = rank_releases(torrents, "movie" if service == "radarr" else "tv", prefer_cached=True)
    results = []
    canonical_norm = normalize_title(title)
    for row in ranked:
        if cached_only and not row.get("realDebridCached"):
            continue
        release_title = str(row.get("title") or "")
        release_norm = normalize_title(release_title)
        confidence = 25
        if canonical_norm and canonical_norm in release_norm:
            confidence = 90
        elif canonical_norm and all(part in release_title.lower() for part in title.lower().split() if len(part) >= 4):
            confidence = 75
        if year and str(year) in release_title:
            confidence = min(100, confidence + 5)
        row["arrnexus_identity_confidence"] = confidence
        row["arrnexus_identity_label"] = "high" if confidence >= 85 else "review" if confidence >= 60 else "low"
        if service == "sonarr":
            row["arrnexus_pack"] = classify_release(release_title).as_dict()
        token = _token(row, service, instance_name, int(arr_id))
        cached_payload = {
            "service": service,
            "instance": instance_name,
            "arr_id": int(arr_id),
            "release": row,
            "query": query,
            "title": title,
            "year": year,
        }
        cache_set(f"arr_rescue:candidate:{token}", cached_payload)
        results.append({**row, "token": token})
    return {
        "service": service,
        "instance": inst.instance,
        "destination_key": inst.destination_key or "default",
        "arr_id": int(arr_id),
        "title": title,
        "year": year,
        "query": query,
        "connected": rd.connected(),
        "cached_only": bool(cached_only),
        "results": results,
        "counts": {
            "total": len(results),
            "cached": sum(1 for r in results if r.get("realDebridCached")),
            "acceptable": sum(1 for r in results if str((r.get("arrnexus_policy") or {}).get("decision") or "allowed") != "rejected"),
        },
    }


async def send_candidate_to_realdebrid(token: str) -> dict[str, Any]:
    if not rd.connected():
        raise RuntimeError("Real-Debrid is not connected in ArrNexus")
    payload = cache_get(f"arr_rescue:candidate:{token}")
    if not isinstance(payload, dict) or not isinstance(payload.get("release"), dict):
        raise RuntimeError("Rescue result expired; search again")
    release = dict(payload["release"])
    if normalize_protocol(release.get("protocol")) != "torrent":
        raise RuntimeError("Only torrent releases can be handed directly to Real-Debrid")
    if str((release.get("arrnexus_policy") or {}).get("decision") or "allowed") == "rejected":
        raise RuntimeError("ArrNexus policy rejected this release; review the policy result instead of bypassing it")

    magnet = str(release.get("magnetUrl") or release.get("magnet") or "")
    info_hash = str(release.get("infoHash") or release.get("infohash") or release.get("hash") or "").strip()
    if not magnet and info_hash:
        magnet = f"magnet:?xt=urn:btih:{info_hash}"
    if magnet:
        added = await rd.add_magnet(magnet)
    else:
        download_url = str(release.get("downloadUrl") or release.get("downloadURL") or "")
        if not download_url:
            raise RuntimeError("Release has neither a magnet/hash nor a Prowlarr download URL")
        fetched = await ProwlarrClient().download_release(download_url)
        if fetched.get("magnet"):
            added = await rd.add_magnet(str(fetched["magnet"]))
        else:
            content = bytes(fetched.get("content") or b"")
            if not content:
                raise RuntimeError("Prowlarr returned an empty torrent payload")
            added = await rd.add_torrent_file(content)
    torrent_id = str((added or {}).get("id") or "")
    if not torrent_id:
        raise RuntimeError("Real-Debrid did not return a torrent ID")
    try:
        await rd.select_all(torrent_id)
    except Exception as exc:
        try:
            await rd.delete_torrent(torrent_id)
        except Exception:
            pass
        raise RuntimeError(f"Real-Debrid accepted the torrent but file selection failed: {exc}") from exc

    title = str(release.get("title") or payload.get("title") or "Torrent release")
    service = str(payload.get("service") or "arr")
    add_activity("arr_rescue", title, f"Sent reviewed {service.title()} rescue candidate to Real-Debrid")
    log_event("info", "arr_rescue", "sent_to_realdebrid", f"{title} sent to Real-Debrid", {
        "torrent_id": torrent_id,
        "service": service,
        "instance": payload.get("instance"),
        "arr_id": payload.get("arr_id"),
        "cached": bool(release.get("realDebridCached")),
    })
    return {
        "ok": True,
        "torrent_id": torrent_id,
        "title": title,
        "service": service,
        "instance": payload.get("instance"),
        "arr_id": payload.get("arr_id"),
        "cached": bool(release.get("realDebridCached")),
    }
