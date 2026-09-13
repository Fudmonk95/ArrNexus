from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
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


SOURCE_LABELS = {
    "dmm_rd": "DMM / Direct RD",
    "arr_managed": "Arr Managed",
    "nzb": "NZB",
    "torrent": "Torrent",
    "unknown": "Unknown",
}

# A source receives ONE primary source. This prevents DMM+NZB etc. appearing
# as duplicate source categories. Underlying transport is retained separately.
SOURCE_PRIORITY = {
    "dmm_rd": 0,
    "nzb": 1,
    "torrent": 2,
    "arr_managed": 3,
    "unknown": 4,
}

# Only out-of-band sources are eligible for the Trusted ManualImport bypass.
TRUSTED_OUT_OF_BAND = {"dmm_rd", "nzb", "torrent"}

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
IMPORT_CONCURRENCY = max(1, min(2, int(os.getenv("MAGIC_IMPORT_CONCURRENCY", "1") or 1)))
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
    "already_present": "Already in Arr",
    "failed": "Import failed",
    "partial": "Needs review (legacy)",
    "imported": "Imported",
    "matched": "Matched",
    "review": "Needs review",
    "unmatched": "Unmatched",
    "discovered": "Discovered",
}
_REVERIFY_TASKS: dict[str, asyncio.Task] = {}

_PROVENANCE_CACHE: dict[str, Any] = {
    "updated": 0.0,
    "value": None,
}

_EPISODE_CACHE: dict[int, tuple[float, list[dict]]] = {}


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

    CREATE TABLE IF NOT EXISTS magic_intake_resolved_sources (
        raw_key TEXT PRIMARY KEY,
        resolution TEXT NOT NULL DEFAULT '',
        media_type TEXT NOT NULL DEFAULT '',
        arr_id INTEGER,
        external_id TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        primary_source TEXT NOT NULL DEFAULT 'unknown',
        transport TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '',
        processed_at TEXT DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_magic_resolved_resolution
        ON magic_intake_resolved_sources(resolution, primary_source);
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

        _ensure_column(conn, "magic_intake_groups", "primary_source", "TEXT NOT NULL DEFAULT 'unknown'")
        _ensure_column(conn, "magic_intake_groups", "source_meta_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "magic_intake_groups", "actionable_sources_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "magic_intake_groups", "missing_items_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "magic_intake_groups", "arr_status", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "magic_intake_groups", "precheck_detail", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "magic_intake_groups", "missing_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "magic_intake_groups", "present_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "magic_intake_groups", "needs_import", "INTEGER NOT NULL DEFAULT 0")

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
        "trusted_dmm_bypass": setting_get("magic.trusted_dmm_bypass", "1") == "1",
    }


def save_settings(values: dict[str, Any]) -> None:
    setting_set("magic.enabled", "1" if values.get("enabled") else "0")
    setting_set("magic.interval_seconds", str(max(20, int(values.get("interval_seconds") or 60))))
    setting_set("magic.auto_match_threshold", str(max(80, min(100, int(values.get("auto_match_threshold") or 95)))))
    setting_set("magic.trusted_dmm_bypass", "1" if values.get("trusted_dmm_bypass", True) else "0")
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
            if video > 0:
                return "movie"
            if audio > 0:
                return "music"

        except OSError:
            pass

    return "movie"


def _zurg_classification_sets() -> dict[str, set[str]]:
    """
    Read only directory names from Zurg's classification views.

    No media contents are opened and no ffprobe is performed.
    """
    configured = setting_get("zurg.root", "/zurg_mnt/zurg") or "/zurg_mnt/zurg"
    zroot = Path(configured)

    result: dict[str, set[str]] = {
        "movie": set(),
        "tv": set(),
        "music": set(),
    }

    folders = {
        "movie": "movies",
        "tv": "shows",
        "music": "music",
    }

    for media_type, folder in folders.items():
        view = zroot / folder
        try:
            result[media_type] = {
                child.name
                for child in view.iterdir()
                if not child.name.startswith(".")
            }
        except OSError:
            result[media_type] = set()

    return result


def _classify_media_type(
    path: Path,
    name: str,
    episodes: list[str],
    views: dict[str, set[str]],
) -> tuple[str, bool, str]:
    memberships = [
        media_type
        for media_type in ("tv", "movie", "music")
        if name in views.get(media_type, set())
    ]

    # A single Zurg classification is the strongest local source of truth.
    if len(memberships) == 1:
        return memberships[0], True, f"zurg:{memberships[0]}"

    # Explicit episode naming is strong TV evidence.
    if episodes or re.search(
        r"(?i)\b(?:season[ ._-]*\d+|complete.*series|episodes?)\b",
        name,
    ):
        return "tv", True, "release:episode-markers"

    # Conflicting Zurg classifications are never trusted automatically.
    if len(memberships) > 1:
        return _media_type(path, name, episodes), False, "zurg:classification-conflict"

    # Last resort. This may help display the item but requires review/Force Match
    # before import.
    return _media_type(path, name, episodes), False, "heuristic"


def _collect_tv_markers(path: Path, limit: int = 800) -> list[str]:
    """
    Collect episode markers from names only.

    This deliberately does not open or probe media files.
    It is used only for a TV title being imported, not for every movie scan.
    """
    found: list[str] = list(_episode_markers(path.name))

    if not path.is_dir():
        return sorted(set(found))

    seen = 0
    stack: list[tuple[Path, int]] = [(path, 0)]

    try:
        while stack and seen < limit:
            current, depth = stack.pop(0)

            for child in current.iterdir():
                seen += 1
                found.extend(_episode_markers(child.name))

                if child.is_dir() and depth < 3:
                    stack.append((child, depth + 1))

                if seen >= limit:
                    break

    except OSError:
        pass

    return sorted(set(found))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def _raw_group_key(media_type: str, title: str, year: int | None) -> str:
    return f"raw:{media_type}:{_slug(title)}:{year or 0}"



def _canonical_key(
    media_type: str,
    external_id: str = "",
    arr_id: int | None = None,
    title: str = "",
    year: int | None = None,
) -> str:
    # Once an item exists in an Arr, that Arr's ID is the authoritative
    # canonical identity. This prevents the same series/movie appearing
    # as several cards because different releases resolved through slightly
    # different metadata/external-ID routes.
    if arr_id:
        return f"{media_type}:arr:{int(arr_id)}"

    if external_id:
        return f"{media_type}:external:{str(external_id).lower()}"

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
        try:
            row["source_meta"] = json.loads(row.get("source_meta_json") or "[]")
        except Exception:
            row["source_meta"] = []

        try:
            row["actionable_sources"] = json.loads(row.get("actionable_sources_json") or "[]")
        except Exception:
            row["actionable_sources"] = []

        try:
            row["missing_items"] = json.loads(row.get("missing_items_json") or "[]")
        except Exception:
            row["missing_items"] = []

        row["needs_import"] = bool(row.get("needs_import"))
        row["source_label"] = SOURCE_LABELS.get(
            str(row.get("primary_source") or "unknown"),
            "Unknown",
        )
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



def _resolved_source_names() -> set[str]:
    ensure_schema()

    with db() as conn:
        rows = conn.execute(
            "SELECT raw_key FROM magic_intake_resolved_sources"
        ).fetchall()

    return {str(row[0]) for row in rows}


def _source_meta_map(group: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("name") or ""): row
        for row in (group.get("source_meta") or [])
        if row.get("name")
    }


def _resolve_sources(
    group: dict[str, Any],
    sources: list[str],
    resolution: str,
    detail: str,
) -> None:
    if not sources:
        return

    ensure_schema()

    meta = _source_meta_map(group)

    with db() as conn:
        for source in sources:
            source_meta = meta.get(source) or {}

            conn.execute(
                """
                INSERT INTO magic_intake_resolved_sources(
                    raw_key,resolution,media_type,arr_id,external_id,title,
                    primary_source,transport,detail,processed_at,last_seen_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(raw_key) DO UPDATE SET
                    resolution=excluded.resolution,
                    media_type=excluded.media_type,
                    arr_id=excluded.arr_id,
                    external_id=excluded.external_id,
                    title=excluded.title,
                    primary_source=excluded.primary_source,
                    transport=excluded.transport,
                    detail=excluded.detail,
                    processed_at=excluded.processed_at,
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    source,
                    resolution,
                    str(group.get("media_type") or ""),
                    int(group.get("match_id") or 0) or None,
                    str(group.get("match_external_id") or ""),
                    str(
                        group.get("match_title")
                        or group.get("normalized_title")
                        or ""
                    ),
                    str(source_meta.get("primary_source") or "unknown"),
                    str(source_meta.get("transport") or ""),
                    str(detail or ""),
                    utcnow(),
                    utcnow(),
                ),
            )

    log_event(
        "info",
        "magic_intake",
        f"resolved_{resolution}",
        f"Resolved {len(sources)} intake source(s): {detail}",
        {
            "group": group.get("group_key") or group.get("canonical_key"),
            "sources": sources,
            "resolution": resolution,
        },
    )


def _release_norm(value: str) -> str:
    value = Path(str(value or "")).name

    # Zurg can expose duplicate release names as "Release Name (2)".
    value = re.sub(r"\s+\(\d+\)$", "", value)

    value = re.sub(
        r"\.(mkv|mp4|avi|mov|m4v|ts|wmv|nzb|torrent)$",
        "",
        value,
        flags=re.I,
    )

    return re.sub(
        r"[^a-z0-9]+",
        " ",
        value.casefold(),
    ).strip()


def _history_transport(row: dict[str, Any]) -> str:
    data = row.get("data") or {}

    candidates = [
        row.get("protocol"),
        data.get("protocol"),
        data.get("downloadProtocol"),
        data.get("downloadClient"),
        data.get("downloadClientName"),
    ]

    text = " ".join(
        str(value or "")
        for value in candidates
    ).casefold()

    if "usenet" in text or "nzb" in text:
        return "nzb"

    if "torrent" in text or "qbittorrent" in text:
        return "torrent"

    return ""


def _history_titles(rows: list[dict]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}

    for row in rows or []:
        data = row.get("data") or {}

        candidates = [
            row.get("sourceTitle"),
            data.get("sourceTitle"),
            data.get("releaseTitle"),
            data.get("title"),
        ]

        title = next(
            (
                str(value)
                for value in candidates
                if str(value or "").strip()
            ),
            "",
        )

        norm = _release_norm(title)

        if not norm:
            continue

        index.setdefault(
            norm,
            {
                "transport": _history_transport(row),
                "event_type": str(row.get("eventType") or ""),
            },
        )

    return index


def _top_names(path: Path, limit: int = 20000) -> set[str]:
    names: set[str] = set()

    try:
        for index, entry in enumerate(os.scandir(path)):
            if index >= limit:
                break
            if not entry.name.startswith("."):
                names.add(entry.name)
    except OSError:
        pass

    return names


async def _source_context() -> dict[str, Any]:
    cached = _PROVENANCE_CACHE.get("value")

    if cached and time.monotonic() - float(
        _PROVENANCE_CACHE.get("updated") or 0
    ) < 600:
        return cached

    radarr_history: list[dict] = []
    sonarr_history: list[dict] = []

    results = await asyncio.gather(
        RadarrClient().history(page_size=2000),
        SonarrClient().history(page_size=2000),
        return_exceptions=True,
    )

    if not isinstance(results[0], Exception):
        payload = results[0]
        radarr_history = (
            list(payload.get("records") or [])
            if isinstance(payload, dict)
            else list(payload or [])
        )

    if not isinstance(results[1], Exception):
        payload = results[1]
        sonarr_history = (
            list(payload.get("records") or [])
            if isinstance(payload, dict)
            else list(payload or [])
        )

    zroot = Path(
        setting_get("zurg.root", "/zurg_mnt/zurg")
        or "/zurg_mnt/zurg"
    )

    value = {
        "radarr_history": _history_titles(radarr_history),
        "sonarr_history": _history_titles(sonarr_history),
        "nzb_names": _top_names(zroot / "__nzb__"),
        "rd_names": _top_names(zroot / "__realdebrid__"),
        "download_names": _top_names(zroot / "__downloads__"),
    }

    _PROVENANCE_CACHE["value"] = value
    _PROVENANCE_CACHE["updated"] = time.monotonic()

    return value


def _provenance_for_source(
    source: str,
    media_type: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    norm = _release_norm(source)

    history = (
        context.get("sonarr_history", {})
        if media_type == "tv"
        else context.get("radarr_history", {})
        if media_type == "movie"
        else {}
    )

    hit = history.get(norm)

    if hit:
        return {
            "name": source,
            "primary_source": "arr_managed",
            "source_label": SOURCE_LABELS["arr_managed"],
            "transport": hit.get("transport") or "",
            "evidence": "Matched Arr grab/import history",
        }

    # If Zurg explicitly exposes the item under __nzb__, this is stronger
    # evidence than simply seeing it in the RD namespace.
    if source in context.get("nzb_names", set()):
        return {
            "name": source,
            "primary_source": "nzb",
            "source_label": SOURCE_LABELS["nzb"],
            "transport": "nzb",
            "evidence": "Present in Zurg __nzb__ and no matching Arr acquisition history",
        }

    # Historical DMM and a manually-added RD magnet are not distinguishable
    # after both have landed in Real-Debrid. Do not invent provenance.
    if source in context.get("rd_names", set()):
        return {
            "name": source,
            "primary_source": "dmm_rd",
            "source_label": SOURCE_LABELS["dmm_rd"],
            "transport": "torrent",
            "evidence": "Out-of-band Real-Debrid item; no matching Arr acquisition history",
        }

    if source in context.get("download_names", set()):
        return {
            "name": source,
            "primary_source": "torrent",
            "source_label": SOURCE_LABELS["torrent"],
            "transport": "torrent",
            "evidence": "Out-of-band Zurg download with no Arr acquisition history",
        }

    return {
        "name": source,
        "primary_source": "unknown",
        "source_label": SOURCE_LABELS["unknown"],
        "transport": "",
        "evidence": "No reliable provenance match",
    }


def _primary_source(meta: list[dict[str, Any]]) -> str:
    values = {
        str(row.get("primary_source") or "unknown")
        for row in meta
    }

    if not values:
        return "unknown"

    return min(
        values,
        key=lambda value: SOURCE_PRIORITY.get(value, 99),
    )


def _season_numbers(value: str) -> set[int]:
    seasons: set[int] = set()

    for match in re.finditer(
        r"(?i)(?:^|[ ._\-])S(\d{1,2})(?=$|[ ._\-])",
        str(value or ""),
    ):
        seasons.add(int(match.group(1)))

    for match in re.finditer(
        r"(?i)\bSeason[ ._\-]*(\d{1,2})\b",
        str(value or ""),
    ):
        seasons.add(int(match.group(1)))

    range_match = re.search(
        r"(?i)\bS(\d{1,2})\s*[-_]\s*S?(\d{1,2})\b",
        str(value or ""),
    )

    if range_match:
        start = int(range_match.group(1))
        end = int(range_match.group(2))

        if end >= start and end - start <= 50:
            seasons.update(range(start, end + 1))

    return seasons


async def _episodes_cached(
    client: SonarrClient,
    series_id: int,
) -> list[dict]:
    cached = _EPISODE_CACHE.get(int(series_id))

    if cached and time.monotonic() - cached[0] < 300:
        return cached[1]

    rows = list(await client.episodes(int(series_id)) or [])

    _EPISODE_CACHE[int(series_id)] = (
        time.monotonic(),
        rows,
    )

    return rows


def _actual_existing(
    media_type: str,
    items: list[dict],
    group: dict[str, Any],
) -> dict | None:
    return _existing_by_match(
        media_type,
        items,
        group,
    )


def _source_is_out_of_band(
    source: str,
    group: dict[str, Any],
) -> bool:
    meta = _source_meta_map(group).get(source) or {}

    return str(
        meta.get("primary_source") or ""
    ) in TRUSTED_OUT_OF_BAND


def _sources_are_trusted(
    group: dict[str, Any],
    sources: list[str],
) -> bool:
    if not sources:
        return False

    meta = _source_meta_map(group)

    for source in sources:
        primary = str(
            (meta.get(source) or {}).get("primary_source")
            or "unknown"
        )

        if primary not in TRUSTED_OUT_OF_BAND:
            return False

    return True



def _smart_destination_key(group: dict[str, Any]) -> str:
    """
    Pick the sensible default destination.

    Kids-tagged media:
      movie -> movies/kids
      tv    -> tv/kids

    Normal:
      movie -> movies/main
      tv    -> tv/shows

    Existing platform/special selections are preserved where sensible.
    The actual existing Arr path still wins later during import.
    """
    media_type = str(group.get("media_type") or "")

    themes = {
        str(value).casefold()
        for value in (
            group.get("themes")
            or _themes(group)
            or []
        )
    }

    current = str(
        group.get("destination_key") or ""
    ).strip().lower()

    if media_type == "movie":
        if "kids" in themes:
            return "kids"

        if current in {
            "christmas",
            "halloween",
            "easter",
        }:
            return current

        return "main"

    if media_type == "tv":
        if "kids" in themes:
            return "kids"

        if current in {
            "netflix",
            "disneyplus",
            "amazon",
            "appletv",
            "bbc",
        }:
            return current

        return "shows"

    if media_type == "music":
        return "music"

    return current


def _json_array(value: Any) -> list:
    if isinstance(value, list):
        return value

    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _cleanup_smart_rows() -> None:
    """
    Clean the historic v13.1/v13.2 verification rows.

    - remove rows whose sources have already been resolved
    - merge duplicate visible cards with the same actual Arr ID
    - migrate them to media_type:arr:<id>
    """
    ensure_schema()

    with db() as conn:
        resolved = {
            str(row[0])
            for row in conn.execute(
                "SELECT raw_key FROM magic_intake_resolved_sources"
            ).fetchall()
        }

        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM magic_intake_groups
                WHERE ignored=0
                  AND state NOT IN ('imported','ignored')
                """
            ).fetchall()
        ]

        # --------------------------------------------------------------
        # First remove source names which are already permanently resolved.
        # --------------------------------------------------------------

        for row in rows:
            sources = [
                str(value)
                for value in _json_array(
                    row.get("source_paths_json")
                )
            ]

            remaining = [
                source
                for source in sources
                if source not in resolved
            ]

            if sources and not remaining:
                conn.execute(
                    "DELETE FROM magic_intake_groups WHERE group_key=?",
                    (row["group_key"],),
                )
                continue

            if remaining != sources:
                conn.execute(
                    """
                    UPDATE magic_intake_groups
                    SET source_paths_json=?,
                        release_count=?,
                        updated_at=?
                    WHERE group_key=?
                    """,
                    (
                        json.dumps(remaining),
                        len(remaining),
                        utcnow(),
                        row["group_key"],
                    ),
                )

        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM magic_intake_groups
                WHERE ignored=0
                  AND state NOT IN ('imported','ignored')
                  AND match_id IS NOT NULL
                """
            ).fetchall()
        ]

        grouped: dict[tuple[str, int], list[dict]] = {}

        for row in rows:
            key = (
                str(row.get("media_type") or ""),
                int(row.get("match_id") or 0),
            )

            if not key[0] or not key[1]:
                continue

            grouped.setdefault(key, []).append(row)

        state_rank = {
            "importing": 100,
            "queued": 95,
            "verifying": 90,
            "partially_verified": 85,
            "numbering_mismatch": 84,
            "verification_timeout": 83,
            "awaiting_arr": 82,
            "failed": 75,
            "source_missing": 70,
            "matched": 60,
            "ready": 58,
            "review": 50,
            "unmatched": 40,
            "discovered": 30,
            "partial": 25,
        }

        for (media_type, arr_id), items in grouped.items():
            if len(items) < 2:
                # Even a single old row should receive the new canonical key.
                item = items[0]
                canonical = f"{media_type}:arr:{arr_id}"

                if (
                    item["group_key"] != canonical
                    and not conn.execute(
                        "SELECT 1 FROM magic_intake_groups WHERE group_key=?",
                        (canonical,),
                    ).fetchone()
                ):
                    conn.execute(
                        "UPDATE magic_intake_events SET group_key=? WHERE group_key=?",
                        (canonical, item["group_key"]),
                    )

                    conn.execute(
                        """
                        UPDATE magic_intake_groups
                        SET group_key=?,
                            canonical_key=?,
                            updated_at=?
                        WHERE group_key=?
                        """,
                        (
                            canonical,
                            canonical,
                            utcnow(),
                            item["group_key"],
                        ),
                    )

                continue

            canonical = f"{media_type}:arr:{arr_id}"

            canonical_row = next(
                (
                    row
                    for row in items
                    if row["group_key"] == canonical
                ),
                None,
            )

            keeper = canonical_row or max(
                items,
                key=lambda row: (
                    state_rank.get(
                        str(row.get("state") or ""),
                        0,
                    ),
                    str(row.get("updated_at") or ""),
                ),
            )

            sources: set[str] = set()
            episodes: set[str] = set()
            genres: set[str] = set()
            actionable: set[str] = set()
            missing_items: set[str] = set()
            moved_paths: set[str] = set()
            meta_by_source: dict[str, dict] = {}

            for row in items:
                sources.update(
                    str(value)
                    for value in _json_array(
                        row.get("source_paths_json")
                    )
                )

                episodes.update(
                    str(value)
                    for value in _json_array(
                        row.get("episodes_json")
                    )
                )

                genres.update(
                    str(value)
                    for value in _json_array(
                        row.get("genres_json")
                    )
                )

                actionable.update(
                    str(value)
                    for value in _json_array(
                        row.get("actionable_sources_json")
                    )
                )

                missing_items.update(
                    str(value)
                    for value in _json_array(
                        row.get("missing_items_json")
                    )
                )

                moved_paths.update(
                    str(value)
                    for value in _json_array(
                        row.get("moved_paths_json")
                    )
                )

                for meta in _json_array(
                    row.get("source_meta_json")
                ):
                    if not isinstance(meta, dict):
                        continue

                    name = str(
                        meta.get("name") or ""
                    )

                    if name:
                        meta_by_source[name] = meta

            sources -= resolved

            if not sources:
                for row in items:
                    conn.execute(
                        "DELETE FROM magic_intake_groups WHERE group_key=?",
                        (row["group_key"],),
                    )

                continue

            actionable &= sources

            best = max(
                items,
                key=lambda row: (
                    state_rank.get(
                        str(row.get("state") or ""),
                        0,
                    ),
                    int(row.get("progress") or 0),
                ),
            )

            meta = [
                value
                for key, value in meta_by_source.items()
                if key in sources
            ]

            primary_source = (
                _primary_source(meta)
                if meta
                else str(
                    best.get("primary_source")
                    or "unknown"
                )
            )

            destination = _smart_destination_key(
                {
                    **best,
                    "media_type": media_type,
                    "genres": sorted(genres),
                }
            )

            # Delete duplicates before renaming keeper to canonical.
            for row in items:
                if row["group_key"] == keeper["group_key"]:
                    continue

                conn.execute(
                    "UPDATE magic_intake_events SET group_key=? WHERE group_key=?",
                    (
                        keeper["group_key"],
                        row["group_key"],
                    ),
                )

                conn.execute(
                    "DELETE FROM magic_intake_groups WHERE group_key=?",
                    (row["group_key"],),
                )

            keeper_key = keeper["group_key"]

            if keeper_key != canonical:
                conn.execute(
                    "UPDATE magic_intake_events SET group_key=? WHERE group_key=?",
                    (
                        canonical,
                        keeper_key,
                    ),
                )

                conn.execute(
                    """
                    UPDATE magic_intake_groups
                    SET group_key=?,
                        canonical_key=?
                    WHERE group_key=?
                    """,
                    (
                        canonical,
                        canonical,
                        keeper_key,
                    ),
                )

                keeper_key = canonical

            conn.execute(
                """
                UPDATE magic_intake_groups
                SET canonical_key=?,
                    source_paths_json=?,
                    release_count=?,
                    episodes_json=?,
                    genres_json=?,
                    source_meta_json=?,
                    actionable_sources_json=?,
                    missing_items_json=?,
                    primary_source=?,
                    destination_key=?,
                    state=?,
                    progress=?,
                    progress_detail=?,
                    error=?,
                    missing_count=?,
                    present_count=?,
                    needs_import=?,
                    moved_paths_json=?,
                    updated_at=?
                WHERE group_key=?
                """,
                (
                    canonical,
                    json.dumps(
                        sorted(
                            sources,
                            key=str.casefold,
                        )
                    ),
                    len(sources),
                    json.dumps(sorted(episodes)),
                    json.dumps(
                        sorted(
                            genres,
                            key=str.casefold,
                        )
                    ),
                    json.dumps(meta),
                    json.dumps(
                        sorted(
                            actionable,
                            key=str.casefold,
                        )
                    ),
                    json.dumps(sorted(missing_items)),
                    primary_source,
                    destination,
                    str(best.get("state") or "matched"),
                    int(best.get("progress") or 0),
                    str(best.get("progress_detail") or ""),
                    str(best.get("error") or ""),
                    len(missing_items),
                    int(best.get("present_count") or 0),
                    1 if actionable else 0,
                    json.dumps(sorted(moved_paths)),
                    utcnow(),
                    keeper_key,
                ),
            )



def _persist_group_annotations(
    group: dict[str, Any],
) -> None:
    destination = _smart_destination_key(group)

    group["destination_key"] = destination

    with db() as conn:
        conn.execute(
            """
            UPDATE magic_intake_groups
            SET primary_source=?,
                source_meta_json=?,
                actionable_sources_json=?,
                missing_items_json=?,
                arr_status=?,
                precheck_detail=?,
                missing_count=?,
                present_count=?,
                needs_import=?,
                destination_key=?,
                updated_at=?
            WHERE group_key=?
            """,
            (
                str(
                    group.get("primary_source")
                    or "unknown"
                ),
                json.dumps(
                    group.get("source_meta")
                    or []
                ),
                json.dumps(
                    group.get("actionable_sources")
                    or []
                ),
                json.dumps(
                    group.get("missing_items")
                    or []
                ),
                str(
                    group.get("arr_status")
                    or ""
                ),
                str(
                    group.get("precheck_detail")
                    or ""
                ),
                int(
                    group.get("missing_count")
                    or 0
                ),
                int(
                    group.get("present_count")
                    or 0
                ),
                1 if group.get("needs_import") else 0,
                destination,
                utcnow(),
                str(group["group_key"]),
            ),
        )


async def _arr_first_precheck(
    group: dict[str, Any],
    radarr_movies: list[dict],
    sonarr_series: list[dict],
    source_context: dict[str, Any],
) -> dict[str, Any] | None:
    source_meta = [
        _provenance_for_source(
            source,
            str(group.get("media_type") or ""),
            source_context,
        )
        for source in (group.get("source_paths") or [])
    ]

    group["source_meta"] = source_meta
    group["primary_source"] = _primary_source(source_meta)
    group["source_label"] = SOURCE_LABELS.get(
        group["primary_source"],
        "Unknown",
    )
    group["actionable_sources"] = []
    group["missing_items"] = []
    group["missing_count"] = 0
    group["present_count"] = 0
    group["needs_import"] = False
    group["arr_status"] = ""
    group["precheck_detail"] = ""

    # Do not spend Arr calls deciding the fate of weak matches.
    if (
        int(group.get("confidence") or 0)
        < settings_state()["auto_match_threshold"]
    ):
        group["arr_status"] = "needs_review"
        group["precheck_detail"] = (
            "Identity is not trusted enough for Arr-first cleanup yet"
        )
        return group

    media_type = str(group.get("media_type") or "")

    if media_type == "movie":
        existing = _actual_existing(
            "movie",
            radarr_movies,
            group,
        )

        if existing:
            group["match_id"] = int(existing["id"])
            group["match_title"] = (
                existing.get("title")
                or group.get("match_title")
                or group.get("normalized_title")
            )
            group["match_year"] = (
                existing.get("year")
                or group.get("match_year")
                or group.get("year")
            )
            group["match_external_id"] = str(
                existing.get("tmdbId")
                or existing.get("imdbId")
                or group.get("match_external_id")
                or ""
            )
            group["canonical_key"] = _canonical_key(
                "movie",
                group["match_external_id"],
                int(existing["id"]),
                group["match_title"],
                group.get("match_year"),
            )

        if existing and bool(existing.get("hasFile")):
            group["arr_status"] = "complete"
            group["present_count"] = 1
            group["missing_count"] = 0
            group["precheck_detail"] = (
                "Radarr already has this movie. No import or rescan required."
            )

            _resolve_sources(
                group,
                list(group.get("source_paths") or []),
                "already_present",
                group["precheck_detail"],
            )

            # Do not create an Inbox card.
            return None

        group["arr_status"] = (
            "missing"
            if existing
            else "not_in_arr"
        )

        actionable = [
            source
            for source in (group.get("source_paths") or [])
            if _source_is_out_of_band(source, group)
        ]

        group["actionable_sources"] = actionable
        group["missing_count"] = 1

        if actionable:
            group["needs_import"] = True
            group["precheck_detail"] = (
                "Radarr does not have a registered movie file. "
                f"{len(actionable)} out-of-band source release(s) can satisfy it."
            )
        else:
            group["precheck_detail"] = (
                "Radarr is missing the movie, but no trusted out-of-band "
                "source was identified. Arr-managed media is left alone."
            )

        return group

    if media_type == "tv":
        existing = _actual_existing(
            "tv",
            sonarr_series,
            group,
        )

        if existing:
            group["match_id"] = int(existing["id"])
            group["match_title"] = (
                existing.get("title")
                or group.get("match_title")
                or group.get("normalized_title")
            )
            group["match_year"] = (
                existing.get("year")
                or group.get("match_year")
                or group.get("year")
            )
            group["match_external_id"] = str(
                existing.get("tvdbId")
                or existing.get("tmdbId")
                or group.get("match_external_id")
                or ""
            )
            group["canonical_key"] = _canonical_key(
                "tv",
                group["match_external_id"],
                int(existing["id"]),
                group["match_title"],
                group.get("match_year"),
            )

        # Series not in Sonarr yet. There is nothing to compare, but only
        # out-of-band sources are candidates for Smart Intake.
        if not existing:
            actionable = [
                source
                for source in (group.get("source_paths") or [])
                if _source_is_out_of_band(source, group)
            ]

            group["arr_status"] = "not_in_arr"
            group["actionable_sources"] = actionable
            group["needs_import"] = bool(actionable)

            group["precheck_detail"] = (
                f"Series is not currently in Sonarr. "
                f"{len(actionable)} out-of-band source release(s) are available."
                if actionable
                else
                "Series is not in Sonarr, but no trusted out-of-band source "
                "was identified."
            )

            return group

        client = SonarrClient()

        rows = await _episodes_cached(
            client,
            int(existing["id"]),
        )

        present_pairs = {
            (
                int(row.get("seasonNumber") or 0),
                int(row.get("episodeNumber") or 0),
            )
            for row in rows
            if row.get("hasFile")
        }

        known_pairs = {
            (
                int(row.get("seasonNumber") or 0),
                int(row.get("episodeNumber") or 0),
            )
            for row in rows
            if int(row.get("episodeNumber") or 0) > 0
        }

        remaining_sources: list[str] = []
        actionable_sources: list[str] = []
        missing_union: set[tuple[int, int]] = set()
        expected_union: set[tuple[int, int]] = set()
        resolved_now: list[str] = []
        unknown_mapping = 0

        for source in list(group.get("source_paths") or []):
            explicit = _episode_pairs(
                _episode_markers(source)
            )

            seasons = _season_numbers(source)

            if explicit:
                expected = explicit & known_pairs

            elif seasons:
                expected = {
                    pair
                    for pair in known_pairs
                    if pair[0] in seasons
                }

            else:
                expected = set()

            # If the release can be mapped and Sonarr already has every
            # contained episode, the source is finished immediately.
            if expected:
                missing = expected - present_pairs

                expected_union.update(expected)

                if not missing:
                    resolved_now.append(source)
                    continue

                missing_union.update(missing)
                remaining_sources.append(source)

                if _source_is_out_of_band(source, group):
                    actionable_sources.append(source)

                continue

            # We cannot prove what episodes this release contains cheaply.
            # Keep it visible, but do not hammer the filesystem or force it.
            remaining_sources.append(source)
            unknown_mapping += 1

        if resolved_now:
            _resolve_sources(
                group,
                resolved_now,
                "already_present",
                "Every identifiable episode in this release is already "
                "registered in Sonarr",
            )

        if not remaining_sources:
            # Every source in this canonical series was already satisfied.
            return None

        group["source_paths"] = remaining_sources
        group["release_count"] = len(remaining_sources)

        group["source_meta"] = [
            row
            for row in source_meta
            if row.get("name") in set(remaining_sources)
        ]

        group["primary_source"] = _primary_source(
            group["source_meta"]
        )

        group["source_label"] = SOURCE_LABELS.get(
            group["primary_source"],
            "Unknown",
        )

        group["actionable_sources"] = actionable_sources

        group["missing_items"] = [
            f"S{season:02d}E{episode:02d}"
            for season, episode in sorted(missing_union)
        ]

        group["missing_count"] = len(missing_union)

        if expected_union:
            group["present_count"] = len(
                expected_union & present_pairs
            )

        group["needs_import"] = bool(actionable_sources)

        if missing_union:
            group["arr_status"] = "partial"

            group["precheck_detail"] = (
                f"Sonarr already has {group['present_count']}/"
                f"{len(expected_union)} identifiable episode(s); "
                f"{len(missing_union)} are missing. "
                f"Only missing out-of-band sources will be imported."
            )

        elif unknown_mapping:
            group["arr_status"] = "mapping_unknown"

            group["precheck_detail"] = (
                f"{unknown_mapping} release(s) cannot be mapped to exact "
                "episodes from their names without deeper scanning. "
                "No automatic import will be forced."
            )

        else:
            group["arr_status"] = "complete"
            group["precheck_detail"] = (
                "Sonarr already contains all identifiable episodes."
            )

        return group

    # Music remains on the existing path for now.
    group["arr_status"] = "not_checked"
    group["precheck_detail"] = (
        "Music Smart Intake pre-check is not enabled in v13.3.0"
    )

    return group


def _discover_provisional(
    base: Path,
    type_overrides: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    resolved = _resolved_source_names()

    entries = [
        p
        for p in base.iterdir()
        if not p.name.startswith(".")
        and p.name.lower() not in ORGANIZED
        and p.name not in resolved
    ]

    type_overrides = type_overrides or {}
    views = _zurg_classification_sets()

    provisional: dict[str, dict[str, Any]] = {}

    for path in entries:
        title, year, episodes = _clean_release_name(path.name)

        if path.name in type_overrides:
            media_type = type_overrides[path.name]
            type_confident = True
            type_source = "manual-override"

        else:
            memberships = [
                media_type
                for media_type in ("tv", "movie", "music")
                if path.name in views.get(media_type, set())
            ]

            if len(memberships) == 1:
                media_type = memberships[0]
                type_confident = True
                type_source = f"zurg:{media_type}"

            elif episodes or re.search(
                r"(?i)\b(?:Season[ ._-]*\d+|complete.*series|episodes?)\b",
                path.name,
            ):
                media_type = "tv"
                type_confident = True
                type_source = "release:episode-or-season-marker"

            else:
                # Do NOT walk the source directory here.
                # Display-only fallback. Force Match is required before
                # Smart Intake can move a weakly-classified item.
                suffix = path.suffix.lower()

                if suffix in AUDIO_EXTS:
                    media_type = "music"
                else:
                    media_type = "movie"

                type_confident = False
                type_source = "name-only-heuristic"

        key = _raw_group_key(
            media_type,
            title,
            year,
        )

        group = provisional.setdefault(
            key,
            {
                "raw_group_key": key,
                "media_type": media_type,
                "normalized_title": title,
                "year": year,
                "source_paths": [],
                "episodes": [],
                "genres": [],
                "state": "discovered",
                "error": "",
                "type_confident": True,
                "type_sources": [],
            },
        )

        group["source_paths"].append(path.name)
        group["episodes"].extend(episodes)

        group["type_confident"] = bool(
            group.get("type_confident", True)
            and type_confident
        )

        group["type_sources"].append(type_source)

    return provisional


async def scan() -> dict[str, Any]:
    ensure_schema()

    if _CACHE.get("running"):
        return cached_state()

    # Do not rebuild groups while an import is physically moving a Magic entry.
    if any(
        not task.done()
        for task in _IMPORT_TASKS.values()
    ):
        _CACHE["last_error"] = (
            "Scan deferred while Magic Intake imports are active"
        )
        return cached_state()

    _CACHE["running"] = True

    try:
        base = root()

        if not base.exists():
            raise RuntimeError(
                f"Magic Intake path does not exist: {base}"
            )

        type_overrides = _type_overrides()

        # These all happen in parallel. Source discovery only reads top-level
        # directory names. No ffprobe and no recursive movie crawling.
        provisional_result, source_context, radarr_result, sonarr_result = (
            await asyncio.gather(
                asyncio.to_thread(
                    _discover_provisional,
                    base,
                    type_overrides,
                ),
                _source_context(),
                RadarrClient().movies(),
                SonarrClient().series(),
                return_exceptions=True,
            )
        )

        if isinstance(provisional_result, Exception):
            raise provisional_result

        provisional = provisional_result

        if isinstance(source_context, Exception):
            source_context = {
                "radarr_history": {},
                "sonarr_history": {},
                "nzb_names": set(),
                "rd_names": set(),
                "download_names": set(),
            }

        radarr_movies = (
            []
            if isinstance(radarr_result, Exception)
            else list(radarr_result or [])
        )

        sonarr_series = (
            []
            if isinstance(sonarr_result, Exception)
            else list(sonarr_result or [])
        )

        known = _known_by_source()

        sem = asyncio.Semaphore(4)

        async def enrich(
            group: dict[str, Any],
        ) -> dict[str, Any]:
            match = _override_for_sources(
                group["source_paths"]
            )

            if not match:
                match = _cached_match_for_sources(
                    group["source_paths"],
                    known,
                )

                if (
                    match
                    and match.get("media_type")
                    and match.get("media_type")
                    != group["media_type"]
                ):
                    match = {}

            if not match:
                async with sem:
                    match = await _best_match(
                        group["media_type"],
                        group["normalized_title"],
                        group.get("year"),
                    )

            if not match:
                match = _matching_known_source(
                    group["source_paths"],
                    known,
                )

                if (
                    match
                    and match.get("media_type")
                    and match.get("media_type")
                    != group["media_type"]
                ):
                    match = {}

            if match:
                confidence = int(
                    match.get("confidence") or 0
                )

                # Strong title metadata is not enough when Movie/TV type itself
                # came only from a fallback guess.
                if (
                    not group.get("type_confident", False)
                    and not match.get("forced")
                ):
                    confidence = min(
                        confidence,
                        89,
                    )

                group.update(
                    {
                        "match_service": {
                            "movie": "radarr",
                            "tv": "sonarr",
                            "music": "lidarr",
                        }[group["media_type"]],
                        "match_id": match.get("id"),
                        "match_title": (
                            match.get("title") or ""
                        ),
                        "match_year": (
                            match.get("year") or None
                        ),
                        "match_external_id": (
                            match.get("external_id") or ""
                        ),
                        "poster_url": (
                            match.get("poster_url") or ""
                        ),
                        "genres": (
                            match.get("genres") or []
                        ),
                        "confidence": confidence,
                        "state": _state_for_confidence(
                            confidence
                        ),
                        "forced": bool(
                            match.get("forced")
                        ),
                    }
                )

                # Before canonical grouping, promote lookup identities to the
                # ACTUAL Arr library identity when one already exists.
                inventory = (
                    radarr_movies
                    if group["media_type"] == "movie"
                    else sonarr_series
                    if group["media_type"] == "tv"
                    else []
                )

                existing = (
                    _actual_existing(
                        group["media_type"],
                        inventory,
                        group,
                    )
                    if inventory
                    else None
                )

                if existing:
                    group["match_id"] = int(
                        existing["id"]
                    )

                    group["match_title"] = (
                        existing.get("title")
                        or group["match_title"]
                    )

                    group["match_year"] = (
                        existing.get("year")
                        or group.get("match_year")
                    )

                    if group["media_type"] == "movie":
                        group["match_external_id"] = str(
                            existing.get("tmdbId")
                            or existing.get("imdbId")
                            or group.get("match_external_id")
                            or ""
                        )

                    elif group["media_type"] == "tv":
                        group["match_external_id"] = str(
                            existing.get("tvdbId")
                            or existing.get("tmdbId")
                            or group.get("match_external_id")
                            or ""
                        )

                group["canonical_key"] = _canonical_key(
                    group["media_type"],
                    group.get("match_external_id")
                    or "",
                    group.get("match_id"),
                    group.get("match_title")
                    or group["normalized_title"],
                    group.get("match_year")
                    or group.get("year"),
                )

            else:
                group.update(
                    {
                        "confidence": 0,
                        "state": "unmatched",
                        "canonical_key": (
                            group["raw_group_key"]
                        ),
                    }
                )

            return group

        enriched = await asyncio.gather(
            *(
                enrich(group)
                for group in provisional.values()
            )
        )

        # Canonical grouping occurs AFTER promotion to the actual Arr ID.
        # This fixes cases where the same Sonarr series appeared as two cards
        # because separate raw releases had slightly different lookup identity.
        canonical: dict[str, dict[str, Any]] = {}

        for group in enriched:
            key = (
                group.get("canonical_key")
                or group["raw_group_key"]
            )

            if key not in canonical:
                canonical[key] = dict(group)
                canonical[key]["group_key"] = key

                if canonical[key].get("match_title"):
                    canonical[key]["normalized_title"] = (
                        canonical[key]["match_title"]
                    )

            else:
                _merge_group(
                    canonical[key],
                    group,
                )

                canonical[key]["state"] = (
                    _state_for_confidence(
                        int(
                            canonical[key].get(
                                "confidence"
                            )
                            or 0
                        )
                    )
                )

        # Arr-first precheck.
        #
        # Movie already hasFile       -> resolve + hide
        # TV release fully in Sonarr  -> resolve + hide
        # Partially missing TV        -> keep only missing/out-of-band work
        # Nothing requires a file probe.
        check_sem = asyncio.Semaphore(3)

        async def precheck(
            group: dict[str, Any],
        ) -> dict[str, Any] | None:
            async with check_sem:
                try:
                    return await _arr_first_precheck(
                        group,
                        radarr_movies,
                        sonarr_series,
                        source_context,
                    )

                except Exception as exc:
                    group["arr_status"] = (
                        "precheck_error"
                    )

                    group["precheck_detail"] = (
                        f"Arr-first check deferred: "
                        f"{type(exc).__name__}: {exc}"
                    )

                    group.setdefault(
                        "primary_source",
                        "unknown",
                    )

                    group.setdefault(
                        "source_meta",
                        [],
                    )

                    group.setdefault(
                        "actionable_sources",
                        [],
                    )

                    group["needs_import"] = False

                    return group

        checked = await asyncio.gather(
            *(
                precheck(group)
                for group in canonical.values()
            )
        )

        active_groups = [
            group
            for group in checked
            if group is not None
        ]

        # Rebuild transient Inbox rows from current top-level state.
        #
        # Post-move verification rows are retained because their source may
        # already have moved out of top-level Magic.
        with db() as conn:
            conn.execute(
                """
                DELETE FROM magic_intake_groups
                WHERE ignored=0
                  AND state IN (
                    'discovered',
                    'matched',
                    'review',
                    'unmatched',
                    'ready',
                    'partial',
                    'queued',
                    'importing',
                    'verifying',
                    'already_present'
                  )
                """
            )

        for group in active_groups:
            _upsert_group(
                group,
                schema_ready=True,
            )

            _persist_group_annotations(
                group
            )

        _cleanup_smart_rows()
        groups = list_groups(True)

        _CACHE.update(
            {
                "last_scan_at": utcnow(),
                "last_error": "",
                "groups": groups,
                "summary": _summary(groups),
            }
        )

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
    rows: list[dict[str, Any]],
    *,
    media_type: str = "all",
    genre: str = "all",
    theme: str = "all",
    source: str = "all",
    state: str = "all",
    q: str = "",
    include_imported: bool = False,
) -> list[dict[str, Any]]:
    media_type = (
        media_type or "all"
    ).casefold()

    genre = (
        genre or "all"
    ).casefold()

    theme = (
        theme or "all"
    ).casefold()

    source = (
        source or "all"
    ).casefold()

    state = (
        state or "all"
    ).casefold()

    q = (
        q or ""
    ).strip().casefold()

    out: list[dict[str, Any]] = []

    for row in rows:
        row_state = str(
            row.get("state") or ""
        ).casefold()

        if (
            not include_imported
            and row_state == "imported"
        ):
            continue

        state_bucket = (
            "importing"
            if row_state
            in {
                "queued",
                "importing",
                "verifying",
            }
            else row_state
        )

        if (
            state == "attention"
            and row_state
            in {
                "partially_verified",
                "numbering_mismatch",
                "verification_timeout",
                "source_missing",
                "failed",
                "partial",
            }
        ):
            state_bucket = "attention"

        if (
            media_type != "all"
            and str(
                row.get("media_type") or ""
            ).casefold()
            != media_type
        ):
            continue

        if (
            source != "all"
            and str(
                row.get("primary_source")
                or "unknown"
            ).casefold()
            != source
        ):
            continue

        if (
            genre != "all"
            and genre
            not in {
                str(value).casefold()
                for value in (
                    row.get("genres") or []
                )
            }
        ):
            continue

        if (
            theme != "all"
            and theme
            not in {
                str(value).casefold()
                for value in (
                    row.get("themes") or []
                )
            }
        ):
            continue

        if (
            state != "all"
            and state_bucket != state
        ):
            continue

        if q:
            haystack = " ".join(
                [
                    str(
                        row.get("display_title")
                        or ""
                    ),
                    *(
                        str(value)
                        for value in (
                            row.get("source_paths")
                            or []
                        )
                    ),
                ]
            ).casefold()

            if q not in haystack:
                continue

        out.append(row)

    return out


def query_groups(
    *,
    media_type: str = "all",
    genre: str = "all",
    theme: str = "all",
    source: str = "all",
    state: str = "all",
    q: str = "",
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
    include_imported: bool = False,
) -> dict[str, Any]:
    snapshot = cached_state()

    filtered = _filter_rows(
        list(snapshot.get("groups") or []),
        media_type=media_type,
        genre=genre,
        theme=theme,
        source=source,
        state=state,
        q=q,
        include_imported=include_imported,
    )

    offset = max(
        0,
        int(offset or 0),
    )

    limit = max(
        12,
        min(
            200,
            int(
                limit
                or DEFAULT_PAGE_SIZE
            ),
        ),
    )

    page = filtered[
        offset : offset + limit
    ]

    return {
        "rows": page,
        "total": len(filtered),
        "offset": offset,
        "limit": limit,
        "has_more": (
            offset + len(page)
            < len(filtered)
        ),
        "summary": (
            snapshot.get("summary")
            or {}
        ),
        "filters": (
            snapshot.get("filters")
            or {}
        ),
        "settings": (
            snapshot.get("settings")
            or {}
        ),
        "revision": (
            snapshot.get("last_scan_at")
            or ""
        ),
        "running": bool(
            snapshot.get("running")
        ),
        "last_error": (
            snapshot.get("last_error")
            or ""
        ),
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

    group = get_group(group_key)

    if not group:
        return

    sources = list(
        group.get("source_paths")
        or []
    )

    if sources:
        _resolve_sources(
            group,
            sources,
            "ignored",
            "Ignored permanently in Magic Intake",
        )

    with db() as conn:
        conn.execute(
            "UPDATE magic_intake_groups "
            "SET ignored=1,state='ignored',updated_at=? "
            "WHERE group_key=?",
            (
                utcnow(),
                group_key,
            ),
        )

        conn.execute(
            """
            INSERT INTO magic_intake_events(
                group_key,
                state,
                detail
            )
            VALUES(?,?,?)
            """,
            (
                group_key,
                "ignored",
                "Ignored permanently by user",
            ),
        )

    _cleanup_smart_rows()
    _CACHE["groups"] = []


def _safe_source(name: str) -> Path:
    base = root().resolve()
    path = (base / name).resolve()
    if path.parent != base:
        raise ValueError("Magic Intake only accepts top-level __magic__ entries")
    return path


def _safe_destination(
    media_type: str,
    destination_key: str,
    title: str,
    year: int | None,
) -> tuple[Path, Path]:
    rel = DESTINATIONS.get(media_type, {}).get(destination_key)
    if not rel:
        raise ValueError("Unknown Magic Intake destination")

    safe_title = re.sub(r'[\\/:*?"<>|]+', " ", title).strip()
    folder_name = (
        f"{safe_title} ({year})"
        if media_type == "movie" and year
        else safe_title
    )

    write_root = root().resolve()
    target_parent = (write_root / rel).resolve()

    if write_root not in target_parent.parents and target_parent != write_root:
        raise ValueError("Destination escaped Magic Intake root")

    arr_parent = arr_prefix() / rel
    return target_parent / folder_name, arr_parent / folder_name


def _actual_arr_destination(
    media_type: str,
    arr_item: dict[str, Any],
    fallback_write: Path,
    fallback_arr: Path,
) -> tuple[Path, Path]:
    """
    Prefer the path that Radarr/Sonarr/Lidarr actually owns.

    This means an existing series keeps its real Sonarr root/category and a newly
    added title uses the path generated by the Arr itself.
    """
    raw_path = str(arr_item.get("path") or "").strip()

    if not raw_path:
        return fallback_write, fallback_arr

    arr_base = arr_prefix()
    arr_path = Path(raw_path)

    try:
        relative = arr_path.relative_to(arr_base)
    except ValueError:
        raise ArrError(
            f"Existing Arr path '{arr_path}' is outside the Magic tree "
            f"'{arr_base}'. Trusted DMM Intake will not move it automatically."
        )

    if (
        not relative.parts
        or any(part in {".", ".."} for part in relative.parts)
    ):
        raise ArrError("Arr returned an unsafe Magic destination path")

    expected_top = {
        "movie": "movies",
        "tv": "tv",
        "music": "music",
    }[media_type]

    if relative.parts[0].lower() != expected_top:
        raise ArrError(
            f"Arr path '{arr_path}' does not agree with confirmed media type "
            f"'{media_type}'. Import stopped for review."
        )

    write_path = root().joinpath(*relative.parts)
    return write_path, arr_path


async def _select_orphan_sources(
    client: Any,
    media_type: str,
    arr_id: int,
    group: dict[str, Any],
    sources: list[str],
) -> tuple[list[str], list[str], str]:
    if media_type == "movie":
        current = await client.movie(arr_id)

        if bool(current.get("hasFile")):
            return (
                [],
                list(sources),
                "Radarr already has a registered movie file",
            )

        return (
            list(sources),
            [],
            "Radarr movie is genuinely missing",
        )

    if media_type == "tv":
        rows = await client.episodes(arr_id)

        present = {
            (
                int(row.get("seasonNumber") or 0),
                int(row.get("episodeNumber") or 0),
            )
            for row in rows
            if row.get("hasFile")
        }

        known = {
            (
                int(row.get("seasonNumber") or 0),
                int(row.get("episodeNumber") or 0),
            )
            for row in rows
            if int(row.get("episodeNumber") or 0) > 0
        }

        needed: list[str] = []
        represented: list[str] = []

        for source in sources:
            pairs = _episode_pairs(
                _episode_markers(source)
            )

            seasons = _season_numbers(
                source
            )

            if pairs:
                expected = pairs & known

            elif seasons:
                expected = {
                    pair
                    for pair in known
                    if pair[0] in seasons
                }

            else:
                expected = set()

            if expected and expected.issubset(
                present
            ):
                represented.append(source)
                continue

            needed.append(source)

        if not needed:
            return (
                [],
                represented,
                "All identifiable episodes from these source releases "
                "are already registered in Sonarr",
            )

        return (
            needed,
            represented,
            f"{len(needed)} release(s) still contain missing or "
            "unregistered TV media",
        )

    return (
        list(sources),
        [],
        "Music uses normal Arr verification",
    )


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


async def _try_manual_import(
    client: Any,
    media_type: str,
    folder: str,
    arr_id: int,
    *,
    trusted: bool = False,
) -> int:
    try:
        if media_type == "movie":
            candidates = await client.manual_import_candidates(
                folder=folder,
                movie_id=arr_id,
            )

            prepared: list[tuple[int, dict, int]] = []

            for candidate in candidates:
                rejections = list(candidate.get("rejections") or [])

                if rejections and not trusted:
                    continue

                item = client.manual_file(candidate)
                path = str(item.get("path") or "")

                if not path:
                    continue

                suffix = Path(path).suffix.lower()
                if suffix and suffix not in VIDEO_EXTS:
                    continue

                # Never trust a parser's movie association when Trusted DMM Intake
                # already has an explicit canonical Radarr identity.
                item["movieId"] = int(arr_id)

                prepared.append(
                    (
                        int(candidate.get("size") or 0),
                        item,
                        len(rejections),
                    )
                )

            if trusted and len(prepared) > 1:
                # Do not force trailers/samples/extras as separate Radarr movie files.
                # DMM/Zurg's main playable video should normally be the largest.
                prepared.sort(key=lambda row: row[0], reverse=True)
                prepared = prepared[:1]

            files = [row[1] for row in prepared]
            bypassed = sum(row[2] for row in prepared)

        elif media_type == "tv":
            candidates = await client.manual_import_candidates(
                folder=folder,
                series_id=arr_id,
            )

            episode_rows = await client.episodes(arr_id)

            pair_to_id: dict[tuple[int, int], int] = {}
            valid_ids: set[int] = set()
            present_ids: set[int] = set()

            for episode in episode_rows:
                eid = int(episode.get("id") or 0)
                if not eid:
                    continue

                pair = (
                    int(episode.get("seasonNumber") or 0),
                    int(episode.get("episodeNumber") or 0),
                )

                valid_ids.add(eid)
                pair_to_id[pair] = eid

                if episode.get("hasFile"):
                    present_ids.add(eid)

            files: list[dict] = []
            bypassed = 0

            for candidate in candidates:
                rejections = list(candidate.get("rejections") or [])

                if rejections and not trusted:
                    continue

                item = client.manual_file(candidate)
                path = str(item.get("path") or "")

                if not path:
                    continue

                suffix = Path(path).suffix.lower()
                if suffix and suffix not in VIDEO_EXTS:
                    continue

                item["seriesId"] = int(arr_id)

                # First choice: explicit episode markers in the actual file name.
                marker_pairs = _episode_pairs(
                    _episode_markers(Path(path).name)
                )

                marker_ids = [
                    pair_to_id[pair]
                    for pair in sorted(marker_pairs)
                    if pair in pair_to_id
                ]

                if marker_ids:
                    episode_ids = marker_ids
                else:
                    # Fallback to Sonarr's mapping, but only IDs proven to belong
                    # to this exact confirmed series.
                    episode_ids = [
                        int(eid)
                        for eid in (item.get("episodeIds") or [])
                        if int(eid) in valid_ids
                    ]

                if not episode_ids:
                    # Trusted means bypass parser rejection labels; it does NOT mean
                    # inventing an episode identity.
                    continue

                existing = [eid for eid in episode_ids if eid in present_ids]
                missing = [eid for eid in episode_ids if eid not in present_ids]

                if not missing:
                    continue

                # A file that maps simultaneously over already-present and missing
                # episodes is left for manual review rather than replacing media.
                if existing and missing:
                    log_event(
                        "warning",
                        "magic_intake",
                        "trusted_overlap_skipped",
                        "Trusted DMM candidate overlaps existing and missing "
                        "Sonarr episodes; left for review",
                        {
                            "series_id": arr_id,
                            "path": path,
                            "existing_episode_ids": existing,
                            "missing_episode_ids": missing,
                        },
                    )
                    continue

                item["episodeIds"] = episode_ids
                files.append(item)

                if rejections:
                    bypassed += len(rejections)

        else:
            candidates = await client.manual_import_candidates(
                folder=folder,
                artist_id=arr_id,
            )

            files = [
                client.manual_file(candidate)
                for candidate in candidates
                if not (candidate.get("rejections") or [])
            ]

            files = [item for item in files if item.get("path")]
            bypassed = 0

        if files:
            await client.manual_import(
                files,
                import_mode="move" if trusted else "auto",
            )

        if trusted:
            log_event(
                "info",
                "magic_intake",
                "trusted_manual_import",
                f"Trusted DMM ManualImport submitted {len(files)} file(s); "
                f"bypassed {bypassed} Arr rejection label(s)",
                {
                    "service": getattr(client, "name", ""),
                    "media_type": media_type,
                    "arr_id": arr_id,
                    "folder": folder,
                    "files": len(files),
                    "rejections_bypassed": bypassed,
                },
            )

        return len(files)

    except Exception as exc:
        log_event(
            "warning",
            "magic_intake",
            "manual_import_submit_failed",
            f"ManualImport submission failed; targeted Arr rescan will still "
            f"run: {type(exc).__name__}: {exc}",
            {
                "service": getattr(client, "name", ""),
                "media_type": media_type,
                "arr_id": arr_id,
                "folder": folder,
                "trusted": trusted,
            },
        )
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


async def import_group(
    group_key: str,
    destination_key: str,
    selected_source: str = "",
) -> dict[str, Any]:
    ensure_schema()

    group = get_group(group_key)
    if not group:
        raise ValueError("Magic Intake group not found")

    if not group.get("match_title"):
        raise ValueError("Match or Force Match this intake group before import")

    threshold = settings_state()["auto_match_threshold"]
    confidence = int(group.get("confidence") or 0)

    if confidence < threshold:
        raise ValueError(
            f"This item is only {confidence}% confident. Force Match the correct "
            f"{'series' if group['media_type'] == 'tv' else 'movie'} before "
            f"Trusted DMM Intake can move it."
        )

    all_sources = list(group.get("source_paths") or [])
    smart_sources = list(group.get("actionable_sources") or [])

    if smart_sources:
        sources = smart_sources
    else:
        sources = all_sources

    # When Arr-first precheck has run and found no actionable out-of-band
    # source, Smart Intake must not start an expensive import anyway.
    if (
        str(group.get("arr_status") or "")
        in {"complete", "mapping_unknown"}
        and not smart_sources
    ):
        raise ValueError(
            group.get("precheck_detail")
            or "No Smart Intake import is required"
        )

    if group["media_type"] == "movie" and len(sources) > 1:
        if not selected_source or selected_source not in sources:
            raise ValueError("Select which out-of-band movie release to import")
        sources = [selected_source]

    elif (
        group["media_type"] == "movie"
        and len(sources) == 1
    ):
        selected_source = sources[0]

    fallback_write, fallback_arr = _safe_destination(
        group["media_type"],
        destination_key,
        group["match_title"],
        group.get("match_year") or group.get("year"),
    )

    dest_parent_arr = fallback_arr.parent
    moved: list[str] = []
    imported_candidates = 0
    skipped_existing: list[str] = []

    trusted = bool(
        settings_state().get("trusted_dmm_bypass", True)
        and group["media_type"] in {"movie", "tv"}
        and _sources_are_trusted(group, sources)
    )

    stage = "Resolving target in Arr"

    try:
        _set_progress(group_key, "importing", 8, stage)

        client, arr_id, arr_item = await _ensure_arr_item(
            group,
            dest_parent_arr,
        )

        # Existing Arr path (or the actual path generated when adding a new title)
        # is authoritative.
        dest_write, dest_arr = _actual_arr_destination(
            group["media_type"],
            arr_item,
            fallback_write,
            fallback_arr,
        )

        stage = "Checking whether DMM media is genuinely orphaned"
        _set_progress(
            group_key,
            "importing",
            14,
            stage,
            arr_id=arr_id,
        )

        sources, skipped_existing, orphan_detail = await _select_orphan_sources(
            client,
            group["media_type"],
            arr_id,
            group,
            sources,
        )

        if not sources:
            detail = orphan_detail

            _set_progress(
                group_key,
                "already_present",
                100,
                detail,
                error="",
                arr_id=arr_id,
            )

            log_event(
                "info",
                "magic_intake",
                "already_present",
                detail,
                {
                    "group": group_key,
                    "arr_id": arr_id,
                    "left_at_top_level": skipped_existing,
                },
            )

            return {
                "ok": True,
                "state": "already_present",
                "detail": detail,
                "moved": [],
                "manual_candidates": 0,
                "arr_id": arr_id,
            }

        stage = "Taking Arr verification baseline"
        before = _verification_baseline(group)

        if not before:
            before = await _verification_snapshot(
                client,
                group["media_type"],
                arr_id,
            )

        _save_verification_context(
            group_key,
            before,
            str(dest_arr),
            moved,
        )

        stage = "Checking top-level __magic__ sources"

        missing = [
            source
            for source in sources
            if not _safe_source(source).exists()
        ]

        if missing:
            if len(missing) == len(sources) and dest_write.exists():
                detail = (
                    "Source was already moved into the confirmed Arr destination; "
                    "continuing trusted reconciliation without moving it again"
                )

                _set_progress(
                    group_key,
                    "awaiting_arr",
                    70,
                    detail,
                    arr_id=arr_id,
                )

                imported_candidates = await _try_manual_import(
                    client,
                    group["media_type"],
                    str(dest_arr),
                    arr_id,
                    trusted=trusted,
                )

                await _rescan(
                    client,
                    group["media_type"],
                    arr_id,
                )

                state, verify_detail = await _verify_with_state(
                    client,
                    group["media_type"],
                    arr_id,
                    group,
                    before,
                    polls=3,
                )

                progress = (
                    100
                    if state == "imported"
                    else 92
                    if state in {"partially_verified", "numbering_mismatch"}
                    else 82
                )

                _set_progress(
                    group_key,
                    state,
                    progress,
                    verify_detail,
                    error="",
                    arr_id=arr_id,
                )

                log_event(
                    "info" if state == "imported" else "warning",
                    "magic_intake",
                    state,
                    verify_detail,
                    {
                        "group": group_key,
                        "paths": [str(dest_arr)],
                        "manual_candidates": imported_candidates,
                        "trusted": trusted,
                        "reconciled": True,
                    },
                )

                return {
                    "ok": state == "imported",
                    "state": state,
                    "detail": verify_detail,
                    "moved": [],
                    "manual_candidates": imported_candidates,
                    "arr_id": arr_id,
                }

            detail = (
                "Source release is missing from top-level __magic__ and the "
                "expected confirmed destination could not be safely reconciled: "
                f"{missing[0]}"
            )

            _set_progress(
                group_key,
                "source_missing",
                100,
                detail,
                error=detail,
                arr_id=arr_id,
            )

            log_event(
                "error",
                "magic_intake",
                "source_missing",
                detail,
                {
                    "group": group_key,
                    "missing": missing,
                },
            )

            return {
                "ok": False,
                "state": "source_missing",
                "detail": detail,
                "moved": [],
                "manual_candidates": 0,
                "arr_id": arr_id,
            }

        # This is the important step:
        #
        # top-level __magic__/Ugly.Release.Name
        #
        # is renamed/moved under the exact canonical Arr-owned movie/series path.
        stage = "Moving trusted DMM release out of top-level __magic__"

        _set_progress(
            group_key,
            "importing",
            28,
            f"Moving {len(sources)} genuine orphan release "
            f"entr{'y' if len(sources) == 1 else 'ies'} into "
            f"{dest_arr}",
            arr_id=arr_id,
        )

        dest_write.parent.mkdir(parents=True, exist_ok=True)

        if (
            len(sources) == 1
            and _safe_source(sources[0]).is_dir()
            and not dest_write.exists()
        ):
            src = _safe_source(sources[0])
            src.rename(dest_write)
            moved.append(str(dest_arr))

        else:
            dest_write.mkdir(parents=True, exist_ok=True)

            for source in sources:
                src = _safe_source(source)
                target = dest_write / src.name

                if target.exists():
                    raise FileExistsError(
                        f"Destination already exists: {target}"
                    )

                src.rename(target)
                moved.append(str(dest_arr / src.name))

        _update_moved_paths(group_key, moved)

        stage = "Submitting trusted Manual Import"

        bypass_text = (
            "Trusted DMM mode: Arr rejection labels may be bypassed after "
            "confirmed identity"
            if trusted
            else "Normal Arr Manual Import rules"
        )

        _set_progress(
            group_key,
            "importing",
            48,
            bypass_text,
            arr_id=arr_id,
        )

        imported_candidates = await _try_manual_import(
            client,
            group["media_type"],
            str(dest_arr),
            arr_id,
            trusted=trusted,
        )

        stage = "Requesting targeted Arr rescan"

        _set_progress(
            group_key,
            "importing",
            66,
            "Targeted Arr rescan requested; no full-library scan",
            arr_id=arr_id,
        )

        await _rescan(
            client,
            group["media_type"],
            arr_id,
        )

        _set_progress(
            group_key,
            "verifying",
            76,
            "DMM source has left top-level __magic__; waiting for Arr "
            "registration confirmation",
            arr_id=arr_id,
        )

        stage = "Verifying imported media in Arr"

        state, detail = await _verify_with_state(
            client,
            group["media_type"],
            arr_id,
            group,
            before,
            polls=6,
        )

        if skipped_existing:
            detail += (
                f" · Left {len(skipped_existing)} already-present DMM "
                f"release(s) at top-level for later duplicate handling"
            )

        if state == "imported":
            progress = 100
        elif state in {"partially_verified", "numbering_mismatch"}:
            progress = 92
        else:
            progress = 82

        _set_progress(
            group_key,
            state,
            progress,
            detail,
            error="",
            arr_id=arr_id,
        )

        if state == "imported":
            _resolve_sources(
                group,
                sources,
                "imported",
                detail,
            )

        log_event(
            "info" if state == "imported" else "warning",
            "magic_intake",
            state,
            detail,
            {
                "group": group_key,
                "paths": moved,
                "manual_candidates": imported_candidates,
                "trusted": trusted,
                "skipped_existing": skipped_existing,
            },
        )

        return {
            "ok": state == "imported",
            "state": state,
            "detail": detail,
            "moved": moved,
            "manual_candidates": imported_candidates,
            "arr_id": arr_id,
            "trusted": trusted,
        }

    except Exception as exc:
        detail = _exception_detail(exc, stage)

        _set_progress(
            group_key,
            "failed",
            100,
            detail,
            error=detail,
        )

        log_event(
            "error",
            "magic_intake",
            "failed",
            detail,
            {
                "group": group_key,
                "paths": moved,
                "exception": type(exc).__name__,
                "trusted": trusted,
            },
        )

        return {
            "ok": False,
            "state": "failed",
            "detail": detail,
            "moved": moved,
            "manual_candidates": imported_candidates,
        }



async def _recheck_group(
    group_key: str,
    *,
    manual: bool = False,
) -> dict[str, Any]:
    ensure_schema()

    group = get_group(group_key)

    if not group:
        return {
            "ok": False,
            "detail": "Magic Intake group no longer exists",
        }

    arr_id = int(
        group.get("match_id")
        or 0
    )

    if not arr_id:
        return {
            "ok": False,
            "detail": "No Arr item ID is available for verification",
        }

    media_type = str(
        group.get("media_type")
        or ""
    )

    source_paths = list(
        group.get("source_paths")
        or []
    )

    # Refresh provenance for old v13.1/v13.2 rows which still show Unknown.
    try:
        context = await _source_context()

        source_meta = [
            _provenance_for_source(
                source,
                media_type,
                context,
            )
            for source in source_paths
        ]

    except Exception:
        source_meta = list(
            group.get("source_meta")
            or []
        )

    group["source_meta"] = source_meta

    if source_meta:
        group["primary_source"] = (
            _primary_source(source_meta)
        )

    actionable = [
        source
        for source in source_paths
        if _source_is_out_of_band(
            source,
            group,
        )
    ]

    group["actionable_sources"] = actionable

    missing_items: list[str] = []
    present_count = 0
    missing_count = 0
    needs_import = False

    try:
        # ------------------------------------------------------------------
        # MOVIE
        # ------------------------------------------------------------------

        if media_type == "movie":
            client = RadarrClient()

            item = await client.movie(
                arr_id
            )

            if item.get("hasFile"):
                state = "imported"

                detail = (
                    "Radarr confirms the movie file is present. "
                    "No further intake work is required."
                )

                resolution = (
                    "already_present"
                    if any(
                        _safe_source(source).exists()
                        for source in source_paths
                    )
                    else "imported"
                )

                _resolve_sources(
                    group,
                    source_paths,
                    resolution,
                    detail,
                )

                present_count = 1

            else:
                top_level_actionable = [
                    source
                    for source in actionable
                    if _safe_source(source).exists()
                ]

                if top_level_actionable:
                    state = (
                        "matched"
                        if int(
                            group.get("confidence")
                            or 0
                        )
                        >= settings_state()[
                            "auto_match_threshold"
                        ]
                        else "review"
                    )

                    needs_import = True

                    detail = (
                        "Radarr still has no movie file. "
                        f"{len(top_level_actionable)} trusted "
                        "out-of-band source release(s) remain "
                        "at top-level Magic and are ready for "
                        "Smart Intake."
                    )

                else:
                    state = "awaiting_arr"

                    detail = (
                        "Radarr still has no registered movie file. "
                        "No trusted top-level source is currently "
                        "available for another automatic intake attempt."
                    )

                missing_count = 1

        # ------------------------------------------------------------------
        # TV
        # ------------------------------------------------------------------

        elif media_type == "tv":
            client = SonarrClient()

            rows = await client.episodes(
                arr_id
            )

            present = {
                (
                    int(
                        row.get("seasonNumber")
                        or 0
                    ),
                    int(
                        row.get("episodeNumber")
                        or 0
                    ),
                )
                for row in rows
                if row.get("hasFile")
            }

            known = {
                (
                    int(
                        row.get("seasonNumber")
                        or 0
                    ),
                    int(
                        row.get("episodeNumber")
                        or 0
                    ),
                )
                for row in rows
                if int(
                    row.get("episodeNumber")
                    or 0
                ) > 0
            }

            expected = set(
                _episode_pairs(
                    group.get("episodes")
                    or []
                )
            )

            # Old cards sometimes had incomplete episodes_json.
            # Rebuild expectations cheaply from source release names too.
            for source in source_paths:
                explicit = _episode_pairs(
                    _episode_markers(source)
                )

                if explicit:
                    expected.update(
                        explicit & known
                    )
                    continue

                seasons = _season_numbers(
                    source
                )

                if seasons:
                    expected.update(
                        pair
                        for pair in known
                        if pair[0] in seasons
                    )

            if expected:
                confirmed = (
                    expected & present
                )

                missing = (
                    expected - present
                )

                present_count = len(
                    confirmed
                )

                missing_count = len(
                    missing
                )

                missing_items = [
                    f"S{season:02d}E{episode:02d}"
                    for season, episode
                    in sorted(missing)
                ]

                if not missing:
                    state = "imported"

                    detail = (
                        "Sonarr confirms all "
                        f"{len(expected)} expected episode file(s). "
                        "This intake item is complete."
                    )

                    resolution = (
                        "already_present"
                        if any(
                            _safe_source(source).exists()
                            for source in source_paths
                        )
                        else "imported"
                    )

                    _resolve_sources(
                        group,
                        source_paths,
                        resolution,
                        detail,
                    )

                else:
                    top_level_actionable = [
                        source
                        for source in actionable
                        if _safe_source(source).exists()
                    ]

                    if top_level_actionable:
                        state = (
                            "matched"
                            if int(
                                group.get("confidence")
                                or 0
                            )
                            >= settings_state()[
                                "auto_match_threshold"
                            ]
                            else "review"
                        )

                        needs_import = True

                        detail = (
                            f"Sonarr confirms {present_count}/"
                            f"{len(expected)} expected episode file(s). "
                            f"{missing_count} remain missing. "
                            "The top-level out-of-band source is "
                            "available for Smart Intake."
                        )

                    else:
                        state = "partially_verified"

                        detail = (
                            f"Sonarr confirms {present_count}/"
                            f"{len(expected)} expected episode file(s). "
                            f"{missing_count} remain missing."
                        )

            else:
                before = _verification_baseline(
                    group
                )

                state, detail = (
                    await _verification_status(
                        client,
                        media_type,
                        arr_id,
                        group,
                        before,
                    )
                )

                if state == "imported":
                    _resolve_sources(
                        group,
                        source_paths,
                        "imported",
                        detail,
                    )

        # ------------------------------------------------------------------
        # MUSIC
        # ------------------------------------------------------------------

        else:
            client = LidarrClient()

            before = _verification_baseline(
                group
            )

            state, detail = (
                await _verification_status(
                    client,
                    media_type,
                    arr_id,
                    group,
                    before,
                )
            )

            if state == "imported":
                _resolve_sources(
                    group,
                    source_paths,
                    "imported",
                    detail,
                )

    except Exception as exc:
        state = str(
            group.get("state")
            or "awaiting_arr"
        )

        detail = (
            "Fresh Arr recheck failed temporarily: "
            f"{type(exc).__name__}: {exc}"
        )

    progress = (
        100
        if state == "imported"
        else 92
        if state == "partially_verified"
        else 20
        if state in {"matched", "review"}
        else 82
    )

    _set_progress(
        group_key,
        state,
        progress,
        detail,
        error="",
        arr_id=arr_id,
    )

    with db() as conn:
        conn.execute(
            """
            UPDATE magic_intake_groups
            SET source_meta_json=?,
                primary_source=?,
                actionable_sources_json=?,
                missing_items_json=?,
                missing_count=?,
                present_count=?,
                needs_import=?,
                precheck_detail=?,
                destination_key=?,
                verify_checks=verify_checks+1,
                updated_at=?
            WHERE group_key=?
            """,
            (
                json.dumps(source_meta),
                str(
                    group.get("primary_source")
                    or "unknown"
                ),
                json.dumps(actionable),
                json.dumps(missing_items),
                missing_count,
                present_count,
                1 if needs_import else 0,
                detail,
                _smart_destination_key(
                    group
                ),
                utcnow(),
                group_key,
            ),
        )

    _cleanup_smart_rows()

    _CACHE["groups"] = []

    log_event(
        "info"
        if state in {
            "imported",
            "matched",
        }
        else "warning",
        "magic_intake",
        f"recheck_{state}",
        detail,
        {
            "group": group_key,
            "manual": manual,
            "missing": missing_items,
            "needs_import": needs_import,
        },
    )

    return {
        "ok": state == "imported",
        "state": state,
        "detail": detail,
        "missing": missing_items,
        "needs_import": needs_import,
    }


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
                row
                for row in list_groups(True)
                if str(row.get("state") or "")
                in {
                    "awaiting_arr",
                    "partially_verified",
                    "numbering_mismatch",
                    "source_missing",
                    "verification_timeout",
                }
                and row.get("match_id")
            ][:20]

            for group in groups:
                key = group["group_key"]

                if (
                    key in _IMPORT_TASKS
                    and not _IMPORT_TASKS[key].done()
                ):
                    continue

                current_state = str(
                    group.get("state") or ""
                )

                updated = _parse_iso(
                    str(
                        group.get("updated_at")
                        or ""
                    )
                )

                # Old Partial / mismatch / timeout rows used to stop being
                # checked after the verification window. Give them a cheap
                # five-minute recheck instead so a later 5/5 automatically
                # disappears without user babysitting.
                if current_state in {
                    "partially_verified",
                    "numbering_mismatch",
                    "verification_timeout",
                    "source_missing",
                }:
                    if (
                        updated
                        and (
                            datetime.now(timezone.utc)
                            - updated
                        ).total_seconds()
                        < 300
                    ):
                        continue

                    await _recheck_group(key)
                    continue

                await _recheck_group(key)

            await asyncio.sleep(
                cfg["verify_interval_seconds"]
            )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            _CACHE["last_error"] = (
                f"Verification loop: {exc}"
            )

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
    if str(group.get("state") or "") == "already_present":
        raise ValueError("This DMM source is already represented in the Arr; no orphan import is required")
    if int(group.get("confidence") or 0) < settings_state()["auto_match_threshold"]:
        raise ValueError("This match is not trusted enough to move. Use Force Match first.")
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


# ===== ArrNexus v13.4 Smart Intake Controls =====

_SMART_BULK_TASK: asyncio.Task | None = None


def _smart_scan_source_names(
    source: Path,
    *,
    max_entries: int = 3000,
    max_depth: int = 4,
) -> tuple[list[str], list[int], int]:
    """
    Inspect FILE/DIRECTORY NAMES only.

    No file contents are read.
    No ffprobe.
    No mediainfo.

    Returns:
        episode markers,
        season numbers,
        names visited
    """
    markers: set[str] = set(_episode_markers(source.name))
    seasons: set[int] = set(_season_numbers(source.name))
    visited = 0

    if not source.exists() or not source.is_dir():
        return sorted(markers), sorted(seasons), visited

    queue: list[tuple[Path, int]] = [(source, 0)]

    while queue and visited < max_entries:
        current, depth = queue.pop(0)

        try:
            entries = list(os.scandir(current))
        except OSError:
            continue

        for entry in entries:
            visited += 1

            markers.update(_episode_markers(entry.name))
            seasons.update(_season_numbers(entry.name))

            if depth < max_depth:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        queue.append((Path(entry.path), depth + 1))
                except OSError:
                    pass

            if visited >= max_entries:
                break

    return sorted(markers), sorted(seasons), visited


def _smart_source_metadata(
    group: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for source in group.get("source_paths") or []:
        rows.append(
            _provenance_for_source(
                str(source),
                str(group.get("media_type") or ""),
                context,
            )
        )

    return rows


def _smart_source_out_of_band(
    source: str,
    source_meta: list[dict[str, Any]],
) -> bool:
    for row in source_meta:
        if str(row.get("name") or "") != str(source):
            continue

        return str(
            row.get("primary_source") or ""
        ) in TRUSTED_OUT_OF_BAND

    return False


def _smart_write_inspection(
    group_key: str,
    *,
    state: str,
    detail: str,
    source_paths: list[str],
    source_meta: list[dict[str, Any]],
    actionable_sources: list[str],
    missing_items: list[str],
    missing_count: int,
    present_count: int,
    needs_import: bool,
    arr_status: str,
    match_id: int | None = None,
    canonical_key: str | None = None,
) -> None:
    primary = (
        _primary_source(source_meta)
        if source_meta
        else "unknown"
    )

    group = get_group(group_key) or {}

    destination = _smart_destination_key(
        {
            **group,
            "source_paths": source_paths,
        }
    )

    with db() as conn:
        conn.execute(
            """
            UPDATE magic_intake_groups
            SET state=?,
                progress=?,
                progress_detail=?,
                error='',
                source_paths_json=?,
                release_count=?,
                source_meta_json=?,
                primary_source=?,
                actionable_sources_json=?,
                missing_items_json=?,
                missing_count=?,
                present_count=?,
                needs_import=?,
                arr_status=?,
                precheck_detail=?,
                destination_key=?,
                match_id=COALESCE(?,match_id),
                canonical_key=COALESCE(?,canonical_key),
                updated_at=?
            WHERE group_key=?
            """,
            (
                state,
                100 if state == "imported" else 20,
                detail,
                json.dumps(source_paths),
                len(source_paths),
                json.dumps(source_meta),
                primary,
                json.dumps(actionable_sources),
                json.dumps(missing_items),
                int(missing_count),
                int(present_count),
                1 if needs_import else 0,
                arr_status,
                detail,
                destination,
                match_id,
                canonical_key,
                utcnow(),
                group_key,
            ),
        )

        conn.execute(
            """
            INSERT INTO magic_intake_events(
                group_key,state,detail
            )
            VALUES(?,?,?)
            """,
            (
                group_key,
                state,
                detail,
            ),
        )

    _CACHE["groups"] = []


async def inspect_group(
    group_key: str,
    *,
    manual: bool = True,
) -> dict[str, Any]:
    """
    v13.4 targeted Arr-first inspection.

    Used after Force Match and by Recheck.

    Only the selected canonical title is inspected.
    """
    ensure_schema()

    group = get_group(group_key)

    if not group:
        return {
            "ok": False,
            "state": "missing",
            "detail": "Magic Intake card no longer exists.",
        }

    media_type = str(
        group.get("media_type") or ""
    )

    sources = [
        str(source)
        for source in (
            group.get("source_paths") or []
        )
    ]

    if not sources:
        return {
            "ok": False,
            "state": "missing",
            "detail": "No intake source releases remain.",
        }

    # Refresh provenance for this title only.
    try:
        context = await _source_context()
        source_meta = _smart_source_metadata(
            group,
            context,
        )
    except Exception:
        source_meta = list(
            group.get("source_meta") or []
        )

    # ------------------------------------------------------------------
    # MOVIE
    # ------------------------------------------------------------------

    if media_type == "movie":
        client = RadarrClient()
        movies = await client.movies()

        existing = _existing_by_match(
            "movie",
            movies,
            group,
        )

        if existing:
            arr_id = int(existing["id"])

            group["match_id"] = arr_id
            group["match_title"] = (
                existing.get("title")
                or group.get("match_title")
            )
            group["match_year"] = (
                existing.get("year")
                or group.get("match_year")
            )

            ext = str(
                existing.get("tmdbId")
                or existing.get("imdbId")
                or group.get("match_external_id")
                or ""
            )

            canonical = _canonical_key(
                "movie",
                ext,
                arr_id,
                group.get("match_title") or "",
                group.get("match_year"),
            )

            if existing.get("hasFile"):
                detail = (
                    "Radarr already has this movie file. "
                    "No import or media scan is required."
                )

                _resolve_sources(
                    {
                        **group,
                        "source_meta": source_meta,
                    },
                    sources,
                    "already_present",
                    detail,
                )

                _cleanup_smart_rows()
                _CACHE["groups"] = []

                return {
                    "ok": True,
                    "state": "resolved",
                    "detail": detail,
                    "needs_import": False,
                    "missing": [],
                }

        else:
            arr_id = int(
                group.get("match_id") or 0
            ) or None

            canonical = str(
                group.get("canonical_key")
                or group_key
            )

        actionable = [
            source
            for source in sources
            if _smart_source_out_of_band(
                source,
                source_meta,
            )
            and _safe_source(source).exists()
        ]

        if actionable:
            state = "matched"
            detail = (
                "Radarr does not currently have a registered movie file. "
                f"{len(actionable)} trusted out-of-band source "
                f"release(s) can be imported."
            )
            needs_import = True
        else:
            state = "review"
            detail = (
                "Radarr does not currently have the movie file, but no "
                "trusted out-of-band top-level source is available for "
                "automatic intake."
            )
            needs_import = False

        _smart_write_inspection(
            group_key,
            state=state,
            detail=detail,
            source_paths=sources,
            source_meta=source_meta,
            actionable_sources=actionable,
            missing_items=["Movie file"],
            missing_count=1,
            present_count=0,
            needs_import=needs_import,
            arr_status="missing",
            match_id=arr_id,
            canonical_key=canonical,
        )

        _cleanup_smart_rows()

        return {
            "ok": True,
            "state": state,
            "detail": detail,
            "needs_import": needs_import,
            "missing": ["Movie file"],
        }

    # ------------------------------------------------------------------
    # TV
    # ------------------------------------------------------------------

    if media_type == "tv":
        client = SonarrClient()
        series_rows = await client.series()

        existing = _existing_by_match(
            "tv",
            series_rows,
            group,
        )

        # Series hasn't been added to Sonarr yet.
        if not existing:
            actionable = [
                source
                for source in sources
                if _smart_source_out_of_band(
                    source,
                    source_meta,
                )
                and _safe_source(source).exists()
            ]

            detail = (
                "This series is not currently in Sonarr. "
                f"{len(actionable)} trusted out-of-band source "
                f"release(s) are available."
            )

            state = (
                "matched"
                if actionable
                else "review"
            )

            _smart_write_inspection(
                group_key,
                state=state,
                detail=detail,
                source_paths=sources,
                source_meta=source_meta,
                actionable_sources=actionable,
                missing_items=[],
                missing_count=0,
                present_count=0,
                needs_import=bool(actionable),
                arr_status="not_in_arr",
                match_id=(
                    int(group.get("match_id") or 0)
                    or None
                ),
                canonical_key=str(
                    group.get("canonical_key")
                    or group_key
                ),
            )

            _cleanup_smart_rows()

            return {
                "ok": True,
                "state": state,
                "detail": detail,
                "needs_import": bool(actionable),
                "missing": [],
            }

        arr_id = int(existing["id"])

        group["match_id"] = arr_id
        group["match_title"] = (
            existing.get("title")
            or group.get("match_title")
        )
        group["match_year"] = (
            existing.get("year")
            or group.get("match_year")
        )

        ext = str(
            existing.get("tvdbId")
            or existing.get("tmdbId")
            or group.get("match_external_id")
            or ""
        )

        canonical = _canonical_key(
            "tv",
            ext,
            arr_id,
            group.get("match_title") or "",
            group.get("match_year"),
        )

        episode_rows = await client.episodes(
            arr_id
        )

        known_pairs = {
            (
                int(row.get("seasonNumber") or 0),
                int(row.get("episodeNumber") or 0),
            )
            for row in episode_rows
            if int(row.get("episodeNumber") or 0) > 0
        }

        present_pairs = {
            (
                int(row.get("seasonNumber") or 0),
                int(row.get("episodeNumber") or 0),
            )
            for row in episode_rows
            if row.get("hasFile")
            and int(row.get("episodeNumber") or 0) > 0
        }

        remaining: list[str] = []
        actionable: list[str] = []
        resolved_now: list[str] = []

        expected_union: set[tuple[int, int]] = set()
        missing_union: set[tuple[int, int]] = set()

        unmapped_sources: list[str] = []
        inspected_names = 0

        for source in sources:
            # ----------------------------------------------------------
            # Cheapest check first: source/release name itself.
            # ----------------------------------------------------------

            markers = set(
                _episode_markers(source)
            )

            seasons = set(
                _season_numbers(source)
            )

            expected: set[
                tuple[int, int]
            ] = set()

            marker_pairs = (
                _episode_pairs(
                    sorted(markers)
                )
            )

            if marker_pairs:
                expected = (
                    marker_pairs
                    & known_pairs
                )

            elif seasons:
                expected = {
                    pair
                    for pair in known_pairs
                    if pair[0] in seasons
                }

            # ----------------------------------------------------------
            # Only if the source name tells us nothing do we look inside
            # THIS release. Names only; never media contents.
            # ----------------------------------------------------------

            if not expected:
                src = _safe_source(source)

                if src.exists():
                    child_markers, child_seasons, visited = (
                        await asyncio.to_thread(
                            _smart_scan_source_names,
                            src,
                        )
                    )

                    inspected_names += visited

                    pairs = _episode_pairs(
                        child_markers
                    )

                    if pairs:
                        expected = (
                            pairs
                            & known_pairs
                        )

                    elif child_seasons:
                        wanted_seasons = set(
                            child_seasons
                        )

                        expected = {
                            pair
                            for pair in known_pairs
                            if pair[0]
                            in wanted_seasons
                        }

            if not expected:
                remaining.append(source)
                unmapped_sources.append(source)
                continue

            expected_union.update(
                expected
            )

            missing = (
                expected
                - present_pairs
            )

            # Already fully represented by Sonarr.
            if not missing:
                resolved_now.append(source)
                continue

            remaining.append(source)
            missing_union.update(missing)

            if (
                _smart_source_out_of_band(
                    source,
                    source_meta,
                )
                and _safe_source(source).exists()
            ):
                actionable.append(source)

        # Permanently suppress source releases whose episodes are all
        # already present.
        if resolved_now:
            _resolve_sources(
                {
                    **group,
                    "source_meta": source_meta,
                },
                resolved_now,
                "already_present",
                "Sonarr already contains every identifiable episode "
                "from this intake source.",
            )

        # Nothing remains to deal with.
        if not remaining:
            detail = (
                "Sonarr already contains all identifiable episodes "
                "from these intake releases. No import is required."
            )

            _cleanup_smart_rows()
            _CACHE["groups"] = []

            return {
                "ok": True,
                "state": "resolved",
                "detail": detail,
                "needs_import": False,
                "missing": [],
            }

        remaining_set = set(
            remaining
        )

        source_meta = [
            row
            for row in source_meta
            if str(row.get("name") or "")
            in remaining_set
        ]

        present_count = len(
            expected_union
            & present_pairs
        )

        missing_items = [
            f"S{season:02d}E{episode:02d}"
            for season, episode
            in sorted(missing_union)
        ]

        if missing_union and actionable:
            state = "matched"
            needs_import = True
            arr_status = "partial"

            detail = (
                f"Sonarr already has {present_count}/"
                f"{len(expected_union)} identifiable episode file(s). "
                f"{len(missing_union)} episode(s) are still missing. "
                f"{len(actionable)} trusted source release(s) can supply them."
            )

        elif missing_union:
            state = "partially_verified"
            needs_import = False
            arr_status = "partial"

            detail = (
                f"Sonarr already has {present_count}/"
                f"{len(expected_union)} identifiable episode file(s). "
                f"{len(missing_union)} remain missing, but no trusted "
                "top-level out-of-band source is currently available."
            )

        elif unmapped_sources:
            state = "review"
            needs_import = False
            arr_status = "mapping_unknown"

            detail = (
                f"{len(unmapped_sources)} release(s) still cannot be mapped "
                "to exact Sonarr episodes from their release/folder/file "
                "names. No media contents were scanned."
            )

        else:
            state = "imported"
            needs_import = False
            arr_status = "complete"

            detail = (
                "Sonarr already contains all identifiable episodes."
            )

        if inspected_names:
            detail += (
                f" Targeted inspection checked {inspected_names} "
                "file/folder name(s) only."
            )

        _smart_write_inspection(
            group_key,
            state=state,
            detail=detail,
            source_paths=remaining,
            source_meta=source_meta,
            actionable_sources=actionable,
            missing_items=missing_items,
            missing_count=len(missing_union),
            present_count=present_count,
            needs_import=needs_import,
            arr_status=arr_status,
            match_id=arr_id,
            canonical_key=canonical,
        )

        _cleanup_smart_rows()
        _CACHE["groups"] = []

        return {
            "ok": True,
            "state": state,
            "detail": detail,
            "needs_import": needs_import,
            "missing": missing_items,
        }

    # ------------------------------------------------------------------
    # MUSIC
    # ------------------------------------------------------------------

    detail = (
        "Targeted Smart Intake inspection currently applies to "
        "movies and TV. Music is unchanged."
    )

    return {
        "ok": True,
        "state": str(group.get("state") or "matched"),
        "detail": detail,
        "needs_import": False,
        "missing": [],
    }


def clear_groups(
    group_keys: list[str],
) -> dict[str, Any]:
    """
    Permanently clear cards from Magic Intake.

    DOES NOT:
      - delete Real-Debrid content
      - delete NZBs/torrents
      - remove anything from Radarr/Sonarr/Lidarr
      - delete media
    """
    ensure_schema()

    unique = list(
        dict.fromkeys(
            str(value)
            for value in group_keys
            if str(value or "").strip()
        )
    )[:200]

    cleared = 0
    missing = 0

    for group_key in unique:
        group = get_group(group_key)

        if not group:
            missing += 1
            continue

        sources = list(
            group.get("source_paths")
            or []
        )

        if sources:
            _resolve_sources(
                group,
                sources,
                "cleared",
                "Cleared permanently from Magic Intake by user. "
                "Underlying media/source was not deleted.",
            )

        with db() as conn:
            conn.execute(
                """
                UPDATE magic_intake_groups
                SET ignored=1,
                    state='ignored',
                    progress=100,
                    progress_detail='Cleared from Intake',
                    updated_at=?
                WHERE group_key=?
                """,
                (
                    utcnow(),
                    group_key,
                ),
            )

            conn.execute(
                """
                INSERT INTO magic_intake_events(
                    group_key,state,detail
                )
                VALUES(?,?,?)
                """,
                (
                    group_key,
                    "ignored",
                    "Cleared from Intake; source/media left untouched",
                ),
            )

        cleared += 1

    _cleanup_smart_rows()
    _CACHE["groups"] = []

    return {
        "ok": True,
        "cleared": cleared,
        "missing": missing,
        "detail": (
            f"Cleared {cleared} item(s) from Magic Intake. "
            "No media or Real-Debrid content was deleted."
        ),
    }


def request_bulk_inspect(
    group_keys: list[str],
) -> dict[str, Any]:
    """
    Sequential bulk recheck.

    Only one selected title is inspected at a time.
    """
    global _SMART_BULK_TASK

    unique = list(
        dict.fromkeys(
            str(value)
            for value in group_keys
            if str(value or "").strip()
        )
    )[:100]

    if not unique:
        raise ValueError(
            "Select at least one Magic Intake item."
        )

    if (
        _SMART_BULK_TASK
        and not _SMART_BULK_TASK.done()
    ):
        return {
            "ok": True,
            "queued": False,
            "detail": (
                "A selected-item Smart Intake recheck is already running."
            ),
        }

    async def runner() -> None:
        global _SMART_BULK_TASK

        try:
            for group_key in unique:
                try:
                    await inspect_group(
                        group_key,
                        manual=True,
                    )
                except Exception as exc:
                    log_event(
                        "warning",
                        "magic_intake",
                        "bulk_inspect_failed",
                        f"Selected-item inspection failed: "
                        f"{type(exc).__name__}: {exc}",
                        {
                            "group": group_key,
                        },
                    )

                # Deliberately sequential and gentle on the Arrs/Zurg.
                await asyncio.sleep(0.35)

        finally:
            _SMART_BULK_TASK = None

    _SMART_BULK_TASK = asyncio.create_task(
        runner(),
        name="magic-smart-bulk-inspect",
    )

    return {
        "ok": True,
        "queued": True,
        "count": len(unique),
        "detail": (
            f"Rechecking {len(unique)} selected item(s) sequentially."
        ),
    }


# ===== End ArrNexus v13.4 Smart Intake Controls =====
