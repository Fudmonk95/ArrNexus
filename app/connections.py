from __future__ import annotations
from dataclasses import dataclass
from .config import settings
from .db import setting_get, setting_set

@dataclass
class Connection:
    service: str
    url: str
    api_key: str


def _env_pair(service: str) -> tuple[str, str]:
    service = service.lower()
    if service == "radarr": return settings.radarr_url, settings.radarr_api_key
    if service == "sonarr": return settings.sonarr_url, settings.sonarr_api_key
    if service == "lidarr": return settings.lidarr_url, settings.lidarr_api_key
    if service == "prowlarr": return settings.prowlarr_url, settings.prowlarr_api_key
    if service == "jellyfin": return settings.jellyfin_url, settings.jellyfin_api_key
    raise KeyError(service)


def get_connection(service: str, instance: str = "main") -> Connection:
    env_url, env_key = _env_pair(service)
    prefix = f"connection.{service.lower()}.{instance}"
    url = setting_get(prefix + ".url", "") or env_url
    key = setting_get(prefix + ".api_key", "") or env_key
    return Connection(service.lower(), url.rstrip('/'), key)


def save_connection(service: str, url: str, api_key: str, instance: str = "main"):
    prefix = f"connection.{service.lower()}.{instance}"
    setting_set(prefix + ".url", url.strip(), False)
    if api_key and api_key != "********":
        setting_set(prefix + ".api_key", api_key.strip(), True)


def get_instance_override(service: str, instance: str) -> Connection | None:
    prefix = f"connection.{service.lower()}.{instance}"
    url = setting_get(prefix + ".url", "")
    key = setting_get(prefix + ".api_key", "")
    if not url and not key:
        return None
    env_url, env_key = _env_pair(service)
    return Connection(service.lower(), (url or env_url).rstrip('/'), key or env_key)
