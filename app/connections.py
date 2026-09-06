from __future__ import annotations

from dataclasses import dataclass

from .config import settings
from .db import setting_get, setting_set


@dataclass(frozen=True)
class Connection:
    service: str
    url: str
    api_key: str


SERVICES = ("radarr", "sonarr", "lidarr", "prowlarr", "jellyfin", "seerr")


def _env_pair(service: str) -> tuple[str, str]:
    service = service.lower()
    pairs = {
        "radarr": (settings.radarr_url, settings.radarr_api_key),
        "sonarr": (settings.sonarr_url, settings.sonarr_api_key),
        "lidarr": (settings.lidarr_url, settings.lidarr_api_key),
        "prowlarr": (settings.prowlarr_url, settings.prowlarr_api_key),
        "jellyfin": (settings.jellyfin_url, settings.jellyfin_api_key),
        "seerr": (settings.seerr_url, settings.seerr_api_key),
    }
    if service not in pairs:
        raise KeyError(service)
    return pairs[service]


def get_connection(service: str) -> Connection:
    env_url, env_key = _env_pair(service)
    prefix = f"connection.{service.lower()}"
    url = setting_get(prefix + ".url", "") or env_url
    key = setting_get(prefix + ".api_key", "") or env_key
    return Connection(service.lower(), url.rstrip("/"), key)


def save_connection(service: str, url: str, api_key: str) -> None:
    if service.lower() not in SERVICES:
        raise ValueError("Unsupported service")
    prefix = f"connection.{service.lower()}"
    setting_set(prefix + ".url", url.strip())
    if api_key and api_key not in {"********", "••••••••"}:
        setting_set(prefix + ".api_key", api_key.strip(), True)
