from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import httpx

from .config import settings
from .db import setting_get, setting_set


@dataclass(frozen=True)
class ConnectorDefinition:
    key: str
    name: str
    category: str
    description: str
    default_url: str = ""
    health_paths: tuple[str, ...] = ("/",)
    capabilities: tuple[str, ...] = ()
    auth_header: str = "X-Api-Key"
    docs_url: str = ""
    native_page: str = ""


BUILTINS: tuple[ConnectorDefinition, ...] = (
    ConnectorDefinition(
        "infinidysk", "InfiniDysk", "Core", "Usenet streaming, SAB-compatible queue, WebDAV and provider telemetry.",
        "http://host.docker.internal:3000", ("/healthz", "/"),
        ("health", "queue", "history", "metrics", "usenet"), "X-Api-Key",
        "https://www.infinidysk.com/", "/infinidysk",
    ),
    ConnectorDefinition(
        "dumb", "DUMB", "Core", "Distributed Unlimited Media Bridge stack controller and service topology.",
        "", ("/api/health", "/health", "/"),
        ("topology", "services", "health", "updates"), "X-Api-Key", "https://dumbarr.com/",
    ),
    ConnectorDefinition(
        "decypharr", "Decypharr", "Core", "Debrid download-client bridge and mount service.",
        "http://host.docker.internal:8282", ("/",),
        ("debrid", "queue", "mount"), "X-Api-Key", "https://github.com/sirrobot01/decypharr",
    ),
    ConnectorDefinition(
        "altmount", "AltMount", "Core", "Alternative Usenet virtual filesystem/WebDAV provider.",
        "http://host.docker.internal:8080", ("/api/health", "/health", "/"),
        ("usenet", "webdav", "mount", "health"), "X-Api-Key", "https://github.com/javi11/altmount",
    ),
    ConnectorDefinition(
        "profilarr", "Profilarr", "Quality", "Quality profiles, custom formats, testing and multi-Arr configuration sync.",
        "http://host.docker.internal:6868", ("/api/health", "/health", "/"),
        ("quality", "custom-formats", "sync", "testing"), "X-Api-Key", "https://github.com/Dictionarry-Hub/profilarr",
    ),
    ConnectorDefinition(
        "neutarr", "NeutArr", "Automation", "Missing-media hunter and quality-upgrade search automation.",
        "http://host.docker.internal:9705", ("/api/health", "/"),
        ("missing", "upgrades", "search", "health"), "X-Api-Key", "https://github.com/I-am-PUID-0/NeutArr",
    ),
    ConnectorDefinition(
        "cleanuparr", "Cleanuparr", "Automation", "Download queue cleanup, strike handling, missing searches and orphan detection.",
        "http://host.docker.internal:11011", ("/api/health", "/health", "/"),
        ("queue", "cleanup", "missing", "upgrades", "security"), "X-Api-Key", "https://github.com/Cleanuparr/Cleanuparr",
    ),
    ConnectorDefinition(
        "maintainerr", "Maintainerr", "Lifecycle", "Rule-based library retention, Leaving Soon collections and cleanup workflows.",
        "http://host.docker.internal:6246", ("/api/health/ready", "/api/health", "/"),
        ("lifecycle", "collections", "rules", "storage"), "X-Api-Key", "https://github.com/Maintainerr/Maintainerr",
    ),
    ConnectorDefinition(
        "bazarr", "Bazarr", "Media", "Subtitle monitoring, searching and upgrades for Radarr/Sonarr libraries.",
        "http://host.docker.internal:6767", ("/api/system/status", "/"),
        ("subtitles", "health", "history"), "X-API-KEY", "https://github.com/morpheus65535/bazarr",
    ),
    ConnectorDefinition(
        "streamystats", "Streamystats", "Analytics", "Jellyfin playback analytics and data visualisation.",
        "", ("/api/health", "/health", "/"),
        ("analytics", "playback", "users", "library"), "X-Api-Key", "https://github.com/fredrikburmester/streamystats",
    ),
    ConnectorDefinition(
        "zilean", "Zilean", "Search", "DMM-sourced Debrid content index exposed through Torznab.",
        "", ("/health", "/"),
        ("search", "debrid", "torznab"), "X-Api-Key", "https://github.com/iPromKnight/zilean",
    ),
    ConnectorDefinition(
        "riven", "Riven", "Core", "Debrid automation, VFS library profiles and metadata-aware virtual libraries.",
        "", ("/api/health", "/health", "/"),
        ("debrid", "vfs", "profiles", "search"), "X-Api-Key", "https://github.com/rivenmedia/riven",
    ),
    ConnectorDefinition(
        "pulsarr", "Pulsarr", "Requests", "Watchlist routing, approvals, quotas and user-aware request automation.",
        "http://host.docker.internal:3003", ("/api/health", "/"),
        ("requests", "routing", "approvals", "watchlists"), "X-Api-Key", "https://github.com/jamcalli/Pulsarr",
    ),
)

PLUGIN_DIR = Path(settings.db_path).resolve().parent / "connectors"


def _plugin_defs() -> list[ConnectorDefinition]:
    out: list[ConnectorDefinition] = []
    try:
        PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return out
    for path in sorted(PLUGIN_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            key = str(raw.get("key") or path.stem).strip().lower().replace(" ", "-")
            name = str(raw.get("name") or key).strip()
            if not key or not name:
                continue
            health = raw.get("health_paths") or [raw.get("health_path") or "/"]
            if isinstance(health, str):
                health = [health]
            caps = raw.get("capabilities") or []
            if isinstance(caps, str):
                caps = [x.strip() for x in caps.split(",") if x.strip()]
            out.append(ConnectorDefinition(
                key=f"plugin-{key}",
                name=name,
                category=str(raw.get("category") or "Community"),
                description=str(raw.get("description") or "Community connector"),
                default_url=str(raw.get("default_url") or ""),
                health_paths=tuple(str(x) for x in health if str(x).startswith("/")) or ("/",),
                capabilities=tuple(str(x) for x in caps),
                auth_header=str(raw.get("auth_header") or "X-Api-Key"),
                docs_url=str(raw.get("docs_url") or ""),
                native_page=str(raw.get("native_page") or ""),
            ))
        except Exception:
            continue
    return out


def connector_definitions() -> list[ConnectorDefinition]:
    return list(BUILTINS) + _plugin_defs()


def connector_definition(key: str) -> ConnectorDefinition | None:
    return next((x for x in connector_definitions() if x.key == key), None)


def connector_config(key: str) -> dict[str, Any]:
    definition = connector_definition(key)
    if not definition:
        return {"key": key, "enabled": False, "url": "", "api_key": ""}
    prefix = f"ecosystem.{key}."
    enabled_raw = setting_get(prefix + "enabled", "")
    url = setting_get(prefix + "url", "") or definition.default_url
    api_key = setting_get(prefix + "api_key", "")
    # Explicitly configured connectors are enabled by default; otherwise opt-in.
    enabled = enabled_raw.lower() in {"1", "true", "yes", "on"} if enabled_raw else bool(setting_get(prefix + "url", ""))
    return {
        **asdict(definition),
        "enabled": enabled,
        "url": url.rstrip("/"),
        "api_key": api_key,
        "has_key": bool(api_key),
    }


def save_connector(key: str, url: str, api_key: str = "", enabled: bool = True) -> None:
    if not connector_definition(key):
        raise ValueError("Unknown connector")
    prefix = f"ecosystem.{key}."
    setting_set(prefix + "url", url.strip().rstrip("/"))
    setting_set(prefix + "enabled", "true" if enabled else "false")
    if api_key and api_key not in {"********", "••••••••"}:
        setting_set(prefix + "api_key", api_key.strip(), True)


def install_connector_plugin(payload: dict, filename: str = "connector.json") -> Path:
    key = str(payload.get("key") or Path(filename).stem).strip().lower().replace(" ", "-")
    name = str(payload.get("name") or "").strip()
    if not key or not name:
        raise ValueError("Connector JSON requires key and name")
    health = payload.get("health_paths") or [payload.get("health_path") or "/"]
    if isinstance(health, str):
        health = [health]
    if any(not str(p).startswith("/") for p in health):
        raise ValueError("health_paths must contain URL paths beginning with /")
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    dest = PLUGIN_DIR / f"{key}.json"
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return dest


def _headers(config: dict) -> dict[str, str]:
    key = str(config.get("api_key") or "")
    if not key:
        return {}
    header = str(config.get("auth_header") or "X-Api-Key")
    return {header: key}


async def probe_connector(key: str, timeout: float = 4.0) -> dict[str, Any]:
    config = connector_config(key)
    definition = connector_definition(key)
    if not definition:
        return {"key": key, "ok": False, "error": "Unknown connector"}
    if not config.get("enabled"):
        return {**config, "ok": False, "state": "disabled", "error": "Disabled"}
    url = str(config.get("url") or "").rstrip("/")
    if not url:
        return {**config, "ok": False, "state": "unconfigured", "error": "URL is not configured"}
    last_error = "No health endpoint responded"
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": "ArrNexus/5.0", **_headers(config)}) as client:
        for path in definition.health_paths:
            try:
                response = await client.get(url + path)
                if response.status_code >= 500:
                    last_error = f"HTTP {response.status_code} at {path}"
                    continue
                if response.status_code in {401, 403}:
                    last_error = f"Authentication required at {path}"
                    continue
                if response.status_code >= 400:
                    last_error = f"HTTP {response.status_code} at {path}"
                    continue
                version = ""
                detail = ""
                try:
                    data = response.json()
                    if isinstance(data, dict):
                        version = str(data.get("version") or data.get("Version") or data.get("serverVersion") or data.get("ServerVersion") or "")
                        detail = str(data.get("status") or data.get("message") or data.get("name") or "")
                except Exception:
                    detail = (response.text or "").strip()[:120]
                return {**config, "ok": True, "state": "online", "status_code": response.status_code, "health_path": path, "version": version, "detail": detail}
            except Exception as exc:
                last_error = str(exc)
    return {**config, "ok": False, "state": "offline", "error": last_error}


async def probe_enabled_connectors() -> list[dict[str, Any]]:
    defs = connector_definitions()
    enabled = [d.key for d in defs if connector_config(d.key).get("enabled")]
    if not enabled:
        return []
    results = await asyncio.gather(*(probe_connector(k) for k in enabled), return_exceptions=True)
    out: list[dict[str, Any]] = []
    for key, result in zip(enabled, results):
        if isinstance(result, Exception):
            cfg = connector_config(key)
            out.append({**cfg, "ok": False, "state": "offline", "error": str(result)})
        else:
            out.append(result)
    return out
