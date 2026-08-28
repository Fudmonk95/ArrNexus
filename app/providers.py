from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .db import setting_get, setting_set


@dataclass(frozen=True)
class ProviderDefinition:
    id: str
    name: str
    category: str
    description: str
    credential_fields: tuple[tuple[str, str, bool], ...]
    capabilities: tuple[str, ...]
    aiostreams: bool = True


PROVIDERS: tuple[ProviderDefinition, ...] = (
    ProviderDefinition("realdebrid", "Real-Debrid", "Debrid", "Large debrid cache and HTTPS playback.", (("apiKey", "API key", True),), ("torrent", "cached", "stream")),
    ProviderDefinition("torbox", "TorBox", "Debrid", "Debrid service with torrent and Usenet capabilities.", (("apiKey", "API key", True),), ("torrent", "usenet", "cached", "stream")),
    ProviderDefinition("premiumize", "Premiumize", "Debrid", "Premium debrid and cloud service.", (("apiKey", "API key", True),), ("torrent", "cached", "stream")),
    ProviderDefinition("alldebrid", "AllDebrid", "Debrid", "Multi-host debrid provider.", (("apiKey", "API key", True),), ("torrent", "cached", "stream")),
    ProviderDefinition("debridlink", "Debrid-Link", "Debrid", "Debrid and seedbox-style service.", (("apiKey", "API key", True),), ("torrent", "cached", "stream")),
    ProviderDefinition("easydebrid", "EasyDebrid", "Debrid", "Debrid provider supported by AIOStreams.", (("apiKey", "API key", True),), ("torrent", "cached", "stream")),
    ProviderDefinition("debrider", "Debrider", "Debrid", "Debrid provider supported by AIOStreams.", (("apiKey", "API key", True),), ("torrent", "cached", "stream")),
    ProviderDefinition("offcloud", "Offcloud", "Debrid", "Cloud download/debrid service.", (("apiKey", "API key", True), ("email", "Email", True), ("password", "Password", True)), ("torrent", "stream")),
    ProviderDefinition("putio", "put.io", "Debrid", "Cloud torrent storage and streaming.", (("clientId", "Client ID", True), ("token", "Token", True)), ("torrent", "stream")),
    ProviderDefinition("pikpak", "PikPak", "Debrid", "Cloud storage resolver supported by AIOStreams.", (("email", "Email", True), ("password", "Password", True)), ("torrent", "stream")),
    ProviderDefinition("seedr", "Seedr", "Debrid", "Cloud torrent service supported by AIOStreams.", (("encodedToken", "Encoded token", True),), ("torrent", "stream")),
    ProviderDefinition("easynews", "Easynews", "Usenet", "Usenet search and streaming provider.", (("username", "Username", False), ("password", "Password", True)), ("usenet", "stream")),
    ProviderDefinition("nzbdav", "InfiniDysk / NzbDAV", "Usenet", "SAB/Newznab/WebDAV bridge used by the DUMB stack.", (("url", "URL", False), ("publicUrl", "Public URL", False), ("apiKey", "API key", True), ("username", "Username", False), ("password", "Password", True), ("aiostreamsAuth", "AIOStreams auth", True)), ("usenet", "sab", "stream")),
    ProviderDefinition("altmount", "AltMount", "Usenet", "Alternative mounted Usenet provider.", (("url", "URL", False), ("publicUrl", "Public URL", False), ("apiKey", "API key", True), ("username", "WebDAV username", False), ("password", "WebDAV password", True), ("aiostreamsAuth", "AIOStreams auth", True)), ("usenet", "mount", "stream")),
    ProviderDefinition("stremio_nntp", "Stremio NNTP", "Usenet", "Direct NNTP servers use AIOStreams structured server configuration; manage this provider in AIOStreams.", tuple(), ("usenet", "stream"), aiostreams=False),
    ProviderDefinition("stremthru_newz", "StremThru Newz", "Usenet", "StremThru Newz resolver service.", (("url", "URL", False), ("authToken", "Auth token", True)), ("usenet", "stream")),
    ProviderDefinition("aiostreams", "AIOStreams Native", "Streaming", "AIOStreams built-in Usenet engine; NNTP providers are configured by the AIOStreams administrator.", (("aiostreamsAuth", "AIOStreams auth token", True),), ("usenet", "stream")),
    ProviderDefinition("torrin", "Torrin", "Streaming", "Resolver service supported by AIOStreams.", (("apiKey", "API key", True),), ("torrent", "stream")),
)

_PROVIDER_MAP = {p.id: p for p in PROVIDERS}


def provider_definition(provider_id: str) -> ProviderDefinition | None:
    return _PROVIDER_MAP.get(str(provider_id or "").strip().lower())


def _prefix(provider_id: str) -> str:
    return f"provider.{provider_id}."


def provider_state(provider_id: str, mask: bool = True) -> dict[str, Any]:
    definition = provider_definition(provider_id)
    if not definition:
        raise ValueError("Unknown provider")
    prefix = _prefix(definition.id)
    enabled = setting_get(prefix + "enabled", "false").lower() in {"1", "true", "yes", "on"}
    credentials: dict[str, str] = {}
    configured_fields: list[str] = []
    for field, _label, secret in definition.credential_fields:
        value = setting_get(prefix + field, "")
        if value:
            configured_fields.append(field)
        credentials[field] = "********" if mask and secret and value else value
    return {
        "id": definition.id,
        "name": definition.name,
        "category": definition.category,
        "description": definition.description,
        "capabilities": list(definition.capabilities),
        "credential_fields": [
            {"id": f, "label": label, "secret": secret, "configured": f in configured_fields}
            for f, label, secret in definition.credential_fields
        ],
        "credentials": credentials,
        "enabled": enabled,
        "configured": enabled and (bool(configured_fields) or not definition.credential_fields),
        "aiostreams": definition.aiostreams,
    }


def list_provider_states(mask: bool = True) -> list[dict[str, Any]]:
    return [provider_state(p.id, mask=mask) for p in PROVIDERS]


def save_provider(provider_id: str, enabled: bool, values: dict[str, str]) -> None:
    definition = provider_definition(provider_id)
    if not definition:
        raise ValueError("Unknown provider")
    prefix = _prefix(definition.id)
    setting_set(prefix + "enabled", "true" if enabled else "false", False)
    allowed = {field: secret for field, _label, secret in definition.credential_fields}
    for field, raw in values.items():
        if field not in allowed:
            continue
        value = str(raw or "").strip()
        if not value or value in {"********", "••••••••"}:
            continue
        setting_set(prefix + field, value, bool(allowed[field]))


def provider_credentials_for_aiostreams() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for definition in PROVIDERS:
        state = provider_state(definition.id, mask=False)
        if not state["enabled"] or not definition.aiostreams:
            continue
        creds = {k: v for k, v in state["credentials"].items() if v}
        # Credential-less AIOStreams-native services are still valid selections.
        if creds or not definition.credential_fields:
            out[definition.id] = creds
    return out


def categories() -> list[str]:
    return list(dict.fromkeys(p.category for p in PROVIDERS))


def migrate_legacy_providers() -> list[str]:
    """Conservatively seed v9 provider records from clearly named legacy settings.

    This is intentionally narrow: only exact historical ArrNexus keys are read,
    and existing provider-registry values always win.
    """
    migrated: list[str] = []
    legacy_map = {
        "realdebrid": {
            "apiKey": ("realdebrid.api_key", "realdebrid.token", "aiostreams.realdebrid.api_key"),
        },
        "nzbdav": {
            "url": ("connector.nzbdav.url", "ecosystem.infinidysk.url", "ecosystem.nzbdav.url"),
            "apiKey": ("connector.nzbdav.api_key", "ecosystem.infinidysk.api_key", "ecosystem.nzbdav.api_key"),
            "username": ("connector.nzbdav.username", "ecosystem.nzbdav.username"),
            "password": ("connector.nzbdav.password", "ecosystem.nzbdav.password"),
        },
    }
    for provider_id, fields in legacy_map.items():
        prefix = _prefix(provider_id)
        found = False
        for target, candidates in fields.items():
            if setting_get(prefix + target, ""):
                found = True
                continue
            for key in candidates:
                value = setting_get(key, "")
                if value:
                    definition = provider_definition(provider_id)
                    secret = next((s for f, _l, s in definition.credential_fields if f == target), False) if definition else False
                    setting_set(prefix + target, value, bool(secret))
                    found = True
                    migrated.append(f"{provider_id}.{target}")
                    break
        if found and not setting_get(prefix + "enabled", ""):
            setting_set(prefix + "enabled", "true", False)
    return migrated
