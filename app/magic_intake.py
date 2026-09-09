from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .arr import RadarrClient, SonarrClient, LidarrClient, ArrError
from .db import db, utcnow, setting_get, setting_set, log_event

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
    "summary": {"total": 0, "matched": 0, "review": 0, "unmatched": 0, "imported": 0, "partial": 0, "importing": 0},
}
_IMPORT_TASKS: dict[str, asyncio.Task] = {}
_IMPORT_CANONICAL_TASKS: dict[str, asyncio.Task] = {}
_SCAN_TASK: asyncio.Task | None = None
_STARTUP_RECOVERY_DONE = False
DEFAULT_PAGE_SIZE = 72
IMPORT_CONCURRENCY = max(1, min(6, int(os.getenv("MAGIC_IMPORT_CONCURRENCY", "3") or 3)))
_IMPORT_SEMAPHORE = asyncio.Semaphore(IMPORT_CONCURRENCY)

POST_MOVE_STATES = {
    "awaiting_arr", "partially_verified", "numbering_mismatch",
    "verification_timeout", "source_missing", "partial",
}
RECHECKABLE_STATES = POST_MOVE_STATES | {"verifying"}
STATE_LABELS = {
    "queued": "Queued",
    "importing": "Importing",
    "verifying": "Verifying",
    "awaiting_arr": "Moved - awaiting Arr",
    "partially_verified": "Partially verified",
    "numbering_mismatch": "Numbering mismatch",
    "verification_timeout": "Verification timeout",
    "source_missing": "Source missing",
    "failed": "Import failed",
    "partial": "Needs review (legacy)",
    "imported": "Imported",
    "matched": "Matched",
    "review": "Needs review",
    "unmatched": "Unmatched",
    "discovered": "Discovered",
}
_REVERIFY_TASKS: dict[str, asyncio.Task] = {}


def _schema() -> str:
    return """
    CREATE TABLE IF NOT EXISTS magic_intake_groups (
        group_key TEXT PRIMARY KEY,
        canonical_key TEXT NOT NULL DEFAULT '',
        media_type TEXT NOT NULL DEFAULT 'unknown',
        normalized_title TEXT NOT NULL DEFAULT '',
        year INTEGER,
        source_paths_json TEXT NOT NULL DEFAULT '[]',
        release_count INTEGER NOT NULL DEFAULT 0,
        episodes_json TEXT NOT NULL DEFAULT '[]',
        genres_json TEXT NOT NULL DEFAULT '[]',
        match_service TEXT NOT NULL DEFAULT '',
        match_id INTEGER,
        match_title TEXT NOT NULL DEFAULT '',
        match_year INTEGER,
        match_external_id TEXT NOT NULL DEFAULT '',
        poster_url TEXT NOT NULL DEFAULT '',
        confidence INTEGER NOT NULL DEFAULT 0,
        destination_key TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL DEFAULT 'discovered',
        progress INTEGER NOT NULL DEFAULT 0,
        progress_detail TEXT NOT NULL DEFAULT '',
        job_id TEXT NOT NULL DEFAULT '',
        verify_baseline_json TEXT NOT NULL DEFAULT '{}',
        verify_started_at TEXT NOT NULL DEFAULT '',
        verify_checks INTEGER NOT NULL DEFAULT 0,
        destination_arr_path TEXT NOT NULL DEFAULT '',
        moved_paths_json TEXT NOT NULL DEFAULT '[]',
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
        genres_json TEXT NOT NULL DEFAULT '[]',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS magic_intake_type_overrides (
        raw_key TEXT PRIMARY KEY,
        media_type TEXT NOT NULL,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    cols = {str(x[1]) for x in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def ensure_schema() -> None:
    global _STARTUP_RECOVERY_DONE
    with db() as conn:
        conn.executescript(_schema())
        # v13.0.0 -> v13.1.x in-place migration for the user's existing router.db.
        for col, ddl in (
            ("canonical_key", "TEXT NOT NULL DEFAULT ''"),
            ("genres_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("progress", "INTEGER NOT NULL DEFAULT 0"),
            ("progress_detail", "TEXT NOT NULL DEFAULT ''"),
            ("job_id", "TEXT NOT NULL DEFAULT ''"),
            ("verify_baseline_json", "TEXT NOT NULL DEFAULT '{}'") ,
            ("verify_started_at", "TEXT NOT NULL DEFAULT ''"),
            ("verify_checks", "INTEGER NOT NULL DEFAULT 0"),
            ("destination_arr_path", "TEXT NOT NULL DEFAULT ''"),
            ("moved_paths_json", "TEXT NOT NULL DEFAULT '[]'"),
        ):
            _ensure_column(conn, "magic_intake_groups", col, ddl)
        _ensure_column(conn, "magic_intake_overrides", "genres_json", "TEXT NOT NULL DEFAULT '[]'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_magic_intake_canonical ON magic_intake_groups(canonical_key)")
        _migrate_force_matches(conn)
        if not _STARTUP_RECOVERY_DONE:
            # Import jobs are in-memory asyncio tasks. After a container restart,
            # rows left as queued/importing/verifying cannot still be running.
            # Mark them interrupted once so a clean scan can rebuild the inbox
            # from what really remains at top-level __magic__.
            conn.execute(
                "UPDATE magic_intake_groups SET state='partial',progress=100,"
                "progress_detail='Interrupted by ArrNexus restart; rebuilding intake state',"
                "error='Interrupted by ArrNexus restart',job_id='',updated_at=? "
                "WHERE state IN ('queued','importing','verifying')",
                (utcnow(),),
            )
            _STARTUP_RECOVERY_DONE = True

        # v13.1.x used one catch-all `partial` state. Preserve the rows but
        # make their meaning explicit on upgrade where the old detail is clear.
        conn.execute(
            "UPDATE magic_intake_groups SET state='partially_verified' "
            "WHERE state='partial' AND progress_detail LIKE 'Partial: Sonarr confirms %/%'"
        )
        conn.execute(
            "UPDATE magic_intake_groups SET state='verification_timeout' "
            "WHERE state='partial' AND progress_detail LIKE 'Arr did not confirm imported files before verification timeout%'"
        )
        conn.execute(
            "UPDATE magic_intake_groups SET state='source_missing' "
            "WHERE state='partial' AND error LIKE '%No such file or directory%'"
        )

        # v13.1.2 could lose the text of timeout/pool failures during a later
        # canonical scan. Rows with no destination path never reached the Zurg
        # move, so they are safe to return to a retryable matched/review state.
        conn.execute(
            "UPDATE magic_intake_groups SET state=CASE WHEN confidence>=95 THEN 'matched' ELSE 'review' END,"
            "progress=0,progress_detail='Previous pre-move attempt failed before any Zurg move; ready to retry with controlled import concurrency',"
            "error='',job_id='',updated_at=? "
            "WHERE state='failed' AND COALESCE(error,'')='' AND COALESCE(destination_arr_path,'')=''",
            (utcnow(),),
        )


def _migrate_force_matches(conn) -> None:
    """Turn old v13 confidence=100 rows into per-source overrides before regrouping."""
    rows = conn.execute(
        "SELECT media_type,match_service,match_id,match_title,match_year,match_external_id,poster_url,genres_json,source_paths_json "
        "FROM magic_intake_groups WHERE confidence>=100 AND match_title<>''"
    ).fetchall()
    for row in rows:
        try:
            sources = json.loads(row[8] or "[]")
        except Exception:
            sources = []
        for source in sources:
            conn.execute(
                """INSERT INTO magic_intake_overrides(raw_key,media_type,service,arr_id,title,year,external_id,poster_url,genres_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(raw_key) DO UPDATE SET media_type=excluded.media_type,service=excluded.service,arr_id=excluded.arr_id,
                   title=excluded.title,year=excluded.year,external_id=excluded.external_id,poster_url=excluded.poster_url,
                   genres_json=excluded.genres_json,updated_at=excluded.updated_at""",
                (str(source), row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7] or "[]", utcnow()),
            )


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
        "verify_window_minutes": max(2, min(60, int(setting_get("magic.verify_window_minutes", "10") or 10))),
        "verify_interval_seconds": max(15, min(300, int(setting_get("magic.verify_interval_seconds", "30") or 30))),
        "import_concurrency": IMPORT_CONCURRENCY,
    }


def save_settings(values: dict[str, Any]) -> None:
    setting_set("magic.enabled", "1" if values.get("enabled") else "0")
    setting_set("magic.interval_seconds", str(max(20, int(values.get("interval_seconds") or 60))))
    setting_set("magic.auto_match_threshold", str(max(80, min(100, int(values.get("auto_match_threshold") or 95)))))
    if values.get("verify_window_minutes") is not None:
        setting_set("magic.verify_window_minutes", str(max(2, min(60, int(values.get("verify_window_minutes") or 10)))))
    if values.get("verify_interval_seconds") is not None:
        setting_set("magic.verify_interval_seconds", str(max(15, min(300, int(values.get("verify_interval_seconds") or 30)))))
    if values.get("magic_root"):
        setting_set("magic.root", str(values["magic_root"]).strip())
    if values.get("arr_prefix"):
        setting_set("magic.arr_prefix", str(values["arr_prefix"]).strip())


def _episode_markers(raw: str) -> list[str]:
    out: list[str] = []
    # S01E07, S01E07-E09, S01E07E08 and similar common release forms.
    pattern = re.compile(r"(?i)S(\d{1,2})E(\d{1,3})(?:\s*[-_.]?\s*E(\d{1,3}))?")
    for match in pattern.finditer(raw):
        season, start = int(match.group(1)), int(match.group(2))
        end = int(match.group(3)) if match.group(3) else start
        if end >= start and end - start <= 100:
            for episode in range(start, end + 1):
                marker = f"S{season:02d}E{episode:02d}"
                if marker not in out:
                    out.append(marker)
    for match in re.finditer(r"(?i)(?<!\d)(\d{1,2})x(\d{1,3})(?!\d)", raw):
        marker = f"S{int(match.group(1)):02d}E{int(match.group(2)):02d}"
        if marker not in out:
            out.append(marker)
    srange = re.search(r"(?i)\bS(\d{1,2})\s*[-_]\s*S?(\d{1,2})\b", raw)
    if srange:
        marker = f"S{int(srange.group(1)):02d}-S{int(srange.group(2)):02d}"
        if marker not in out:
            out.append(marker)
    return out


def _clean_release_name(name: str) -> tuple[str, int | None, list[str]]:
    raw = Path(name).stem
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", raw)
    year = int(year_match.group(1)) if year_match else None
    eps = _episode_markers(raw)
    text = re.sub(r"(?i)\bS\d{1,2}E\d{1,3}(?:\s*[-_.]?\s*E?\d{1,3})?\b", " ", raw)
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
    if episodes or re.search(r"(?i)\b(?:season|complete.*series|episodes?)\b", name):
        return "tv"
    ext = path.suffix.lower()
    if ext in AUDIO_EXTS:
        return "music"
    if path.is_dir():
        try:
            audio = video = episodic = seen = 0
            stack = [(path, 0)]
            while stack and seen < 60:
                current, depth = stack.pop(0)
                for child in current.iterdir():
                    seen += 1
                    if _episode_markers(child.name):
                        episodic += 1
                    if child.is_dir() and depth < 1:
                        stack.append((child, depth + 1))
                    else:
                        suffix = child.suffix.lower()
                        audio += int(suffix in AUDIO_EXTS)
                        video += int(suffix in VIDEO_EXTS)
                    if seen >= 60:
                        break
            if episodic:
                return "tv"
            # A movie directory can contain commentary/soundtrack audio. If we
            # saw any real video, it is not a music release. Only classify a
            # directory as music when it contains audio and no video at all.
            if video > 0:
                return "movie"
            if audio > 0:
                return "music"
        except OSError:
            pass
    return "movie"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def _raw_group_key(media_type: str, title: str, year: int | None) -> str:
    return f"raw:{media_type}:{_slug(title)}:{year or 0}"


def _canonical_key(media_type: str, external_id: str = "", arr_id: int | None = None, title: str = "", year: int | None = None) -> str:
    if external_id:
        return f"{media_type}:external:{str(external_id).lower()}"
    if arr_id:
        return f"{media_type}:arr:{int(arr_id)}"
    return f"{media_type}:title:{_slug(title)}:{year or 0}"


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


def _genres(candidate: dict) -> list[str]:
    values = candidate.get("genres") or []
    if isinstance(values, str):
        values = [x.strip() for x in re.split(r"[,/|]", values) if x.strip()]
    return sorted({str(x).strip() for x in values if str(x).strip()}, key=str.casefold)


def _candidate_identity(media_type: str, c: dict) -> tuple[int | None, str]:
    if media_type == "movie":
        return (int(c.get("id") or 0) or None, str(c.get("tmdbId") or c.get("imdbId") or ""))
    if media_type == "tv":
        return (int(c.get("id") or 0) or None, str(c.get("tvdbId") or c.get("tmdbId") or ""))
    return (int(c.get("id") or 0) or None, str(c.get("foreignArtistId") or ""))


def _score(title: str, year: int | None, candidate: dict, media_type: str) -> int:
    ct = str(candidate.get("title") or candidate.get("artistName") or candidate.get("name") or "")
    cy = candidate.get("year") or candidate.get("firstAired") or candidate.get("releaseDate") or ""

    def norm(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()

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
    if year and str(year) in str(cy):
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
            "genres": _genres(c),
        })
    return rows


async def _best_music_match(title: str, year: int | None) -> dict[str, Any]:
    client = LidarrClient()
    try:
        albums = await client.album_lookup(f"{title} {year or ''}".strip())
    except Exception:
        albums = []
    scored: list[tuple[int, dict]] = []
    for album in list(albums or [])[:20]:
        album_title = str(album.get("title") or "")
        pseudo = {"title": album_title, "releaseDate": album.get("releaseDate") or ""}
        score = _score(title, year, pseudo, "music")
        artist = album.get("artist") or {}
        artist_name = artist.get("artistName") or artist.get("name") or album.get("artistName") or ""
        external = str(artist.get("foreignArtistId") or album.get("foreignArtistId") or "")
        artist_id = int(artist.get("id") or 0) or None
        if artist_name and (external or artist_id):
            scored.append((score, {
                "raw": artist or album,
                "id": artist_id,
                "external_id": external,
                "title": artist_name,
                "year": "",
                "poster_url": _poster(artist) or _poster(album),
                "overview": str(artist.get("overview") or album.get("overview") or "")[:500],
                "genres": sorted(set(_genres(artist) + _genres(album)), key=str.casefold),
                "confidence": score,
                "album_title": album_title,
            }))
    if scored:
        return max(scored, key=lambda item: item[0])[1]
    candidates = await lookup("music", title)
    if not candidates:
        return {}
    best = max(candidates, key=lambda x: _score(title, year, x["raw"], "music"))
    out = dict(best)
    out["confidence"] = _score(title, year, best["raw"], "music")
    return out


async def _best_match(media_type: str, title: str, year: int | None) -> dict[str, Any]:
    if media_type == "music":
        return await _best_music_match(title, year)
    candidates = await lookup(media_type, f"{title} {year or ''}".strip())
    if not candidates:
        return {}
    best = max(candidates, key=lambda x: _score(title, year, x["raw"], media_type))
    out = dict(best)
    out["confidence"] = _score(title, year, best["raw"], media_type)
    return out


def _type_overrides() -> dict[str, str]:
    ensure_schema()
    with db() as conn:
        rows = conn.execute("SELECT raw_key,media_type FROM magic_intake_type_overrides").fetchall()
    return {str(row[0]): str(row[1]) for row in rows if str(row[1]) in {"movie", "tv", "music"}}


def set_media_type(group_key: str, media_type: str) -> dict[str, Any]:
    """Persist a source-level Movie/TV/Music override and clear stale Arr identity."""
    ensure_schema()
    media_type = str(media_type or "").strip().lower()
    if media_type not in {"movie", "tv", "music"}:
        raise ValueError("Media type must be movie, tv or music")
    group = get_group(group_key)
    if not group:
        raise ValueError("Magic Intake group not found")
    sources = list(group.get("source_paths") or [])
    if not sources:
        raise ValueError("This intake card no longer has top-level source releases")
    missing = [source for source in sources if not _safe_source(source).exists()]
    if missing:
        raise ValueError("Media type can only be changed while the source still exists at top-level __magic__")
    with db() as conn:
        for source in sources:
            conn.execute(
                """INSERT INTO magic_intake_type_overrides(raw_key,media_type,updated_at) VALUES(?,?,?)
                   ON CONFLICT(raw_key) DO UPDATE SET media_type=excluded.media_type,updated_at=excluded.updated_at""",
                (source, media_type, utcnow()),
            )
            # A Force Match against the old service must not survive a type change.
            conn.execute("DELETE FROM magic_intake_overrides WHERE raw_key=?", (source,))
        conn.execute(
            """UPDATE magic_intake_groups SET media_type=?,canonical_key='',match_service='',match_id=NULL,match_title='',
               match_year=NULL,match_external_id='',poster_url='',genres_json='[]',confidence=0,destination_key='',
               state='discovered',progress=0,progress_detail='Media type changed; metadata will be rematched',job_id='',
               error='',verify_baseline_json='{}',verify_started_at='',verify_checks=0,destination_arr_path='',moved_paths_json='[]',updated_at=?
               WHERE group_key=?""",
            (media_type, utcnow(), group_key),
        )
        conn.execute(
            "INSERT INTO magic_intake_events(group_key,state,detail) VALUES(?,?,?)",
            (group_key, "discovered", f"Media type manually changed to {media_type}"),
        )
    _CACHE["groups"] = []
    return {"ok": True, "media_type": media_type, "detail": f"Media type changed to {media_type}; rematch scan queued"}


def _override_for_sources(sources: list[str]) -> dict[str, Any]:
    if not sources:
        return {}
    ensure_schema()
    placeholders = ",".join("?" for _ in sources)
    with db() as conn:
        rows = conn.execute(
            f"SELECT * FROM magic_intake_overrides WHERE raw_key IN ({placeholders}) ORDER BY updated_at DESC",
            tuple(sources),
        ).fetchall()
    if not rows:
        return {}
    row = dict(rows[0])
    try:
        genres = json.loads(row.get("genres_json") or "[]")
    except Exception:
        genres = []
    return {
        "id": row.get("arr_id"), "external_id": row.get("external_id") or "", "title": row.get("title") or "",
        "year": row.get("year") or "", "poster_url": row.get("poster_url") or "", "genres": genres,
        "confidence": 100, "forced": True,
    }


def _known_by_source() -> dict[str, dict[str, Any]]:
    ensure_schema()
    result: dict[str, dict[str, Any]] = {}
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM magic_intake_groups WHERE ignored=0 AND match_title<>'' AND confidence>=80"
        ).fetchall()
    for raw in rows:
        row = dict(raw)
        try:
            sources = json.loads(row.get("source_paths_json") or "[]")
            genres = json.loads(row.get("genres_json") or "[]")
        except Exception:
            continue
        candidate = {
            "id": row.get("match_id"), "external_id": row.get("match_external_id") or "",
            "title": row.get("match_title") or "", "year": row.get("match_year") or "",
            "poster_url": row.get("poster_url") or "", "genres": genres,
            "confidence": int(row.get("confidence") or 0), "cached": True,
            "canonical_key": row.get("canonical_key") or "", "media_type": row.get("media_type") or "",
        }
        for source in sources:
            result[str(source)] = candidate
    return result


def _matching_known_source(sources: list[str], known: dict[str, dict[str, Any]]) -> dict[str, Any]:
    matches = [known[x] for x in sources if x in known]
    if not matches:
        return {}
    first = matches[0]
    identity = str(first.get("external_id") or first.get("id") or first.get("title") or "")
    if identity and all(str(x.get("external_id") or x.get("id") or x.get("title") or "") == identity for x in matches):
        return dict(first)
    return {}


def _cached_match_for_sources(sources: list[str], known: dict[str, dict[str, Any]]) -> dict[str, Any]:
    first = _matching_known_source(sources, known)
    if not first:
        return {}
    # Rows created by v13.0 have no canonical_key. Re-look them up once in
    # v13.1 so canonical identity + genres are refreshed. If that refresh
    # fails, scan() falls back to this legacy identity rather than discarding
    # a previously-good match.
    if not first.get("canonical_key"):
        return {}
    return first


def _merge_group(target: dict[str, Any], source: dict[str, Any]) -> None:
    target["source_paths"] = sorted(set(target.get("source_paths", []) + source.get("source_paths", [])), key=str.casefold)
    target["episodes"] = sorted(set(target.get("episodes", []) + source.get("episodes", [])))
    target["genres"] = sorted(set(target.get("genres", []) + source.get("genres", [])), key=str.casefold)
    scores = [x for x in (int(target.get("confidence") or 0), int(source.get("confidence") or 0)) if x > 0]
    if scores:
        target["confidence"] = min(scores)
    if not target.get("poster_url") and source.get("poster_url"):
        target["poster_url"] = source["poster_url"]
    if source.get("forced"):
        target["forced"] = True


def _state_for_confidence(confidence: int) -> str:
    if confidence >= settings_state()["auto_match_threshold"]:
        return "matched"
    if confidence >= 80:
        return "review"
    return "unmatched"


def _upsert_group(group: dict[str, Any], *, schema_ready: bool = False) -> None:
    if not schema_ready:
        ensure_schema()
    now = utcnow()
    genres_json = json.dumps(sorted(set(group.get("genres") or []), key=str.casefold))
    with db() as conn:
        old = conn.execute(
            "SELECT state,ignored,match_id,match_title,confidence,destination_key,poster_url,progress,progress_detail,job_id,error "
            "FROM magic_intake_groups WHERE group_key=?", (group["group_key"],)
        ).fetchone()
        state = str(group.get("state") or (old["state"] if old else "discovered"))
        ignored = int(old["ignored"] if old else 0)
        match_id = group.get("match_id") if group.get("match_id") is not None else (old["match_id"] if old else None)
        match_title = group.get("match_title") or (old["match_title"] if old else "")
        confidence = int(group.get("confidence") if group.get("confidence") is not None else (old["confidence"] if old else 0))
        destination = group.get("destination_key") or (old["destination_key"] if old else "")
        poster = group.get("poster_url") or (old["poster_url"] if old else "")
        progress = int(group.get("progress") if group.get("progress") is not None else (old["progress"] if old else 0))
        progress_detail = group.get("progress_detail") or (old["progress_detail"] if old else "")
        job_id = group.get("job_id") or (old["job_id"] if old else "")
        conn.execute(
            """INSERT INTO magic_intake_groups(group_key,canonical_key,media_type,normalized_title,year,source_paths_json,release_count,episodes_json,genres_json,
               match_service,match_id,match_title,match_year,match_external_id,poster_url,confidence,destination_key,state,progress,progress_detail,job_id,ignored,error,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(group_key) DO UPDATE SET canonical_key=excluded.canonical_key,media_type=excluded.media_type,normalized_title=excluded.normalized_title,year=excluded.year,
               source_paths_json=excluded.source_paths_json,release_count=excluded.release_count,episodes_json=excluded.episodes_json,genres_json=excluded.genres_json,
               match_service=CASE WHEN excluded.match_id IS NOT NULL OR excluded.match_external_id<>'' THEN excluded.match_service ELSE magic_intake_groups.match_service END,
               match_id=COALESCE(excluded.match_id,magic_intake_groups.match_id),
               match_title=CASE WHEN excluded.match_title<>'' THEN excluded.match_title ELSE magic_intake_groups.match_title END,
               match_year=COALESCE(excluded.match_year,magic_intake_groups.match_year),
               match_external_id=CASE WHEN excluded.match_external_id<>'' THEN excluded.match_external_id ELSE magic_intake_groups.match_external_id END,
               poster_url=CASE WHEN excluded.poster_url<>'' THEN excluded.poster_url ELSE magic_intake_groups.poster_url END,
               confidence=CASE WHEN excluded.confidence>0 THEN excluded.confidence ELSE magic_intake_groups.confidence END,
               destination_key=CASE WHEN excluded.destination_key<>'' THEN excluded.destination_key ELSE magic_intake_groups.destination_key END,
               state=CASE WHEN magic_intake_groups.state IN ('imported','queued','importing','verifying','awaiting_arr','partially_verified','numbering_mismatch','verification_timeout','source_missing','failed','partial') THEN magic_intake_groups.state ELSE excluded.state END,
               progress=CASE WHEN magic_intake_groups.state IN ('queued','importing','verifying','awaiting_arr','partially_verified','numbering_mismatch','verification_timeout','source_missing','failed') THEN magic_intake_groups.progress ELSE excluded.progress END,
               progress_detail=CASE WHEN magic_intake_groups.state IN ('queued','importing','verifying','awaiting_arr','partially_verified','numbering_mismatch','verification_timeout','source_missing','failed') THEN magic_intake_groups.progress_detail ELSE excluded.progress_detail END,
               job_id=CASE WHEN magic_intake_groups.state IN ('queued','importing','verifying','awaiting_arr','partially_verified','numbering_mismatch','verification_timeout','source_missing','failed') THEN magic_intake_groups.job_id ELSE excluded.job_id END,
               error=CASE WHEN magic_intake_groups.state IN ('failed','source_missing') AND excluded.error='' THEN magic_intake_groups.error ELSE excluded.error END,
               updated_at=excluded.updated_at""",
            (group["group_key"], group.get("canonical_key") or group["group_key"], group["media_type"], group["normalized_title"], group.get("year"),
             json.dumps(group.get("source_paths") or []), len(group.get("source_paths") or []), json.dumps(sorted(set(group.get("episodes") or []))), genres_json,
             group.get("match_service") or "", match_id, match_title, group.get("match_year"), group.get("match_external_id") or "", poster, confidence,
             destination, state, progress, progress_detail, job_id, ignored, group.get("error") or "", now),
        )
        if not old:
            conn.execute("INSERT INTO magic_intake_events(group_key,state,detail) VALUES(?,?,?)", (group["group_key"], "discovered", "Found in top-level __magic__ intake"))


def _season_summary(episodes: list[str]) -> list[int]:
    seasons: set[int] = set()
    for marker in episodes:
        match = re.match(r"S(\d{2})E\d+", marker)
        if match:
            seasons.add(int(match.group(1)))
            continue
        match = re.match(r"S(\d{2})-S(\d{2})", marker)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if end >= start and end - start <= 50:
                seasons.update(range(start, end + 1))
    return sorted(seasons)


def _themes(row: dict[str, Any]) -> list[str]:
    text = " ".join([
        str(row.get("match_title") or row.get("normalized_title") or ""),
        " ".join(row.get("genres") or []),
    ]).casefold()
    out: set[str] = set()
    genres = {str(x).casefold() for x in row.get("genres") or []}
    if genres & {"family", "animation", "children", "kids"}:
        out.add("kids")
    if any(word in text for word in ("christmas", "xmas", "santa", "holiday")):
        out.add("christmas")
    if "horror" in genres or any(word in text for word in ("halloween", "haunted", "horror")):
        out.add("halloween")
    return sorted(out)


def list_groups(include_imported: bool = True) -> list[dict[str, Any]]:
    ensure_schema()
    sql = "SELECT * FROM magic_intake_groups WHERE ignored=0"
    if not include_imported:
        sql += " AND state NOT IN ('imported','ignored')"
    sql += " ORDER BY CASE state WHEN 'importing' THEN 0 WHEN 'verifying' THEN 1 WHEN 'awaiting_arr' THEN 2 WHEN 'partially_verified' THEN 3 WHEN 'numbering_mismatch' THEN 4 WHEN 'verification_timeout' THEN 5 WHEN 'source_missing' THEN 6 WHEN 'failed' THEN 7 WHEN 'partial' THEN 8 WHEN 'matched' THEN 9 WHEN 'review' THEN 10 WHEN 'discovered' THEN 11 WHEN 'unmatched' THEN 12 ELSE 13 END, confidence DESC, updated_at DESC"
    with db() as conn:
        rows = [dict(x) for x in conn.execute(sql).fetchall()]
    for row in rows:
        try:
            row["source_paths"] = json.loads(row.pop("source_paths_json") or "[]")
        except Exception:
            row["source_paths"] = []
        try:
            row["episodes"] = json.loads(row.pop("episodes_json") or "[]")
        except Exception:
            row["episodes"] = []
        try:
            row["genres"] = json.loads(row.pop("genres_json") or "[]")
        except Exception:
            row["genres"] = []
        try:
            row["verify_baseline"] = json.loads(row.get("verify_baseline_json") or "{}")
        except Exception:
            row["verify_baseline"] = {}
        try:
            row["moved_paths"] = json.loads(row.get("moved_paths_json") or "[]")
        except Exception:
            row["moved_paths"] = []
        row["destination_options"] = DESTINATIONS.get(row["media_type"], {})
        row["seasons"] = _season_summary(row["episodes"])
        row["episode_count"] = len(_episode_pairs(row["episodes"]))
        row["themes"] = _themes(row)
        row["display_title"] = row.get("match_title") or row.get("normalized_title") or "Unknown"
        row["status_label"] = STATE_LABELS.get(str(row.get("state") or ""), str(row.get("state") or "").replace("_", " ").title())
        row["recheckable"] = str(row.get("state") or "") in RECHECKABLE_STATES and bool(row.get("match_id"))
        row["dom_id"] = "magic-" + _slug(row["group_key"])[-90:]
    return rows


def get_group(group_key: str) -> dict | None:
    for row in list_groups(True):
        if row["group_key"] == group_key:
            return row
    return None


def _summary(groups: list[dict]) -> dict[str, int]:
    attention_states = {"partially_verified", "numbering_mismatch", "verification_timeout", "source_missing", "failed", "partial"}
    return {
        "total": len(groups),
        "matched": sum(1 for x in groups if x["state"] in {"matched", "ready"}),
        "review": sum(1 for x in groups if x["state"] == "review"),
        "unmatched": sum(1 for x in groups if x["state"] in {"discovered", "unmatched"}),
        "imported": sum(1 for x in groups if x["state"] == "imported"),
        "awaiting": sum(1 for x in groups if x["state"] == "awaiting_arr"),
        "attention": sum(1 for x in groups if x["state"] in attention_states),
        "partial": sum(1 for x in groups if x["state"] in attention_states),
        "importing": sum(1 for x in groups if x["state"] in {"queued", "importing", "verifying"}),
    }


def filter_options(groups: list[dict[str, Any]] | None = None) -> dict[str, list[str]]:
    groups = groups if groups is not None else list_groups(True)
    genres = sorted({g for row in groups for g in row.get("genres", [])}, key=str.casefold)
    themes = sorted({t for row in groups for t in row.get("themes", [])})
    return {"genres": genres, "themes": themes}


def _discover_provisional(base: Path, type_overrides: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    # FUSE directory walking/classification is deliberately synchronous here
    # because scan() runs this helper in a worker thread. It must never block
    # the FastAPI event loop while thousands of __magic__ entries are examined.
    entries = [p for p in base.iterdir() if not p.name.startswith(".") and p.name.lower() not in ORGANIZED]
    type_overrides = type_overrides or {}
    provisional: dict[str, dict[str, Any]] = {}
    for path in entries:
        title, year, episodes = _clean_release_name(path.name)
        media_type = type_overrides.get(path.name) or _media_type(path, path.name, episodes)
        key = _raw_group_key(media_type, title, year)
        group = provisional.setdefault(key, {
            "raw_group_key": key, "media_type": media_type, "normalized_title": title, "year": year,
            "source_paths": [], "episodes": [], "genres": [], "state": "discovered", "error": "",
        })
        group["source_paths"].append(path.name)
        group["episodes"].extend(episodes)
    return provisional


async def scan() -> dict[str, Any]:
    ensure_schema()
    if _CACHE.get("running"):
        return cached_state()
    # Do not rebuild/delete canonical rows while imports are moving entries.
    # The next scan will run as soon as those background jobs have settled.
    if any(not task.done() for task in _IMPORT_TASKS.values()):
        _CACHE["last_error"] = "Scan deferred while Magic Intake imports are active"
        return cached_state()
    _CACHE["running"] = True
    try:
        base = root()
        if not base.exists():
            raise RuntimeError(f"Magic Intake path does not exist: {base}")
        type_overrides = _type_overrides()
        provisional = await asyncio.to_thread(_discover_provisional, base, type_overrides)
        known = _known_by_source()

        sem = asyncio.Semaphore(6)

        async def enrich(group: dict[str, Any]) -> dict[str, Any]:
            match = _override_for_sources(group["source_paths"])
            if not match:
                match = _cached_match_for_sources(group["source_paths"], known)
                if match and match.get("media_type") and match.get("media_type") != group["media_type"]:
                    match = {}
            if not match:
                async with sem:
                    match = await _best_match(group["media_type"], group["normalized_title"], group.get("year"))
            if not match:
                # Preserve a v13.0 match if the local Arr lookup is temporarily
                # unavailable during the first v13.1 canonicalisation scan.
                match = _matching_known_source(group["source_paths"], known)
                if match and match.get("media_type") and match.get("media_type") != group["media_type"]:
                    match = {}
            if match:
                confidence = int(match.get("confidence") or 0)
                group.update({
                    "match_service": {"movie": "radarr", "tv": "sonarr", "music": "lidarr"}[group["media_type"]],
                    "match_id": match.get("id"), "match_title": match.get("title") or "",
                    "match_year": match.get("year") or None, "match_external_id": match.get("external_id") or "",
                    "poster_url": match.get("poster_url") or "", "genres": match.get("genres") or [],
                    "confidence": confidence, "state": _state_for_confidence(confidence), "forced": bool(match.get("forced")),
                })
                group["canonical_key"] = _canonical_key(
                    group["media_type"], group.get("match_external_id") or "", group.get("match_id"),
                    group.get("match_title") or group["normalized_title"], group.get("match_year") or group.get("year"),
                )
            else:
                group.update({"confidence": 0, "state": "unmatched", "canonical_key": group["raw_group_key"]})
            return group

        enriched = await asyncio.gather(*(enrich(group) for group in provisional.values()))

        canonical: dict[str, dict[str, Any]] = {}
        for group in enriched:
            key = group.get("canonical_key") or group["raw_group_key"]
            if key not in canonical:
                canonical[key] = dict(group)
                canonical[key]["group_key"] = key
                if canonical[key].get("match_title"):
                    canonical[key]["normalized_title"] = canonical[key]["match_title"]
            else:
                _merge_group(canonical[key], group)
                canonical[key]["state"] = _state_for_confidence(int(canonical[key].get("confidence") or 0))

        # The inbox is a projection of what exists at top-level __magic__ now.
        # Rebuild every non-imported visible row from that source of truth. This
        # removes stale v13.0/v13.1 partial/running duplicates whose files were
        # already moved, while imported history remains intact.
        with db() as conn:
            conn.execute(
                "DELETE FROM magic_intake_groups WHERE ignored=0 AND state IN "
                "('discovered','matched','review','unmatched','ready','partial','queued','importing','verifying')"
            )
        for group in canonical.values():
            _upsert_group(group, schema_ready=True)

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
    return {**_CACHE, "settings": settings_state(), "destinations": DESTINATIONS, "filters": filter_options(_CACHE["groups"])}


def _filter_rows(
    rows: list[dict[str, Any]], *, media_type: str = "all", genre: str = "all",
    theme: str = "all", state: str = "all", q: str = "", include_imported: bool = False,
) -> list[dict[str, Any]]:
    media_type = (media_type or "all").casefold()
    genre = (genre or "all").casefold()
    theme = (theme or "all").casefold()
    state = (state or "all").casefold()
    q = (q or "").strip().casefold()
    out: list[dict[str, Any]] = []
    for row in rows:
        row_state = str(row.get("state") or "").casefold()
        if not include_imported and row_state == "imported":
            continue
        state_bucket = "importing" if row_state in {"queued", "importing", "verifying"} else row_state
        if state == "attention" and row_state in {"partially_verified", "numbering_mismatch", "verification_timeout", "source_missing", "failed", "partial"}:
            state_bucket = "attention"
        if media_type != "all" and str(row.get("media_type") or "").casefold() != media_type:
            continue
        if genre != "all" and genre not in {str(x).casefold() for x in row.get("genres") or []}:
            continue
        if theme != "all" and theme not in {str(x).casefold() for x in row.get("themes") or []}:
            continue
        if state != "all" and state_bucket != state:
            continue
        if q:
            haystack = " ".join([str(row.get("display_title") or ""), *(str(x) for x in row.get("source_paths") or [])]).casefold()
            if q not in haystack:
                continue
        out.append(row)
    return out


def query_groups(
    *, media_type: str = "all", genre: str = "all", theme: str = "all", state: str = "all",
    q: str = "", offset: int = 0, limit: int = DEFAULT_PAGE_SIZE, include_imported: bool = False,
) -> dict[str, Any]:
    snapshot = cached_state()
    filtered = _filter_rows(
        list(snapshot.get("groups") or []), media_type=media_type, genre=genre, theme=theme, state=state,
        q=q, include_imported=include_imported,
    )
    offset = max(0, int(offset or 0))
    limit = max(12, min(200, int(limit or DEFAULT_PAGE_SIZE)))
    page = filtered[offset:offset + limit]
    return {
        "rows": page, "total": len(filtered), "offset": offset, "limit": limit,
        "has_more": offset + len(page) < len(filtered),
        "summary": snapshot.get("summary") or {}, "filters": snapshot.get("filters") or {},
        "settings": snapshot.get("settings") or {}, "revision": snapshot.get("last_scan_at") or "",
        "running": bool(snapshot.get("running")), "last_error": snapshot.get("last_error") or "",
    }


def lightweight_state() -> dict[str, Any]:
    snapshot = cached_state()
    groups = list(snapshot.get("groups") or [])
    active = [row for row in groups if str(row.get("state") or "") in {"queued", "importing", "verifying", "awaiting_arr"}]
    attention = [row for row in groups if str(row.get("state") or "") in {"partially_verified", "numbering_mismatch", "verification_timeout", "source_missing", "failed", "partial"}][:50]
    imported = [row for row in groups if str(row.get("state") or "") == "imported"][:40]
    # Keep polling small: only progress/attention/recent terminal rows, never
    # the full source-release catalogue.
    updates = active + attention + imported
    compact_updates = [{
        "group_key": row.get("group_key") or "", "state": row.get("state") or "",
        "status_label": row.get("status_label") or STATE_LABELS.get(str(row.get("state") or ""), str(row.get("state") or "")),
        "recheckable": bool(row.get("recheckable")),
        "progress": int(row.get("progress") or 0), "progress_detail": row.get("progress_detail") or "",
        "error": row.get("error") or "", "genres": row.get("genres") or [], "themes": row.get("themes") or [],
    } for row in updates]
    return {
        "summary": snapshot.get("summary") or {}, "filters": snapshot.get("filters") or {},
        "settings": snapshot.get("settings") or {}, "revision": snapshot.get("last_scan_at") or "",
        "running": bool(snapshot.get("running")), "last_error": snapshot.get("last_error") or "",
        "updates": compact_updates,
    }


async def _background_scan_runner() -> None:
    global _SCAN_TASK
    try:
        await scan()
    finally:
        _SCAN_TASK = None


def request_scan() -> dict[str, Any]:
    global _SCAN_TASK
    if _SCAN_TASK and not _SCAN_TASK.done():
        return {"ok": True, "queued": False, "detail": "Magic Intake scan is already running"}
    if any(not task.done() for task in _IMPORT_TASKS.values()):
        return {"ok": True, "queued": False, "detail": "Scan deferred until active imports finish"}
    _SCAN_TASK = asyncio.create_task(_background_scan_runner(), name="magic-intake-manual-scan")
    return {"ok": True, "queued": True, "detail": "Magic Intake scan started in the background"}


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


def force_match(group_key: str, candidate: dict[str, Any]) -> dict[str, Any]:
    ensure_schema()
    group = get_group(group_key)
    if not group:
        raise ValueError("Magic Intake group not found")
    service = {"movie": "radarr", "tv": "sonarr", "music": "lidarr"}[group["media_type"]]
    genres = candidate.get("genres") or []
    external_id = str(candidate.get("external_id") or "")
    canonical = _canonical_key(group["media_type"], external_id, candidate.get("id"), candidate.get("title") or "", candidate.get("year") or group.get("year"))
    with db() as conn:
        for source in group.get("source_paths") or []:
            conn.execute(
                """INSERT INTO magic_intake_overrides(raw_key,media_type,service,arr_id,title,year,external_id,poster_url,genres_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(raw_key) DO UPDATE SET media_type=excluded.media_type,service=excluded.service,arr_id=excluded.arr_id,title=excluded.title,
                   year=excluded.year,external_id=excluded.external_id,poster_url=excluded.poster_url,genres_json=excluded.genres_json,updated_at=excluded.updated_at""",
                (source, group["media_type"], service, candidate.get("id"), candidate.get("title") or "", candidate.get("year") or None,
                 external_id, candidate.get("poster_url") or "", json.dumps(genres), utcnow()),
            )
        conn.execute(
            """UPDATE magic_intake_groups SET canonical_key=?,match_service=?,match_id=?,match_title=?,match_year=?,match_external_id=?,poster_url=?,genres_json=?,
               confidence=100,state='matched',error='',updated_at=? WHERE group_key=?""",
            (canonical, service, candidate.get("id"), candidate.get("title") or "", candidate.get("year") or None,
             external_id, candidate.get("poster_url") or "", json.dumps(genres), utcnow(), group_key),
        )
        conn.execute("INSERT INTO magic_intake_events(group_key,state,detail) VALUES(?,?,?)", (group_key, "matched", f"Force matched to {candidate.get('title') or 'selected item'}"))
    _CACHE["groups"] = []
    return get_group(group_key) or {"group_key": group_key, "match_title": candidate.get("title") or "", "confidence": 100}


def ignore(group_key: str) -> None:
    ensure_schema()
    with db() as conn:
        conn.execute("UPDATE magic_intake_groups SET ignored=1,state='ignored',updated_at=? WHERE group_key=?", (utcnow(), group_key))
        conn.execute("INSERT INTO magic_intake_events(group_key,state,detail) VALUES(?,?,?)", (group_key, "ignored", "Ignored by user"))
    _CACHE["groups"] = []


def _safe_source(name: str) -> Path:
    base = root().resolve()
    path = (base / name).resolve()
    if path.parent != base:
        raise ValueError("Magic Intake only accepts top-level __magic__ entries")
    return path


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


def _exception_detail(exc: BaseException, stage: str = "Import failed") -> str:
    text = str(exc).strip()
    if not text:
        text = repr(exc)
    return f"{stage}: {type(exc).__name__}: {text}"


async def _validate_arr_root(client: Any, desired: Path) -> str:
    desired_text = str(desired).rstrip("/")
    roots = await client.roots()
    configured = [str(row.get("path") or "").rstrip("/") for row in (roots or []) if row.get("path")]
    if desired_text in configured:
        return desired_text
    shown = ", ".join(configured[:12]) or "none"
    raise ArrError(
        f"{client.name}: Magic destination root '{desired_text}' is not configured as an Arr root folder. "
        f"Configured root folder(s): {shown}"
    )


async def _ensure_arr_item(group: dict, target_parent_arr: Path) -> tuple[Any, int, dict]:
    media_type = group["media_type"]
    term = group.get("match_title") or group["normalized_title"]
    if media_type == "movie":
        client = RadarrClient(); items = await client.movies(); existing = _existing_by_match(media_type, items, group)
        if existing:
            return client, int(existing["id"]), existing
        lookups = await client.lookup(term)
        candidate = next((x for x in lookups if str(x.get("tmdbId") or x.get("imdbId") or "") == str(group.get("match_external_id") or "")), lookups[0] if lookups else None)
        if not candidate:
            raise ArrError("Radarr lookup no longer returns the selected movie")
        root_path = await _validate_arr_root(client, target_parent_arr)
        added = await client.add_movie(candidate, root_path, search=False, monitored=True)
        return client, int(added["id"]), added
    if media_type == "tv":
        client = SonarrClient(); items = await client.series(); existing = _existing_by_match(media_type, items, group)
        if existing:
            return client, int(existing["id"]), existing
        lookups = await client.lookup(term)
        candidate = next((x for x in lookups if str(x.get("tvdbId") or x.get("tmdbId") or "") == str(group.get("match_external_id") or "")), lookups[0] if lookups else None)
        if not candidate:
            raise ArrError("Sonarr lookup no longer returns the selected series")
        root_path = await _validate_arr_root(client, target_parent_arr)
        added = await client.add_series(candidate, root_path, search=False, monitored=True)
        return client, int(added["id"]), added
    client = LidarrClient(); items = await client.artists(); existing = _existing_by_match(media_type, items, group)
    if existing:
        return client, int(existing["id"]), existing
    lookups = await client.artist_lookup(term)
    candidate = next((x for x in lookups if str(x.get("foreignArtistId") or "") == str(group.get("match_external_id") or "")), lookups[0] if lookups else None)
    if not candidate:
        raise ArrError("Lidarr lookup no longer returns the selected artist")
    root_path = await _validate_arr_root(client, target_parent_arr)
    added = await client.add_artist(candidate, root_path, search=False)
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


def _episode_pairs(markers: list[str]) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    for marker in markers:
        match = re.fullmatch(r"S(\d{2})E(\d{2,3})", marker)
        if match:
            pairs.add((int(match.group(1)), int(match.group(2))))
    return pairs


async def _verification_snapshot(client: Any, media_type: str, arr_id: int) -> dict[str, Any]:
    try:
        if media_type == "movie":
            item = await client.movie(arr_id)
            return {"has_file": bool(item.get("hasFile"))}
        if media_type == "tv":
            episodes = await client.episodes(arr_id)
            present = sorted(
                (int(x.get("seasonNumber") or 0), int(x.get("episodeNumber") or 0))
                for x in episodes if x.get("hasFile")
            )
            return {"present": [list(x) for x in present], "count": len(present)}
        albums = await client.albums(arr_id)
        count = sum(int((x.get("statistics") or {}).get("trackFileCount") or 0) for x in albums)
        return {"track_count": count}
    except Exception:
        return {}


def _client_for_media(media_type: str) -> Any:
    if media_type == "movie":
        return RadarrClient()
    if media_type == "tv":
        return SonarrClient()
    return LidarrClient()


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _save_verification_context(
    group_key: str, before: dict[str, Any], destination_arr_path: str, moved_paths: list[str] | None = None,
) -> None:
    ensure_schema()
    with db() as conn:
        conn.execute(
            "UPDATE magic_intake_groups SET verify_baseline_json=?,verify_started_at=?,verify_checks=0,"
            "destination_arr_path=?,moved_paths_json=?,updated_at=? WHERE group_key=?",
            (
                json.dumps(before or {}), utcnow(), str(destination_arr_path or ""),
                json.dumps(list(moved_paths or [])), utcnow(), group_key,
            ),
        )
    _CACHE["groups"] = []


def _update_moved_paths(group_key: str, moved_paths: list[str]) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE magic_intake_groups SET moved_paths_json=?,updated_at=? WHERE group_key=?",
            (json.dumps(list(moved_paths or [])), utcnow(), group_key),
        )
    _CACHE["groups"] = []


def _verification_baseline(group: dict[str, Any]) -> dict[str, Any]:
    baseline = group.get("verify_baseline")
    if isinstance(baseline, dict):
        return baseline
    try:
        return json.loads(group.get("verify_baseline_json") or "{}")
    except Exception:
        return {}


async def _verification_status(
    client: Any, media_type: str, arr_id: int, group: dict[str, Any], before: dict[str, Any],
) -> tuple[str, str]:
    """Return an explicit verification state instead of the old catch-all partial."""
    if media_type == "movie":
        item = await client.movie(arr_id)
        if item.get("hasFile"):
            return "imported", "Radarr confirms the movie file is present"
        return "awaiting_arr", "Movie was moved; waiting for Radarr to confirm hasFile"

    if media_type == "tv":
        episodes = await client.episodes(arr_id)
        present = {
            (int(x.get("seasonNumber") or 0), int(x.get("episodeNumber") or 0))
            for x in episodes if x.get("hasFile")
        }
        expected = _episode_pairs(group.get("episodes") or [])
        if expected:
            confirmed = len(expected & present)
            if confirmed == len(expected):
                return "imported", f"Sonarr confirms all {confirmed} expected episode file(s)"
            if confirmed:
                return "partially_verified", f"Sonarr confirms {confirmed}/{len(expected)} expected episode file(s)"

            expected_seasons = {season for season, _episode in expected}
            same_season_files = {pair for pair in present if pair[0] in expected_seasons}
            if same_season_files:
                seasons = ", ".join(str(x) for x in sorted(expected_seasons))
                return (
                    "numbering_mismatch",
                    f"Sonarr has {len(same_season_files)} file(s) in expected season(s) {seasons}, "
                    f"but 0/{len(expected)} exact SxxExx markers match. Likely episode-order/numbering mismatch",
                )
            return "awaiting_arr", f"Moved successfully; Sonarr currently confirms 0/{len(expected)} expected episode file(s)"

        before_count = int(before.get("count") or 0)
        if len(present) > before_count:
            return "imported", f"Sonarr confirms {len(present) - before_count} new episode file(s)"
        return "awaiting_arr", f"Waiting for Sonarr to register new episode files (currently {len(present)})"

    albums = await client.albums(arr_id)
    count = sum(int((x.get("statistics") or {}).get("trackFileCount") or 0) for x in albums)
    before_count = int(before.get("track_count") or 0)
    if count > before_count:
        return "imported", f"Lidarr confirms {count - before_count} new track file(s)"
    if before_count == 0 and count > 0:
        return "imported", f"Lidarr confirms {count} track file(s)"
    return "awaiting_arr", f"Music was moved; waiting for Lidarr to register new tracks (currently {count})"


async def _verify_with_state(
    client: Any, media_type: str, arr_id: int, group: dict[str, Any], before: dict[str, Any], polls: int = 6,
) -> tuple[str, str]:
    state = "awaiting_arr"
    detail = "Moved successfully; waiting for Arr to confirm imported media"
    for _ in range(max(1, int(polls))):
        await asyncio.sleep(2)
        try:
            state, detail = await _verification_status(client, media_type, arr_id, group, before)
            if state == "imported":
                return state, detail
        except Exception as exc:
            detail = f"Waiting for Arr verification: {exc}"
    return state, detail


async def _verify(client: Any, media_type: str, arr_id: int, group: dict[str, Any], before: dict[str, Any]) -> tuple[bool, str]:
    """Compatibility wrapper retained for tests/older call sites."""
    state, detail = await _verify_with_state(client, media_type, arr_id, group, before)
    return state == "imported", detail


def _set_progress(group_key: str, state: str, progress: int, detail: str, *, error: str = "", arr_id: int | None = None) -> None:
    ensure_schema()
    with db() as conn:
        if arr_id is None:
            conn.execute(
                "UPDATE magic_intake_groups SET state=?,progress=?,progress_detail=?,error=?,updated_at=? WHERE group_key=?",
                (state, max(0, min(100, int(progress))), detail, error, utcnow(), group_key),
            )
        else:
            conn.execute(
                "UPDATE magic_intake_groups SET match_id=?,state=?,progress=?,progress_detail=?,error=?,updated_at=? WHERE group_key=?",
                (arr_id, state, max(0, min(100, int(progress))), detail, error, utcnow(), group_key),
            )
        conn.execute("INSERT INTO magic_intake_events(group_key,state,detail) VALUES(?,?,?)", (group_key, state, detail))
    _CACHE["groups"] = []


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

    dest_write, dest_arr = _safe_destination(
        group["media_type"], destination_key, group["match_title"], group.get("match_year") or group.get("year")
    )
    dest_parent_arr = dest_arr.parent
    moved: list[str] = []
    imported_candidates = 0

    stage = "Resolving target in Arr"
    try:
        _set_progress(group_key, "importing", 10, stage)
        client, arr_id, _ = await _ensure_arr_item(group, dest_parent_arr)
        stage = "Taking Arr verification baseline"
        before = _verification_baseline(group)
        if not before:
            before = await _verification_snapshot(client, group["media_type"], arr_id)
        _save_verification_context(group_key, before, str(dest_arr), moved)

        stage = "Checking top-level __magic__ sources"
        missing = [source for source in sources if not _safe_source(source).exists()]
        if missing:
            # A previous attempt may already have completed the Zurg virtual move.
            # Never try to move the same vanished top-level release again. If the
            # destination exists, switch to Arr reconciliation/verification.
            if len(missing) == len(sources) and dest_write.exists():
                detail = "Source already moved into the selected destination; rechecking Arr instead of moving it again"
                _set_progress(group_key, "awaiting_arr", 72, detail, arr_id=arr_id)
                imported_candidates = await _try_manual_import(client, group["media_type"], str(dest_arr), arr_id)
                await _rescan(client, group["media_type"], arr_id)
                state, verify_detail = await _verify_with_state(client, group["media_type"], arr_id, group, before, polls=3)
                progress = 100 if state == "imported" else (92 if state in {"partially_verified", "numbering_mismatch"} else 82)
                _set_progress(group_key, state, progress, verify_detail, error="", arr_id=arr_id)
                log_event(
                    "info" if state == "imported" else "warning", "magic_intake", state, verify_detail,
                    {"group": group_key, "paths": [str(dest_arr)], "manual_candidates": imported_candidates, "reconciled": True},
                )
                return {"ok": state == "imported", "state": state, "detail": verify_detail, "moved": [], "manual_candidates": imported_candidates, "arr_id": arr_id}

            detail = f"Source release is missing from top-level __magic__ and the expected destination could not be safely reconciled: {missing[0]}"
            _set_progress(group_key, "source_missing", 100, detail, error=detail, arr_id=arr_id)
            log_event("error", "magic_intake", "source_missing", detail, {"group": group_key, "missing": missing})
            return {"ok": False, "state": "source_missing", "detail": detail, "moved": [], "manual_candidates": 0, "arr_id": arr_id}

        stage = "Moving release entries inside __magic__"
        _set_progress(group_key, "importing", 25, f"Moving {len(sources)} release entr{'y' if len(sources) == 1 else 'ies'} inside __magic__", arr_id=arr_id)
        dest_write.parent.mkdir(parents=True, exist_ok=True)
        if len(sources) == 1 and _safe_source(sources[0]).is_dir() and not dest_write.exists():
            src = _safe_source(sources[0])
            src.rename(dest_write)
            moved.append(str(dest_arr))
        else:
            dest_write.mkdir(parents=True, exist_ok=True)
            for source in sources:
                src = _safe_source(source)
                target = dest_write / src.name
                if target.exists():
                    raise FileExistsError(f"Destination already exists: {target}")
                src.rename(target)
                moved.append(str(dest_arr / src.name))
        _update_moved_paths(group_key, moved)

        stage = "Requesting Manual Import candidates from Arr"
        _set_progress(group_key, "importing", 50, "Asking Arr for eligible Manual Import candidates", arr_id=arr_id)
        imported_candidates = await _try_manual_import(client, group["media_type"], str(dest_arr), arr_id)
        stage = "Requesting targeted Arr rescan"
        _set_progress(group_key, "importing", 65, "Targeted Arr rescan requested", arr_id=arr_id)
        await _rescan(client, group["media_type"], arr_id)
        _set_progress(group_key, "verifying", 75, "Waiting for Arr to confirm the imported media", arr_id=arr_id)

        stage = "Verifying imported media in Arr"
        state, detail = await _verify_with_state(client, group["media_type"], arr_id, group, before, polls=6)
        if state == "imported":
            progress = 100
        elif state in {"partially_verified", "numbering_mismatch"}:
            progress = 92
        else:
            progress = 82
        _set_progress(group_key, state, progress, detail, error="", arr_id=arr_id)
        log_event(
            "info" if state == "imported" else "warning", "magic_intake", state, detail,
            {"group": group_key, "paths": moved, "manual_candidates": imported_candidates},
        )
        return {"ok": state == "imported", "state": state, "detail": detail, "moved": moved, "manual_candidates": imported_candidates, "arr_id": arr_id}
    except Exception as exc:
        detail = _exception_detail(exc, stage)
        _set_progress(group_key, "failed", 100, detail, error=detail)
        log_event("error", "magic_intake", "failed", detail, {"group": group_key, "paths": moved, "exception": type(exc).__name__})
        return {"ok": False, "state": "failed", "detail": detail, "moved": moved, "manual_candidates": imported_candidates}


async def _recheck_group(group_key: str, *, manual: bool = False) -> dict[str, Any]:
    ensure_schema()
    group = get_group(group_key)
    if not group:
        return {"ok": False, "detail": "Magic Intake group no longer exists"}
    arr_id = int(group.get("match_id") or 0)
    if not arr_id:
        return {"ok": False, "detail": "No Arr item ID is available for verification"}

    media_type = str(group.get("media_type") or "")
    client = _client_for_media(media_type)
    before = _verification_baseline(group)
    if not group.get("verify_started_at"):
        _save_verification_context(group_key, before, str(group.get("destination_arr_path") or ""), group.get("moved_paths") or [])
        group = get_group(group_key) or group

    try:
        state, detail = await _verification_status(client, media_type, arr_id, group, before)
    except Exception as exc:
        state, detail = "awaiting_arr", f"Arr verification check failed temporarily: {exc}"

    # Preserve a true missing-source diagnosis when neither the original intake
    # entry nor the expected destination exists. If the destination *does*
    # exist, this is a previous move and normal Arr reconciliation can continue.
    if str(group.get("state") or "") == "source_missing" and state == "awaiting_arr":
        destination = str(group.get("destination_arr_path") or "")
        if not destination or not Path(destination).exists():
            state = "source_missing"
            detail = "Source is no longer in top-level __magic__ and the expected destination is not present; manual attention is required"

    started = _parse_iso(str(group.get("verify_started_at") or "")) or _parse_iso(str(group.get("updated_at") or ""))
    age_seconds = (datetime.now(timezone.utc) - started).total_seconds() if started else 0
    window_seconds = settings_state()["verify_window_minutes"] * 60

    if state != "imported" and age_seconds >= window_seconds and state == "awaiting_arr":
        state = "verification_timeout"
        detail = f"Arr still has not confirmed the moved media after {settings_state()['verify_window_minutes']} minute(s). Use Recheck now after Arr finishes processing"

    progress = 100 if state in {"imported", "verification_timeout", "source_missing", "failed"} else (94 if state in {"partially_verified", "numbering_mismatch"} else 86)
    _set_progress(group_key, state, progress, detail, error="" if state not in {"source_missing", "failed"} else detail, arr_id=arr_id)
    with db() as conn:
        conn.execute("UPDATE magic_intake_groups SET verify_checks=verify_checks+1,updated_at=? WHERE group_key=?", (utcnow(), group_key))
    _CACHE["groups"] = []
    log_event("info" if state == "imported" else "warning", "magic_intake", f"recheck_{state}", detail, {"group": group_key, "manual": manual})
    return {"ok": state == "imported", "state": state, "detail": detail}


def request_recheck(group_key: str) -> dict[str, Any]:
    current = _REVERIFY_TASKS.get(group_key)
    if current and not current.done():
        return {"ok": True, "queued": False, "detail": "Verification recheck is already running"}

    async def runner() -> None:
        try:
            await _recheck_group(group_key, manual=True)
        finally:
            _REVERIFY_TASKS.pop(group_key, None)

    task = asyncio.create_task(runner(), name=f"magic-recheck-{_slug(group_key)[-30:]}")
    _REVERIFY_TASKS[group_key] = task
    return {"ok": True, "queued": True, "detail": "Arr verification recheck started"}


async def verification_loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            cfg = settings_state()
            groups = [
                row for row in list_groups(True)
                if str(row.get("state") or "") in {"awaiting_arr", "partially_verified", "numbering_mismatch", "source_missing", "verification_timeout"}
                and row.get("match_id")
            ][:12]
            for group in groups:
                if group["group_key"] in _IMPORT_TASKS and not _IMPORT_TASKS[group["group_key"]].done():
                    continue
                started = _parse_iso(str(group.get("verify_started_at") or "")) or _parse_iso(str(group.get("updated_at") or ""))
                current_state = str(group.get("state") or "")
                if current_state == "verification_timeout":
                    # A timed-out movie/show can still become valid later. Give
                    # it a cheap automatic recheck every five minutes so users
                    # do not need to babysit slow Arr imports indefinitely.
                    updated = _parse_iso(str(group.get("updated_at") or ""))
                    if updated and (datetime.now(timezone.utc) - updated).total_seconds() < 300:
                        continue
                    await _recheck_group(group["group_key"])
                    continue
                if started and (datetime.now(timezone.utc) - started).total_seconds() > cfg["verify_window_minutes"] * 60:
                    # Awaiting Arr gets one final pass so it can become a clear
                    # timeout. Partial/mismatch rows remain actionable without
                    # hammering Sonarr forever after the configured window.
                    if current_state == "awaiting_arr":
                        await _recheck_group(group["group_key"])
                    continue
                await _recheck_group(group["group_key"])
            await asyncio.sleep(cfg["verify_interval_seconds"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _CACHE["last_error"] = f"Verification loop: {exc}"
            await asyncio.sleep(30)



async def _run_import_job(job_id: str, group_key: str, canonical_key: str, destination_key: str, selected_source: str) -> None:
    try:
        _set_progress(group_key, "queued", 3, f"Waiting for an import slot (max {IMPORT_CONCURRENCY} concurrent)")
        async with _IMPORT_SEMAPHORE:
            _set_progress(group_key, "queued", 5, "Import slot acquired; starting")
            await import_group(group_key, destination_key, selected_source)
    except asyncio.CancelledError:
        _set_progress(group_key, "failed", 100, "Import task was cancelled", error="Import task was cancelled")
        raise
    except Exception as exc:
        detail = _exception_detail(exc, "Background import task")
        _set_progress(group_key, "failed", 100, detail, error=detail)
        log_event("error", "magic_intake", "failed", detail, {"group": group_key, "exception": type(exc).__name__})
    finally:
        _IMPORT_TASKS.pop(group_key, None)
        if _IMPORT_CANONICAL_TASKS.get(canonical_key) is asyncio.current_task():
            _IMPORT_CANONICAL_TASKS.pop(canonical_key, None)


def enqueue_import(group_key: str, destination_key: str, selected_source: str = "") -> dict[str, Any]:
    ensure_schema()
    group = get_group(group_key)
    if not group:
        raise ValueError("Magic Intake group not found")
    if str(group.get("state") or "") in POST_MOVE_STATES:
        raise ValueError(f"{STATE_LABELS.get(str(group.get('state') or ''), 'This import')} is already past the move stage. Use Recheck now instead of importing it again")
    canonical_key = str(group.get("canonical_key") or group_key)
    current = _IMPORT_TASKS.get(group_key)
    if current and not current.done():
        return {"ok": True, "queued": False, "job_id": group.get("job_id") or "", "detail": "Import is already running"}
    canonical_task = _IMPORT_CANONICAL_TASKS.get(canonical_key)
    if canonical_task and not canonical_task.done():
        return {"ok": True, "queued": False, "job_id": "", "detail": "An import for this same canonical title is already running"}
    job_id = secrets.token_hex(8)
    with db() as conn:
        conn.execute(
            "UPDATE magic_intake_groups SET state='queued',destination_key=?,progress=2,progress_detail='Queued for background import',job_id=?,error='',updated_at=? WHERE group_key=?",
            (destination_key, job_id, utcnow(), group_key),
        )
        conn.execute("INSERT INTO magic_intake_events(group_key,state,detail) VALUES(?,?,?)", (group_key, "queued", "Queued for background import"))
    task = asyncio.create_task(_run_import_job(job_id, group_key, canonical_key, destination_key, selected_source), name=f"magic-import-{job_id}")
    _IMPORT_TASKS[group_key] = task
    _IMPORT_CANONICAL_TASKS[canonical_key] = task
    _CACHE["groups"] = []
    queued_count = sum(1 for task in _IMPORT_TASKS.values() if task and not task.done())
    return {"ok": True, "queued": True, "job_id": job_id, "detail": f"Import queued ({queued_count} active/queued; max {IMPORT_CONCURRENCY} running at once)"}


def events(group_key: str, limit: int = 80) -> list[dict]:
    ensure_schema()
    with db() as conn:
        rows = conn.execute("SELECT * FROM magic_intake_events WHERE group_key=? ORDER BY id DESC LIMIT ?", (group_key, int(limit))).fetchall()
    return [dict(x) for x in rows]
