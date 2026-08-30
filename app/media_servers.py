from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from urllib.parse import urljoin
from xml.etree import ElementTree

import httpx

from .connections import get_connection
from .db import setting_delete, setting_get, setting_set


@dataclass(frozen=True)
class MediaServerDefinition:
    key: str
    name: str
    default_port: int
    token_label: str
    description: str


BUILTIN_MEDIA_SERVERS: tuple[MediaServerDefinition, ...] = (
    MediaServerDefinition("jellyfin", "Jellyfin", 8096, "API key", "Open-source media server and the original ArrNexus media-server integration."),
    MediaServerDefinition("plex", "Plex", 32400, "X-Plex-Token", "Plex Media Server connection for health/library visibility."),
    MediaServerDefinition("emby", "Emby", 8096, "API key", "Emby Server connection using its integration API key."),
)


def definitions() -> list[MediaServerDefinition]:
    return list(BUILTIN_MEDIA_SERVERS)


def _base(url: str) -> str:
    value = (url or "").strip().rstrip("/")
    if value and not value.startswith(("http://", "https://")):
        value = "http://" + value
    return value


async def probe_builtin(kind: str, url: str, token: str) -> dict:
    kind = (kind or "").strip().lower()
    base = _base(url)
    if not base:
        return {"ok": False, "configured": False, "error": "URL not configured"}
    headers = {"Accept": "application/json, application/xml;q=0.9, */*;q=0.5", "User-Agent": "ArrNexus/9.3"}
    timeout = httpx.Timeout(4.0, connect=3.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            if kind == "plex":
                params = {"X-Plex-Token": token} if token else {}
                response = await client.get(base + "/", params=params)
                response.raise_for_status()
                root = ElementTree.fromstring(response.text)
                return {
                    "ok": True,
                    "configured": bool(token),
                    "name": root.attrib.get("friendlyName") or root.attrib.get("machineIdentifier") or "Plex Media Server",
                    "version": root.attrib.get("version") or "Connected",
                    "detail": "Authenticated Plex server endpoint" if token else "Plex endpoint reachable; token not saved",
                }
            if kind in {"jellyfin", "emby"}:
                if token:
                    headers2 = {"X-Emby-Token": token}
                else:
                    headers2 = {}
                response = await client.get(base + "/System/Info", headers=headers2)
                response.raise_for_status()
                data = response.json()
                return {
                    "ok": True,
                    "configured": bool(token),
                    "name": data.get("ServerName") or data.get("Name") or kind.title(),
                    "version": data.get("Version") or data.get("version") or "Connected",
                    "detail": "Authenticated media-server API verified" if token else "Server reachable; API key not saved",
                }
    except Exception as exc:
        return {"ok": False, "configured": bool(base and token), "error": str(exc)}
    return {"ok": False, "configured": False, "error": f"Unsupported media server: {kind}"}


def builtin_state(kind: str) -> dict:
    conn = get_connection(kind)
    definition = next((d for d in BUILTIN_MEDIA_SERVERS if d.key == kind), None)
    return {
        "kind": kind,
        "name": definition.name if definition else kind.title(),
        "description": definition.description if definition else "Media server",
        "token_label": definition.token_label if definition else "API token",
        "url": conn.url,
        "has_token": bool(conn.api_key),
        "configured": bool(conn.url and conn.api_key),
    }


_CUSTOM_META_KEY = "media_servers.custom"


def _load_custom_meta() -> list[dict]:
    raw = setting_get(_CUSTOM_META_KEY, "")
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ident = re.sub(r"[^a-zA-Z0-9_-]", "", str(row.get("id") or ""))[:64]
        if not ident:
            continue
        out.append({
            "id": ident,
            "name": str(row.get("name") or "External media server")[:120],
            "url": _base(str(row.get("url") or "")),
            "health_path": str(row.get("health_path") or "/")[:300],
            "auth_mode": str(row.get("auth_mode") or "none") if str(row.get("auth_mode") or "none") in {"none", "bearer", "header", "query"} else "none",
            "auth_name": str(row.get("auth_name") or "Authorization")[:120],
        })
    return out


def list_custom(mask: bool = True) -> list[dict]:
    out = []
    for row in _load_custom_meta():
        secret_value = setting_get(f"media_server.custom.{row['id']}.secret", "")
        state = dict(row)
        state["has_secret"] = bool(secret_value)
        if not mask:
            state["secret"] = secret_value
        out.append(state)
    return out


def save_custom(name: str, url: str, health_path: str, auth_mode: str, auth_name: str, secret_value: str, ident: str = "") -> str:
    rows = _load_custom_meta()
    ident = re.sub(r"[^a-zA-Z0-9_-]", "", ident or "")[:64] or secrets.token_hex(8)
    auth_mode = auth_mode if auth_mode in {"none", "bearer", "header", "query"} else "none"
    path = (health_path or "/").strip()
    if not path.startswith("/"):
        path = "/" + path
    item = {
        "id": ident,
        "name": (name or "External media server").strip()[:120],
        "url": _base(url),
        "health_path": path[:300],
        "auth_mode": auth_mode,
        "auth_name": (auth_name or ("Authorization" if auth_mode == "bearer" else "X-Api-Key")).strip()[:120],
    }
    replaced = False
    for index, row in enumerate(rows):
        if row.get("id") == ident:
            rows[index] = item
            replaced = True
            break
    if not replaced:
        rows.append(item)
    setting_set(_CUSTOM_META_KEY, json.dumps(rows), False)
    if secret_value and secret_value not in {"********", "••••••••"}:
        setting_set(f"media_server.custom.{ident}.secret", secret_value.strip(), True)
    return ident


def delete_custom(ident: str) -> None:
    ident = re.sub(r"[^a-zA-Z0-9_-]", "", ident or "")[:64]
    rows = [row for row in _load_custom_meta() if row.get("id") != ident]
    setting_set(_CUSTOM_META_KEY, json.dumps(rows), False)
    setting_delete(f"media_server.custom.{ident}.secret")


async def probe_custom(row: dict) -> dict:
    base = _base(str(row.get("url") or ""))
    if not base:
        return {**row, "ok": False, "error": "URL not configured"}
    health_path = str(row.get("health_path") or "/")
    target = urljoin(base + "/", health_path.lstrip("/"))
    secret_value = setting_get(f"media_server.custom.{row.get('id')}.secret", "")
    headers = {"User-Agent": "ArrNexus/9.3", "Accept": "application/json, text/plain, text/html;q=0.8, */*;q=0.5"}
    params = {}
    mode = str(row.get("auth_mode") or "none")
    name = str(row.get("auth_name") or "")
    if secret_value:
        if mode == "bearer":
            headers["Authorization"] = f"Bearer {secret_value}"
        elif mode == "header" and name:
            headers[name] = secret_value
        elif mode == "query" and name:
            params[name] = secret_value
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=3.0), follow_redirects=True, headers=headers) as client:
            response = await client.get(target, params=params)
        response.raise_for_status()
        return {
            **row,
            "ok": True,
            "configured": bool(base),
            "status_code": response.status_code,
            "content_type": (response.headers.get("content-type") or "").split(";", 1)[0],
            "detail": f"HTTP {response.status_code} verified",
        }
    except Exception as exc:
        return {**row, "ok": False, "configured": bool(base), "error": str(exc)}
