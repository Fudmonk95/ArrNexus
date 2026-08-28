from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request as URLRequest, urlopen

from .config import settings
from .connections import get_connection
from .db import setting_get, setting_rows, setting_set
from .providers import provider_credentials_for_aiostreams


URL_KEY = "aiostreams.url"
USER_KEY = "aiostreams.user"
CREDENTIAL_KEY = "aiostreams.credential"
ENCRYPTED_PASSWORD_KEY = "aiostreams.encrypted_password"
LAST_SYNC_KEY = "aiostreams.last_sync"
MANUAL_RD_KEY = "aiostreams.realdebrid.api_key"

_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|apikey|credential|authorization|auth$)",
    re.IGNORECASE,
)
_URL_SECRET = re.compile(
    r"(?i)([?&](?:api[_-]?key|apikey|token|password|secret|auth)=)[^&#\s]+"
)
_URL_USERINFO = re.compile(r"(?i)(https?://)[^/@\s]+@")
_SEARCH_URL_KEY = re.compile(r"(?:^|_)(?:url|uri|href)$|url$", re.IGNORECASE)
_SEARCH_HEADER_KEY = re.compile(r"(?:authorization|cookie|header|proxyheaders?)", re.IGNORECASE)


class AIOStreamsError(RuntimeError):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_base_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("AIOStreams URL must be a complete http:// or https:// URL")
    if parsed.username or parsed.password:
        raise ValueError("Do not embed credentials in the AIOStreams URL")
    return value


def connection_settings(mask: bool = True) -> dict[str, Any]:
    url = setting_get(URL_KEY, "")
    user = setting_get(USER_KEY, "")
    credential = setting_get(CREDENTIAL_KEY, "")
    encrypted = setting_get(ENCRYPTED_PASSWORD_KEY, "")
    effective = encrypted or credential
    return {
        "url": url,
        "user": mask_identifier(user) if mask else user,
        "configured": bool(url and user and effective),
        "has_credential": bool(credential),
        "has_encrypted_password": bool(encrypted),
        "last_sync": setting_get(LAST_SYNC_KEY, ""),
    }


def save_connection(url: str = "", user: str = "", credential: str = "") -> None:
    current_url = setting_get(URL_KEY, "")
    current_user = setting_get(USER_KEY, "")
    new_url = _safe_base_url(url) if (url or "").strip() else current_url
    supplied_user = (user or "").strip()
    new_user = supplied_user or current_user
    supplied_credential = (credential or "").strip()
    user_changed = bool(supplied_user and current_user and supplied_user != current_user)

    if new_url:
        setting_set(URL_KEY, new_url, False)
    if new_user:
        if user_changed:
            # AIOStreams encrypted/raw credentials are tied to the previous
            # configuration identity. Never silently pair them with a new user.
            setting_set(ENCRYPTED_PASSWORD_KEY, "", True)
            if not supplied_credential:
                setting_set(CREDENTIAL_KEY, "", True)
        setting_set(USER_KEY, new_user, False)
    if supplied_credential:
        setting_set(CREDENTIAL_KEY, supplied_credential, True)
        # Prefer a freshly returned encryptedPassword only after a successful GET.
        setting_set(ENCRYPTED_PASSWORD_KEY, "", True)


def save_manual_realdebrid_key(value: str) -> None:
    if (value or "").strip():
        setting_set(MANUAL_RD_KEY, value.strip(), True)


def mask_identifier(value: str) -> str:
    value = str(value or "")
    if not value:
        return "Not configured"
    if len(value) <= 8:
        return "••••••••"
    return value[:4] + "…" + value[-4:]


def _effective_auth() -> tuple[str, str]:
    user = setting_get(USER_KEY, "").strip()
    encrypted = setting_get(ENCRYPTED_PASSWORD_KEY, "").strip()
    credential = setting_get(CREDENTIAL_KEY, "").strip()
    password = encrypted or credential
    if not user or not password:
        raise AIOStreamsError("AIOStreams user/alias and password are not configured")
    return user, password


def _endpoint(path: str, query: dict[str, Any] | None = None) -> str:
    base = _safe_base_url(setting_get(URL_KEY, ""))
    if not base:
        raise AIOStreamsError("AIOStreams URL is not configured")
    path = "/" + path.lstrip("/")
    url = base + path
    if query:
        clean = {k: v for k, v in query.items() if v is not None and v != ""}
        if clean:
            url += "?" + urlencode(clean, doseq=True)
    return url


def _error_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            msg = error.get("message") or error.get("detail") or error.get("code")
            if msg:
                return str(msg)
        if error:
            return str(error)
        if payload.get("detail"):
            return str(payload["detail"])
    return fallback


def _request_sync(
    method: str,
    path: str,
    *,
    auth: bool = True,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 12.0,
) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "ArrNexus/9.0"}
    if auth:
        user, password = _effective_auth()
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = "Basic " + token
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = URLRequest(_endpoint(path, query), data=data, headers=headers, method=method.upper())
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return {"success": True, "data": None}
            payload = json.loads(raw.decode("utf-8", errors="replace"))
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8", errors="replace"))
        except Exception:
            payload = None
        raise AIOStreamsError(_error_message(payload, f"AIOStreams returned HTTP {exc.code}")) from None
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise AIOStreamsError(f"Unable to reach AIOStreams: {reason}") from None
    except TimeoutError:
        raise AIOStreamsError("AIOStreams request timed out") from None
    except json.JSONDecodeError:
        raise AIOStreamsError("AIOStreams returned a non-JSON API response") from None

    if isinstance(payload, dict) and payload.get("success") is False:
        raise AIOStreamsError(_error_message(payload, "AIOStreams API request failed"))
    if not isinstance(payload, dict):
        raise AIOStreamsError("AIOStreams returned an unexpected API response")
    return payload


async def _request(*args, **kwargs) -> dict[str, Any]:
    return await asyncio.to_thread(_request_sync, *args, **kwargs)


async def status() -> dict[str, Any]:
    """Check the public AIOStreams status endpoint without user credentials."""
    try:
        payload = await _request("GET", "/api/v1/status", auth=False, timeout=6.0)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return {
            "ok": True,
            "detail": payload.get("detail") or "AIOStreams API reachable",
            "data": data,
        }
    except Exception as exc:
        return {"ok": False, "detail": str(exc), "data": {}}


async def get_user(raw: bool = True) -> dict[str, Any]:
    """Fetch the complete AIOStreams User API config.

    raw=True deliberately avoids merging a parent configuration into the stored
    user config, which makes preview/apply digests deterministic.
    """
    payload = await _request("GET", "/api/v1/user", query={"raw": "true" if raw else "false"})
    data = payload.get("data") or {}
    if not isinstance(data, dict) or not isinstance(data.get("userData"), dict):
        raise AIOStreamsError("AIOStreams user API did not return a userData object")
    encrypted = str(data.get("encryptedPassword") or "").strip()
    if encrypted:
        # AIOStreams explicitly permits encryptedPassword as the Basic-auth
        # password on future requests, so the raw password does not need to be
        # reused once a successful GET has returned this token.
        setting_set(ENCRYPTED_PASSWORD_KEY, encrypted, True)
    return data


async def update_user(config: dict[str, Any]) -> dict[str, Any]:
    """Full replacement PUT. Never call this with a partial ArrNexus object."""
    payload = await _request("PUT", "/api/v1/user", body={"config": config})
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    return data


async def verify() -> dict[str, Any]:
    health = await status()
    if not health.get("ok"):
        raise AIOStreamsError(str(health.get("detail") or "AIOStreams status check failed"))
    data = await get_user(raw=True)
    cfg = data.get("userData") or {}
    return {
        "ok": True,
        "services": len(cfg.get("services") or []) if isinstance(cfg.get("services"), list) else 0,
        "presets": len(cfg.get("presets") or []) if isinstance(cfg.get("presets"), list) else 0,
        "encrypted_password_saved": bool(data.get("encryptedPassword")),
    }


async def search(media_type: str, external_id: str, format_results: bool = True) -> dict[str, Any]:
    media_type = (media_type or "").strip().lower()
    external_id = (external_id or "").strip()
    if media_type not in {"movie", "series", "anime"}:
        raise AIOStreamsError("Search type must be movie, series or anime")
    if not external_id:
        raise AIOStreamsError("An IMDb/TMDB/Stremio-compatible ID is required")
    payload = await _request(
        "GET",
        "/api/v1/search",
        query={"type": media_type, "id": external_id, "format": "true" if format_results else "false"},
        timeout=35.0,
    )
    data = payload.get("data") or {}
    return data if isinstance(data, dict) else {}


def _raw_setting_map() -> dict[str, str]:
    try:
        return {str(row.get("key") or ""): str(row.get("value") or "") for row in setting_rows()}
    except Exception:
        return {}


def _detect_realdebrid_key() -> str:
    manual = setting_get(MANUAL_RD_KEY, "").strip()
    if manual:
        return manual
    rows = _raw_setting_map()
    exact = (
        "realdebrid.api_key", "realdebrid.token", "real_debrid.api_key", "real_debrid.token",
        "rd.api_key", "rd.token", "debrid.realdebrid.api_key", "debrid.realdebrid.token",
    )
    for key in exact:
        if rows.get(key):
            return rows[key]
    for key, value in rows.items():
        low = key.lower()
        if value and ("realdebrid" in low or "real_debrid" in low) and any(x in low for x in ("token", "api_key", "apikey", "key")):
            return value
    for attr in (
        "realdebrid_api_key", "realdebrid_token", "real_debrid_api_key", "real_debrid_token",
        "rd_api_key", "rd_token",
    ):
        try:
            value = getattr(settings, attr, "")
        except Exception:
            value = ""
        if value:
            return str(value)
    return ""


def _detect_nzbdav_credentials() -> dict[str, str]:
    """Conservatively reuse clearly named NzbDAV values already stored by ArrNexus.

    Missing fields are intentionally left untouched rather than guessed. This
    protects AIOStreams operator/default credentials and any unrelated schema.
    """
    rows = _raw_setting_map()
    found: dict[str, str] = {}
    for key, value in rows.items():
        if not value:
            continue
        low = key.lower().replace("-", "_")
        if "nzbdav" not in low:
            continue
        compact = re.sub(r"[^a-z0-9]", "", low)
        if "publicurl" in compact:
            found.setdefault("publicUrl", value)
        elif compact.endswith("apikey") or "nzbdavapikey" in compact:
            found.setdefault("apiKey", value)
        elif compact.endswith("aiostreamsauth"):
            found.setdefault("aiostreamsAuth", value)
        elif compact.endswith("username") or compact.endswith("webdavuser"):
            found.setdefault("username", value)
        elif compact.endswith("password") or compact.endswith("webdavpassword"):
            found.setdefault("password", value)
        elif compact.endswith("url"):
            found.setdefault("url", value)
    return found


def discover_arrnexus_integrations() -> dict[str, Any]:
    prowlarr = {"url": "", "has_api_key": False, "api_key": ""}
    try:
        conn = get_connection("prowlarr")
        prowlarr = {
            "url": str(conn.url or ""),
            "has_api_key": bool(conn.api_key),
            "api_key": str(conn.api_key or ""),
        }
    except Exception:
        pass
    rd_key = _detect_realdebrid_key()
    nzbdav = _detect_nzbdav_credentials()
    providers = provider_credentials_for_aiostreams()
    # Backwards compatibility: v8 installations may have Real-Debrid/NzbDAV
    # configured outside the v9 provider registry. Keep discovering them, but
    # never discard explicit provider-registry values.
    if rd_key:
        providers.setdefault("realdebrid", {}).setdefault("apiKey", rd_key)
    if nzbdav:
        target = providers.setdefault("nzbdav", {})
        for field, value in nzbdav.items():
            target.setdefault(field, value)
    return {
        "prowlarr": prowlarr,
        "realdebrid": {"available": bool(rd_key), "api_key": rd_key},
        "nzbdav": {"available": bool(nzbdav), "credentials": nzbdav, "fields": sorted(nzbdav)},
        "providers": providers,
    }


def integration_summary(integrations: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return UI-safe discovery state without credential values."""
    integrations = integrations or discover_arrnexus_integrations()
    p = integrations.get("prowlarr") or {}
    rd = integrations.get("realdebrid") or {}
    nzb = integrations.get("nzbdav") or {}
    providers = integrations.get("providers") or {}
    return {
        "prowlarr": {
            "url": str(p.get("url") or ""),
            "has_api_key": bool(p.get("api_key") or p.get("has_api_key")),
        },
        "realdebrid": {"available": bool(rd.get("api_key") or rd.get("available") or providers.get("realdebrid"))},
        "nzbdav": {
            "available": bool(nzb.get("available") or providers.get("nzbdav")),
            "fields": list(nzb.get("fields") or sorted((nzb.get("credentials") or providers.get("nzbdav") or {}).keys())),
        },
        "providers": [
            {"id": str(provider_id), "fields": sorted((credentials or {}).keys())}
            for provider_id, credentials in sorted(providers.items())
        ],
    }


def _ensure_service(config: dict[str, Any], service_id: str) -> tuple[dict[str, Any], bool]:
    services = config.get("services")
    if not isinstance(services, list):
        services = []
        config["services"] = services
    for entry in services:
        if isinstance(entry, dict) and str(entry.get("id") or "") == service_id:
            if not isinstance(entry.get("credentials"), dict):
                entry["credentials"] = {}
            return entry, False
    entry = {"id": service_id, "enabled": False, "credentials": {}}
    services.append(entry)
    return entry, True


def _enabled_service_ids(config: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for entry in config.get("services") or []:
        if isinstance(entry, dict) and entry.get("enabled") and entry.get("id"):
            out.append(str(entry["id"]))
    return out


def merge_autowire(
    existing: dict[str, Any],
    integrations: dict[str, Any],
    *,
    wire_prowlarr: bool = True,
    wire_realdebrid: bool = True,
    wire_nzbdav: bool = True,
) -> dict[str, Any]:
    """Merge ArrNexus integrations into a deep copy of full AIOStreams userData."""
    config = copy.deepcopy(existing or {})
    changes: list[str] = []
    warnings: list[str] = []

    # v9 provider-neutral wiring. Values from ArrNexus only fill missing remote
    # credential fields; operator defaults/forced credentials and user-owned
    # AIOStreams values are never blindly replaced.
    provider_payload = integrations.get("providers") or {}
    if isinstance(provider_payload, dict):
        for provider_id, detected in provider_payload.items():
            provider_id = str(provider_id or "").strip()
            if not provider_id or provider_id in {"realdebrid", "nzbdav"}:
                continue
            entry, created = _ensure_service(config, provider_id)
            was_enabled = bool(entry.get("enabled"))
            entry["enabled"] = True
            if created or not was_enabled:
                changes.append(f"Enable the {provider_id} service in AIOStreams")
            creds = entry.setdefault("credentials", {})
            copied: list[str] = []
            for field, raw in (detected or {}).items():
                value = str(raw or "").strip()
                if value and not creds.get(str(field)):
                    creds[str(field)] = value
                    copied.append(str(field))
            if copied:
                changes.append(f"Reuse ArrNexus {provider_id} provider settings for: " + ", ".join(copied))

    if wire_realdebrid:
        rd = integrations.get("realdebrid") or {}
        provider_rd = (integrations.get("providers") or {}).get("realdebrid") or {}
        entry, created = _ensure_service(config, "realdebrid")
        was_enabled = bool(entry.get("enabled"))
        entry["enabled"] = True
        if created or not was_enabled:
            changes.append("Enable the Real-Debrid service in AIOStreams")
        api_key = str(provider_rd.get("apiKey") or rd.get("api_key") or "")
        if api_key:
            creds = entry.setdefault("credentials", {})
            if not creds.get("apiKey"):
                creds["apiKey"] = api_key
                changes.append("Supply the existing ArrNexus Real-Debrid API key to AIOStreams")
        elif not (entry.get("credentials") or {}):
            warnings.append(
                "Real-Debrid was enabled but ArrNexus could not locate a reusable key. "
                "AIOStreams may still have operator-level default/forced credentials; otherwise configure the service there."
            )

    if wire_nzbdav:
        entry, created = _ensure_service(config, "nzbdav")
        was_enabled = bool(entry.get("enabled"))
        entry["enabled"] = True
        if created or not was_enabled:
            changes.append("Enable the NzbDAV service in AIOStreams")
        detected_nzbdav = dict((integrations.get("nzbdav") or {}).get("credentials") or {})
        for field, value in (((integrations.get("providers") or {}).get("nzbdav") or {}).items()):
            detected_nzbdav.setdefault(field, value)
        creds = entry.setdefault("credentials", {})
        copied_fields: list[str] = []
        for field in ("url", "publicUrl", "apiKey", "username", "password", "aiostreamsAuth"):
            value = str(detected_nzbdav.get(field) or "").strip()
            # Preserve every AIOStreams value already present. ArrNexus only fills
            # unambiguously identified missing fields.
            if value and not creds.get(field):
                creds[field] = value
                copied_fields.append(field)
        if copied_fields:
            changes.append("Reuse detected ArrNexus NzbDAV settings for: " + ", ".join(copied_fields))
        if not (creds.get("url") and creds.get("apiKey")):
            warnings.append(
                "NzbDAV is enabled, but ArrNexus could not confirm both its URL and API key. "
                "Existing AIOStreams credentials were preserved; verify NzbDAV in AIOStreams before relying on it."
            )

    if wire_prowlarr:
        p = integrations.get("prowlarr") or {}
        prowlarr_url = str(p.get("url") or "").strip().rstrip("/")
        if not prowlarr_url:
            warnings.append("Prowlarr is not configured in ArrNexus, so no AIOStreams Prowlarr preset was changed.")
        else:
            presets = config.get("presets")
            if not isinstance(presets, list):
                presets = []
                config["presets"] = presets
            preset = next((x for x in presets if isinstance(x, dict) and x.get("type") == "prowlarr"), None)
            if preset is None:
                preset = {
                    "type": "prowlarr",
                    "instanceId": secrets.token_hex(2)[:3],
                    "enabled": True,
                    "options": {
                        "name": "Prowlarr",
                        "timeout": 20000,
                        "prowlarrUrl": prowlarr_url,
                        "indexers": "",
                        # Current AIOStreams semantics: empty sources means both
                        # torrent and usenet Prowlarr indexers may be used.
                        "sources": [],
                        "mediaTypes": [],
                        "useMultipleInstances": False,
                    },
                }
                presets.append(preset)
                changes.append("Create an AIOStreams Prowlarr preset using the ArrNexus Prowlarr URL")
            options = preset.setdefault("options", {})
            if options.get("prowlarrUrl") != prowlarr_url:
                options["prowlarrUrl"] = prowlarr_url
                changes.append("Update the AIOStreams Prowlarr preset URL")
            prowlarr_api_key = str(p.get("api_key") or "").strip()
            if prowlarr_api_key and options.get("prowlarrApiKey") != prowlarr_api_key:
                options["prowlarrApiKey"] = prowlarr_api_key
                changes.append("Supply the existing ArrNexus Prowlarr API key to the AIOStreams Prowlarr preset")
            preset["enabled"] = True

            selected = list(dict.fromkeys(_enabled_service_ids(config)))
            existing_services = options.get("services")
            if isinstance(existing_services, list) and existing_services:
                # Respect an explicit user allow-list and only extend it with the
                # enabled resolver services ArrNexus just wired.
                merged_services = list(dict.fromkeys([str(x) for x in existing_services if x] + selected))
                if merged_services != existing_services:
                    options["services"] = merged_services
                    changes.append("Extend the existing Prowlarr service allow-list with enabled ArrNexus resolver services")
            else:
                # Empty/omitted means automatic in AIOStreams. Keep that behaviour
                # so unrelated configured services are not silently excluded.
                options.pop("services", None)
            if not prowlarr_api_key:
                warnings.append(
                    "ArrNexus has a Prowlarr URL but no API key, so the AIOStreams Prowlarr preset may still require a key before it can query Prowlarr."
                )

    return {"config": config, "changes": changes, "warnings": warnings}


def config_digest(config: dict[str, Any]) -> str:
    raw = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def sanitize_for_display(value: Any, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(str(key or "")):
        if value in (None, "", {}, []):
            return value
        return "********"
    if isinstance(value, dict):
        return {str(k): sanitize_for_display(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_for_display(v, key) for v in value]
    if isinstance(value, str):
        text = _URL_USERINFO.sub(r"\1<credentials-redacted>@", value)
        text = _URL_SECRET.sub(r"\1<redacted>", text)
        return text
    return value


def safe_json(value: Any) -> str:
    return json.dumps(sanitize_for_display(value), indent=2, ensure_ascii=False, sort_keys=True)


def _sanitize_search(value: Any, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(str(key or "")) or _SEARCH_HEADER_KEY.search(str(key or "")):
        if value in (None, "", {}, []):
            return value
        return "********"
    if _SEARCH_URL_KEY.search(str(key or "")):
        if value in (None, ""):
            return value
        return "<playback-url-redacted>"
    if isinstance(value, dict):
        return {str(k): _sanitize_search(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_search(v, key) for v in value]
    return sanitize_for_display(value, key)


def safe_search_payload(data: dict[str, Any]) -> dict[str, Any]:
    safe = _sanitize_search(data)
    if not isinstance(safe, dict):
        return {}
    results = safe.get("results")
    if isinstance(results, list):
        safe["results"] = results[:50]
    return safe


def _backup_dir() -> Path:
    db_path = Path(str(settings.db_path)).expanduser()
    parent = db_path.parent if str(db_path.parent) not in {"", "."} else Path("data")
    directory = parent / "aiostreams-backups"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory


def create_backup(config: dict[str, Any], reason: str = "sync") -> dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    digest = config_digest(config)[:12]
    clean_reason = re.sub(r"[^a-z0-9_-]+", "-", (reason or "sync").lower()).strip("-") or "sync"
    # Include entropy so multiple writes in the same second do not replace an
    # earlier safety backup.
    path = _backup_dir() / f"{stamp}-{clean_reason}-{digest}-{secrets.token_hex(2)}.json"
    raw = json.dumps(config, indent=2, ensure_ascii=False, sort_keys=True)
    path.write_text(raw + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return {"name": path.name, "created": stamp, "size": path.stat().st_size, "digest": digest}


def list_backups(limit: int = 30) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(_backup_dir().glob("*.json"), reverse=True)[: max(1, int(limit))]:
        try:
            stat = path.stat()
            out.append({
                "name": path.name,
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            })
        except OSError:
            continue
    return out


def load_backup(name: str) -> dict[str, Any]:
    name = os.path.basename(str(name or ""))
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.json", name):
        raise AIOStreamsError("Invalid backup name")
    path = _backup_dir() / name
    if not path.is_file():
        raise AIOStreamsError("AIOStreams backup was not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AIOStreamsError(f"Unable to read backup: {exc}") from None
    if not isinstance(data, dict):
        raise AIOStreamsError("Backup does not contain a configuration object")
    return data


async def apply_autowire(
    expected_digest: str,
    *,
    wire_prowlarr: bool,
    wire_realdebrid: bool,
    wire_nzbdav: bool,
) -> dict[str, Any]:
    current = await get_user(raw=True)
    existing = current["userData"]
    actual = config_digest(existing)
    if not expected_digest:
        raise AIOStreamsError("Auto-Wire Apply requires a fresh preview digest")
    if not secrets.compare_digest(actual, expected_digest):
        raise AIOStreamsError("AIOStreams configuration changed after the preview. Refresh the preview before applying.")

    integrations = discover_arrnexus_integrations()
    plan = merge_autowire(
        existing,
        integrations,
        wire_prowlarr=wire_prowlarr,
        wire_realdebrid=wire_realdebrid,
        wire_nzbdav=wire_nzbdav,
    )
    target_digest = config_digest(plan["config"])
    if secrets.compare_digest(actual, target_digest):
        return {
            "backup": None,
            "digest": actual,
            "changes": [],
            "warnings": plan["warnings"],
            "no_change": True,
        }

    backup = create_backup(existing, "before-autowire")
    await update_user(plan["config"])
    verified = await get_user(raw=True)
    new_digest = config_digest(verified["userData"])
    if not secrets.compare_digest(new_digest, target_digest):
        raise AIOStreamsError(
            "AIOStreams accepted the update but the verified configuration digest differs from the preview. "
            f"A safety backup was created as {backup['name']}; review the remote configuration before another write."
        )
    setting_set(LAST_SYNC_KEY, utcnow(), False)
    return {
        "backup": backup,
        "digest": new_digest,
        "changes": plan["changes"],
        "warnings": plan["warnings"],
        "no_change": False,
    }


async def rollback(name: str) -> dict[str, Any]:
    target = load_backup(name)
    current = await get_user(raw=True)
    safety = create_backup(current["userData"], "before-rollback")
    target_digest = config_digest(target)
    await update_user(target)
    verified = await get_user(raw=True)
    verified_digest = config_digest(verified["userData"])
    if not secrets.compare_digest(verified_digest, target_digest):
        raise AIOStreamsError(
            "Rollback PUT completed but the verified remote configuration does not match the selected backup. "
            f"The pre-rollback state is preserved in {safety['name']}."
        )
    setting_set(LAST_SYNC_KEY, utcnow(), False)
    return {"digest": verified_digest, "restored": name, "safety_backup": safety}


def endpoint_helpers() -> dict[str, str]:
    try:
        base = _safe_base_url(setting_get(URL_KEY, ""))
    except Exception:
        base = ""
    return {
        "newznab": (base + "/api/v1/newznab/api") if base else "",
        "torznab": (base + "/api/v1/torznab/api") if base else "",
        "sabnzbd": (base + "/api/v1/sabnzbd") if base else "",
    }
