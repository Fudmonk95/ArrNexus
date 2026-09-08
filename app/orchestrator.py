from __future__ import annotations

import asyncio
import copy
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import time
from typing import Any

from .arr import LidarrClient, RadarrClient, SonarrClient, records
from .db import (
    db,
    orchestrator_events,
    orchestrator_get,
    orchestrator_list,
    orchestrator_upsert,
    recovery_get,
    recovery_set_pause,
    setting_get,
    setting_set,
    utcnow,
)
from . import zurg

_CACHE: dict[str, Any] = {
    "updated_monotonic": 0.0,
    "updated_at": "",
    "summary": {"missing": 0, "queued": 0, "searching": 0, "working": 0, "cooldown": 0, "attention": 0},
    "rows": [],
    "error": "",
    "refreshing": False,
}
_LOCK = asyncio.Lock()
_LAST_DISPATCH = 0.0

DEFAULTS = {
    "enabled": "false",
    "dry_run": "true",
    "radarr": "true",
    "sonarr": "true",
    "lidarr": "true",
    "batch_size": "3",
    "delay_seconds": "30",
    "max_active": "5",
    "daily_limit": "50",
    "max_search_attempts": "5",
    "max_failures": "3",
    "cooldown_hours": "6",
    "search_observe_minutes": "10",
    "priority": "least_attempts",
    "neutarr_coexist": "true",
}


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
        return setting_get(f"orchestrator.{name}", DEFAULTS[name])

    return {
        "enabled": _bool(get("enabled")),
        "dry_run": _bool(get("dry_run"), True),
        "radarr": _bool(get("radarr"), True),
        "sonarr": _bool(get("sonarr"), True),
        "lidarr": _bool(get("lidarr"), True),
        "batch_size": _int(get("batch_size"), 3, 1, 5),
        "delay_seconds": _int(get("delay_seconds"), 30, 5, 3600),
        "max_active": _int(get("max_active"), 5, 1, 50),
        "daily_limit": _int(get("daily_limit"), 50, 1, 1000),
        "max_search_attempts": _int(get("max_search_attempts"), 5, 1, 20),
        "max_failures": _int(get("max_failures"), 3, 1, 20),
        "cooldown_hours": _float(get("cooldown_hours"), 6.0, 0.1, 168.0),
        "search_observe_minutes": _int(get("search_observe_minutes"), 10, 2, 180),
        "priority": get("priority") if get("priority") in {"least_attempts", "oldest", "newest", "random"} else "least_attempts",
        "neutarr_coexist": _bool(get("neutarr_coexist"), True),
    }


def save_settings(values: dict[str, Any]) -> None:
    clean = {
        "enabled": str(bool(values.get("enabled"))).lower(),
        "dry_run": str(bool(values.get("dry_run"))).lower(),
        "radarr": str(bool(values.get("radarr"))).lower(),
        "sonarr": str(bool(values.get("sonarr"))).lower(),
        "lidarr": str(bool(values.get("lidarr"))).lower(),
        "batch_size": str(_int(values.get("batch_size"), 3, 1, 5)),
        "delay_seconds": str(_int(values.get("delay_seconds"), 30, 5, 3600)),
        "max_active": str(_int(values.get("max_active"), 5, 1, 50)),
        "daily_limit": str(_int(values.get("daily_limit"), 50, 1, 1000)),
        "max_search_attempts": str(_int(values.get("max_search_attempts"), 5, 1, 20)),
        "max_failures": str(_int(values.get("max_failures"), 3, 1, 20)),
        "cooldown_hours": str(_float(values.get("cooldown_hours"), 6.0, 0.1, 168.0)),
        "search_observe_minutes": str(_int(values.get("search_observe_minutes"), 10, 2, 180)),
        "priority": str(values.get("priority") or "least_attempts"),
        "neutarr_coexist": str(bool(values.get("neutarr_coexist"))).lower(),
    }
    for key, value in clean.items():
        setting_set(f"orchestrator.{key}", value)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _queue_records(payload: Any) -> list[dict]:
    return records(payload)


def _queue_active_map(rad: list[dict], son: list[dict], lid: list[dict]) -> dict[str, set[int]]:
    return {
        "radarr": {int(x.get("movieId")) for x in rad if x.get("movieId")},
        "sonarr": {int(x.get("seriesId")) for x in son if x.get("seriesId")},
        "lidarr": {int(x.get("albumId")) for x in lid if x.get("albumId")},
    }


def _base_item(item_key: str, service: str, media_type: str, title: str, **kwargs) -> dict[str, Any]:
    old = orchestrator_get(item_key) or {}
    return {
        "item_key": item_key,
        "service": service,
        "media_type": media_type,
        "title": title,
        "arr_id": kwargs.get("arr_id"),
        "sub_id": kwargs.get("sub_id"),
        "series_id": kwargs.get("series_id"),
        "season_number": kwargs.get("season_number"),
        "state": old.get("state") or "detected",
        "attempts": int(old.get("attempts") or 0),
        "detail": old.get("detail") or "Missing and monitored",
        "last_search_at": old.get("last_search_at"),
        "next_retry_at": old.get("next_retry_at"),
        "last_seen_at": utcnow(),
    }


def _radarr_candidates(payload: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for movie in records(payload):
        mid = int(movie.get("id") or 0)
        if not mid or movie.get("monitored") is False or movie.get("hasFile") is True:
            continue
        title = str(movie.get("title") or f"Movie #{mid}")
        year = movie.get("year")
        if year:
            title = f"{title} ({year})"
        out.append(_base_item(f"radarr:movie:{mid}", "Radarr", "movie", title, arr_id=mid, sub_id=mid))
    return out


def _sonarr_candidates(payload: Any) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for ep in records(payload):
        if ep.get("monitored") is False or ep.get("hasFile") is True:
            continue
        sid = int(ep.get("seriesId") or (ep.get("series") or {}).get("id") or 0)
        season = int(ep.get("seasonNumber") or 0)
        eid = int(ep.get("id") or 0)
        if not sid or not eid:
            continue
        grouped[(sid, season)].append(ep)

    out: list[dict[str, Any]] = []
    for (sid, season), eps in grouped.items():
        series = eps[0].get("series") or {}
        series_title = str(series.get("title") or eps[0].get("seriesTitle") or f"Series #{sid}")
        if len(eps) >= 2:
            key = f"sonarr:season:{sid}:{season}"
            title = f"{series_title} · Season {season} · {len(eps)} missing"
            out.append(_base_item(key, "Sonarr", "season", title, arr_id=sid, series_id=sid, season_number=season))
        else:
            ep = eps[0]
            eid = int(ep.get("id"))
            number = ep.get("episodeNumber")
            ep_title = str(ep.get("title") or f"Episode {number or eid}")
            title = f"{series_title} · S{season:02d}E{int(number or 0):02d} · {ep_title}"
            out.append(_base_item(f"sonarr:episode:{eid}", "Sonarr", "episode", title, arr_id=sid, sub_id=eid, series_id=sid, season_number=season))
    return out


def _lidarr_candidates(payload: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for album in records(payload):
        aid = int(album.get("id") or 0)
        if not aid or album.get("monitored") is False:
            continue
        artist = album.get("artist") or {}
        artist_name = str(artist.get("artistName") or artist.get("name") or album.get("artistName") or "Unknown artist")
        album_title = str(album.get("title") or f"Album #{aid}")
        out.append(_base_item(f"lidarr:album:{aid}", "Lidarr", "album", f"{artist_name} · {album_title}", arr_id=aid, sub_id=aid))
    return out


def _state_from_environment(item: dict[str, Any], active: dict[str, set[int]], cfg: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    service = str(item.get("service") or "").lower()
    rec = recovery_get(item["item_key"])
    failures = int(rec.get("failures") or 0)
    if rec.get("paused") or failures >= int(cfg["max_failures"]):
        item["state"] = "attention"
        item["detail"] = rec.get("pause_reason") or f"Paused after {failures} failed releases"
        return item

    recovery_cooldown = _parse_dt(rec.get("cooldown_until"))
    item_cooldown = _parse_dt(item.get("next_retry_at"))
    cooldown = recovery_cooldown or item_cooldown
    if cooldown and cooldown > now:
        item["state"] = "cooldown"
        item["next_retry_at"] = cooldown.isoformat()
        item["detail"] = f"Cooldown until {cooldown.isoformat(timespec='minutes')}"
        return item
    # A completed orchestrator cooldown becomes eligible again. Without this
    # branch an old last_search_at would create a fresh cooldown forever.
    if item_cooldown and item_cooldown <= now and not recovery_cooldown:
        item["state"] = "detected"
        item["next_retry_at"] = None
        item["detail"] = "Cooldown completed; eligible for another controlled search"
        return item

    active_id = int(item.get("series_id") or item.get("arr_id") or 0)
    if active_id and active_id in active.get(service, set()):
        item["state"] = "queued"
        item["detail"] = f"Active in {item['service']} queue; ArrNexus will not duplicate the search"
        return item

    # Zurg correlation is deliberately title-based and read-only. A match means
    # the acquisition is already moving and should never be searched again.
    title = str(item.get("title") or "").split(" · ")[0]
    matches = zurg.title_matches(title, None, 2)
    if matches.get("magic") or matches.get("nzb"):
        item["state"] = "working"
        item["detail"] = "Zurg is scraping/working on this media"
        return item
    if matches.get("downloads"):
        item["state"] = "working"
        item["detail"] = "Zurg is exposing this media in __downloads__"
        return item
    if matches.get("movies") or matches.get("shows"):
        item["state"] = "mounted"
        item["detail"] = "Media is already visible in the Zurg library"
        return item

    last_search = _parse_dt(item.get("last_search_at"))
    attempts = int(item.get("attempts") or 0)
    if attempts >= int(cfg["max_search_attempts"]):
        item["state"] = "attention"
        item["detail"] = f"Paused after {attempts} searches without a usable acquisition"
        recovery_set_pause(item["item_key"], True, item["detail"])
        return item
    if last_search:
        observe_until = last_search + timedelta(minutes=int(cfg["search_observe_minutes"]))
        if observe_until > now:
            item["state"] = "searching"
            item["detail"] = "Search dispatched; waiting for queue/Zurg activity"
            return item
        cooldown_until = now + timedelta(hours=float(cfg["cooldown_hours"]))
        item["state"] = "cooldown"
        item["next_retry_at"] = cooldown_until.isoformat()
        item["detail"] = "No acquisition appeared after the search; cooling down before another attempt"
        return item

    item["state"] = "detected"
    item["detail"] = "Missing, monitored and currently idle"
    return item


async def _fetch_missing() -> tuple[list[dict], dict[str, set[int]], list[str]]:
    cfg = settings_state()
    rad, son, lid = RadarrClient(), SonarrClient(), LidarrClient()
    tasks: list[tuple[str, Any]] = []
    if cfg["radarr"]:
        tasks += [("radarr_missing", rad.missing()), ("radarr_queue", rad.queue())]
    if cfg["sonarr"]:
        tasks += [("sonarr_missing", son.missing()), ("sonarr_queue", son.queue())]
    if cfg["lidarr"]:
        tasks += [("lidarr_missing", lid.missing()), ("lidarr_queue", lid.queue())]

    results = await asyncio.gather(*(asyncio.wait_for(coro, timeout=20) for _, coro in tasks), return_exceptions=True)
    data: dict[str, Any] = {}
    errors: list[str] = []
    for (name, _), result in zip(tasks, results):
        if isinstance(result, Exception):
            errors.append(f"{name}: {result}")
            data[name] = []
        else:
            data[name] = result

    candidates: list[dict] = []
    if cfg["radarr"]:
        candidates.extend(_radarr_candidates(data.get("radarr_missing")))
    if cfg["sonarr"]:
        candidates.extend(_sonarr_candidates(data.get("sonarr_missing")))
    if cfg["lidarr"]:
        candidates.extend(_lidarr_candidates(data.get("lidarr_missing")))

    active = _queue_active_map(
        _queue_records(data.get("radarr_queue")),
        _queue_records(data.get("sonarr_queue")),
        _queue_records(data.get("lidarr_queue")),
    )
    return candidates, active, errors


def _daily_search_count() -> int:
    day = _now().date().isoformat()
    with db() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM orchestrator_events WHERE state='search_dispatched' AND substr(created_at,1,10)=?",
            (day,),
        ).fetchone()[0])


async def refresh() -> dict[str, Any]:
    if _LOCK.locked():
        return cached_state()
    async with _LOCK:
        _CACHE["refreshing"] = True
        try:
            cfg = settings_state()
            candidates, active, errors = await _fetch_missing()
            for item in candidates:
                item = _state_from_environment(item, active, cfg)
                orchestrator_upsert(item)

            current_keys = {x["item_key"] for x in candidates}
            # When a previously-missing item leaves the wanted/missing feed,
            # treat that as resolved. This clears retry counters for a future
            # genuinely-new missing event while keeping the event/audit history.
            if not errors:
                for old_item in orchestrator_list(1200):
                    if old_item.get("item_key") in current_keys or old_item.get("state") == "resolved":
                        continue
                    if old_item.get("state") in {"detected", "queued", "searching", "working", "mounted", "cooldown", "attention"}:
                        orchestrator_upsert({
                            **old_item, "state": "resolved", "attempts": 0,
                            "detail": "No longer reported missing by the Arr",
                            "last_search_at": None, "next_retry_at": None,
                            "last_seen_at": utcnow(),
                        })
                        from .db import recovery_clear
                        recovery_clear(str(old_item.get("item_key") or ""))

            rows = orchestrator_list(800)
            # Keep only currently seen missing/active/attention records plus recent
            # attention records. Completed stale rows are deliberately not deleted;
            # they remain useful history in events without cluttering the queue.
            visible = [x for x in rows if x["item_key"] in current_keys or x.get("state") == "attention"]
            for row in visible:
                row["failures"] = int(recovery_get(row["item_key"]).get("failures") or 0)
            summary = {
                "missing": len(current_keys),
                "queued": sum(1 for x in visible if x.get("state") == "queued"),
                "searching": sum(1 for x in visible if x.get("state") == "searching"),
                "working": sum(1 for x in visible if x.get("state") in {"working", "mounted"}),
                "cooldown": sum(1 for x in visible if x.get("state") == "cooldown"),
                "attention": sum(1 for x in visible if x.get("state") == "attention"),
                "daily_searches": _daily_search_count(),
            }
            _CACHE.update({
                "updated_monotonic": time.monotonic(),
                "updated_at": utcnow(),
                "summary": summary,
                "rows": visible,
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
    out = {
        "summary": copy.deepcopy(_CACHE.get("summary") or {}),
        "rows": copy.deepcopy(_CACHE.get("rows") or []),
        "updated_at": _CACHE.get("updated_at") or "",
        "error": _CACHE.get("error") or "",
        "refreshing": bool(_CACHE.get("refreshing")),
        "settings": settings_state(),
        "events": orchestrator_events(limit=120),
    }
    updated = float(_CACHE.get("updated_monotonic") or 0)
    out["ready"] = bool(updated)
    out["age_seconds"] = max(0.0, time.monotonic() - updated) if updated else None
    return out


def _candidate_sort(rows: list[dict], priority: str) -> list[dict]:
    if priority == "newest":
        return sorted(rows, key=lambda x: str(x.get("created_at") or ""), reverse=True)
    if priority == "oldest":
        return sorted(rows, key=lambda x: str(x.get("created_at") or ""))
    if priority == "random":
        # Deterministic-ish daily shuffle avoids importing random just for ordering.
        day = _now().date().isoformat()
        return sorted(rows, key=lambda x: hash(day + str(x.get("item_key"))))
    return sorted(rows, key=lambda x: (int(x.get("attempts") or 0), str(x.get("created_at") or "")))


async def _trigger_search(item: dict[str, Any], dry_run: bool) -> str:
    service = str(item.get("service") or "")
    if dry_run:
        return f"DRY RUN: would trigger {service} search"
    if service == "Radarr":
        result = await RadarrClient().search(int(item["arr_id"]))
    elif service == "Sonarr":
        if item.get("media_type") == "season":
            result = await SonarrClient().season_search(int(item["series_id"]), int(item["season_number"]))
        else:
            result = await SonarrClient().episode_search([int(item["sub_id"])])
    elif service == "Lidarr":
        result = await LidarrClient().album_search([int(item["sub_id"] or item["arr_id"])])
    else:
        raise RuntimeError(f"Unsupported orchestrator service: {service}")
    cid = (result or {}).get("id") if isinstance(result, dict) else ""
    return f"Search dispatched{f' (command {cid})' if cid else ''}"


async def dispatch_once(item_key: str = "", manual: bool = False) -> dict[str, Any]:
    global _LAST_DISPATCH
    cfg = settings_state()
    if not cfg["enabled"] and not (item_key or manual):
        return {"ok": True, "action": "disabled", "detail": "Orchestrator is disabled"}
    if cfg.get("neutarr_coexist") and not (item_key or manual):
        return {
            "ok": True,
            "action": "deferred_to_neutarr",
            "detail": "NeutArr coexistence mode is enabled; NeutArr owns automatic missing-media search cadence while ArrNexus monitors, rate-limits manual searches and handles queue recovery.",
        }

    await refresh()
    rows = list(_CACHE.get("rows") or [])
    eligible = [x for x in rows if x.get("state") == "detected"]
    if item_key:
        eligible = [x for x in rows if x.get("item_key") == item_key and x.get("state") in {"detected", "cooldown", "attention"}]
        if eligible and eligible[0].get("state") == "attention":
            recovery_set_pause(item_key, False, "")
            eligible[0]["state"] = "detected"
    if not eligible:
        return {"ok": True, "action": "idle", "detail": "No eligible missing media"}

    active_count = sum(1 for x in rows if x.get("state") in {"queued", "searching", "working", "mounted"})
    if active_count >= int(cfg["max_active"]):
        return {"ok": True, "action": "throttled", "detail": f"Active acquisition limit reached ({active_count}/{cfg['max_active']})"}
    if _daily_search_count() >= int(cfg["daily_limit"]):
        return {"ok": True, "action": "daily_limit", "detail": "Daily search limit reached"}
    if not (item_key or manual) and time.monotonic() - _LAST_DISPATCH < int(cfg["delay_seconds"]):
        return {"ok": True, "action": "rate_limited", "detail": "Waiting for configured search delay"}

    item = _candidate_sort(eligible, cfg["priority"])[0]
    detail = await _trigger_search(item, bool(cfg["dry_run"]))
    now = utcnow()
    if cfg["dry_run"]:
        orchestrator_upsert({**item, "state": "detected", "detail": detail, "last_seen_at": now})
    else:
        attempts = int(item.get("attempts") or 0) + 1
        orchestrator_upsert({
            **item,
            "state": "searching",
            "attempts": attempts,
            "detail": detail,
            "last_search_at": now,
            "next_retry_at": None,
            "last_seen_at": now,
        })
        # Explicit event name makes daily counting independent of UI state wording.
        with db() as conn:
            conn.execute("INSERT INTO orchestrator_events(item_key,state,detail,created_at) VALUES(?,?,?,?)", (item["item_key"], "search_dispatched", detail, now))
        _LAST_DISPATCH = time.monotonic()
    return {"ok": True, "action": "dry_run" if cfg["dry_run"] else "search", "item": item["item_key"], "detail": detail}


async def dispatcher_loop(interval: float = 5.0) -> None:
    await asyncio.sleep(3)
    while True:
        try:
            cfg = settings_state()
            if cfg["enabled"]:
                for _ in range(int(cfg["batch_size"])):
                    result = await dispatch_once()
                    if result.get("action") not in {"search"}:
                        break
                    await asyncio.sleep(max(1, int(cfg["delay_seconds"])))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _CACHE["error"] = str(exc)
        await asyncio.sleep(interval)


async def scan_loop(interval: float = 60.0) -> None:
    await asyncio.sleep(2)
    while True:
        try:
            await refresh()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _CACHE["error"] = str(exc)
        await asyncio.sleep(interval)


def pause_item(item_key: str, reason: str = "Paused manually") -> None:
    recovery_set_pause(item_key, True, reason)
    item = orchestrator_get(item_key)
    if item:
        orchestrator_upsert({**item, "state": "attention", "detail": reason, "last_seen_at": utcnow()})


def resume_item(item_key: str) -> None:
    from .db import recovery_clear
    recovery_clear(item_key)
    item = orchestrator_get(item_key)
    if item:
        orchestrator_upsert({
            **item, "state": "detected", "attempts": 0,
            "detail": "Resumed manually; retry counters reset",
            "last_search_at": None, "next_retry_at": None, "last_seen_at": utcnow(),
        })
