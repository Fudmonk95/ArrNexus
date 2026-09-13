from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from .connections import get_connection
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
    "prowlarr": "Prowlarr",
    "seerrng": "SeerrNG",
    "jellyfin": "Jellyfin",
    "bazarr": "Bazarr",
    "whisparr": "Whisparr",
    "neutarr": "NeutArr",
    "maintainerr": "Maintainerr",
    "profilarr": "Profilarr",
    "homarr": "Homarr",
}

SUBDOMAIN_NAMES = {
    "seerrng": "seerr",
}

WIDGETS: dict[str, dict[str, str]] = {
    "service-grid": {"name": "Services", "description": "Health, CPU/RAM and one-click links to your media services."},
    "active-requests": {"name": "Active requests", "description": "Current Seerr/Arr/Zurg acquisition flow."},
    "jellyfin-streams": {"name": "Jellyfin streams", "description": "Current playback sessions and transcodes."},
    "recent-media": {"name": "Recent media", "description": "Recently added Jellyfin movies and episodes."},
    "docker-stats": {"name": "Docker stats", "description": "Container CPU, RAM and restart state from MediaStack."},
    "updates": {"name": "Updates", "description": "Containers with a newer image digest available."},
    "missing-queue": {"name": "Missing & queued media", "description": "Missing-media and queue-janitor state."},
    "zurg-health": {"name": "Zurg health", "description": "Filesystem and resource-pressure summary."},
}

DEFAULT_BOARDS = [
    {
        "id": "overview",
        "name": "Overview",
        "description": "The original ArrNexus orchestration dashboard.",
        "builtin": True,
        "widgets": [],
    },
    {
        "id": "media-hub",
        "name": "Media Hub",
        "description": "Homarr-style native media dashboard powered by ArrNexus integrations.",
        "builtin": False,
        "widgets": [
            "service-grid",
            "active-requests",
            "jellyfin-streams",
            "recent-media",
            "docker-stats",
            "updates",
            "missing-queue",
            "zurg-health",
        ],
    },
]


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return cleaned[:64] or "board"


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
        widgets = [str(x) for x in (row.get("widgets") or []) if str(x) in WIDGETS]
        out.append({
            "id": bid,
            "name": str(row.get("name") or bid.replace("-", " ").title()),
            "description": str(row.get("description") or ""),
            "builtin": bool(row.get("builtin")) or bid == "overview",
            "widgets": widgets,
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
    selected = [key for key in widgets if key in WIDGETS]
    row = {
        "id": bid,
        "name": str(name or bid.replace("-", " ").title()).strip(),
        "description": str(description or "").strip(),
        "builtin": False,
        "widgets": selected,
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
    value = re.sub(r"^https?://", "", value).strip("/")
    return value


def set_public_domain(value: str) -> str:
    domain = re.sub(r"^https?://", "", str(value or "").strip().lower()).strip("/")
    setting_set(PUBLIC_DOMAIN_KEY, domain)
    if domain:
        current = setting_get("app.public_url", "").strip()
        if not current or "example.com" in current or current.startswith("http://"):
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
    if not domain:
        return ""
    subdomain = SUBDOMAIN_NAMES.get(key, key)
    return f"https://{subdomain}.{domain}"


def set_service_url(service: str, value: str) -> None:
    key = str(service or "").lower()
    if key not in SERVICE_KEYS:
        raise ValueError("Unsupported dashboard service")
    setting_set(f"dashboard.url.{key}", str(value or "").strip().rstrip("/"))


def service_links() -> list[dict[str, str]]:
    return [
        {
            "key": key,
            "name": SERVICE_LABELS.get(key, key.title()),
            "url": service_url(key),
            "override": service_override(key),
        }
        for key in SERVICE_KEYS
    ]


def board_api() -> dict[str, Any]:
    return {
        "ok": True,
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
        rows.append({
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
        })
    return rows


async def jellyfin_recent(limit: int = 12) -> list[dict[str, Any]]:
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
                "Fields": "DateCreated,SeriesName,ProductionYear",
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
        }
        for item in (data.get("Items") or [])
    ]


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
    sessions: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    if "jellyfin-streams" in widgets:
        sessions = await jellyfin_sessions()
    if "recent-media" in widgets:
        recent = await jellyfin_recent()
    active = [
        row for row in (live.get("rows") or [])
        if str(row.get("stage") or "") not in {"available", "complete", "failed", "declined"}
    ][:12]
    updates = [row for row in (stack.get("services") or []) if row.get("update_available")]
    return {
        "board": board,
        "boards": boards(),
        "home_board": home_board(),
        "services": _service_tiles(stack),
        "active_requests": active,
        "jellyfin_sessions": sessions,
        "recent_media": recent,
        "updates": updates,
        "stack": stack,
        "orchestrator": orchestrator,
        "janitor": janitor,
        "zurg": zurg,
        "public_domain": public_domain(),
        "service_links": service_links(),
    }
