from __future__ import annotations

import asyncio
import json
import re
import time
from collections import deque
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from .arr import LidarrClient, RadarrClient, SonarrClient, records
from .db import setting_get, setting_set
from .jellyfin import JellyfinClient

BOARDS_KEY = "dashboard.boards.v1"
PUBLIC_DOMAIN_KEY = "dashboard.public_domain"
HOME_BOARD_KEY = "dashboard.home_board"

SERVICE_KEYS = (
    "arrnexus", "zurg", "sonarr", "radarr", "lidarr", "prowlarr", "seerrng",
    "jellyfin", "bazarr", "whisparr", "neutarr", "maintainerr", "profilarr", "homarr",
)

SERVICE_LABELS = {
    "arrnexus": "ArrNexus",
    "zurg": "Zurg",
    "sonarr": "Sonarr",
    "radarr": "Radarr",
    "lidarr": "Lidarr",
    "lidarr-postgres": "Lidarr PostgreSQL",
    "prowlarr": "Prowlarr",
    "seerrng": "SeerrNG",
    "jellyfin": "Jellyfin",
    "bazarr": "Bazarr",
    "whisparr": "Whisparr",
    "neutarr": "NeutArr",
    "maintainerr": "Maintainerr",
    "profilarr": "Profilarr",
    "profilarr-parser": "Profilarr Parser",
    "homarr": "Homarr",
}

ICON_NAMES = {
    "arrnexus": "docker",
    "zurg": "rclone",
    "sonarr": "sonarr",
    "radarr": "radarr",
    "lidarr": "lidarr",
    "lidarr-postgres": "postgresql",
    "prowlarr": "prowlarr",
    "seerrng": "jellyseerr",
    "jellyfin": "jellyfin",
    "bazarr": "bazarr",
    "whisparr": "whisparr",
    "neutarr": "docker",
    "maintainerr": "maintainerr",
    "profilarr": "profilarr",
    "profilarr-parser": "profilarr",
    "homarr": "homarr",
}

SUBDOMAIN_NAMES = {"seerrng": "seerr"}

# Dashboard v2 deliberately keeps the widget catalogue declarative. Adding a new
# native integration should only require a catalogue entry, renderer and data
# provider rather than another one-off dashboard page.
WIDGETS: dict[str, dict[str, Any]] = {
    "app-launcher": {"name": "Apps", "description": "Colourful app tiles with health and direct links.", "category": "Apps", "w": 12, "h": 2},
    "host-resources": {"name": "System resources", "description": "Live CPU and RAM gauges with history graphs.", "category": "System", "w": 4, "h": 3},
    "docker-stats": {"name": "Docker containers", "description": "Container CPU, memory, health and restart state.", "category": "System", "w": 8, "h": 4},
    "jellyfin-streams": {"name": "Jellyfin streams", "description": "Now playing, direct play and transcoding sessions.", "category": "Media", "w": 8, "h": 3},
    "recent-media": {"name": "Recently added", "description": "Poster-rich recently added Jellyfin media.", "category": "Media", "w": 12, "h": 4},
    "active-requests": {"name": "Active requests", "description": "Current Seerr → Arr → Zurg request flow.", "category": "Media", "w": 6, "h": 3},
    "downloads": {"name": "Downloads", "description": "Sonarr, Radarr and Lidarr queue progress.", "category": "Media", "w": 6, "h": 3},
    "calendar": {"name": "Calendar", "description": "Upcoming Sonarr episodes and Radarr movie releases.", "category": "Media", "w": 6, "h": 3},
    "updates": {"name": "Updates", "description": "MediaStack services with newer image digests.", "category": "System", "w": 4, "h": 2},
    "missing-queue": {"name": "Missing & queue", "description": "Missing-media and Queue Janitor summary.", "category": "ArrNexus", "w": 4, "h": 2},
    "zurg-health": {"name": "Zurg", "description": "Filesystem, library and resource-pressure summary.", "category": "ArrNexus", "w": 4, "h": 2},
    "clock": {"name": "Clock", "description": "Large live clock and date.", "category": "Utility", "w": 3, "h": 2},
    "bookmarks": {"name": "Bookmarks", "description": "Quick links for anything, not only media services.", "category": "Utility", "w": 4, "h": 2},
    "note": {"name": "Note", "description": "A free-form note on the board.", "category": "Utility", "w": 4, "h": 2},
    "iframe": {"name": "Embedded page", "description": "Embed a trusted HTTP(S) page in a resizable widget.", "category": "Utility", "w": 6, "h": 4},
}

DEFAULT_APPEARANCE = {
    "columns": 12,
    "row_height": 74,
    "accent": "#8b5cf6",
    "panel_opacity": 0.82,
    "blur": 18,
    "radius": 18,
    "background": "radial-gradient(circle at 15% 10%, #17355f 0, transparent 35%),radial-gradient(circle at 85% 0%, #3b1b5d 0, transparent 32%),linear-gradient(145deg,#06101d 0%,#0b1728 58%,#111827 100%)",
}


def _default_item(kind: str, index: int, options: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = WIDGETS.get(kind) or {"w": 4, "h": 2}
    return {
        "id": f"{kind}-{index + 1}",
        "kind": kind,
        "order": index,
        "w": int(meta.get("w") or 4),
        "h": int(meta.get("h") or 2),
        "options": dict(options or {}),
    }


DEFAULT_MEDIA_KINDS = [
    "app-launcher", "host-resources", "jellyfin-streams", "downloads", "calendar",
    "active-requests", "recent-media", "docker-stats", "zurg-health", "missing-queue", "updates",
]

DEFAULT_BOARDS = [
    {
        "id": "overview",
        "name": "Overview",
        "description": "The original ArrNexus orchestration dashboard.",
        "builtin": True,
        "widgets": [],
        "items": [],
        "appearance": deepcopy(DEFAULT_APPEARANCE),
    },
    {
        "id": "media-hub",
        "name": "Media Hub",
        "description": "Your visual media command centre.",
        "builtin": False,
        "widgets": list(DEFAULT_MEDIA_KINDS),
        "items": [_default_item(kind, i) for i, kind in enumerate(DEFAULT_MEDIA_KINDS)],
        "appearance": deepcopy(DEFAULT_APPEARANCE),
    },
]

_METRIC_HISTORY: dict[str, deque[float]] = {
    "cpu": deque(maxlen=90),
    "memory": deque(maxlen=90),
}
_LIVE_CACHE: dict[str, tuple[float, Any]] = {}


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return cleaned[:64] or "board"


def _safe_int(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except Exception:
        return fallback


def _sanitize_options(options: Any) -> dict[str, Any]:
    if not isinstance(options, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in list(options.items())[:30]:
        skey = re.sub(r"[^a-zA-Z0-9_-]", "", str(key))[:40]
        if not skey:
            continue
        if isinstance(value, (bool, int, float)):
            out[skey] = value
        elif isinstance(value, str):
            out[skey] = value[:4000]
        elif isinstance(value, list):
            out[skey] = [str(x)[:300] for x in value[:50]]
    return out


def _normalize_item(item: dict[str, Any], index: int) -> dict[str, Any] | None:
    kind = str(item.get("kind") or "")
    if kind not in WIDGETS:
        return None
    meta = WIDGETS[kind]
    iid = _slug(str(item.get("id") or f"{kind}-{index + 1}"))
    return {
        "id": iid,
        "kind": kind,
        "order": _safe_int(item.get("order", index), 0, 999, index),
        "w": _safe_int(item.get("w", meta.get("w", 4)), 2, 12, int(meta.get("w") or 4)),
        "h": _safe_int(item.get("h", meta.get("h", 2)), 1, 8, int(meta.get("h") or 2)),
        "options": _sanitize_options(item.get("options")),
    }


def _normalize_appearance(raw: Any) -> dict[str, Any]:
    data = dict(DEFAULT_APPEARANCE)
    if isinstance(raw, dict):
        data.update({k: raw[k] for k in DEFAULT_APPEARANCE if k in raw})
    data["columns"] = _safe_int(data.get("columns"), 6, 24, 12)
    data["row_height"] = _safe_int(data.get("row_height"), 48, 140, 74)
    data["blur"] = _safe_int(data.get("blur"), 0, 40, 18)
    data["radius"] = _safe_int(data.get("radius"), 0, 32, 18)
    try:
        data["panel_opacity"] = max(0.25, min(1.0, float(data.get("panel_opacity") or 0.82)))
    except Exception:
        data["panel_opacity"] = 0.82
    accent = str(data.get("accent") or "#8b5cf6")
    data["accent"] = accent if re.fullmatch(r"#[0-9a-fA-F]{6}", accent) else "#8b5cf6"
    bg = str(data.get("background") or DEFAULT_APPEARANCE["background"]).strip()[:1500]
    lowered = bg.lower()
    if "javascript:" in lowered or "expression(" in lowered:
        bg = DEFAULT_APPEARANCE["background"]
    if lowered.startswith("http://") or lowered.startswith("https://"):
        bg = f'linear-gradient(rgba(4,10,18,.58),rgba(4,10,18,.72)),url("{bg}") center/cover fixed'
    data["background"] = bg
    return data


def _load_boards() -> list[dict[str, Any]]:
    raw = setting_get(BOARDS_KEY, "")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                rows = [row for row in parsed if isinstance(row, dict)]
                if rows:
                    return rows
        except Exception:
            pass
    return deepcopy(DEFAULT_BOARDS)


def boards() -> list[dict[str, Any]]:
    rows = _load_boards()
    if not any(str(row.get("id") or "") == "overview" for row in rows):
        rows.insert(0, deepcopy(DEFAULT_BOARDS[0]))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        bid = _slug(str(row.get("id") or row.get("name") or "board"))
        if bid in seen:
            continue
        seen.add(bid)
        raw_items = row.get("items") or []
        items: list[dict[str, Any]] = []
        if isinstance(raw_items, list):
            for index, item in enumerate(raw_items):
                if isinstance(item, dict):
                    normalized = _normalize_item(item, index)
                    if normalized:
                        items.append(normalized)
        # Transparent migration from the first simple Media Hub implementation.
        if not items:
            legacy = [str(x) for x in (row.get("widgets") or []) if str(x) in WIDGETS]
            items = [_default_item(kind, i) for i, kind in enumerate(legacy)]
        items.sort(key=lambda x: int(x.get("order") or 0))
        widgets = list(dict.fromkeys(str(x.get("kind")) for x in items))
        out.append({
            "id": bid,
            "name": str(row.get("name") or bid.replace("-", " ").title()),
            "description": str(row.get("description") or ""),
            "builtin": bool(row.get("builtin")) or bid == "overview",
            "widgets": widgets,
            "items": items,
            "appearance": _normalize_appearance(row.get("appearance")),
        })
    return out


def save_boards(rows: list[dict[str, Any]]) -> None:
    setting_set(BOARDS_KEY, json.dumps(rows, ensure_ascii=False, separators=(",", ":")))


def get_board(board_id: str) -> dict[str, Any] | None:
    key = _slug(board_id)
    return next((deepcopy(row) for row in boards() if row["id"] == key), None)


def save_board(board_id: str, name: str, widgets: list[str], description: str = "") -> dict[str, Any]:
    rows = boards()
    bid = _slug(board_id or name)
    if bid == "overview":
        raise ValueError("The built-in Overview board cannot be replaced")
    existing = next((row for row in rows if row["id"] == bid), None)
    selected = [key for key in widgets if key in WIDGETS]
    items = deepcopy((existing or {}).get("items") or [])
    if selected:
        old_by_kind: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            old_by_kind.setdefault(str(item.get("kind") or ""), []).append(item)
        rebuilt: list[dict[str, Any]] = []
        for index, kind in enumerate(selected):
            candidate = (old_by_kind.get(kind) or [None]).pop(0)
            rebuilt.append(candidate or _default_item(kind, index))
        items = rebuilt
    row = {
        "id": bid,
        "name": str(name or bid.replace("-", " ").title()).strip(),
        "description": str(description or "").strip(),
        "builtin": False,
        "widgets": list(dict.fromkeys(str(x.get("kind")) for x in items)),
        "items": items,
        "appearance": _normalize_appearance((existing or {}).get("appearance")),
    }
    replaced = False
    for index, current in enumerate(rows):
        if current["id"] == bid:
            rows[index] = row
            replaced = True
            break
    if not replaced:
        rows.append(row)
    save_boards(rows)
    return row


def save_layout(board_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    bid = _slug(board_id)
    rows = boards()
    index = next((i for i, row in enumerate(rows) if row["id"] == bid), None)
    if index is None or bid == "overview":
        raise ValueError("Unknown editable board")
    current = rows[index]
    current_by_id = {str(item.get("id")): item for item in current.get("items") or []}
    raw_items = payload.get("items") or []
    new_items: list[dict[str, Any]] = []
    if not isinstance(raw_items, list):
        raise ValueError("items must be a list")
    for order, raw in enumerate(raw_items[:80]):
        if not isinstance(raw, dict):
            continue
        iid = _slug(str(raw.get("id") or ""))
        previous = current_by_id.get(iid) or {}
        kind = str(raw.get("kind") or previous.get("kind") or "")
        if kind not in WIDGETS:
            continue
        merged = {
            "id": iid or f"{kind}-{order + 1}",
            "kind": kind,
            "order": order,
            "w": raw.get("w", previous.get("w")),
            "h": raw.get("h", previous.get("h")),
            "options": previous.get("options") or raw.get("options") or {},
        }
        normalized = _normalize_item(merged, order)
        if normalized:
            new_items.append(normalized)
    current["items"] = new_items
    current["widgets"] = list(dict.fromkeys(x["kind"] for x in new_items))
    rows[index] = current
    save_boards(rows)
    return current


def add_item(board_id: str, kind: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    bid = _slug(board_id)
    if kind not in WIDGETS:
        raise ValueError("Unknown widget type")
    rows = boards()
    index = next((i for i, row in enumerate(rows) if row["id"] == bid), None)
    if index is None or bid == "overview":
        raise ValueError("Unknown editable board")
    row = rows[index]
    items = list(row.get("items") or [])
    stamp = int(time.time() * 1000)
    item = _default_item(kind, len(items), options)
    item["id"] = f"{kind}-{stamp}"
    item["order"] = len(items)
    items.append(item)
    row["items"] = items
    row["widgets"] = list(dict.fromkeys(x["kind"] for x in items))
    rows[index] = row
    save_boards(rows)
    return item


def remove_item(board_id: str, item_id: str) -> None:
    bid = _slug(board_id)
    rows = boards()
    index = next((i for i, row in enumerate(rows) if row["id"] == bid), None)
    if index is None or bid == "overview":
        raise ValueError("Unknown editable board")
    row = rows[index]
    iid = _slug(item_id)
    row["items"] = [item for item in (row.get("items") or []) if str(item.get("id")) != iid]
    for order, item in enumerate(row["items"]):
        item["order"] = order
    row["widgets"] = list(dict.fromkeys(x["kind"] for x in row["items"]))
    rows[index] = row
    save_boards(rows)


def save_appearance(board_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    bid = _slug(board_id)
    rows = boards()
    index = next((i for i, row in enumerate(rows) if row["id"] == bid), None)
    if index is None or bid == "overview":
        raise ValueError("Unknown editable board")
    rows[index]["appearance"] = _normalize_appearance(raw)
    save_boards(rows)
    return rows[index]["appearance"]


def delete_board(board_id: str) -> None:
    bid = _slug(board_id)
    if bid == "overview":
        raise ValueError("The built-in Overview board cannot be deleted")
    save_boards([row for row in boards() if row["id"] != bid])
    if home_board() == bid:
        setting_set(HOME_BOARD_KEY, "overview")


def home_board() -> str:
    value = _slug(setting_get(HOME_BOARD_KEY, "overview"))
    return value if get_board(value) else "overview"


def set_home_board(board_id: str) -> None:
    bid = _slug(board_id)
    if not get_board(bid):
        raise ValueError("Unknown dashboard board")
    setting_set(HOME_BOARD_KEY, bid)


def public_domain() -> str:
    value = setting_get(PUBLIC_DOMAIN_KEY, "").strip().lower()
    return re.sub(r"^https?://", "", value).strip("/")


def set_public_domain(value: str) -> str:
    domain = re.sub(r"^https?://", "", str(value or "").strip().lower()).strip("/")
    setting_set(PUBLIC_DOMAIN_KEY, domain)
    if domain:
        current = setting_get("app.public_url", "").strip()
        parsed = urlparse(current) if current else None
        current_host = (parsed.hostname or "") if parsed else ""
        if not current or "example.com" in current or (parsed and parsed.scheme != "https") or current_host.startswith("192.168."):
            setting_set("app.public_url", f"https://arrnexus.{domain}")
    return domain


def service_override(service: str) -> str:
    key = str(service or "").lower()
    return setting_get(f"dashboard.url.{key}", "").strip().rstrip("/")


def service_url(service: str) -> str:
    key = str(service or "").lower()
    override = service_override(key)
    if override:
        return override
    domain = public_domain()
    if not domain or key not in SERVICE_KEYS:
        return ""
    return f"https://{SUBDOMAIN_NAMES.get(key, key)}.{domain}"


def set_service_url(service: str, value: str) -> None:
    key = str(service or "").lower()
    if key not in SERVICE_KEYS:
        raise ValueError("Unsupported dashboard service")
    setting_set(f"dashboard.url.{key}", str(value or "").strip().rstrip("/"))


def service_icon(service: str) -> str:
    icon = ICON_NAMES.get(str(service or "").lower(), "docker")
    return f"https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/{icon}.svg"


def service_links() -> list[dict[str, str]]:
    return [
        {
            "key": key,
            "name": SERVICE_LABELS.get(key, key.title()),
            "url": service_url(key),
            "override": service_override(key),
            "icon": service_icon(key),
        }
        for key in SERVICE_KEYS
    ]


def board_api() -> dict[str, Any]:
    return {
        "ok": True,
        "schema": 2,
        "boards": boards(),
        "home_board": home_board(),
        "widgets": [{"key": key, **value} for key, value in WIDGETS.items()],
        "public_domain": public_domain(),
        "service_links": service_links(),
        "spotify_public_url": setting_get("app.public_url", ""),
    }


def _service_tiles(stack: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in stack.get("services") or []:
        name = str(item.get("name") or "")
        if name in {"arrnexus-stack-agent", "arrnexus-mediastack-ui-test"}:
            continue
        state = str(item.get("state") or "unknown").lower()
        health = str(item.get("health") or "").lower()
        if state != "running":
            status = "bad"
        elif health and health not in {"healthy", "starting"}:
            status = "bad"
        elif health == "starting":
            status = "warn"
        else:
            status = "good"
        rows.append({
            **item,
            "label": SERVICE_LABELS.get(name, name.replace("-", " ").title()),
            "public_url": service_url(name),
            "icon": service_icon(name),
            "board_status": status,
        })
    return sorted(rows, key=lambda row: str(row.get("label") or "").lower())


async def jellyfin_sessions() -> list[dict[str, Any]]:
    client = JellyfinClient()
    if not client.configured:
        return []
    try:
        data = await client.request("GET", "/Sessions")
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for session in data or []:
        now = session.get("NowPlayingItem") or {}
        if not now:
            continue
        play_state = session.get("PlayState") or {}
        transcode = session.get("TranscodingInfo") or {}
        runtime_ticks = float(now.get("RunTimeTicks") or 0)
        position_ticks = float(play_state.get("PositionTicks") or 0)
        progress = round((position_ticks / runtime_ticks) * 100.0, 1) if runtime_ticks else 0.0
        rows.append({
            "id": str(now.get("Id") or ""),
            "user": session.get("UserName") or "Unknown user",
            "client": session.get("Client") or session.get("DeviceName") or "Jellyfin",
            "title": now.get("Name") or "Unknown title",
            "series": now.get("SeriesName") or "",
            "type": now.get("Type") or "",
            "paused": bool(play_state.get("IsPaused")),
            "transcoding": bool(transcode),
            "video_codec": transcode.get("VideoCodec") or "",
            "audio_codec": transcode.get("AudioCodec") or "",
            "bitrate": int(transcode.get("Bitrate") or 0),
            "progress": progress,
            "image": f"/api/dashboard/jellyfin/image/{now.get('Id')}" if now.get("Id") else "",
        })
    return rows


async def jellyfin_recent(limit: int = 16) -> list[dict[str, Any]]:
    client = JellyfinClient()
    if not client.configured:
        return []
    try:
        data = await client.request(
            "GET",
            "/Items",
            params={
                "Recursive": "true",
                "IncludeItemTypes": "Movie,Episode",
                "SortBy": "DateCreated",
                "SortOrder": "Descending",
                "Fields": "DateCreated,SeriesName,ProductionYear,ImageTags",
                "Limit": max(1, min(int(limit), 30)),
            },
        )
    except Exception:
        return []
    return [
        {
            "id": str(item.get("Id") or ""),
            "title": item.get("Name") or "Unknown",
            "series": item.get("SeriesName") or "",
            "type": item.get("Type") or "",
            "year": item.get("ProductionYear") or "",
            "date": item.get("DateCreated") or "",
            "image": f"/api/dashboard/jellyfin/image/{item.get('Id')}" if item.get("Id") else "",
        }
        for item in (data.get("Items") or [])
    ]


async def _cached_live(key: str, ttl: float, factory):
    cached = _LIVE_CACHE.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < ttl:
        return deepcopy(cached[1])
    value = await factory()
    _LIVE_CACHE[key] = (now, deepcopy(value))
    return value


async def download_queue(limit: int = 15) -> list[dict[str, Any]]:
    async def load():
        clients = [("Sonarr", SonarrClient()), ("Radarr", RadarrClient()), ("Lidarr", LidarrClient())]
        results = await asyncio.gather(*(client.queue(100) for _, client in clients), return_exceptions=True)
        rows: list[dict[str, Any]] = []
        for (label, _), payload in zip(clients, results):
            if isinstance(payload, Exception):
                continue
            for item in records(payload):
                size = float(item.get("size") or 0)
                left = float(item.get("sizeleft") or item.get("sizeLeft") or 0)
                progress = round(max(0.0, min(100.0, ((size - left) / size) * 100.0)), 1) if size else 0.0
                title = item.get("title") or item.get("movie", {}).get("title") or item.get("series", {}).get("title") or "Download"
                rows.append({
                    "service": label,
                    "title": title,
                    "status": item.get("status") or item.get("trackedDownloadStatus") or "queued",
                    "timeleft": item.get("timeleft") or item.get("timeLeft") or "",
                    "progress": progress,
                })
        return rows[:limit]
    return await _cached_live("downloads", 12.0, load)


async def calendar_items(days: int = 14, limit: int = 24) -> list[dict[str, Any]]:
    async def load():
        start = datetime.now(timezone.utc)
        end = start + timedelta(days=days)
        params = {"start": start.isoformat(), "end": end.isoformat()}
        sonarr, radarr = SonarrClient(), RadarrClient()
        results = await asyncio.gather(
            sonarr.request("GET", "/api/v3/calendar", params={**params, "includeSeries": True}),
            radarr.request("GET", "/api/v3/calendar", params=params),
            return_exceptions=True,
        )
        rows: list[dict[str, Any]] = []
        if not isinstance(results[0], Exception):
            for item in results[0] or []:
                series = item.get("series") or {}
                rows.append({
                    "service": "Sonarr",
                    "title": series.get("title") or item.get("title") or "Episode",
                    "subtitle": f"S{int(item.get('seasonNumber') or 0):02d}E{int(item.get('episodeNumber') or 0):02d} · {item.get('title') or ''}".strip(" ·"),
                    "date": item.get("airDateUtc") or item.get("airDate") or "",
                })
        if not isinstance(results[1], Exception):
            for item in results[1] or []:
                rows.append({
                    "service": "Radarr",
                    "title": item.get("title") or "Movie",
                    "subtitle": str(item.get("year") or ""),
                    "date": item.get("digitalRelease") or item.get("physicalRelease") or item.get("inCinemas") or "",
                })
        rows.sort(key=lambda x: str(x.get("date") or "9999"))
        return rows[:limit]
    return await _cached_live("calendar", 60.0, load)


def _record_metrics(stack: dict[str, Any]) -> dict[str, Any]:
    summary = stack.get("summary") or {}
    cpu = float(summary.get("cpu_percent") or 0)
    memory = float(summary.get("memory_percent") or 0)
    _METRIC_HISTORY["cpu"].append(round(cpu, 2))
    _METRIC_HISTORY["memory"].append(round(memory, 2))
    return {
        "cpu": round(cpu, 1),
        "memory": round(memory, 1),
        "memory_used": summary.get("memory_used_human") or "—",
        "memory_total": summary.get("memory_total_human") or "—",
        "cpu_history": list(_METRIC_HISTORY["cpu"]),
        "memory_history": list(_METRIC_HISTORY["memory"]),
        "running": int(summary.get("running") or 0),
        "total": int(summary.get("total") or 0),
        "updates": int(summary.get("updates") or 0),
    }


async def runtime(
    board: dict[str, Any],
    *,
    stack: dict[str, Any],
    live: dict[str, Any],
    orchestrator: dict[str, Any],
    janitor: dict[str, Any],
    zurg: dict[str, Any],
) -> dict[str, Any]:
    widgets = set(board.get("widgets") or [])
    jobs: list[tuple[str, Any]] = []
    if "jellyfin-streams" in widgets:
        jobs.append(("jellyfin_sessions", jellyfin_sessions()))
    if "recent-media" in widgets:
        jobs.append(("recent_media", jellyfin_recent()))
    if "downloads" in widgets:
        jobs.append(("downloads", download_queue()))
    if "calendar" in widgets:
        jobs.append(("calendar", calendar_items()))
    values: dict[str, Any] = {"jellyfin_sessions": [], "recent_media": [], "downloads": [], "calendar": []}
    if jobs:
        results = await asyncio.gather(*(job for _, job in jobs), return_exceptions=True)
        for (name, _), result in zip(jobs, results):
            if not isinstance(result, Exception):
                values[name] = result
    active = [
        row for row in (live.get("rows") or [])
        if str(row.get("stage") or "") not in {"available", "complete", "failed", "declined"}
    ][:12]
    updates = [row for row in (stack.get("services") or []) if row.get("update_available")]
    return {
        "board": board,
        "boards": boards(),
        "home_board": home_board(),
        "widget_catalogue": [{"key": key, **value} for key, value in WIDGETS.items()],
        "services": _service_tiles(stack),
        "active_requests": active,
        "jellyfin_sessions": values["jellyfin_sessions"],
        "recent_media": values["recent_media"],
        "downloads": values["downloads"],
        "calendar": values["calendar"],
        "metrics": _record_metrics(stack),
        "updates": updates,
        "stack": stack,
        "orchestrator": orchestrator,
        "janitor": janitor,
        "zurg": zurg,
        "public_domain": public_domain(),
        "service_links": service_links(),
    }
