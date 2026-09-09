from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .arr import RadarrClient, SonarrClient, LidarrClient, ArrError
from .db import db, utcnow, setting_get, setting_set, log_event
from .config import settings

MAGIC_ROOT_DEFAULT = os.getenv("MAGIC_ROOT", "/zurg_magic").strip() or "/zurg_magic"
MAGIC_ARR_PREFIX_DEFAULT = os.getenv("MAGIC_ARR_PREFIX", "/zurg_mnt/zurg/__magic__").strip() or "/zurg_mnt/zurg/__magic__"
ORGANIZED = {"movies", "tv", "music"}
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".wmv"}
AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav"}
QUALITY_TOKENS = {
    "1080p", "2160p", "720p", "480p", "bluray", "blu-ray", "webdl", "web-dl", "webrip", "hdtv",
    "remux", "x264", "x265", "h264", "h265", "hevc", "aac", "ddp5", "atmos", "proper", "repack",
}

DESTINATIONS = {
    "movie": {
        "main": "movies/main",
        "kids": "movies/kids",
        "christmas": "movies/christmas",
        "halloween": "movies/halloween",
        "easter": "movies/easter",
    },
    "tv": {
        "shows": "tv/shows",
        "kids": "tv/kids",
        "netflix": "tv/netflix",
        "disneyplus": "tv/disneyplus",
        "amazon": "tv/amazon",
        "appletv": "tv/appletv",
        "bbc": "tv/bbc",
    },
    "music": {"music": "music"},
}

_CACHE: dict[str, Any] = {
    "last_scan_at": "",
    "last_error": "",
    "running": False,
    "groups": [],
    "summary": {"total": 0, "matched": 0, "review": 0, "unmatched": 0, "imported": 0, "partial": 0},
}


def _schema() -> str:
    return """
    CREATE TABLE IF NOT EXISTS magic_intake_groups (
        group_key TEXT PRIMARY KEY,
        media_type TEXT NOT NULL DEFAULT 'unknown',
        normalized_title TEXT NOT NULL DEFAULT '',
        year INTEGER,
        source_paths_json TEXT NOT NULL DEFAULT '[]',
        release_count INTEGER NOT NULL DEFAULT 0,
        episodes_json TEXT NOT NULL DEFAULT '[]',
        match_service TEXT NOT NULL DEFAULT '',
        match_id INTEGER,
        match_title TEXT NOT NULL DEFAULT '',
        match_year INTEGER,
        match_external_id TEXT NOT NULL DEFAULT '',
        poster_url TEXT NOT NULL DEFAULT '',
        confidence INTEGER NOT NULL DEFAULT 0,
        destination_key TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL DEFAULT 'discovered',
        ignored INTEGER NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_magic_intake_state ON magic_intake_groups(state, ignored, confidence);

    CREATE TABLE IF NOT EXISTS magic_intake_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_key TEXT NOT NULL,
        state TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_magic_events_group ON magic_intake_events(group_key, id DESC);

    CREATE TABLE IF NOT EXISTS magic_intake_overrides (
        raw_key TEXT PRIMARY KEY,
        media_type TEXT NOT NULL,
        service TEXT NOT NULL,
        arr_id INTEGER,
        title TEXT NOT NULL DEFAULT '',
        year INTEGER,
        external_id TEXT NOT NULL DEFAULT '',
        poster_url TEXT NOT NULL DEFAULT '',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """


def ensure_schema() -> None:
    with db() as conn:
        conn.executescript(_schema())


def root() -> Path:
    return Path(setting_get("magic.root", MAGIC_ROOT_DEFAULT) or MAGIC_ROOT_DEFAULT)


def arr_prefix() -> Path:
    return Path(setting_get("magic.arr_prefix", MAGIC_ARR_PREFIX_DEFAULT) or MAGIC_ARR_PREFIX_DEFAULT)


def settings_state() -> dict[str, Any]:
    return {
        "enabled": setting_get("magic.enabled", "1") == "1",
        "interval_seconds": max(20, int(setting_get("magic.interval_seconds", "60") or 60)),
        "root": str(root()),
        "arr_prefix": str(arr_prefix()),
        "auto_match_threshold": max(80, min(100, int(setting_get("magic.auto_match_threshold", "95") or 95))),
    }


def save_settings(values: dict[str, Any]) -> None:
    setting_set("magic.enabled", "1" if values.get("enabled") else "0")
    setting_set("magic.interval_seconds", str(max(20, int(values.get("interval_seconds") or 60))))
    setting_set("magic.auto_match_threshold", str(max(80, min(100, int(values.get("auto_match_threshold") or 95)))))
    if values.get("magic_root"):
        setting_set("magic.root", str(values["magic_root"]).strip())
    if values.get("arr_prefix"):
        setting_set("magic.arr_prefix", str(values["arr_prefix"]).strip())


def _clean_release_name(name: str) -> tuple[str, int | None, list[str]]:
    raw = Path(name).stem
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", raw)
    year = int(year_match.group(1)) if year_match else None
    eps = []
    for m in re.finditer(r"(?i)\bS(\d{1,2})E(\d{1,3})\b", raw):
        eps.append(f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}")
    srange = re.search(r"(?i)\bS(\d{1,2})\s*[-_]\s*S?(\d{1,2})\b", raw)
    if srange:
        eps.append(f"S{int(srange.group(1)):02d}-S{int(srange.group(2)):02d}")
    text = re.sub(r"(?i)\bS\d{1,2}E\d{1,3}\b", " ", raw)
    text = re.sub(r"(?i)\bS\d{1,2}\s*[-_]\s*S?\d{1,2}\b", " ", text)
    text = re.sub(r"(?i)\bSeason[ ._-]*\d{1,2}\b", " ", text)
    text = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", text)
    text = re.sub(r"[._]+", " ", text)
    parts = []
    for token in text.split():
        if token.lower() in QUALITY_TOKENS:
            break
        if re.match(r"(?i)^(?:ddp?|dts|aac|x26[45]|h26[45])", token):
            break
        parts.append(token)
    text = " ".join(parts)
    text = re.sub(r"\s+", " ", text).strip(" -._")
    return text or raw.replace(".", " "), year, eps


def _media_type(path: Path, name: str, episodes: list[str]) -> str:
    if episodes or re.search(r"(?i)\b(?:season|complete.series|complete.series|episodes?)\b", name):
        return "tv"
    ext = path.suffix.lower()
    if ext in AUDIO_EXTS:
        return "music"
    if path.is_dir():
        try:
            sample = []
            for child in path.iterdir():
                sample.append(child.suffix.lower())
                if len(sample) >= 30:
                    break
            if sample and sum(1 for x in sample if x in AUDIO_EXTS) > sum(1 for x in sample if x in VIDEO_EXTS):
                return "music"
        except OSError:
            pass
    return "movie"


def _group_key(media_type: str, title: str, year: int | None) -> str:
    norm = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return f"{media_type}:{norm}:{year or 0}"


def _poster(candidate: dict) -> str:
    images = candidate.get("images") or []
    for kind in ("poster", "cover", "fanart"):
        for img in images:
            if str(img.get("coverType") or img.get("type") or "").lower() == kind and img.get("remoteUrl"):
                return str(img["remoteUrl"])
    for img in images:
        if img.get("remoteUrl"):
            return str(img["remoteUrl"])
    return ""


def _candidate_identity(media_type: str, c: dict) -> tuple[int | None, str]:
    if media_type == "movie":
        return (int(c.get("id") or 0) or None, str(c.get("tmdbId") or c.get("imdbId") or ""))
    if media_type == "tv":
        return (int(c.get("id") or 0) or None, str(c.get("tvdbId") or c.get("tmdbId") or ""))
    return (int(c.get("id") or 0) or None, str(c.get("foreignArtistId") or ""))


def _score(title: str, year: int | None, candidate: dict, media_type: str) -> int:
    ct = str(candidate.get("title") or candidate.get("artistName") or candidate.get("name") or "")
    cy = candidate.get("year") or candidate.get("firstAired") or candidate.get("releaseDate") or ""
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()
    a, b = norm(title), norm(ct)
    if not a or not b:
        return 0
    if a == b:
        score = 95
    elif a in b or b in a:
        score = 88
    else:
        aset, bset = set(a.split()), set(b.split())
        score = int(100 * len(aset & bset) / max(1, len(aset | bset)))
    if year and str(year) and str(year) in str(cy):
        score += 5
    return min(100, score)


async def lookup(media_type: str, term: str) -> list[dict]:
    term = str(term or "").strip()
    if not term:
        return []
    try:
        if media_type == "tv":
            raw = await SonarrClient().lookup(term)
        elif media_type == "music":
            raw = await LidarrClient().artist_lookup(term)
        else:
            raw = await RadarrClient().lookup(term)
    except Exception:
        return []
    rows = []
    for c in list(raw or [])[:20]:
        title = c.get("title") or c.get("artistName") or c.get("name") or "Unknown"
        item_id, external = _candidate_identity(media_type, c)
        rows.append({
            "raw": c,
            "id": item_id,
            "external_id": external,
            "title": title,
            "year": c.get("year") or "",
            "poster_url": _poster(c),
            "overview": str(c.get("overview") or "")[:500],
        })
    return rows


async def _best_match(media_type: str, title: str, year: int | None) -> dict[str, Any]:
    candidates = await lookup(media_type, f"{title} {year or ''}".strip())
    if not candidates:
        return {}
    best = max(candidates, key=lambda x: _score(title, year, x["raw"], media_type))
    best = dict(best)
    best["confidence"] = _score(title, year, best["raw"], media_type)
    return best


def _upsert_group(group: dict[str, Any]) -> None:
    ensure_schema()
    now = utcnow()
    with db() as conn:
        old = conn.execute("SELECT state,ignored,match_id,match_title,confidence,destination_key,poster_url FROM magic_intake_groups WHERE group_key=?", (group["group_key"],)).fetchone()
        state = str(group.get("state") or (old["state"] if old else "discovered"))
        ignored = int(old["ignored"] if old else 0)
        match_id = group.get("match_id") if group.get("match_id") is not None else (old["match_id"] if old else None)
        match_title = group.get("match_title") or (old["match_title"] if old else "")
        confidence = int(group.get("confidence") if group.get("confidence") is not None else (old["confidence"] if old else 0))
        destination = group.get("destination_key") or (old["destination_key"] if old else "")
        poster = group.get("poster_url") or (old["poster_url"] if old else "")
        conn.execute(
            """INSERT INTO magic_intake_groups(group_key,media_type,normalized_title,year,source_paths_json,release_count,episodes_json,
               match_service,match_id,match_title,match_year,match_external_id,poster_url,confidence,destination_key,state,ignored,error,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(group_key) DO UPDATE SET media_type=excluded.media_type,normalized_title=excluded.normalized_title,year=excluded.year,
               source_paths_json=excluded.source_paths_json,release_count=excluded.release_count,episodes_json=excluded.episodes_json,
               match_service=CASE WHEN excluded.match_id IS NOT NULL THEN excluded.match_service ELSE magic_intake_groups.match_service END,
               match_id=COALESCE(excluded.match_id,magic_intake_groups.match_id),
               match_title=CASE WHEN excluded.match_title<>'' THEN excluded.match_title ELSE magic_intake_groups.match_title END,
               match_year=COALESCE(excluded.match_year,magic_intake_groups.match_year),
               match_external_id=CASE WHEN excluded.match_external_id<>'' THEN excluded.match_external_id ELSE magic_intake_groups.match_external_id END,
               poster_url=CASE WHEN excluded.poster_url<>'' THEN excluded.poster_url ELSE magic_intake_groups.poster_url END,
               confidence=CASE WHEN excluded.confidence>0 THEN excluded.confidence ELSE magic_intake_groups.confidence END,
               destination_key=CASE WHEN excluded.destination_key<>'' THEN excluded.destination_key ELSE magic_intake_groups.destination_key END,
               state=CASE WHEN magic_intake_groups.state IN ('imported','ignored') THEN magic_intake_groups.state ELSE excluded.state END,
               error=excluded.error,updated_at=excluded.updated_at""",
            (group["group_key"], group["media_type"], group["normalized_title"], group.get("year"), json.dumps(group.get("source_paths") or []),
             len(group.get("source_paths") or []), json.dumps(sorted(set(group.get("episodes") or []))), group.get("match_service") or "",
             match_id, match_title, group.get("match_year"), group.get("match_external_id") or "", poster, confidence,
             destination, state, ignored, group.get("error") or "", now),
        )
        if not old:
            conn.execute("INSERT INTO magic_intake_events(group_key,state,detail) VALUES(?,?,?)", (group["group_key"], "discovered", "Found in top-level __magic__ intake"))


def list_groups(include_imported: bool = True) -> list[dict[str, Any]]:
    ensure_schema()
    sql = "SELECT * FROM magic_intake_groups WHERE ignored=0"
    if not include_imported:
        sql += " AND state NOT IN ('imported','ignored')"
    sql += " ORDER BY CASE state WHEN 'importing' THEN 0 WHEN 'partial' THEN 1 WHEN 'matched' THEN 2 WHEN 'review' THEN 3 WHEN 'discovered' THEN 4 WHEN 'unmatched' THEN 5 ELSE 6 END, confidence DESC, updated_at DESC"
    with db() as conn:
        rows = [dict(x) for x in conn.execute(sql).fetchall()]
    for row in rows:
        row["source_paths"] = json.loads(row.pop("source_paths_json") or "[]")
        row["episodes"] = json.loads(row.pop("episodes_json") or "[]")
        row["destination_options"] = DESTINATIONS.get(row["media_type"], {})
    return rows


def get_group(group_key: str) -> dict | None:
    for row in list_groups(True):
        if row["group_key"] == group_key:
            return row
    return None


def _summary(groups: list[dict]) -> dict[str, int]:
    return {
        "total": len(groups),
        "matched": sum(1 for x in groups if x["state"] in {"matched", "ready"}),
        "review": sum(1 for x in groups if x["state"] == "review"),
        "unmatched": sum(1 for x in groups if x["state"] in {"discovered", "unmatched"}),
        "imported": sum(1 for x in groups if x["state"] == "imported"),
        "partial": sum(1 for x in groups if x["state"] == "partial"),
    }


async def scan() -> dict[str, Any]:
    ensure_schema()
    _CACHE["running"] = True
    try:
        base = root()
        if not base.exists():
            raise RuntimeError(f"Magic Intake path does not exist: {base}")
        entries = []
        for p in base.iterdir():
            if p.name.startswith(".") or p.name.lower() in ORGANIZED:
                continue
            entries.append(p)
        buckets: dict[str, dict[str, Any]] = {}
        for p in entries:
            title, year, episodes = _clean_release_name(p.name)
            media_type = _media_type(p, p.name, episodes)
            key = _group_key(media_type, title, year)
            g = buckets.setdefault(key, {
                "group_key": key, "media_type": media_type, "normalized_title": title, "year": year,
                "source_paths": [], "episodes": [], "state": "discovered", "error": "",
            })
            g["source_paths"].append(p.name)
            g["episodes"].extend(episodes)

        sem = asyncio.Semaphore(4)
        async def enrich(g: dict[str, Any]):
            existing = None
            with db() as conn:
                existing = conn.execute("SELECT match_id,match_title,confidence,state FROM magic_intake_groups WHERE group_key=?", (g["group_key"],)).fetchone()
            if existing and existing["match_title"] and int(existing["confidence"] or 0) >= 100:
                _upsert_group(g)
                return
            async with sem:
                match = await _best_match(g["media_type"], g["normalized_title"], g.get("year"))
            if match:
                threshold = settings_state()["auto_match_threshold"]
                g.update({
                    "match_service": {"movie": "radarr", "tv": "sonarr", "music": "lidarr"}[g["media_type"]],
                    "match_id": match.get("id"), "match_title": match.get("title") or "", "match_year": match.get("year") or None,
                    "match_external_id": match.get("external_id") or "", "poster_url": match.get("poster_url") or "",
                    "confidence": int(match.get("confidence") or 0),
                    "state": "matched" if int(match.get("confidence") or 0) >= threshold else "review",
                })
            else:
                g["state"] = "unmatched"
            _upsert_group(g)

        await asyncio.gather(*(enrich(g) for g in buckets.values()))
        groups = list_groups(True)
        _CACHE.update({"last_scan_at": utcnow(), "last_error": "", "groups": groups, "summary": _summary(groups)})
        return cached_state()
    except Exception as exc:
        _CACHE["last_error"] = str(exc)
        raise
    finally:
        _CACHE["running"] = False


def cached_state() -> dict[str, Any]:
    if not _CACHE["groups"]:
        groups = list_groups(True)
        _CACHE["groups"] = groups
        _CACHE["summary"] = _summary(groups)
    return {**_CACHE, "settings": settings_state(), "destinations": DESTINATIONS}


async def scan_loop() -> None:
    await asyncio.sleep(8)
    while True:
        try:
            cfg = settings_state()
            if cfg["enabled"]:
                await scan()
            await asyncio.sleep(cfg["interval_seconds"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _CACHE["last_error"] = str(exc)
            await asyncio.sleep(30)


def force_match(group_key: str, candidate: dict[str, Any]) -> None:
    ensure_schema()
    group = get_group(group_key)
    if not group:
        raise ValueError("Magic Intake group not found")
    service = {"movie": "radarr", "tv": "sonarr", "music": "lidarr"}[group["media_type"]]
    with db() as conn:
        conn.execute(
            """UPDATE magic_intake_groups SET match_service=?,match_id=?,match_title=?,match_year=?,match_external_id=?,poster_url=?,confidence=100,state='matched',error='',updated_at=? WHERE group_key=?""",
            (service, candidate.get("id"), candidate.get("title") or "", candidate.get("year") or None,
             candidate.get("external_id") or "", candidate.get("poster_url") or "", utcnow(), group_key),
        )
        conn.execute("INSERT INTO magic_intake_events(group_key,state,detail) VALUES(?,?,?)", (group_key, "matched", f"Force matched to {candidate.get('title') or 'selected item'}"))
    _CACHE["groups"] = []


def ignore(group_key: str) -> None:
    ensure_schema()
    with db() as conn:
        conn.execute("UPDATE magic_intake_groups SET ignored=1,state='ignored',updated_at=? WHERE group_key=?", (utcnow(), group_key))
        conn.execute("INSERT INTO magic_intake_events(group_key,state,detail) VALUES(?,?,?)", (group_key, "ignored", "Ignored by user"))
    _CACHE["groups"] = []


def _safe_source(name: str) -> Path:
    base = root().resolve()
    p = (base / name).resolve()
    if p.parent != base:
        raise ValueError("Magic Intake only accepts top-level __magic__ entries")
    return p


def _safe_destination(media_type: str, destination_key: str, title: str, year: int | None) -> tuple[Path, Path]:
    rel = DESTINATIONS.get(media_type, {}).get(destination_key)
    if not rel:
        raise ValueError("Unknown Magic Intake destination")
    safe_title = re.sub(r"[\\/:*?\"<>|]+", " ", title).strip()
    folder_name = f"{safe_title} ({year})" if media_type == "movie" and year else safe_title
    write_root = root().resolve()
    target_parent = (write_root / rel).resolve()
    if write_root not in target_parent.parents and target_parent != write_root:
        raise ValueError("Destination escaped Magic Intake root")
    arr_parent = arr_prefix() / rel
    return target_parent / folder_name, arr_parent / folder_name


def _existing_by_match(media_type: str, items: list[dict], group: dict) -> dict | None:
    mid = int(group.get("match_id") or 0)
    ext = str(group.get("match_external_id") or "")
    title = str(group.get("match_title") or group.get("normalized_title") or "").casefold()
    for item in items:
        if mid and int(item.get("id") or 0) == mid:
            return item
        vals = [item.get("tmdbId"), item.get("tvdbId"), item.get("foreignArtistId"), item.get("imdbId")]
        if ext and ext in {str(x or "") for x in vals}:
            return item
        if title and str(item.get("title") or item.get("artistName") or "").casefold() == title:
            return item
    return None


async def _ensure_arr_item(group: dict, target_parent_arr: Path) -> tuple[Any, int, dict]:
    media_type = group["media_type"]
    term = group.get("match_title") or group["normalized_title"]
    if media_type == "movie":
        client = RadarrClient(); items = await client.movies(); existing = _existing_by_match(media_type, items, group)
        if existing:
            return client, int(existing["id"]), existing
        lookups = await client.lookup(term)
        candidate = next((x for x in lookups if str(x.get("tmdbId") or x.get("imdbId") or "") == str(group.get("match_external_id") or "")), lookups[0] if lookups else None)
        if not candidate: raise ArrError("Radarr lookup no longer returns the selected movie")
        added = await client.add_movie(candidate, str(target_parent_arr), search=False, monitored=True)
        return client, int(added["id"]), added
    if media_type == "tv":
        client = SonarrClient(); items = await client.series(); existing = _existing_by_match(media_type, items, group)
        if existing:
            return client, int(existing["id"]), existing
        lookups = await client.lookup(term)
        candidate = next((x for x in lookups if str(x.get("tvdbId") or x.get("tmdbId") or "") == str(group.get("match_external_id") or "")), lookups[0] if lookups else None)
        if not candidate: raise ArrError("Sonarr lookup no longer returns the selected series")
        added = await client.add_series(candidate, str(target_parent_arr), search=False, monitored=True)
        return client, int(added["id"]), added
    client = LidarrClient(); items = await client.artists(); existing = _existing_by_match(media_type, items, group)
    if existing:
        return client, int(existing["id"]), existing
    lookups = await client.artist_lookup(term)
    candidate = next((x for x in lookups if str(x.get("foreignArtistId") or "") == str(group.get("match_external_id") or "")), lookups[0] if lookups else None)
    if not candidate: raise ArrError("Lidarr lookup no longer returns the selected artist")
    added = await client.add_artist(candidate, str(target_parent_arr), search=False)
    return client, int(added["id"]), added


async def _try_manual_import(client: Any, media_type: str, folder: str, arr_id: int) -> int:
    try:
        if media_type == "movie":
            candidates = await client.manual_import_candidates(folder=folder, movie_id=arr_id)
            files = [client.manual_file(x) for x in candidates if not (x.get("rejections") or [])]
        elif media_type == "tv":
            candidates = await client.manual_import_candidates(folder=folder, series_id=arr_id)
            files = [client.manual_file(x) for x in candidates if not (x.get("rejections") or [])]
        else:
            candidates = await client.manual_import_candidates(folder=folder, artist_id=arr_id)
            files = [client.manual_file(x) for x in candidates if not (x.get("rejections") or [])]
        files = [x for x in files if x.get("path")]
        if files:
            await client.manual_import(files)
        return len(files)
    except Exception:
        return 0


async def _rescan(client: Any, media_type: str, arr_id: int) -> None:
    commands = []
    if media_type == "movie":
        commands = [{"name": "RescanMovie", "movieId": arr_id}, {"name": "RefreshMovie", "movieIds": [arr_id]}]
    elif media_type == "tv":
        commands = [{"name": "RescanSeries", "seriesId": arr_id}, {"name": "RefreshSeries", "seriesId": arr_id}]
    else:
        commands = [{"name": "RefreshArtist", "artistId": arr_id}, {"name": "RescanFolders"}]
    for payload in commands:
        try:
            await client.command(payload)
            return
        except Exception:
            continue


async def _verify(client: Any, media_type: str, arr_id: int, expected_episodes: int = 0) -> tuple[bool, str]:
    for _ in range(12):
        await asyncio.sleep(2)
        try:
            if media_type == "movie":
                item = await client.movie(arr_id)
                if item.get("hasFile"):
                    return True, "Radarr confirms movie file present"
            elif media_type == "tv":
                eps = await client.episodes(arr_id)
                have = sum(1 for x in eps if x.get("hasFile"))
                if have and (not expected_episodes or have >= expected_episodes):
                    return True, f"Sonarr confirms {have} episode file(s)"
                if have:
                    return False, f"Partial: Sonarr currently confirms {have}/{expected_episodes or '?'} episode file(s)"
            else:
                albums = await client.albums(arr_id)
                have = sum(int((x.get("statistics") or {}).get("trackFileCount") or 0) for x in albums)
                if have:
                    return True, f"Lidarr confirms {have} track file(s)"
        except Exception:
            pass
    return False, "Arr did not confirm imported files before verification timeout"


async def import_group(group_key: str, destination_key: str, selected_source: str = "") -> dict[str, Any]:
    ensure_schema()
    group = get_group(group_key)
    if not group:
        raise ValueError("Magic Intake group not found")
    if not group.get("match_title"):
        raise ValueError("Match or Force Match this intake group before import")
    sources = list(group.get("source_paths") or [])
    if group["media_type"] == "movie" and len(sources) > 1:
        if not selected_source or selected_source not in sources:
            raise ValueError("Select which movie release to import")
        sources = [selected_source]
    dest_write, dest_arr = _safe_destination(group["media_type"], destination_key, group["match_title"], group.get("match_year") or group.get("year"))
    dest_parent_arr = dest_arr.parent
    with db() as conn:
        conn.execute("UPDATE magic_intake_groups SET state='importing',destination_key=?,error='',updated_at=? WHERE group_key=?", (destination_key, utcnow(), group_key))
        conn.execute("INSERT INTO magic_intake_events(group_key,state,detail) VALUES(?,?,?)", (group_key, "moving", f"Virtual move into {dest_arr}"))
    moved = []
    try:
        # Resolve/add the Arr item first, with searching disabled. This prevents a
        # failed metadata/API operation from leaving a successfully moved release
        # stranded before ArrNexus knows which Arr entity owns it.
        client, arr_id, arr_item = await _ensure_arr_item(group, dest_parent_arr)

        dest_write.parent.mkdir(parents=True, exist_ok=True)
        if len(sources) == 1 and _safe_source(sources[0]).is_dir() and not dest_write.exists():
            src = _safe_source(sources[0]); src.rename(dest_write); moved.append(str(dest_arr))
        else:
            dest_write.mkdir(parents=True, exist_ok=True)
            for source in sources:
                src = _safe_source(source)
                target = dest_write / src.name
                if target.exists():
                    raise FileExistsError(f"Destination already exists: {target}")
                src.rename(target); moved.append(str(dest_arr / src.name))

        with db() as conn:
            conn.execute("UPDATE magic_intake_groups SET match_id=?,state='verifying',updated_at=? WHERE group_key=?", (arr_id, utcnow(), group_key))
            conn.execute("INSERT INTO magic_intake_events(group_key,state,detail) VALUES(?,?,?)", (group_key, "arr_rescan", f"Targeted {group.get('match_service') or group['media_type']} rescan/import for ID {arr_id}"))
        imported_candidates = await _try_manual_import(client, group["media_type"], str(dest_arr), arr_id)
        await _rescan(client, group["media_type"], arr_id)
        ok, detail = await _verify(client, group["media_type"], arr_id, len(group.get("episodes") or []))
        state = "imported" if ok else "partial"
        with db() as conn:
            conn.execute("UPDATE magic_intake_groups SET state=?,error=?,updated_at=? WHERE group_key=?", (state, "" if ok else detail, utcnow(), group_key))
            conn.execute("INSERT INTO magic_intake_events(group_key,state,detail) VALUES(?,?,?)", (group_key, state, detail))
        log_event("info" if ok else "warning", "magic_intake", state, detail, {"group": group_key, "paths": moved, "manual_candidates": imported_candidates})
        _CACHE["groups"] = []
        return {"ok": ok, "state": state, "detail": detail, "moved": moved, "manual_candidates": imported_candidates, "arr_id": arr_id}
    except Exception as exc:
        with db() as conn:
            conn.execute("UPDATE magic_intake_groups SET state='partial',error=?,updated_at=? WHERE group_key=?", (str(exc), utcnow(), group_key))
            conn.execute("INSERT INTO magic_intake_events(group_key,state,detail) VALUES(?,?,?)", (group_key, "partial", str(exc)))
        _CACHE["groups"] = []
        raise


def events(group_key: str, limit: int = 80) -> list[dict]:
    ensure_schema()
    with db() as conn:
        rows = conn.execute("SELECT * FROM magic_intake_events WHERE group_key=? ORDER BY id DESC LIMIT ?", (group_key, int(limit))).fetchall()
    return [dict(x) for x in rows]
