from __future__ import annotations

from typing import Any

from .connections import get_connection
from .db import list_mounts, setting_get
from .namespace import namespace_status
from .providers import list_provider_states

CORE_SERVICES = ("radarr", "sonarr", "prowlarr")
OPTIONAL_SERVICES = ("lidarr", "jellyfin", "seerr")


def stack_readiness() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(key: str, label: str, ok: bool, detail: str, required: bool = True, action: str = ""):
        checks.append({"key": key, "label": label, "ok": bool(ok), "detail": detail, "required": required, "action": action})

    for service in CORE_SERVICES + OPTIONAL_SERVICES:
        conn = get_connection(service)
        configured = bool((conn.url or "").strip() and (conn.api_key or "").strip())
        add(
            f"connection-{service}", f"{service.title()} connection", configured,
            "URL and API key saved" if configured else "Not configured",
            required=service in CORE_SERVICES, action="/arrs",
        )

    ns = namespace_status()
    add("namespace", "DUMB / Arr mount namespace", bool(ns.get("ok")), str(ns.get("detail") or ns.get("error") or "Namespace discovery"), True, "/settings")

    mounts = list_mounts(False)
    add("mounts", "Library mount registry", bool(mounts), f"{len(mounts)} mount(s) registered" if mounts else "No library mounts registered", True, "/settings")

    providers = list_provider_states(mask=True)
    configured_providers = [p for p in providers if p.get("configured")]
    add("providers", "Acquisition provider", bool(configured_providers), f"{len(configured_providers)} provider(s) configured" if configured_providers else "No provider configured yet", False, "/providers")

    required = [c for c in checks if c["required"]]
    optional = [c for c in checks if not c["required"]]
    required_weight = 85
    optional_weight = 15
    score = 0
    if required:
        score += round(required_weight * sum(1 for c in required if c["ok"]) / len(required))
    if optional:
        score += round(optional_weight * sum(1 for c in optional if c["ok"]) / len(optional))
    return {
        "score": min(100, score),
        "checks": checks,
        "ready": all(c["ok"] for c in required),
        "required_ok": sum(1 for c in required if c["ok"]),
        "required_total": len(required),
        "provider_count": len(configured_providers),
        "mount_count": len(mounts),
    }
