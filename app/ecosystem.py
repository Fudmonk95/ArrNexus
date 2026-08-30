from __future__ import annotations

import asyncio
import json
import time
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
    auth_type: str = "api_key"  # api_key, bearer, basic_login, none, generic
    key_label: str = "API / service key"
    username_label: str = "Username"
    password_label: str = "Password"


BUILTINS: tuple[ConnectorDefinition, ...] = (
    ConnectorDefinition(
        "infinidysk", "InfiniDysk", "Core", "Usenet streaming, SAB-compatible queue, WebDAV and provider telemetry.",
        "http://host.docker.internal:3000", ("/healthz",),
        ("health", "queue", "history", "metrics", "usenet", "warnings"), "X-Api-Key",
        "https://www.infinidysk.com/", "/infinidysk", "api_key", "SAB API key",
    ),
    ConnectorDefinition(
        "dumb", "DUMB", "Core", "Distributed Unlimited Media Bridge stack controller, topology and service logs.",
        "http://host.docker.internal:8000", ("/health", "/"),
        ("topology", "services", "health", "updates", "logs"), "X-Api-Key", "https://dumbarr.com/", "", "none", "Optional API key",
    ),
    ConnectorDefinition(
        "decypharr", "Decypharr", "Core", "Debrid download-client bridge, torrent library, repair and mount service.",
        "http://host.docker.internal:8282", ("/version",),
        ("debrid", "queue", "mount", "repair", "browse"), "Authorization", "https://github.com/sirrobot01/decypharr", "/decypharr", "bearer", "API token",
    ),
    ConnectorDefinition(
        "altmount", "AltMount", "Core", "Alternative Usenet virtual filesystem/WebDAV provider.",
        "http://host.docker.internal:8585", ("/",),
        ("usenet", "webdav", "mount", "health"), "Authorization", "https://github.com/javi11/altmount", "", "basic_login", "API key (compatibility only)",
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
        "http://host.docker.internal:6767", ("/api/system/status",),
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
            if isinstance(health, str): health = [health]
            caps = raw.get("capabilities") or []
            if isinstance(caps, str): caps = [x.strip() for x in caps.split(",") if x.strip()]
            out.append(ConnectorDefinition(
                key=f"plugin-{key}", name=name, category=str(raw.get("category") or "Community"),
                description=str(raw.get("description") or "Community connector"), default_url=str(raw.get("default_url") or ""),
                health_paths=tuple(str(x) for x in health if str(x).startswith("/")) or ("/",), capabilities=tuple(str(x) for x in caps),
                auth_header=str(raw.get("auth_header") or "X-Api-Key"), docs_url=str(raw.get("docs_url") or ""), native_page=str(raw.get("native_page") or ""),
                auth_type=str(raw.get("auth_type") or "api_key"), key_label=str(raw.get("key_label") or "API / service key"),
            ))
        except Exception:
            continue
    return out


def connector_definitions() -> list[ConnectorDefinition]: return list(BUILTINS) + _plugin_defs()
def connector_definition(key: str) -> ConnectorDefinition | None: return next((x for x in connector_definitions() if x.key == key), None)


def connector_config(key: str) -> dict[str, Any]:
    definition = connector_definition(key)
    if not definition: return {"key": key, "enabled": False, "url": "", "api_key": "", "username":"", "password":""}
    prefix = f"ecosystem.{key}."
    enabled_raw = setting_get(prefix + "enabled", "")
    url = setting_get(prefix + "url", "") or definition.default_url
    api_key = setting_get(prefix + "api_key", "")
    username = setting_get(prefix + "username", "")
    password = setting_get(prefix + "password", "")
    enabled = enabled_raw.lower() in {"1", "true", "yes", "on"} if enabled_raw else bool(setting_get(prefix + "url", ""))
    return {
        **asdict(definition), "enabled": enabled, "url": url.rstrip("/"), "api_key": api_key,
        "username": username, "password": password, "has_key": bool(api_key), "has_password": bool(password),
    }


def save_connector(key: str, url: str, api_key: str = "", enabled: bool = True, username: str = "", password: str = "") -> None:
    if not connector_definition(key): raise ValueError("Unknown connector")
    prefix = f"ecosystem.{key}."
    setting_set(prefix + "url", url.strip().rstrip("/")); setting_set(prefix + "enabled", "true" if enabled else "false")
    if api_key and api_key not in {"********", "••••••••"}: setting_set(prefix + "api_key", api_key.strip(), True)
    if username.strip(): setting_set(prefix + "username", username.strip())
    if password and password not in {"********", "••••••••"}: setting_set(prefix + "password", password, True)


def install_connector_plugin(payload: dict, filename: str = "connector.json") -> Path:
    key = str(payload.get("key") or Path(filename).stem).strip().lower().replace(" ", "-")
    name = str(payload.get("name") or "").strip()
    if not key or not name: raise ValueError("Connector JSON requires key and name")
    health = payload.get("health_paths") or [payload.get("health_path") or "/"]
    if isinstance(health, str): health = [health]
    if any(not str(p).startswith("/") for p in health): raise ValueError("health_paths must contain URL paths beginning with /")
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True); dest = PLUGIN_DIR / f"{key}.json"; dest.write_text(json.dumps(payload, indent=2), encoding="utf-8"); return dest


def _generic_headers(config: dict) -> dict[str, str]:
    key = str(config.get("api_key") or "")
    if not key: return {}
    header = str(config.get("auth_header") or "X-Api-Key")
    if header.lower() == "authorization" and config.get("auth_type") == "bearer": return {"Authorization": f"Bearer {key}"}
    return {header: key}


def _extract_version(response: httpx.Response) -> tuple[str, str]:
    version = ""; detail = ""
    try:
        data = response.json()
        if isinstance(data, dict):
            version = str(data.get("version") or data.get("Version") or data.get("serverVersion") or data.get("ServerVersion") or "")
            detail = str(data.get("status") or data.get("message") or data.get("name") or "")
    except Exception:
        detail = (response.text or "").strip()[:120]
    return version, detail


async def _request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> tuple[httpx.Response, int]:
    start = time.perf_counter(); r = await client.request(method, url, **kwargs); ms = int((time.perf_counter()-start)*1000); return r, ms


async def _probe_infinidysk(config: dict, client: httpx.AsyncClient) -> dict[str, Any]:
    url = config["url"]
    health, ms = await _request(client, "GET", url + "/healthz")
    if health.status_code >= 400: return {"reachable": False, "api_ok": False, "auth_ok": False, "error": f"Health HTTP {health.status_code}", "latency_ms": ms}
    version, detail = _extract_version(health)
    key = str(config.get("api_key") or "")
    if not key: return {"reachable": True, "api_ok": False, "auth_ok": False, "error": "SAB API key is not configured", "latency_ms": ms, "version": version, "detail": detail}
    r, api_ms = await _request(client, "GET", url + "/api", params={"mode":"queue","output":"json","start":0,"limit":1,"apikey":key})
    if r.status_code in {401,403}: return {"reachable": True, "auth_ok": False, "api_ok": False, "error": f"SAB authentication failed (HTTP {r.status_code})", "latency_ms": api_ms, "version": version}
    if r.status_code >= 400: return {"reachable": True, "auth_ok": False, "api_ok": False, "error": f"SAB API HTTP {r.status_code}: {r.text[:120]}", "latency_ms": api_ms, "version": version}
    try:
        payload=r.json()
        if isinstance(payload,dict) and payload.get("error"): return {"reachable":True,"auth_ok":False,"api_ok":False,"error":str(payload.get("error")),"latency_ms":api_ms,"version":version}
    except Exception: pass
    return {"reachable": True, "auth_ok": True, "api_ok": True, "latency_ms": api_ms, "version": version, "detail": detail or "SAB queue authenticated"}


async def _probe_decypharr(config: dict, client: httpx.AsyncClient) -> dict[str, Any]:
    url=config["url"]
    v, ms=await _request(client,"GET",url+"/version")
    if v.status_code>=400: return {"reachable":False,"auth_ok":False,"api_ok":False,"error":f"Version endpoint HTTP {v.status_code}","latency_ms":ms}
    version, detail=_extract_version(v)
    key=str(config.get("api_key") or "")
    if not key: return {"reachable":True,"auth_ok":False,"api_ok":False,"error":"Decypharr API token is not configured","latency_ms":ms,"version":version}
    r, api_ms=await _request(client,"GET",url+"/api/torrents",headers={"Authorization":f"Bearer {key}"})
    if r.status_code in {401,403}: return {"reachable":True,"auth_ok":False,"api_ok":False,"error":f"API token rejected (HTTP {r.status_code})","latency_ms":api_ms,"version":version}
    if r.status_code>=400: return {"reachable":True,"auth_ok":False,"api_ok":False,"error":f"Authenticated API HTTP {r.status_code}: {r.text[:120]}","latency_ms":api_ms,"version":version}
    return {"reachable":True,"auth_ok":True,"api_ok":True,"latency_ms":api_ms,"version":version,"detail":detail or "Authenticated torrent API"}


async def _probe_altmount(config: dict, client: httpx.AsyncClient) -> dict[str, Any]:
    url=config["url"]; username=str(config.get("username") or ""); password=str(config.get("password") or "")
    try:
        root, ms=await _request(client,"GET",url+"/")
        reachable=root.status_code<500
    except Exception as exc:
        return {"reachable":False,"auth_ok":False,"api_ok":False,"error":str(exc)}
    if not username or not password:
        return {"reachable":reachable,"auth_ok":False,"api_ok":False,"error":"AltMount username/password are required for its management API","latency_ms":ms}
    r, login_ms=await _request(client,"POST",url+"/api/auth/login",json={"username":username,"password":password})
    if r.status_code in {401,403}: return {"reachable":True,"auth_ok":False,"api_ok":False,"error":f"AltMount login rejected (HTTP {r.status_code})","latency_ms":login_ms}
    if r.status_code>=400: return {"reachable":True,"auth_ok":False,"api_ok":False,"error":f"AltMount login HTTP {r.status_code}","latency_ms":login_ms}
    # httpx retains the JWT cookie set by the login request.
    check, api_ms=await _request(client,"GET",url+"/api/system/health")
    if check.status_code==404: check, api_ms=await _request(client,"GET",url+"/api/system/status")
    if check.status_code>=400: return {"reachable":True,"auth_ok":True,"api_ok":False,"error":f"Authenticated AltMount API HTTP {check.status_code}","latency_ms":api_ms}
    version,detail=_extract_version(check)
    return {"reachable":True,"auth_ok":True,"api_ok":True,"latency_ms":api_ms,"version":version,"detail":detail or "JWT login verified"}


async def _probe_dumb(config: dict, client: httpx.AsyncClient) -> dict[str, Any]:
    """DUMB has a separate API (normally :8000) and frontend (:3005).
    Do not accept a pretty HTML frontend page as proof that the API works."""
    url=config["url"]
    try:
        r,ms=await _request(client,"GET",url+"/health")
    except Exception as exc:
        return {"reachable":False,"auth_ok":False,"api_ok":False,"error":str(exc)}
    if r.status_code>=400:
        return {"reachable":True,"auth_ok":None,"api_ok":False,"error":f"DUMB API health HTTP {r.status_code}","latency_ms":ms}
    ctype=(r.headers.get("content-type") or "").lower()
    try:
        data=r.json()
    except Exception:
        data=None
    if not isinstance(data,dict):
        hint=" The DUMB frontend appears to be configured; use the DUMB API URL (normally port 8000), not frontend port 3005." if "html" in ctype or "<!doctype" in r.text[:200].lower() else ""
        return {"reachable":True,"auth_ok":None,"api_ok":False,"error":"DUMB /health did not return JSON."+hint,"latency_ms":ms}
    status=str(data.get("status") or "").lower()
    ok=status in {"healthy","ok"} or data.get("ok") is True
    return {"reachable":True,"auth_ok":None,"api_ok":bool(ok),"latency_ms":ms,"version":str(data.get("version") or ""),"detail":"DUMB API health verified" if ok else str(data.get("details") or data)}


async def _probe_generic(config: dict, definition: ConnectorDefinition, client: httpx.AsyncClient) -> dict[str, Any]:
    last_error="No health endpoint responded"; headers=_generic_headers(config)
    for path in definition.health_paths:
        try:
            r, ms=await _request(client,"GET",config["url"]+path,headers=headers)
            if r.status_code in {401,403}: return {"reachable":True,"auth_ok":False,"api_ok":False,"error":f"Authentication required/rejected at {path} (HTTP {r.status_code})","latency_ms":ms}
            if r.status_code>=400: last_error=f"HTTP {r.status_code} at {path}"; continue
            version,detail=_extract_version(r)
            auth_ok=True if definition.auth_type=="none" or bool(config.get("api_key")) else None
            return {"reachable":True,"auth_ok":auth_ok,"api_ok":True,"latency_ms":ms,"version":version,"detail":detail or f"HTTP {r.status_code}"}
        except Exception as exc: last_error=str(exc)
    return {"reachable":False,"auth_ok":False,"api_ok":False,"error":last_error}


async def probe_connector(key: str, timeout: float = 5.0) -> dict[str, Any]:
    config=connector_config(key); definition=connector_definition(key)
    if not definition: return {"key":key,"ok":False,"error":"Unknown connector"}
    if not config.get("enabled"): return {**config,"ok":False,"state":"disabled","reachable":False,"auth_ok":False,"api_ok":False,"error":"Disabled"}
    url=str(config.get("url") or "").rstrip("/")
    if not url: return {**config,"ok":False,"state":"unconfigured","reachable":False,"auth_ok":False,"api_ok":False,"error":"URL is not configured"}
    if not url.startswith(("http://","https://")): return {**config,"ok":False,"state":"invalid","reachable":False,"auth_ok":False,"api_ok":False,"error":"URL must start with http:// or https://"}
    headers={"User-Agent":"ArrNexus/9.0"}
    async with httpx.AsyncClient(timeout=timeout,follow_redirects=True,headers=headers) as client:
        try:
            if key=="infinidysk": result=await _probe_infinidysk(config,client)
            elif key=="dumb": result=await _probe_dumb(config,client)
            elif key=="decypharr": result=await _probe_decypharr(config,client)
            elif key=="altmount": result=await _probe_altmount(config,client)
            else: result=await _probe_generic(config,definition,client)
        except Exception as exc: result={"reachable":False,"auth_ok":False,"api_ok":False,"error":str(exc)}
    ok=bool(result.get("reachable") and result.get("api_ok") and result.get("auth_ok") is not False)
    state="online" if ok else "auth_failed" if result.get("reachable") and result.get("auth_ok") is False else "offline"
    return {**config,**result,"ok":ok,"state":state}


async def probe_enabled_connectors() -> list[dict[str, Any]]:
    enabled=[d.key for d in connector_definitions() if connector_config(d.key).get("enabled")]
    if not enabled: return []
    results=await asyncio.gather(*(probe_connector(k) for k in enabled),return_exceptions=True); out=[]
    for key,result in zip(enabled,results):
        if isinstance(result,Exception): out.append({**connector_config(key),"ok":False,"state":"offline","reachable":False,"auth_ok":False,"api_ok":False,"error":str(result)})
        else: out.append(result)
    return out
