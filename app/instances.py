from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from .config import settings
from .connections import get_connection, get_instance_override

DATA_RE = re.compile(r"--data=(\S+)")
PORT_RE = re.compile(r"--port=(\d+)")


@dataclass
class ArrInstance:
    service: str
    instance: str
    pid: int
    data_dir: str
    port: int
    api_key: str
    url: str
    root: str | None
    destination_key: str | None

    def dict(self):
        d = asdict(self)
        d["api_key"] = "***" if self.api_key else ""
        return d


def _read_cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def _config_values(pid: int, data_dir: str) -> tuple[int | None, str]:
    cfg = Path(f"/proc/{pid}/root{data_dir}/config.xml")
    try:
        root = ET.parse(cfg).getroot()
    except Exception:
        return None, ""
    port = None
    key = ""
    p = root.find(".//Port")
    if p is not None and (p.text or "").strip().isdigit():
        port = int((p.text or "").strip())
    k = root.find(".//ApiKey")
    if k is not None:
        key = (k.text or "").strip()
    return port, key


def destination_for(service: str, instance: str) -> tuple[str | None, str | None]:
    s = instance.lower().replace("_", "-")
    if service == "radarr":
        mapping = {
            "nzbdav": ("default", settings.radarr_default_root),
            "kids-nzbdav": ("kids", settings.radarr_kids_root),
            "christmas-nzbdav": ("christmas", settings.radarr_christmas_root),
            "halloween-nzbdav": ("halloween", settings.radarr_halloween_root),
            "easter-nzbdav": ("easter", settings.radarr_easter_root),
        }
    elif service == "sonarr":
        mapping = {
            "nzbdav": ("default", settings.sonarr_default_root),
            "kids-nzbdav": ("kids", settings.sonarr_kids_root),
            "netflix-nzbdav": ("netflix", settings.sonarr_netflix_root),
            "disneyplus-nzbdav": ("disney", settings.sonarr_disney_root),
            "disney-nzbdav": ("disney", settings.sonarr_disney_root),
            "amazon-nzbdav": ("amazon", settings.sonarr_amazon_root),
            "appletv-nzbdav": ("apple", settings.sonarr_apple_root),
            "apple-nzbdav": ("apple", settings.sonarr_apple_root),
            "bbc-nzbdav": ("bbc", settings.sonarr_bbc_root),
        }
    elif service == "lidarr":
        mapping = {"nzbdav": ("default", settings.lidarr_root)}
    else:
        mapping = {}
    return mapping.get(s, (None, None))


def discover_instances() -> list[ArrInstance]:
    primary_radarr = get_connection("radarr")
    host = urlsplit(primary_radarr.url).hostname or settings.arr_host
    scheme = urlsplit(primary_radarr.url).scheme or "http"
    found: list[ArrInstance] = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        pid = int(proc.name)
        cmd = _read_cmdline(pid)
        if not cmd:
            continue
        service = None
        if "/opt/radarr/" in cmd and "Radarr" in cmd:
            service = "radarr"
        elif "/opt/sonarr/" in cmd and "Sonarr" in cmd:
            service = "sonarr"
        elif "/opt/lidarr/" in cmd and "Lidarr" in cmd:
            service = "lidarr"
        if not service:
            continue
        dm = DATA_RE.search(cmd)
        if not dm:
            continue
        data_dir = dm.group(1).rstrip("/")
        instance = Path(data_dir).name
        port, key = _config_values(pid, data_dir)
        pm = PORT_RE.search(cmd)
        if not port and pm:
            port = int(pm.group(1))
        if not port:
            # fallback to the known primary app ports
            port = {"radarr": 7878, "sonarr": 8989, "lidarr": 8686}[service]
        dest_key, root = destination_for(service, instance)
        default_url = f"{scheme}://{host}:{port}"
        # The DUMB mount namespace may expose config.xml but not always in a way
        # a Docker process can parse. Prefer explicit UI overrides; for the main
        # nzbdav instance fall back to the configured environment/API key.
        override = get_instance_override(service, instance)
        if override:
            default_url = override.url or default_url
            key = override.api_key or key
        elif instance == "nzbdav":
            primary = get_connection(service)
            default_url = primary.url or default_url
            key = key or primary.api_key
        found.append(ArrInstance(service, instance, pid, data_dir, port, key, default_url, root, dest_key))
    # one row per service/instance, newest PID wins if duplicate process is shutting down
    dedup: dict[tuple[str, str], ArrInstance] = {}
    for item in sorted(found, key=lambda x: x.pid):
        dedup[(item.service, item.instance)] = item
    return sorted(dedup.values(), key=lambda x: (x.service, x.instance))


def get_instance(service: str, destination_key: str | None = None) -> ArrInstance | None:
    instances = [x for x in discover_instances() if x.service == service]
    if destination_key is not None:
        exact = [x for x in instances if x.destination_key == destination_key]
        if exact:
            return exact[0]
    # Prefer legacy/main nzbdav instance.
    for x in instances:
        if x.instance == "nzbdav":
            return x
    return instances[0] if instances else None
