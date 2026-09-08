from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
import time
from typing import Any

from .arr import LidarrClient, RadarrClient, SonarrClient, records
from . import zurg
from .db import (
    db,
    janitor_history,
    janitor_log_action,
    recovery_get,
    recovery_list,
    recovery_register_failure,
    recovery_set_pause,
    setting_get,
    setting_set,
    utcnow,
)

_CACHE: dict[str, Any] = {
    "updated_monotonic": 0.0,
    "updated_at": "",
    "rows": [],
    "summary": {"queue": 0, "healthy": 0, "warning": 0, "actionable": 0, "attention": 0},
    "error": "",
    "refreshing": False,
}
_LOCK = asyncio.Lock()

DEFAULTS = {
    "enabled": "false",
    "dry_run": "true",
    "radarr": "true",
    "sonarr": "true",
    "lidarr": "true",
    "auto_import": "true",
    "cleanup_bad": "true",
    "search_after_cleanup": "true",
    "swaparr_defer_stalled": "true",
    "interval_seconds": "30",
    "warning_grace_minutes": "5",
    "max_failures": "3",
    "cooldown_hours": "6",
    "min_movie_seconds": "300",
    "min_episode_seconds": "60",
    "min_music_seconds": "20",
}

BAD_RELEASE_PATTERNS = (
    "download failed",
    "failed to download",
    "download client reported failure",
    "no files found are eligible for import",
    "no files found are eligible",
    "no video files were found",
    "missing articles",
    "article missing",
    "broken archive",
    "corrupt archive",
    "archive is corrupt",
    "unpack failed",
    "extraction failed",
    "failed to extract",
    "provider failure",
    "provider failed",
    "real-debrid error",
    "real debrid error",
    "zurg timeout",
    "timed out waiting for",
    "ffprobe failed",
    "unable to probe",
    "invalid media file",
    "not a valid video file",
)
INVALID_MAPPING_PATTERNS = (
    "invalid season",
    "invalid episode",
    "episode could not be matched",
    "unable to parse episode",
    "unable to determine episodes",
    "found multiple artists",
    "multiple artists",
    "album could not be matched",
)
ID_IMPORT_PATTERNS = (
    "matched to movie by id",
    "matched by id",
    "found matching movie via grab history",
    "manual import required",
)
SAMPLE_UNCERTAIN_PATTERNS = (
    "unable to determine if file is a sample",
    "unable to determine if the file is a sample",
)
SAMPLE_BAD_PATTERNS = (
    "is a sample",
    "sample file",
    "sample detected",
)
STALL_PATTERNS = (
    "stalled",
    "download stalled",
    "no progress",
    "download is stuck",
)


def _bool(value: str, default: bool = False) -> bool:
    if value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except Exception:
        return default


def _float(value: Any, default: float, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except Exception:
        return default


def settings_state() -> dict[str, Any]:
    def get(name: str) -> str:
        return setting_get(f"janitor.{name}", DEFAULTS[name])
    return {
        "enabled": _bool(get("enabled")),
        "dry_run": _bool(get("dry_run"), True),
        "radarr": _bool(get("radarr"), True),
        "sonarr": _bool(get("sonarr"), True),
        "lidarr": _bool(get("lidarr"), True),
        "auto_import": _bool(get("auto_import"), True),
        "cleanup_bad": _bool(get("cleanup_bad"), True),
        "search_after_cleanup": _bool(get("search_after_cleanup"), True),
        "swaparr_defer_stalled": _bool(get("swaparr_defer_stalled"), True),
        "interval_seconds": _int(get("interval_seconds"), 30, 10, 3600),
        "warning_grace_minutes": _int(get("warning_grace_minutes"), 5, 0, 180),
        "max_failures": _int(get("max_failures"), 3, 1, 20),
        "cooldown_hours": _float(get("cooldown_hours"), 6.0, 0.1, 168.0),
        "min_movie_seconds": _int(get("min_movie_seconds"), 300, 30, 7200),
        "min_episode_seconds": _int(get("min_episode_seconds"), 60, 20, 3600),
        "min_music_seconds": _int(get("min_music_seconds"), 20, 5, 1800),
    }


def save_settings(values: dict[str, Any]) -> None:
    for name in ("enabled", "dry_run", "radarr", "sonarr", "lidarr", "auto_import", "cleanup_bad", "search_after_cleanup", "swaparr_defer_stalled"):
        setting_set(f"janitor.{name}", str(bool(values.get(name))).lower())
    for name, default, low, high in (
        ("interval_seconds", 30, 10, 3600),
        ("warning_grace_minutes", 5, 0, 180),
        ("max_failures", 3, 1, 20),
        ("min_movie_seconds", 300, 30, 7200),
        ("min_episode_seconds", 60, 20, 3600),
        ("min_music_seconds", 20, 5, 1800),
    ):
        setting_set(f"janitor.{name}", str(_int(values.get(name), default, low, high)))
    setting_set("janitor.cooldown_hours", str(_float(values.get("cooldown_hours"), 6.0, 0.1, 168.0)))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_text(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_text(v) for v in value)
    return str(value)


def _queue_message(row: dict) -> str:
    parts = [
        row.get("title"), row.get("status"), row.get("trackedDownloadStatus"), row.get("trackedDownloadState"),
        row.get("errorMessage"), row.get("statusMessages"), row.get("messages"), row.get("sourceTitle"),
    ]
    return re.sub(r"\s+", " ", " ".join(_text(x) for x in parts if x is not None)).strip()


def _contains(text: str, patterns: tuple[str, ...]) -> bool:
    low = text.casefold()
    return any(p in low for p in patterns)


def _release_title(row: dict) -> str:
    return str(row.get("title") or row.get("sourceTitle") or row.get("downloadId") or f"Queue item {row.get('id')}")


def _media_key(service: str, row: dict) -> str:
    s = service.lower()
    if s == "radarr":
        mid = int(row.get("movieId") or (row.get("movie") or {}).get("id") or 0)
        return f"radarr:movie:{mid}" if mid else f"radarr:queue:{row.get('id')}"
    if s == "sonarr":
        ep = row.get("episode") or {}
        eid = int(row.get("episodeId") or ep.get("id") or 0)
        sid = int(row.get("seriesId") or (row.get("series") or {}).get("id") or 0)
        season = row.get("seasonNumber") or ep.get("seasonNumber")
        if eid:
            return f"sonarr:episode:{eid}"
        if sid and season is not None:
            return f"sonarr:season:{sid}:{int(season)}"
        return f"sonarr:series:{sid}" if sid else f"sonarr:queue:{row.get('id')}"
    if s == "lidarr":
        aid = int(row.get("albumId") or (row.get("album") or {}).get("id") or 0)
        return f"lidarr:album:{aid}" if aid else f"lidarr:queue:{row.get('id')}"
    return f"{s}:queue:{row.get('id')}"


def _output_path(row: dict) -> str:
    info = row.get("downloadClientInfo") or {}
    return str(row.get("outputPath") or info.get("outputPath") or row.get("path") or "")


def _download_id(row: dict) -> str:
    return str(row.get("downloadId") or (row.get("downloadClientInfo") or {}).get("downloadId") or "")


def _added_dt(row: dict) -> datetime | None:
    value = row.get("added") or row.get("addedDate") or row.get("downloadedDate")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def classify(service: str, row: dict, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or settings_state()
    message = _queue_message(row)
    low = message.casefold()
    status = str(row.get("status") or "").casefold()
    tracked = str(row.get("trackedDownloadStatus") or "").casefold()
    state = str(row.get("trackedDownloadState") or "").casefold()
    severity = "healthy"
    classification = "healthy"
    action = "observe"
    reason = "Queue item looks healthy"

    # Mapping errors are intentionally evaluated before the broad "manual import"
    # wording. A queue message can contain both, and invalid season/episode/album
    # mapping must never be force-imported just because it also says Manual Import.
    if _contains(message, INVALID_MAPPING_PATTERNS):
        severity, classification, action = "actionable", "invalid_mapping", "blocklist_retry"
        reason = "Invalid season/episode/album mapping; do not blindly import"
    elif _contains(message, ID_IMPORT_PATTERNS):
        severity, classification, action = "warning", "id_manual_import", "auto_import"
        reason = "Valid media may exist; attempt explicit ID-based import before deleting anything"
    elif _contains(message, SAMPLE_UNCERTAIN_PATTERNS):
        severity, classification, action = "warning", "sample_uncertain", "probe"
        reason = "Sample status is uncertain; inspect the media before deciding"
    elif _contains(message, SAMPLE_BAD_PATTERNS):
        severity, classification, action = "actionable", "sample", "blocklist_retry"
        reason = "Release was identified as a sample"
    elif _contains(message, BAD_RELEASE_PATTERNS) or status in {"failed", "error"} or tracked in {"failed", "error"} or state == "failed":
        severity, classification, action = "actionable", "bad_release", "blocklist_retry"
        reason = "Release/download is reported as failed or unusable"
    elif _contains(message, STALL_PATTERNS):
        if cfg.get("swaparr_defer_stalled"):
            severity, classification, action = "warning", "stalled_deferred", "defer_swaparr"
            reason = "Stalled download is deferred to NeutArr/Swaparr"
        else:
            severity, classification, action = "actionable", "stalled", "blocklist_retry"
            reason = "Stalled download configured for ArrNexus cleanup"
    elif tracked in {"warning"} or state in {"importblocked", "importpending"}:
        severity, classification, action = "warning", "import_warning", "observe"
        reason = "Import warning is not recognised as safe to auto-delete"

    added = _added_dt(row)
    if severity in {"warning", "actionable"} and added and cfg.get("warning_grace_minutes", 0):
        age_minutes = (datetime.now(timezone.utc) - added).total_seconds() / 60
        if age_minutes < float(cfg["warning_grace_minutes"]):
            action = "grace"
            reason = f"Waiting for {cfg['warning_grace_minutes']} minute warning grace period"

    media_key = _media_key(service, row)
    rec = recovery_get(media_key)
    if rec.get("paused"):
        severity, action = "attention", "attention"
        reason = rec.get("pause_reason") or "Automatic recovery is paused for this item"

    return {
        "service": service,
        "queue_id": str(row.get("id") or ""),
        "media_key": media_key,
        "release_title": _release_title(row),
        "classification": classification,
        "severity": severity,
        "recommended_action": action,
        "reason": reason,
        "message": message,
        "output_path": _output_path(row),
        "download_id": _download_id(row),
        "failures": int(rec.get("failures") or 0),
        "paused": bool(rec.get("paused")),
        "raw": row,
    }


def _client_for(service: str):
    if service == "Radarr":
        return RadarrClient()
    if service == "Sonarr":
        return SonarrClient()
    if service == "Lidarr":
        return LidarrClient()
    raise RuntimeError(f"Unsupported queue service: {service}")


def _manual_rejection_text(candidate: dict) -> str:
    return _text(candidate.get("rejections") or candidate.get("Rejections") or "")


def _safe_manual_candidate(candidate: dict) -> bool:
    if not candidate.get("path"):
        return False
    rejection = _manual_rejection_text(candidate).casefold()
    if not rejection:
        return True
    # The exact ID/manual-import warning is allowed. Any unrelated rejection
    # remains a human-attention item rather than being force-imported.
    dangerous = (
        "sample", "invalid season", "invalid episode", "unable to parse", "quality", "language",
        "does not contain", "not wanted", "existing file", "multiple artists", "unknown movie", "unknown series",
    )
    if any(x in rejection for x in dangerous):
        return False
    return _contains(rejection, ID_IMPORT_PATTERNS) or "manual import" in rejection


def _candidate_payload(service: str, candidate: dict) -> dict:
    if service == "Radarr":
        return RadarrClient.manual_file(candidate)
    if service == "Sonarr":
        return SonarrClient.manual_file(candidate)
    if service == "Lidarr":
        return LidarrClient.manual_file(candidate)
    return {}


async def attempt_auto_import(item: dict[str, Any], dry_run: bool) -> tuple[str, str]:
    service = item["service"]
    row = item["raw"]
    client = _client_for(service)
    folder = item.get("output_path") or ""
    download_id = item.get("download_id") or ""
    kwargs: dict[str, Any] = {"folder": folder, "download_id": download_id}
    if service == "Radarr":
        kwargs["movie_id"] = int(row.get("movieId") or (row.get("movie") or {}).get("id") or 0) or None
    elif service == "Sonarr":
        kwargs["series_id"] = int(row.get("seriesId") or (row.get("series") or {}).get("id") or 0) or None
    elif service == "Lidarr":
        kwargs["artist_id"] = int(row.get("artistId") or (row.get("artist") or {}).get("id") or 0) or None

    candidates = await asyncio.wait_for(client.manual_import_candidates(**kwargs), timeout=25)
    safe: list[dict] = []
    for candidate in candidates:
        payload = _candidate_payload(service, candidate)
        if _safe_manual_candidate(candidate) and payload.get("path"):
            if service == "Radarr" and not payload.get("movieId"):
                continue
            if service == "Sonarr" and (not payload.get("seriesId") or not payload.get("episodeIds")):
                continue
            if service == "Lidarr" and (not payload.get("artistId") or not payload.get("albumId")):
                continue
            safe.append(payload)
    if not safe:
        return "attention", f"Manual import candidates found: {len(candidates)}, but none were safe for automatic ID-based import"
    if dry_run:
        return "dry_run", f"Would submit ManualImport for {len(safe)} safe file(s) using importMode=auto"
    result = await asyncio.wait_for(client.manual_import(safe, import_mode="auto"), timeout=25)
    cid = result.get("id") if isinstance(result, dict) else None
    return "submitted", f"ManualImport submitted for {len(safe)} file(s){f' (command {cid})' if cid else ''}"


def _find_probe_file(path_text: str) -> Path | None:
    if not path_text:
        return None
    path = Path(path_text)
    if path.is_file():
        return path
    if not path.is_dir():
        return None
    extensions = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}
    found: list[Path] = []
    try:
        for candidate in path.rglob("*"):
            if candidate.is_file() and candidate.suffix.lower() in extensions:
                found.append(candidate)
                if len(found) >= 80:
                    break
    except OSError:
        return None
    if not found:
        return None
    try:
        return max(found, key=lambda p: p.stat().st_size)
    except OSError:
        return found[0]


async def probe_media(item: dict[str, Any], cfg: dict[str, Any]) -> tuple[str, str]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return "unknown", "ffprobe is not installed in the ArrNexus image"
    target = await asyncio.to_thread(_find_probe_file, item.get("output_path") or "")
    if not target:
        # DUMB and ArrNexus do not necessarily share the same download-client
        # path. If Zurg is already exposing the release, probe that read-only
        # working path instead of treating a path-namespace mismatch as failure.
        matches = zurg.title_matches(item.get("release_title") or "", None, 5)
        for candidate in matches.get("working") or []:
            target = await asyncio.to_thread(_find_probe_file, candidate)
            if target:
                break
    if not target:
        return "unknown", "No readable queue/Zurg media path was visible to ArrNexus; leaving the item for attention"
    try:
        proc = await asyncio.create_subprocess_exec(
            ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(target),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=25)
    except Exception as exc:
        return "unreadable", f"ffprobe failed: {exc}"
    if proc.returncode != 0:
        return "unreadable", f"ffprobe could not read {target.name}: {stderr.decode(errors='replace')[:260]}"
    try:
        duration = float((json.loads(stdout.decode()) or {}).get("format", {}).get("duration") or 0)
    except Exception:
        duration = 0
    service = item["service"]
    minimum = cfg["min_movie_seconds"] if service == "Radarr" else cfg["min_episode_seconds"] if service == "Sonarr" else cfg["min_music_seconds"]
    if duration < float(minimum):
        return "too_short", f"ffprobe read {target.name}, but duration {duration:.1f}s is below the {minimum}s safety threshold"
    return "readable", f"ffprobe read {target.name} successfully ({duration:.1f}s)"


async def _search_another(item: dict[str, Any]) -> str:
    row = item["raw"]
    service = item["service"]
    if service == "Radarr":
        mid = int(row.get("movieId") or (row.get("movie") or {}).get("id") or 0)
        if not mid:
            return "No movie ID was available for retry"
        result = await RadarrClient().search(mid)
    elif service == "Sonarr":
        ep = row.get("episode") or {}
        eid = int(row.get("episodeId") or ep.get("id") or 0)
        sid = int(row.get("seriesId") or (row.get("series") or {}).get("id") or 0)
        season = row.get("seasonNumber") or ep.get("seasonNumber")
        if eid:
            result = await SonarrClient().episode_search([eid])
        elif sid and season is not None:
            result = await SonarrClient().season_search(sid, int(season))
        elif sid:
            result = await SonarrClient().search(sid)
        else:
            return "No Sonarr media ID was available for retry"
    elif service == "Lidarr":
        aid = int(row.get("albumId") or (row.get("album") or {}).get("id") or 0)
        if not aid:
            return "No album ID was available for retry"
        result = await LidarrClient().album_search([aid])
    else:
        return "Unsupported service"
    cid = result.get("id") if isinstance(result, dict) else None
    return f"Search another release requested{f' (command {cid})' if cid else ''}"


def _recently_acted(service: str, queue_id: str, action: str, minutes: int = 10) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM janitor_actions WHERE service=? AND queue_id=? AND action=? AND created_at>=? ORDER BY id DESC LIMIT 1",
            (service, str(queue_id), action, cutoff),
        ).fetchone()
    return bool(row)


async def cleanup_bad_release(item: dict[str, Any], cfg: dict[str, Any]) -> tuple[str, str]:
    media_key = item["media_key"]
    current = recovery_get(media_key)
    next_failure = int(current.get("failures") or 0) + 1
    max_failures = int(cfg["max_failures"])
    pause = next_failure >= max_failures
    cooldown_until = (datetime.now(timezone.utc) + timedelta(hours=float(cfg["cooldown_hours"]))).isoformat() if pause else ""
    title = item["release_title"]

    if cfg["dry_run"]:
        detail = f"Would remove from client + blocklist exact release, record failure {next_failure}/{max_failures}"
        if cfg["search_after_cleanup"] and not pause:
            detail += ", then search another release"
        if pause:
            detail += f"; hard limit reached so automatic search would stop for attention (cooldown {cfg['cooldown_hours']}h)"
        return "dry_run", detail

    client = _client_for(item["service"])
    await asyncio.wait_for(
        client.remove_queue_item(item["queue_id"], remove_from_client=True, blocklist=True, skip_redownload=True),
        timeout=25,
    )
    rec = recovery_register_failure(
        media_key, item["service"], title, cooldown_until=cooldown_until, pause=pause,
        reason=(f"Paused after {next_failure} failed releases: {title}" if pause else ""),
    )
    if pause:
        return "attention", f"Removed + blocklisted release. Hard retry limit reached ({rec['failures']}/{max_failures}); automatic searching stopped"
    retry_detail = ""
    if cfg["search_after_cleanup"]:
        await asyncio.sleep(1)
        retry_detail = "; " + await _search_another(item)
    return "cleaned", f"Removed from client + blocklisted exact release (failure {rec['failures']}/{max_failures}){retry_detail}"


async def execute(item: dict[str, Any], force: bool = False) -> dict[str, Any]:
    cfg = settings_state()
    action = item.get("recommended_action") or "observe"
    service = item["service"]
    queue_id = item["queue_id"]
    if not force and _recently_acted(service, queue_id, action, 10):
        return {"outcome": "deduplicated", "detail": "A recent identical action is already recorded"}

    outcome, detail = "observed", item.get("reason") or "Observed"
    if action in {"observe", "grace", "defer_swaparr", "attention"}:
        outcome = action
    elif action == "auto_import":
        if not cfg["auto_import"]:
            outcome, detail = "disabled", "Automatic ID-based import is disabled"
        else:
            outcome, detail = await attempt_auto_import(item, bool(cfg["dry_run"]))
    elif action == "probe":
        probe_state, probe_detail = await probe_media(item, cfg)
        if probe_state == "readable":
            if cfg["auto_import"]:
                import_outcome, import_detail = await attempt_auto_import(item, bool(cfg["dry_run"]))
                outcome, detail = import_outcome, f"{probe_detail}; {import_detail}"
            else:
                outcome, detail = "attention", f"{probe_detail}; automatic import is disabled"
        elif probe_state in {"unreadable", "too_short"} and cfg["cleanup_bad"]:
            outcome, cleanup_detail = await cleanup_bad_release(item, cfg)
            detail = f"{probe_detail}; {cleanup_detail}"
        else:
            outcome, detail = "attention", probe_detail
    elif action == "blocklist_retry":
        if not cfg["cleanup_bad"]:
            outcome, detail = "disabled", "Bad-release cleanup is disabled"
        else:
            outcome, detail = await cleanup_bad_release(item, cfg)

    janitor_log_action(
        service, queue_id, item["media_key"], item["release_title"], item["classification"], action,
        outcome, detail, bool(cfg["dry_run"]),
    )
    return {"outcome": outcome, "detail": detail}


async def _fetch_queue() -> tuple[list[dict[str, Any]], list[str]]:
    cfg = settings_state()
    tasks: list[tuple[str, Any]] = []
    if cfg["radarr"]:
        tasks.append(("Radarr", RadarrClient().queue()))
    if cfg["sonarr"]:
        tasks.append(("Sonarr", SonarrClient().queue()))
    if cfg["lidarr"]:
        tasks.append(("Lidarr", LidarrClient().queue()))
    results = await asyncio.gather(*(asyncio.wait_for(coro, timeout=20) for _, coro in tasks), return_exceptions=True)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for (service, _), result in zip(tasks, results):
        if isinstance(result, Exception):
            errors.append(f"{service}: {result}")
            continue
        for row in records(result):
            rows.append(classify(service, row, cfg))
    severity_order = {"attention": 0, "actionable": 1, "warning": 2, "healthy": 3}
    rows.sort(key=lambda x: (severity_order.get(x["severity"], 9), -int(x.get("failures") or 0), x["service"], x["release_title"].casefold()))
    return rows, errors


async def refresh(run_actions: bool = False, allow_disabled: bool = False) -> dict[str, Any]:
    if _LOCK.locked():
        return cached_state()
    async with _LOCK:
        _CACHE["refreshing"] = True
        try:
            cfg = settings_state()
            rows, errors = await _fetch_queue()
            if run_actions and (cfg["enabled"] or allow_disabled):
                for item in rows:
                    if item["recommended_action"] in {"auto_import", "probe", "blocklist_retry"}:
                        try:
                            result = await execute(item)
                            item["last_outcome"] = result["outcome"]
                            item["last_detail"] = result["detail"]
                        except Exception as exc:
                            item["last_outcome"] = "error"
                            item["last_detail"] = str(exc)
                            janitor_log_action(
                                item["service"], item["queue_id"], item["media_key"], item["release_title"],
                                item["classification"], item["recommended_action"], "error", str(exc), bool(cfg["dry_run"]),
                            )
            summary = {
                "queue": len(rows),
                "healthy": sum(1 for x in rows if x["severity"] == "healthy"),
                "warning": sum(1 for x in rows if x["severity"] == "warning"),
                "actionable": sum(1 for x in rows if x["severity"] == "actionable"),
                "attention": sum(1 for x in rows if x["severity"] == "attention") + len(recovery_list(300, paused_only=True)),
            }
            _CACHE.update({
                "updated_monotonic": time.monotonic(),
                "updated_at": utcnow(),
                "rows": rows,
                "summary": summary,
                "error": " · ".join(errors),
            })
            return cached_state()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _CACHE["error"] = str(exc)
            return cached_state()
        finally:
            _CACHE["refreshing"] = False


def cached_state() -> dict[str, Any]:
    updated = float(_CACHE.get("updated_monotonic") or 0)
    rows = copy.deepcopy(_CACHE.get("rows") or [])
    # Raw Arr queue payloads can be large and may contain download-client path
    # details. Keep them private to the action engine rather than returning them
    # through templates/API responses.
    for row in rows:
        row.pop("raw", None)
    return {
        "rows": rows,
        "summary": copy.deepcopy(_CACHE.get("summary") or {}),
        "updated_at": _CACHE.get("updated_at") or "",
        "error": _CACHE.get("error") or "",
        "refreshing": bool(_CACHE.get("refreshing")),
        "ready": bool(updated),
        "age_seconds": max(0.0, time.monotonic() - updated) if updated else None,
        "settings": settings_state(),
        "history": janitor_history(160),
        "attention": recovery_list(300, paused_only=True),
    }


def get_cached_item(service: str, queue_id: str) -> dict[str, Any] | None:
    for item in _CACHE.get("rows") or []:
        if item.get("service") == service and str(item.get("queue_id")) == str(queue_id):
            return copy.deepcopy(item)
    return None


async def scan_loop() -> None:
    await asyncio.sleep(4)
    while True:
        try:
            cfg = settings_state()
            await refresh(run_actions=bool(cfg["enabled"]))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _CACHE["error"] = str(exc)
        await asyncio.sleep(int(settings_state()["interval_seconds"]))


def resume_media(media_key: str) -> None:
    from .db import recovery_clear
    recovery_clear(media_key)


def pause_media(media_key: str, reason: str = "Paused manually") -> None:
    recovery_set_pause(media_key, True, reason)
