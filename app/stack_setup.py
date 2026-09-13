from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .db import setting_get, setting_set
from .mediastack_catalog import (
    AUTO_UPDATE_BLOCKED_DEFAULT,
    SERVICE_CATALOG,
    catalog as service_catalog,
    expand_dependencies,
    grouped_catalog,
)

STATE_KEY = "mediastack.setup.state"
COMPLETED_KEY = "mediastack.setup.completed"
UPDATE_MODE_KEY = "mediastack.update.mode"
UPDATE_TIME_KEY = "mediastack.update.time"
SERVICE_POLICIES_KEY = "mediastack.update.service_policies"

DEFAULT_STATE: dict[str, Any] = {
    "mode": "adopt",
    "selected_services": ["zurg", "sonarr", "radarr", "lidarr", "prowlarr", "seerrng", "jellyfin"],
    "stack_root": "/opt/arrnexus-mediastack",
    "zurg_mount_root": "/zurg_mnt",
    "config_strategy": "keep-existing",
    "tz": "Europe/London",
    "puid": 1000,
    "pgid": 1000,
    "jellyfin_gpu": "auto",
    "update_mode": "review",
    "update_time": "04:00",
    "rollback": True,
    "stabilization_seconds": 30,
}


def _load_json_setting(key: str, default: Any) -> Any:
    raw = setting_get(key, "")
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def state() -> dict[str, Any]:
    current = dict(DEFAULT_STATE)
    loaded = _load_json_setting(STATE_KEY, {})
    if isinstance(loaded, dict):
        current.update(loaded)
    current["selected_services"] = expand_dependencies(current.get("selected_services") or [])
    current["completed"] = setting_get(COMPLETED_KEY, "").lower() in {"1", "true", "yes"}
    return current


def _valid_time(value: str) -> str:
    value = str(value or "").strip()
    try:
        hour_s, minute_s = value.split(":", 1)
        hour = int(hour_s)
        minute = int(minute_s)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    except Exception:
        pass
    return "04:00"


def normalise(payload: dict[str, Any]) -> dict[str, Any]:
    selected = payload.get("selected_services") or []
    if isinstance(selected, str):
        selected = [part.strip() for part in selected.split(",") if part.strip()]
    selected = expand_dependencies(selected)

    mode = str(payload.get("mode") or "adopt").strip().lower()
    if mode not in {"adopt", "fresh"}:
        mode = "adopt"

    config_strategy = str(payload.get("config_strategy") or "keep-existing").strip().lower()
    if config_strategy not in {"keep-existing", "managed-root"}:
        config_strategy = "keep-existing"

    update_mode = str(payload.get("update_mode") or "review").strip().lower()
    if update_mode not in {"manual", "review", "automatic"}:
        update_mode = "review"

    gpu = str(payload.get("jellyfin_gpu") or "auto").strip().lower()
    if gpu not in {"auto", "enabled", "disabled"}:
        gpu = "auto"

    def _int(name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(payload.get(name, default))
        except Exception:
            value = default
        return max(minimum, min(maximum, value))

    return {
        "mode": mode,
        "selected_services": selected,
        "stack_root": str(payload.get("stack_root") or DEFAULT_STATE["stack_root"]).strip() or DEFAULT_STATE["stack_root"],
        "zurg_mount_root": str(payload.get("zurg_mount_root") or DEFAULT_STATE["zurg_mount_root"]).strip() or DEFAULT_STATE["zurg_mount_root"],
        "config_strategy": config_strategy,
        "tz": str(payload.get("tz") or DEFAULT_STATE["tz"]).strip() or DEFAULT_STATE["tz"],
        "puid": _int("puid", 1000, 1, 65535),
        "pgid": _int("pgid", 1000, 1, 65535),
        "jellyfin_gpu": gpu,
        "update_mode": update_mode,
        "update_time": _valid_time(str(payload.get("update_time") or "04:00")),
        "rollback": bool(payload.get("rollback", True)),
        "stabilization_seconds": _int("stabilization_seconds", 30, 5, 300),
    }


def save(payload: dict[str, Any], *, complete: bool = False) -> dict[str, Any]:
    current = normalise(payload)
    setting_set(STATE_KEY, json.dumps(current, separators=(",", ":"), ensure_ascii=False))
    setting_set(UPDATE_MODE_KEY, current["update_mode"])
    setting_set(UPDATE_TIME_KEY, current["update_time"])
    if complete:
        setting_set(COMPLETED_KEY, "true")
    return state()


def service_policies(current_state: dict[str, Any] | None = None) -> dict[str, str]:
    saved = _load_json_setting(SERVICE_POLICIES_KEY, {})
    if not isinstance(saved, dict):
        saved = {}
    current_state = current_state or state()
    mode = str(current_state.get("update_mode") or "review")
    policies: dict[str, str] = {}
    for key in current_state.get("selected_services") or []:
        info = SERVICE_CATALOG.get(key) or {}
        default = str(info.get("update_policy") or "manual")
        policy = str(saved.get(key) or (mode if mode != "automatic" else default))
        if key in AUTO_UPDATE_BLOCKED_DEFAULT and key not in saved:
            policy = "manual"
        if policy not in {"manual", "review", "automatic"}:
            policy = "manual"
        policies[key] = policy
    return policies


def save_service_policies(policies: dict[str, Any]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for key, value in (policies or {}).items():
        if key not in SERVICE_CATALOG:
            continue
        policy = str(value or "").lower()
        if policy in {"manual", "review", "automatic"}:
            clean[key] = policy
    setting_set(SERVICE_POLICIES_KEY, json.dumps(clean, separators=(",", ":"), ensure_ascii=False))
    return service_policies()


def _live_map(stack_snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("name") or ""): row for row in (stack_snapshot.get("services") or [])}


def adoption_plan(stack_snapshot: dict[str, Any], current_state: dict[str, Any] | None = None) -> dict[str, Any]:
    current_state = current_state or state()
    selected = set(current_state.get("selected_services") or [])
    live = _live_map(stack_snapshot)
    rows: list[dict[str, Any]] = []

    for key in current_state.get("selected_services") or []:
        info = SERVICE_CATALOG.get(key) or {}
        found = live.get(key)
        if not found and key == "seerrng":
            found = live.get("seerr")
        rows.append(
            {
                "key": key,
                "name": info.get("name") or key,
                "present": bool(found),
                "container": found.get("name") if found else "",
                "image": (found or {}).get("image") or info.get("image") or "",
                "state": (found or {}).get("state") or "not installed",
                "mounts": (found or {}).get("mounts") or [],
                "ports": (found or {}).get("ports") or [],
                "action": "adopt" if found else "install",
                "risk": info.get("update_risk") or "normal",
                "dependencies": info.get("dependencies") or [],
            }
        )

    unmanaged = [
        {"name": name, "image": row.get("image") or "", "state": row.get("state") or ""}
        for name, row in sorted(live.items())
        if name not in selected and name not in {"arrnexus", "arrnexus-stack-agent", "arrnexus-mediastack-ui-test"}
    ]

    return {
        "mode": current_state.get("mode"),
        "selected_count": len(rows),
        "already_present": sum(1 for row in rows if row["present"]),
        "to_install": sum(1 for row in rows if not row["present"]),
        "services": rows,
        "unselected_live_services": unmanaged,
        "config_strategy": current_state.get("config_strategy"),
        "stack_root": current_state.get("stack_root"),
        "zurg_mount_root": current_state.get("zurg_mount_root"),
        "update_mode": current_state.get("update_mode"),
        "update_time": current_state.get("update_time"),
        "rollback": current_state.get("rollback"),
        "service_policies": service_policies(current_state),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def preflight(stack_snapshot: dict[str, Any], current_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run non-destructive setup checks using the Docker state we can observe."""
    current_state = current_state or state()
    selected = set(current_state.get("selected_services") or [])
    live = _live_map(stack_snapshot)
    checks: list[dict[str, Any]] = []

    def add(key: str, label: str, ok: bool, detail: str, *, blocking: bool = False, level: str | None = None) -> None:
        checks.append(
            {
                "key": key,
                "label": label,
                "ok": bool(ok),
                "blocking": bool(blocking and not ok),
                "level": level or ("good" if ok else ("bad" if blocking else "warn")),
                "detail": detail,
            }
        )

    connected = bool(stack_snapshot.get("connected"))
    add("agent", "Stack Agent", connected, "Docker control plane is reachable." if connected else "Stack Agent is not reachable.", blocking=True)

    system = stack_snapshot.get("system") or {}
    docker_ok = bool(system.get("docker_version"))
    add("docker", "Docker engine", docker_ok, f"Docker {system.get('docker_version')} · {system.get('cpus') or '—'} CPUs" if docker_ok else "Docker host details are unavailable.", blocking=True)

    zurg_root = str(current_state.get("zurg_mount_root") or "/zurg_mnt").rstrip("/") or "/zurg_mnt"
    zurg = live.get("zurg")
    if "zurg" in selected:
        if zurg:
            owner_mount = next(
                (
                    m
                    for m in zurg.get("mounts") or []
                    if str(m.get("destination") or "").rstrip("/") == zurg_root
                ),
                None,
            )
            propagation = str((owner_mount or {}).get("propagation") or "")
            add(
                "zurg-propagation",
                "Zurg mount propagation",
                bool(owner_mount) and propagation in {"shared", "rshared"},
                f"{zurg_root} is mounted into Zurg with {propagation or 'unknown'} propagation." if owner_mount else f"No {zurg_root} bind was reported for Zurg.",
                blocking=current_state.get("mode") == "adopt",
            )
        else:
            add("zurg-present", "Zurg", current_state.get("mode") == "fresh", "Zurg is not running yet; fresh install will create it." if current_state.get("mode") == "fresh" else "Zurg was selected for adoption but is not running.", blocking=current_state.get("mode") == "adopt")

    consumer_failures: list[str] = []
    consumers_checked = 0
    for key in selected:
        info = SERVICE_CATALOG.get(key) or {}
        if info.get("uses_zurg_mount") != "consumer":
            continue
        row = live.get(key)
        if not row and key == "seerrng":
            row = live.get("seerr")
        if not row:
            continue
        consumers_checked += 1
        mount = next((m for m in row.get("mounts") or [] if str(m.get("destination") or "").rstrip("/") == zurg_root), None)
        prop = str((mount or {}).get("propagation") or "")
        if not mount or prop not in {"slave", "rslave"}:
            consumer_failures.append(str(row.get("name") or key))
    if consumers_checked:
        add(
            "consumer-propagation",
            "Media consumer propagation",
            not consumer_failures,
            f"{consumers_checked} existing consumer(s) receive {zurg_root} with rslave/slave propagation." if not consumer_failures else "Incorrect/missing propagation: " + ", ".join(consumer_failures),
            blocking=current_state.get("mode") == "adopt",
        )

    # Fresh-install collision check. Existing selected containers are adoption
    # candidates and therefore do not count as conflicts.
    if current_state.get("mode") == "fresh":
        used_ports: dict[str, str] = {}
        for name, row in live.items():
            for port in row.get("ports") or []:
                host_port = str(port.get("host_port") or "")
                if host_port:
                    used_ports.setdefault(host_port, name)
        collisions: list[str] = []
        for key in selected:
            info = SERVICE_CATALOG.get(key) or {}
            port = info.get("port")
            if port and str(port) in used_ports and used_ports[str(port)] != key:
                collisions.append(f"{port} ({used_ports[str(port)]})")
        add("ports", "Port availability", not collisions, "No selected fresh-install ports conflict with observed containers." if not collisions else "Conflicting host ports: " + ", ".join(collisions), blocking=True)

    caps = stack_snapshot.get("capabilities") or {}
    write_enabled = bool(caps.get("write_enabled"))
    add(
        "write-channel",
        "Authenticated Docker write channel",
        write_enabled,
        "Lifecycle/update actions are unlocked." if write_enabled else "Locked by design. You can save/review the plan without changing Docker.",
        blocking=False,
        level="good" if write_enabled else "warn",
    )

    if "jellyfin" in selected:
        jellyfin = live.get("jellyfin")
        if jellyfin:
            add("jellyfin", "Jellyfin adoption", True, f"Existing Jellyfin container found ({jellyfin.get('image') or 'image unknown'}). GPU device access is preserved from Docker inspect during managed recreation.")
        else:
            add("jellyfin", "Jellyfin", True, "Jellyfin will be installed from the pinned known-working image; /dev/dri is requested when GPU mode is enabled/auto.", level="good")

    blocking = sum(1 for check in checks if check.get("blocking"))
    warnings = sum(1 for check in checks if not check.get("ok") and not check.get("blocking"))
    return {"ok": blocking == 0, "blocking": blocking, "warnings": warnings, "checks": checks}


def _yaml_quote(value: Any) -> str:
    text = str(value)
    if not text:
        return "''"
    if all(ch.isalnum() or ch in "._/-:" for ch in text):
        return text
    return json.dumps(text)


def recovery_compose(stack_snapshot: dict[str, Any], current_state: dict[str, Any] | None = None) -> str:
    """Generate a recovery Compose skeleton from the current live bindings.

    This is an export/recovery artifact, not the primary runtime controller.
    ArrNexus manages adopted containers through the Stack Agent so adoption
    does not require a disruptive big-bang recreation of the whole server.
    """
    plan = adoption_plan(stack_snapshot, current_state)
    lines = [
        "name: arrnexus-mediastack",
        "",
        "# Generated by ArrNexus MediaStack. Review before disaster-recovery use.",
        "# Existing bind paths are intentionally preserved when adopting a live server.",
        "services:",
    ]
    for row in plan["services"]:
        key = row["key"]
        info = SERVICE_CATALOG.get(key) or {}
        lines.extend(
            [
                f"  {key}:",
                f"    image: {_yaml_quote(row['image'])}",
                f"    container_name: {_yaml_quote(row['container'] or key)}",
                "    restart: unless-stopped",
                "    labels:",
                '      arrnexus.mediastack: "true"',
            ]
        )
        ports = row.get("ports") or []
        if ports:
            lines.append("    ports:")
            for port in ports:
                host_port = str(port.get("host_port") or "")
                internal = str(port.get("container") or "")
                if host_port and internal:
                    lines.append(f"      - {_yaml_quote(host_port + ':' + internal)}")
        mounts = row.get("mounts") or []
        if mounts:
            lines.append("    volumes:")
            for mount in mounts:
                source = str(mount.get("source") or "")
                target = str(mount.get("destination") or "")
                if not source or not target:
                    continue
                suffix = ""
                if not bool(mount.get("rw", True)):
                    suffix = ":ro"
                propagation = str(mount.get("propagation") or "")
                if propagation and propagation not in {"", "rprivate", "private"}:
                    suffix += f",{propagation}" if suffix else f":{propagation}"
                lines.append(f"      - {_yaml_quote(source + ':' + target + suffix)}")
        if info.get("dependencies"):
            lines.append("    depends_on:")
            for dep in info["dependencies"]:
                if dep in plan["service_policies"]:
                    lines.append(f"      - {dep}")
        lines.append("")
    lines.extend(["networks:", "  default:", "    name: arrnexus-media", ""])
    return "\n".join(lines)


def page_context(stack_snapshot: dict[str, Any]) -> dict[str, Any]:
    current = state()
    return {
        "state": current,
        "catalog": service_catalog(),
        "groups": grouped_catalog(),
        "plan": adoption_plan(stack_snapshot, current),
        "preflight": preflight(stack_snapshot, current),
        "policies": service_policies(current),
    }
