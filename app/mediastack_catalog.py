from __future__ import annotations

from copy import deepcopy
from typing import Any

# ArrNexus deliberately borrows the operational ideas of guided setup,
# per-service lifecycle controls and safe updates from mature media-stack
# managers, but keeps a service-per-container architecture. This avoids the
# single-container mount/process coupling that caused trouble on this server.

SERVICE_CATALOG: dict[str, dict[str, Any]] = {
    "zurg": {
        "name": "Zurg",
        "category": "Storage",
        "description": "Debrid/Usenet filesystem and WebDAV layer. Owns the FUSE mount at /zurg_mnt.",
        "image": "ghcr.io/debridmediamanager/zurg:latest",
        "port": 9999,
        "default": True,
        "required": True,
        "dependencies": [],
        "config_target": "/config",
        "managed_config": "zurg",
        "uses_zurg_mount": "owner",
        "update_policy": "manual",
        "update_risk": "high",
        "notes": "Updates temporarily remove the FUSE mount, so Zurg is never auto-updated by default.",
    },
    "sonarr": {
        "name": "Sonarr",
        "category": "Arr",
        "description": "TV automation.",
        "image": "lscr.io/linuxserver/sonarr:latest",
        "port": 8989,
        "default": True,
        "dependencies": ["zurg"],
        "config_target": "/config",
        "managed_config": "sonarr",
        "uses_zurg_mount": "consumer",
        "update_policy": "automatic",
        "update_risk": "normal",
    },
    "radarr": {
        "name": "Radarr",
        "category": "Arr",
        "description": "Movie automation.",
        "image": "lscr.io/linuxserver/radarr:latest",
        "port": 7878,
        "default": True,
        "dependencies": ["zurg"],
        "config_target": "/config",
        "managed_config": "radarr",
        "uses_zurg_mount": "consumer",
        "update_policy": "automatic",
        "update_risk": "normal",
    },
    "lidarr": {
        "name": "Lidarr",
        "category": "Arr",
        "description": "Music automation.",
        "image": "lscr.io/linuxserver/lidarr:latest",
        "port": 8686,
        "default": True,
        "dependencies": ["zurg", "lidarr-postgres"],
        "config_target": "/config",
        "managed_config": "lidarr",
        "uses_zurg_mount": "consumer",
        "update_policy": "automatic",
        "update_risk": "normal",
    },
    "lidarr-postgres": {
        "name": "Lidarr PostgreSQL",
        "category": "Database",
        "description": "Existing PostgreSQL dependency for Lidarr.",
        "image": "postgres:16",
        "port": 5432,
        "default": True,
        "hidden": True,
        "dependencies": [],
        "managed_config": "lidarr-postgres",
        "uses_zurg_mount": "none",
        "update_policy": "manual",
        "update_risk": "high",
    },
    "prowlarr": {
        "name": "Prowlarr",
        "category": "Arr",
        "description": "Indexer management and Arr synchronisation.",
        "image": "lscr.io/linuxserver/prowlarr:latest",
        "port": 9696,
        "default": True,
        "dependencies": [],
        "config_target": "/config",
        "managed_config": "prowlarr",
        "uses_zurg_mount": "none",
        "update_policy": "automatic",
        "update_risk": "normal",
    },
    "seerrng": {
        "name": "SeerrNG",
        "category": "Requests",
        "description": "Request/discovery frontend feeding Sonarr and Radarr.",
        "image": "snapetech/seerrng:latest",
        "port": 5055,
        "default": True,
        "dependencies": ["sonarr", "radarr"],
        "config_target": "/app/config",
        "managed_config": "seerrng",
        "uses_zurg_mount": "none",
        "update_policy": "automatic",
        "update_risk": "normal",
    },
    "jellyfin": {
        "name": "Jellyfin",
        "category": "Media Server",
        "description": "Primary media server with optional /dev/dri hardware acceleration.",
        "image": "ghcr.io/jellyfin/jellyfin:10.11.11",
        "port": 8096,
        "default": True,
        "dependencies": ["zurg"],
        "config_target": "/config",
        "managed_config": "jellyfin",
        "uses_zurg_mount": "consumer",
        "gpu": True,
        "update_policy": "manual",
        "update_risk": "high",
        "notes": "Pinned by default so Jellyfin 12 is not installed until plugin compatibility is approved.",
    },
    "bazarr": {
        "name": "Bazarr",
        "category": "Optional",
        "description": "Subtitle automation.",
        "image": "lscr.io/linuxserver/bazarr:latest",
        "port": 6767,
        "default": False,
        "dependencies": ["sonarr", "radarr", "zurg"],
        "config_target": "/config",
        "managed_config": "bazarr",
        "uses_zurg_mount": "consumer",
        "update_policy": "automatic",
        "update_risk": "normal",
    },
    "whisparr": {
        "name": "Whisparr",
        "category": "Optional",
        "description": "Additional Arr instance currently present on the server.",
        "image": "ghcr.io/thespad/whisparr:v3.5.0-release.1585-spad50",
        "port": 6969,
        "default": False,
        "dependencies": ["zurg", "prowlarr"],
        "config_target": "/config",
        "managed_config": "whisparr",
        "uses_zurg_mount": "consumer",
        "update_policy": "manual",
        "update_risk": "normal",
    },
    "neutarr": {
        "name": "NeutArr",
        "category": "Optional",
        "description": "Backlog/search automation for Arr services.",
        "image": "iampuid0/neutarr:1.11.1",
        "port": 9705,
        "default": False,
        "dependencies": ["sonarr", "radarr"],
        "config_target": "/config",
        "managed_config": "neutarr",
        "uses_zurg_mount": "none",
        "update_policy": "manual",
        "update_risk": "normal",
    },
    "maintainerr": {
        "name": "Maintainerr",
        "category": "Optional",
        "description": "Rule-based library review and cleanup.",
        "image": "ghcr.io/maintainerr/maintainerr:3.27.0",
        "port": 6246,
        "default": False,
        "dependencies": ["jellyfin"],
        "config_target": "/opt/data",
        "managed_config": "maintainerr",
        "uses_zurg_mount": "none",
        "update_policy": "manual",
        "update_risk": "normal",
    },
    "profilarr": {
        "name": "Profilarr",
        "category": "Optional",
        "description": "Profile/custom-format management for Sonarr and Radarr.",
        "image": "ghcr.io/dictionarry-hub/profilarr:develop",
        "port": 6868,
        "default": False,
        "dependencies": ["sonarr", "radarr", "profilarr-parser"],
        "config_target": "/config",
        "managed_config": "profilarr",
        "uses_zurg_mount": "none",
        "update_policy": "manual",
        "update_risk": "normal",
    },
    "profilarr-parser": {
        "name": "Profilarr Parser",
        "category": "Optional",
        "description": "Required companion parser for Profilarr.",
        "image": "ghcr.io/dictionarry-hub/profilarr-parser:develop",
        "port": None,
        "default": False,
        "hidden": True,
        "dependencies": [],
        "managed_config": "profilarr-parser",
        "uses_zurg_mount": "none",
        "update_policy": "manual",
        "update_risk": "normal",
    },
    "homarr": {
        "name": "Homarr",
        "category": "Optional",
        "description": "Optional dashboard. ArrNexus itself can replace most of its operational role.",
        "image": "ghcr.io/homarr-labs/homarr:latest",
        "port": 7575,
        "default": False,
        "dependencies": [],
        "config_target": "/appdata",
        "managed_config": "homarr",
        "uses_zurg_mount": "none",
        "update_policy": "automatic",
        "update_risk": "normal",
    },
}

ALWAYS_MANAGED = {"arrnexus", "arrnexus-stack-agent"}
AUTO_UPDATE_BLOCKED_DEFAULT = {"arrnexus", "zurg", "jellyfin", "lidarr-postgres"}


def catalog() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, value in SERVICE_CATALOG.items():
        item = deepcopy(value)
        item["key"] = key
        rows.append(item)
    return rows


def service(key: str) -> dict[str, Any] | None:
    value = SERVICE_CATALOG.get(str(key or "").strip())
    if value is None:
        return None
    item = deepcopy(value)
    item["key"] = str(key)
    return item


def expand_dependencies(selected: list[str] | set[str]) -> list[str]:
    result = {str(x) for x in selected if str(x) in SERVICE_CATALOG}
    changed = True
    while changed:
        changed = False
        for key in list(result):
            for dep in SERVICE_CATALOG[key].get("dependencies") or []:
                if dep in SERVICE_CATALOG and dep not in result:
                    result.add(dep)
                    changed = True
    return sorted(result)


def grouped_catalog() -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in catalog():
        if item.get("hidden"):
            continue
        groups.setdefault(str(item.get("category") or "Other"), []).append(item)
    return groups
