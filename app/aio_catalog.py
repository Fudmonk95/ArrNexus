from __future__ import annotations

from copy import deepcopy
from typing import Any

# ArrNexus AIO project catalogue.
#
# This catalogue is intentionally separate from the transitional Docker
# MediaStack catalogue.  The AIO runtime will eventually run selected projects
# as supervised child services inside the single ArrNexus container, while the
# current production server can be adopted service-by-service.
#
# "availability":
#   ready      - already present on the current server and can be adopted first
#   target     - part of the AIO target catalogue; installer recipe still needs
#                to be validated before it is exposed as installable
#   retired    - retained for import/compatibility only, not recommended for a
#                new installation
#
# Nothing marked "target" should be silently installed.  The guided installer
# must only enable a project once a tested installer recipe exists.

AIO_PROJECTS: dict[str, dict[str, Any]] = {
    "arrnexus": {
        "name": "ArrNexus",
        "category": "Core",
        "description": "Control plane, guided setup, orchestration and MediaStack UI.",
        "upstream": "https://github.com/Fudmonk95/ArrNexus",
        "port": 8484,
        "required": True,
        "default": True,
        "availability": "ready",
    },
    "zurg": {
        "name": "Zurg",
        "category": "Storage / Debrid",
        "description": "Debrid-backed WebDAV/filesystem layer.",
        "upstream": "https://github.com/debridmediamanager/zurg-testing",
        "port": 9999,
        "default": True,
        "availability": "ready",
    },
    "rclone": {
        "name": "rclone",
        "category": "Storage / Debrid",
        "description": "Mount and transfer layer used by storage providers.",
        "upstream": "https://github.com/rclone/rclone",
        "port": None,
        "default": True,
        "availability": "target",
    },
    "altmount": {
        "name": "AltMount",
        "category": "Storage / Debrid",
        "description": "Alternative debrid mount service.",
        "upstream": "https://github.com/javi11/altmount",
        "port": 8088,
        "default": False,
        "availability": "target",
    },
    "cli-debrid": {
        "name": "cli_debrid",
        "category": "Storage / Debrid",
        "description": "Debrid automation and queue tooling.",
        "upstream": "https://github.com/godver3/cli_debrid",
        "port": 5000,
        "default": False,
        "availability": "target",
    },
    "decypharr": {
        "name": "Decypharr",
        "category": "Storage / Debrid",
        "description": "Debrid media integration layer.",
        "upstream": "https://github.com/sirrobot01/decypharr",
        "port": 8282,
        "default": False,
        "availability": "target",
    },
    "infinidysk": {
        "name": "InfiniDysk",
        "category": "Storage / Debrid",
        "description": "Alternative media filesystem/indexing layer.",
        "upstream": "https://github.com/infinidysk/infinidysk",
        "port": 3000,
        "default": False,
        "availability": "target",
    },
    "sonarr": {
        "name": "Sonarr",
        "category": "Arr Automation",
        "description": "TV automation.",
        "upstream": "https://github.com/Sonarr/Sonarr",
        "port": 8989,
        "default": True,
        "availability": "ready",
    },
    "radarr": {
        "name": "Radarr",
        "category": "Arr Automation",
        "description": "Movie automation.",
        "upstream": "https://github.com/Radarr/Radarr",
        "port": 7878,
        "default": True,
        "availability": "ready",
    },
    "lidarr": {
        "name": "Lidarr",
        "category": "Arr Automation",
        "description": "Music automation.",
        "upstream": "https://github.com/Lidarr/Lidarr",
        "port": 8686,
        "default": True,
        "availability": "ready",
    },
    "prowlarr": {
        "name": "Prowlarr",
        "category": "Arr Automation",
        "description": "Indexer management and Arr synchronisation.",
        "upstream": "https://github.com/Prowlarr/Prowlarr",
        "port": 9696,
        "default": True,
        "availability": "ready",
    },
    "whisparr": {
        "name": "Whisparr",
        "category": "Arr Automation",
        "description": "Additional Servarr-family automation service.",
        "upstream": "https://github.com/Whisparr/Whisparr",
        "port": 6969,
        "default": False,
        "availability": "ready",
    },
    "readarr": {
        "name": "Readarr",
        "category": "Arr Automation",
        "description": "Retired ebook/audiobook Servarr application; import compatibility only.",
        "upstream": "https://github.com/Readarr/Readarr",
        "port": 8787,
        "default": False,
        "availability": "retired",
        "notes": "Upstream was retired and archived in 2025. Do not offer for a normal fresh install.",
    },
    "bazarr": {
        "name": "Bazarr",
        "category": "Automation",
        "description": "Subtitle automation for Sonarr and Radarr.",
        "upstream": "https://github.com/morpheus65535/bazarr",
        "port": 6767,
        "default": False,
        "availability": "ready",
    },
    "neutarr": {
        "name": "NeutArr",
        "category": "Automation",
        "description": "Automated post-processing/search workflows.",
        "upstream": "https://github.com/I-am-PUID-0/NeutArr",
        "port": 9705,
        "default": False,
        "availability": "ready",
    },
    "maintainerr": {
        "name": "Maintainerr",
        "category": "Automation",
        "description": "Scheduled library maintenance and cleanup.",
        "upstream": "https://github.com/Maintainerr/Maintainerr",
        "port": 6246,
        "default": False,
        "availability": "ready",
    },
    "profilarr": {
        "name": "Profilarr",
        "category": "Automation",
        "description": "Quality profile and custom-format management.",
        "upstream": "https://github.com/Dictionarry-Hub/profilarr",
        "port": 6868,
        "default": False,
        "availability": "ready",
    },
    "profilarr-parser": {
        "name": "Profilarr Parser",
        "category": "Automation",
        "description": "Companion parser used by the current Profilarr deployment.",
        "upstream": "https://github.com/Dictionarry-Hub/profilarr",
        "port": None,
        "default": False,
        "availability": "ready",
        "hidden": True,
    },
    "pulsarr": {
        "name": "Pulsarr",
        "category": "Automation",
        "description": "Advanced release tracking and filtering.",
        "upstream": "https://github.com/jamcalli/Pulsarr",
        "port": 3003,
        "default": False,
        "availability": "target",
    },
    "aiostreams": {
        "name": "AIOStreams",
        "category": "Streaming / Discovery",
        "description": "Streaming aggregation frontend/API.",
        "upstream": "https://github.com/Viren070/AIOStreams",
        "port": 3006,
        "default": False,
        "availability": "target",
    },
    "seerrng": {
        "name": "Seerr",
        "category": "Requests",
        "description": "Media request and discovery frontend.",
        "upstream": "https://github.com/seerr-team/seerr",
        "port": 5055,
        "default": True,
        "availability": "ready",
    },
    "riven": {
        "name": "Riven",
        "category": "Requests / Automation",
        "description": "Alternative media automation platform.",
        "upstream": "https://github.com/rivenmedia/riven",
        "port": 3000,
        "default": False,
        "availability": "target",
    },
    "jellyfin": {
        "name": "Jellyfin",
        "category": "Media Server",
        "description": "Open-source media server.",
        "upstream": "https://github.com/jellyfin/jellyfin",
        "port": 8096,
        "default": True,
        "availability": "ready",
    },
    "plex": {
        "name": "Plex Media Server",
        "category": "Media Server",
        "description": "Plex media server.",
        "upstream": "https://www.plex.tv/media-server-downloads/",
        "port": 32400,
        "default": False,
        "availability": "target",
    },
    "emby": {
        "name": "Emby Media Server",
        "category": "Media Server",
        "description": "Emby media server.",
        "upstream": "https://emby.media/",
        "port": 8096,
        "default": False,
        "availability": "target",
    },
    "tautulli": {
        "name": "Tautulli",
        "category": "Media Server",
        "description": "Plex monitoring and statistics.",
        "upstream": "https://github.com/Tautulli/Tautulli",
        "port": 8181,
        "default": False,
        "availability": "target",
    },
    "mediastorm": {
        "name": "mediastorm",
        "category": "Media Ecosystem",
        "description": "All-in-one media hub / streaming destination.",
        "upstream": "https://github.com/godver3/mediastorm",
        "port": None,
        "default": False,
        "availability": "target",
    },
    "postgresql": {
        "name": "PostgreSQL",
        "category": "Database",
        "description": "Shared PostgreSQL runtime for services that support it.",
        "upstream": "https://www.postgresql.org/",
        "port": 5432,
        "default": False,
        "availability": "ready",
        "hidden": True,
    },
    "lidarr-postgres": {
        "name": "Lidarr PostgreSQL",
        "category": "Database",
        "description": "Current Lidarr PostgreSQL instance.",
        "upstream": "https://www.postgresql.org/",
        "port": 5432,
        "default": False,
        "availability": "ready",
        "hidden": True,
    },
    "pgadmin": {
        "name": "pgAdmin 4",
        "category": "Database",
        "description": "PostgreSQL administration UI.",
        "upstream": "https://www.pgadmin.org/",
        "port": 5050,
        "default": False,
        "availability": "target",
    },
    "dmbdb": {
        "name": "dmbdb",
        "category": "Database / Discovery",
        "description": "DMB database component available as an optional project.",
        "upstream": "https://github.com/nicocapalbo/dmbdb",
        "port": None,
        "default": False,
        "availability": "target",
    },
    "phalanx-db": {
        "name": "phalanx_db",
        "category": "Database / Discovery",
        "description": "Hyperswarm-backed database service.",
        "upstream": "https://github.com/godver3/phalanx_db_hyperswarm",
        "port": 8888,
        "default": False,
        "availability": "target",
    },
    "zilean": {
        "name": "Zilean",
        "category": "Indexing / Discovery",
        "description": "Debrid index/search service.",
        "upstream": "https://github.com/iPromKnight/zilean",
        "port": 8182,
        "default": False,
        "availability": "target",
    },
    "authelia": {
        "name": "Authelia",
        "category": "Infrastructure",
        "description": "Identity provider and access control.",
        "upstream": "https://github.com/authelia/authelia",
        "port": 9091,
        "default": False,
        "availability": "target",
    },
    "traefik-proxy-admin": {
        "name": "Traefik Proxy Admin",
        "category": "Infrastructure",
        "description": "Traefik administration and route management.",
        "upstream": "https://github.com/I-am-PUID-0/traefik-proxy-admin",
        "port": 3004,
        "default": False,
        "availability": "target",
    },
    "cloudflared": {
        "name": "cloudflared",
        "category": "Infrastructure",
        "description": "Cloudflare Tunnel client.",
        "upstream": "https://github.com/cloudflare/cloudflared",
        "port": None,
        "default": False,
        "availability": "target",
    },
    "homarr": {
        "name": "Homarr",
        "category": "Dashboard",
        "description": "Existing optional dashboard; ArrNexus can replace most dashboard duties.",
        "upstream": "https://github.com/homarr-labs/homarr",
        "port": 7575,
        "default": False,
        "availability": "ready",
    },
    "glances": {
        "name": "Glances",
        "category": "Monitoring",
        "description": "Host/container metrics dashboard currently installed on this server.",
        "upstream": "https://github.com/nicolargo/glances",
        "port": 61208,
        "default": False,
        "availability": "ready",
    },
}


def projects(*, include_hidden: bool = False, include_retired: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, value in AIO_PROJECTS.items():
        if value.get("hidden") and not include_hidden:
            continue
        if value.get("availability") == "retired" and not include_retired:
            continue
        item = deepcopy(value)
        item["key"] = key
        rows.append(item)
    return rows


def grouped_projects(*, include_retired: bool = True) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in projects(include_retired=include_retired):
        groups.setdefault(str(item.get("category") or "Other"), []).append(item)
    return groups


def project(key: str) -> dict[str, Any] | None:
    value = AIO_PROJECTS.get(str(key or "").strip())
    if value is None:
        return None
    item = deepcopy(value)
    item["key"] = str(key)
    return item


def ready_project_keys() -> set[str]:
    return {key for key, value in AIO_PROJECTS.items() if value.get("availability") == "ready"}
