from __future__ import annotations
import asyncio
from pathlib import Path
from dataclasses import replace
from typing import Any

from .arr import RadarrClient, SonarrClient, LidarrClient, poster_url, ArrError
from .config import settings
from .paths import movie_roots, tv_roots, lidarr_root, source_root
from .instances import discover_instances, get_instance, ArrInstance
from .scanner import ScanItem, inspect_item, normalize_title, invalidate_scan_cache
from .namespace import is_within_logical
from .routing import decide_movie, decide_tv, RouteDecision
from .importer import import_movie_source, import_tv_source, ImportErrorSafe
from .language_guard import inspect_source_languages, load_language_policy
from .library import invalidate_library_cache
from . import realdebrid as rd
from .db import log_import, add_activity, learn_exact_route, track_request, request_map, set_item_state
from . import media_identity
from .process_control import CancelledOperation
from . import tv_source_selection


class LanguageRejectedSafe(ImportErrorSafe):
    """Controlled Language Guard block, distinct from an ArrNexus failure."""
    def __init__(self, message: str, *, cleanup: dict | None = None, replacement_started: bool = False, manual_review: bool = False):
        super().__init__(message)
        self.cleanup = cleanup or {}
        self.replacement_started = bool(replacement_started)
        self.manual_review = bool(manual_review)


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
    pairs = await _all_instances(service)

    async def load(pair):
        client, inst = pair
        try:
            entries = await (client.movies() if service == "radarr" else client.series())
            return client, inst, entries or []
        except Exception:
            return client, inst, []

    # Specialist DUMB Arr instances are independent; serially downloading each
    # full library made Item Review wait for every instance in turn.
    for client, inst, entries in await asyncio.gather(*(load(pair) for pair in pairs)):
        for entry in entries:
            if normalize_title(entry.get("title", "")) != target:
                continue
            if item.media_type == "movie" and item.year_guess and entry.get("year") and int(entry.get("year")) != int(item.year_guess):
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
        roots = movie_roots() if item.media_type == "movie" else tv_roots()
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




async def _existing_target_external(client, service: str, candidate: dict) -> dict | None:
    """Resolve an already-owned Arr item by stable external ID before POST.

    Title/year matching can miss unusual release naming. Radarr/Sonarr reject a
    duplicate POST with HTTP 400, but that is an idempotent existing-library
    condition, not an import failure.
    """
    id_key = "tmdbId" if service == "radarr" else "tvdbId"
    external_id = candidate.get(id_key)
    if not external_id:
        return None
    try:
        rows = await (client.movies() if service == "radarr" else client.series())
    except Exception:
        return None
    for row in rows or []:
        if row.get(id_key) and str(row.get(id_key)) == str(external_id):
            return row
    return None

async def import_one(
    source_path: str,
    destination_key: str | None = None,
    candidate_index: int = -1,
    media_type_override: str | None = None,
    *,
    selected_files: list[str] | None = None,
    cancel_check=None,
) -> dict:
    """Import one source, optionally restricting a TV pack to selected episodes.

    v10.5 deliberately treats Language Checks OFF as a hard bypass: no ffprobe
    language inspection is started and stale Language Guard cache/state cannot
    block this import. TV selected_files are produced by the season-aware group
    planner, so combined files can remain pending while safe episodes import.
    """
    if cancel_check and cancel_check():
        raise CancelledOperation("Import cancelled before identification")
    detected_item = inspect_item(source_path)
    resolved_item, identity = media_identity.apply_to_item(detected_item)
    override = str(media_type_override or "").strip().lower()
    if override not in {"movie", "tv"}:
        override = ""
    item = replace(resolved_item, media_type=override) if override and override != resolved_item.media_type else resolved_item

    if item.media_type == "tv":
        from . import tv_recovery
        recovery_plan = await tv_recovery.analyse_source(source_path, cancel_check=cancel_check)
        selected = {str(x) for x in (selected_files or [])}
        split_rows = [
            x for x in (recovery_plan.get("files") or [])
            if x.get("needs_split") and (not selected or str(x.get("path") or "") in selected)
        ]
        if split_rows:
            labels = ", ".join(str(x.get("name") or "TV file") for x in split_rows[:3])
            more = f" (+{len(split_rows)-3} more)" if len(split_rows) > 3 else ""
            raise ImportErrorSafe(
                f"TV Recovery review required before Sonarr import: {labels}{more}. "
                "Combined-season video detected or joined-episode media found."
            )

    if cancel_check and cancel_check():
        raise CancelledOperation("Import cancelled before Arr matching")
    routed = await route_item(item)
    recommended: RouteDecision = routed["decision"]
    chosen = destination_key or recommended.key
    service = "radarr" if item.media_type == "movie" else "sonarr"
    roots = movie_roots() if item.media_type == "movie" else tv_roots()
    if chosen not in roots:
        raise ImportErrorSafe(f"Invalid {item.media_type} destination: {chosen}")
    root = roots[chosen]

    existing = routed["existing"]
    existing_inst = routed["existing_instance"]
    target_client, target_inst = client_for_destination(service, chosen)

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
        arr_item = await _existing_target_external(client, service, candidate)
        if arr_item is None:
            try:
                arr_item = await (client.add_movie(candidate, root, search=False) if service == "radarr" else client.add_series(candidate, root, search=False))
            except ArrError as exc:
                text = str(exc)
                duplicate = any(token in text for token in (
                    "MovieExistsValidator", "SeriesExistsValidator", "already been added",
                    "already configured for an existing movie", "already configured for an existing series",
                ))
                if not duplicate:
                    raise
                arr_item = await _existing_target_external(client, service, candidate)
                if arr_item is None:
                    raise
        actual_destination_key = chosen

    if service == "radarr":
        dest_dir = arr_item.get("path") or f"{root}/{arr_item['title']} ({arr_item.get('year', '')})"
    else:
        dest_dir = arr_item.get("path") or f"{root}/{arr_item['title']}"

    # Master Language Checks OFF is intentionally evaluated before any probe.
    policy = load_language_policy()
    language = {"status": "disabled", "compliant": True, "summary": "Language Checks OFF — imports will bypass Language Guard"}
    if policy.enabled:
        language = await asyncio.to_thread(
            inspect_source_languages,
            source_path,
            item.fingerprint,
            False,
            selected_files=selected_files if item.media_type == "tv" else None,
            cancel_check=cancel_check,
        )
    if policy.enabled and not bool(language.get("compliant")):
        reason = str(language.get("summary") or "Language policy not met")
        manual_review = str(language.get("status") or "") == "unknown"
        replacement_started = False
        if policy.auto_upgrade_search and not manual_review:
            try:
                await client.search(int(arr_item["id"]))
                replacement_started = True
            except Exception as exc:
                reason += f"; replacement search failed: {exc}"
        if replacement_started:
            reason += "; English replacement search queued in Arr"

        cleanup = {"ok": False, "deleted": False, "reason": "Source retained for manual review" if manual_review else "Rejected-source cleanup disabled"}
        original_provider_source = is_within_logical(source_path, source_root())
        if policy.remove_rejected_debrid and not original_provider_source:
            cleanup = {"ok": False, "deleted": False, "reason": "Provider cleanup is not applicable to ArrNexus recovered media; the original archive/provider source is retained"}
        elif policy.remove_rejected_debrid and not manual_review and bool(language.get("destructive_safe")):
            try:
                cleanup = await rd.delete_source_torrent_exact(source_path, item.size_bytes)
            except Exception as exc:
                cleanup = {"ok": False, "deleted": False, "reason": str(exc)}
        elif policy.remove_rejected_debrid and not manual_review and not bool(language.get("destructive_safe")):
            cleanup = {"ok": False, "deleted": False, "reason": "Cleanup refused because the language decision is not destructive-safe"}

        if cleanup.get("deleted"):
            reason += f"; rejected Real-Debrid source removed (torrent {cleanup.get('torrent_id')})"
            state = "language_rejected_removed"
            invalidate_scan_cache(); invalidate_library_cache()
        elif manual_review:
            reason += "; source retained — no destructive cleanup is allowed for uncertain/probe-failed results"
            state = "language_review"
        else:
            reason += f"; rejected source retained: {cleanup.get('reason') or 'provider cleanup did not complete'}"
            state = "language_rejected"

        set_item_state(source_path, state, reason)
        log_import(
            source_path=source_path, source_name=item.name, media_type=item.media_type,
            destination_key=actual_destination_key, destination_path=dest_dir, arr_name=service,
            arr_instance=(existing_inst.instance if existing_inst else (target_inst.instance if target_inst else "configured-main")),
            arr_id=arr_item.get("id"), status=state, note=reason, created_paths=[],
            source_fingerprint=item.fingerprint, source_quality=item.quality,
        )
        add_activity("language_guard", item.title_guess, reason, source_path)
        raise LanguageRejectedSafe(
            f"Language Guard {'requires manual review' if manual_review else 'blocked import'}: {reason}",
            cleanup=cleanup, replacement_started=replacement_started, manual_review=manual_review,
        )

    if cancel_check and cancel_check():
        raise CancelledOperation("Import cancelled before library linking")
    if service == "radarr":
        created = import_movie_source(source_path, dest_dir, arr_item.get("title", item.title_guess), arr_item.get("year") or item.year_guess, cancel_check=cancel_check)
        await client.rescan(int(arr_item["id"]))
    else:
        created = import_tv_source(
            source_path, dest_dir, arr_item.get("title", item.title_guess),
            selected_files=selected_files, cancel_check=cancel_check,
        )
        await client.rescan(int(arr_item["id"]))

    invalidate_library_cache()
    if destination_key and destination_key != recommended.key:
        learn_exact_route(item.media_type, item.title_guess, destination_key)

    arr_instance_name = existing_inst.instance if existing_inst else (target_inst.instance if target_inst else "configured-main")
    subset_note = f" from selected season-aware files ({len(selected_files)})" if selected_files else ""
    import_id = log_import(
        source_path=source_path, source_name=item.name, media_type=item.media_type,
        destination_key=actual_destination_key, destination_path=dest_dir, arr_name=service,
        arr_instance=arr_instance_name, arr_id=arr_item.get("id"), status="complete",
        note=f"Created/verified {len(created)} symlink(s){subset_note}", created_paths=created,
        source_fingerprint=item.fingerprint, source_quality=item.quality,
    )
    add_activity("import", item.title_guess, f"{service}/{arr_instance_name} → {actual_destination_key} ({len(created)} links)", source_path)
    return {
        "ok": True, "import_id": import_id, "item": item.dict(), "detected_media_type": detected_item.media_type,
        "media_type_override": override, "identity_override": identity, "destination_key": actual_destination_key,
        "destination_path": dest_dir, "created": created, "arr": service, "arr_instance": arr_instance_name,
        "arr_id": arr_item.get("id"), "language_checks": "on" if policy.enabled else "off",
    }


async def import_grouped_tv_sources(
    source_paths: list[str],
    destination_key: str | None = None,
    *,
    primary_source: str = "",
    cancel_check=None,
) -> dict:
    """Import every safe season/episode in a grouped TV series independently."""
    plan = await tv_source_selection.build_import_plan(source_paths, cancel_check=cancel_check)
    results: list[dict] = []
    created: list[str] = []
    chosen_destination = destination_key
    for source_path, selected in (plan.get("selected_by_source") or {}).items():
        if cancel_check and cancel_check():
            raise CancelledOperation("Grouped TV import cancelled")
        if not selected:
            continue
        result = await import_one(
            source_path, chosen_destination, media_type_override="tv",
            selected_files=list(selected), cancel_check=cancel_check,
        )
        chosen_destination = result.get("destination_key") or chosen_destination
        results.append(result)
        created.extend(result.get("created") or [])

    for season in plan.get("seasons") or []:
        status = str(season.get("status") or "")
        if int(season.get("selected_count") or 0) > 0:
            season["status"] = "imported" if status == "ready" else "partial_imported"
    plan["summary"] = tv_source_selection.plan_summary(plan, imported=True)
    return {
        "ok": bool(created), "created": created, "results": results, "plan": plan,
        "destination_key": chosen_destination or "auto",
        "arr_instance": next((x.get("arr_instance") for x in results if x.get("arr_instance")), "sonarr"),
        "primary_source": primary_source or (source_paths[0] if source_paths else ""),
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


async def discover_add(candidate: dict, media_type: str, destination_key: str = "auto", search: bool = True, user_id: int | None = None, monitored: bool = True) -> dict:
    if media_type == "movie":
        decision = decide_movie(candidate.get("title", ""), candidate)
        key = decision.key if destination_key == "auto" else destination_key
        root = movie_roots()[key]
        client, inst = client_for_destination("radarr", key)
        existing = [x for x in await client.movies() if x.get("tmdbId") and x.get("tmdbId") == candidate.get("tmdbId")]
        movie = existing[0] if existing else await client.add_movie(candidate, root, search=False, monitored=monitored)
        if search:
            await client.search(int(movie["id"]))
        inst_name = inst.instance if inst else "main"
        track_request("movie", str(movie.get("tmdbId") or candidate.get("tmdbId") or ""), movie.get("title", candidate.get("title", "Movie")), movie.get("year"), key, inst_name, movie.get("id"), "requested", user_id=user_id)
        add_activity("discover", movie.get("title", "Movie"), f"Added to Radarr/{inst_name} and search queued")
        return {"item": movie, "destination": key, "instance": inst_name}
    decision = decide_tv(candidate.get("title", ""), candidate)
    key = decision.key if destination_key == "auto" else destination_key
    root = tv_roots()[key]
    client, inst = client_for_destination("sonarr", key)
    tvdb = candidate.get("tvdbId")
    existing = [x for x in await client.series() if tvdb and x.get("tvdbId") == tvdb]
    series = existing[0] if existing else await client.add_series(candidate, root, search=False, monitored=monitored)
    if search:
        await client.search(int(series["id"]))
    inst_name = inst.instance if inst else "main"
    track_request("tv", str(series.get("tvdbId") or tvdb or ""), series.get("title", candidate.get("title", "Series")), series.get("year"), key, inst_name, series.get("id"), "requested", user_id=user_id)
    add_activity("discover", series.get("title", "Series"), f"Added to Sonarr/{inst_name} and search queued")
    return {"item": series, "destination": key, "instance": inst_name}

# Public alias used by dashboards/queue aggregation.
client_for_instance = _client_for_instance
