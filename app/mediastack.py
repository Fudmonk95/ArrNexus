from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import os
import time
from typing import Any

import httpx

from .db import log_event, setting_get
from . import stack_setup

AGENT_URL = os.getenv("MEDIASTACK_AGENT_URL", "http://arrnexus-stack-agent:8787").rstrip("/")
AGENT_TOKEN = os.getenv("MEDIASTACK_AGENT_TOKEN", "").strip()
FAST_INTERVAL = max(5.0, float(os.getenv("MEDIASTACK_REFRESH_SECONDS", "10")))
UPDATE_INTERVAL = max(60.0, float(os.getenv("MEDIASTACK_UPDATE_SECONDS", "1800")))
CONFIG_INTERVAL = max(30.0, float(os.getenv("MEDIASTACK_CONFIG_SECONDS", "300")))
JOB_INTERVAL = max(2.0, float(os.getenv("MEDIASTACK_JOB_SECONDS", "5")))

_CACHE: dict[str, Any] = {
    "agent_url": AGENT_URL,
    "connected": False,
    "updated_at": "",
    "updated_monotonic": 0.0,
    "error": "",
    "health": {},
    "capabilities": {},
    "system": {},
    "services": [],
    "updates": {},
    "configs": {"roots": {}, "files": []},
    "jobs": [],
    "updates_checked_at": "",
    "configs_checked_at": "",
    "jobs_checked_at": "",
}
_LOCK = asyncio.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _human_bytes(value: int | float | None) -> str:
    if value is None:
        return "—"
    amount = float(max(0, value))
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} PiB"


def _resource_pressure(item: dict[str, Any]) -> tuple[str, list[str]]:
    """Classify current pressure separately from Docker's health status.

    CPU may legitimately exceed 100% on a multi-core host, so it is displayed
    rather than treated as a fault. Historical restarts are useful context but
    are only a warning by themselves; current memory pressure can make the
    status critical.
    """
    memory_percent = float(item.get("memory_percent") or 0)
    restart_count = int(item.get("restart_count") or 0)
    alerts: list[str] = []
    severity = "good"

    if memory_percent >= 95:
        severity = "bad"
        alerts.append(f"RAM {memory_percent:.1f}% of container limit")
    elif memory_percent >= 85:
        severity = "warn"
        alerts.append(f"RAM {memory_percent:.1f}% of container limit")

    if restart_count >= 2:
        if severity == "good":
            severity = "warn"
        alerts.append(f"{restart_count} historical container restarts")

    return severity, alerts


def _decorate_service(row: dict[str, Any], update_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    item = dict(row)
    state = str(item.get("state") or "unknown").lower()
    health = str(item.get("health") or "").lower()
    if state != "running":
        status_class = "bad"
        status_text = state or "stopped"
    elif health and health != "healthy":
        status_class = "warn" if health == "starting" else "bad"
        status_text = health
    else:
        status_class = "good"
        status_text = health or "running"

    resource_class, resource_alerts = _resource_pressure(item)
    update = update_map.get(str(item.get("name") or ""), {})
    item.update(
        {
            "status_class": status_class,
            "status_text": status_text,
            "resource_class": resource_class,
            "resource_alerts": resource_alerts,
            "resource_attention": bool(resource_alerts),
            "memory_human": _human_bytes(item.get("memory_used")),
            "memory_limit_human": _human_bytes(item.get("memory_limit")),
            "network_rx_human": _human_bytes(item.get("network_rx")),
            "network_tx_human": _human_bytes(item.get("network_tx")),
            "update": update,
            "update_available": bool(update.get("update_available")),
        }
    )
    return item


def _decorate_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(raw)
    update_rows = ((out.get("updates") or {}).get("services") or [])
    update_map = {str(row.get("name") or ""): row for row in update_rows}
    services = [_decorate_service(row, update_map) for row in (out.get("services") or [])]
    out["services"] = services

    running = sum(1 for row in services if str(row.get("state") or "").lower() == "running")
    healthy = sum(1 for row in services if row.get("status_class") == "good")
    updates = sum(1 for row in services if row.get("update_available"))
    health_attention = sum(1 for row in services if row.get("status_class") in {"warn", "bad"})
    pressure_attention = sum(1 for row in services if row.get("resource_attention"))
    warnings = sum(1 for row in services if row.get("resource_class") == "warn")
    critical = sum(1 for row in services if row.get("resource_class") == "bad")
    attention_names = sorted(
        {
            str(row.get("name") or "")
            for row in services
            if row.get("status_class") in {"warn", "bad"} or row.get("resource_attention")
        }
    )
    cpu_total = sum(float(row.get("cpu_percent") or 0) for row in services)
    memory_used = sum(int(row.get("memory_used") or 0) for row in services)
    system = out.get("system") or {}
    memory_total = int(system.get("memory_total") or 0)

    out["summary"] = {
        "total": len(services),
        "running": running,
        "healthy": healthy,
        "attention": len(attention_names),
        "health_attention": health_attention,
        "pressure_attention": pressure_attention,
        "warnings": warnings,
        "critical": critical,
        "attention_names": attention_names,
        "updates": updates,
        "cpu_percent": round(cpu_total, 2),
        "memory_used": memory_used,
        "memory_used_human": _human_bytes(memory_used),
        "memory_total": memory_total,
        "memory_total_human": _human_bytes(memory_total),
        "memory_percent": round((memory_used / memory_total) * 100.0, 1) if memory_total else 0.0,
    }
    return out


def _headers(write: bool = False) -> dict[str, str]:
    headers: dict[str, str] = {}
    if write and AGENT_TOKEN:
        headers["Authorization"] = f"Bearer {AGENT_TOKEN}"
    return headers


async def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_payload: dict[str, Any] | None = None,
    timeout: float = 12.0,
    write: bool = False,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(timeout, 4.0))) as client:
        response = await client.request(
            method,
            f"{AGENT_URL}{path}",
            params=params,
            json=json_payload,
            headers=_headers(write),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected MediaStack response from {path}")
        return payload


async def _get(path: str, *, params: dict[str, Any] | None = None, timeout: float = 12.0) -> dict[str, Any]:
    return await _request("GET", path, params=params, timeout=timeout)


async def _post(
    path: str,
    *,
    json_payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    return await _request("POST", path, json_payload=json_payload, timeout=timeout, write=True)


async def refresh_status() -> dict[str, Any]:
    async with _LOCK:
        try:
            health, capabilities, system, containers = await asyncio.gather(
                _get("/health", timeout=5.0),
                _get("/api/capabilities", timeout=5.0),
                _get("/api/system", timeout=8.0),
                _get("/api/containers", timeout=20.0),
            )
            _CACHE.update(
                {
                    "connected": bool(health.get("ok")),
                    "health": health,
                    "capabilities": capabilities,
                    "system": system,
                    "services": containers.get("services") or [],
                    "updated_at": _now_iso(),
                    "updated_monotonic": time.monotonic(),
                    "error": "",
                }
            )
        except Exception as exc:
            _CACHE.update(
                {
                    "connected": False,
                    "updated_at": _now_iso(),
                    "updated_monotonic": time.monotonic(),
                    "error": str(exc),
                }
            )
    return cached_snapshot()


async def refresh_updates() -> dict[str, Any]:
    try:
        payload = await _get("/api/updates", timeout=60.0)
        async with _LOCK:
            _CACHE["updates"] = payload
            _CACHE["updates_checked_at"] = _now_iso()
        return payload
    except Exception as exc:
        async with _LOCK:
            current = dict(_CACHE.get("updates") or {})
            current["error"] = str(exc)
            _CACHE["updates"] = current
            _CACHE["updates_checked_at"] = _now_iso()
        return _CACHE["updates"]


async def refresh_configs() -> dict[str, Any]:
    try:
        payload = await _get("/api/configs", timeout=20.0)
        async with _LOCK:
            _CACHE["configs"] = payload
            _CACHE["configs_checked_at"] = _now_iso()
        return payload
    except Exception as exc:
        async with _LOCK:
            current = dict(_CACHE.get("configs") or {})
            current["error"] = str(exc)
            _CACHE["configs"] = current
            _CACHE["configs_checked_at"] = _now_iso()
        return _CACHE["configs"]


async def refresh_jobs() -> dict[str, Any]:
    try:
        payload = await _get("/api/jobs", timeout=8.0)
        async with _LOCK:
            _CACHE["jobs"] = payload.get("jobs") or []
            _CACHE["jobs_checked_at"] = _now_iso()
        return payload
    except Exception as exc:
        return {"ok": False, "error": str(exc), "jobs": list(_CACHE.get("jobs") or [])}


async def refresh_all() -> dict[str, Any]:
    await refresh_status()
    await asyncio.gather(refresh_updates(), refresh_configs(), refresh_jobs())
    return cached_snapshot()


def cached_snapshot() -> dict[str, Any]:
    out = _decorate_snapshot(_CACHE)
    updated = float(_CACHE.get("updated_monotonic") or 0)
    out["age_seconds"] = max(0.0, time.monotonic() - updated) if updated else None
    out["write_enabled"] = bool((out.get("capabilities") or {}).get("write_enabled"))
    return out


async def status_loop() -> None:
    while True:
        try:
            await refresh_status()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(FAST_INTERVAL)


async def metadata_loop() -> None:
    await asyncio.sleep(2.0)
    last_updates = 0.0
    last_configs = 0.0
    while True:
        try:
            now = time.monotonic()
            jobs = [refresh_jobs()]
            if not last_updates or now - last_updates >= UPDATE_INTERVAL:
                jobs.append(refresh_updates())
                last_updates = now
            if not last_configs or now - last_configs >= CONFIG_INTERVAL:
                jobs.append(refresh_configs())
                last_configs = now
            await asyncio.gather(*jobs)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(max(5.0, min(30.0, JOB_INTERVAL)))


async def logs(name: str, tail: int = 250) -> dict[str, Any]:
    safe_tail = max(1, min(2000, int(tail)))
    return await _get(f"/api/logs/{name}", params={"tail": safe_tail}, timeout=15.0)


async def config_file(root: str, path: str) -> dict[str, Any]:
    return await _get("/api/config", params={"root": root, "path": path}, timeout=15.0)


async def discovery() -> dict[str, Any]:
    return await _get("/api/discovery", timeout=20.0)


async def update_plan(name: str) -> dict[str, Any]:
    return await _get(f"/api/update-plan/{name}", timeout=20.0)


async def lifecycle(name: str, action: str) -> dict[str, Any]:
    result = await _post(f"/api/actions/{name}/{action}", timeout=75.0)
    log_event("info", "mediastack", "lifecycle", f"{action.title()} requested for {name}", result)
    await refresh_status()
    return result


async def start_update(name: str) -> dict[str, Any]:
    result = await _post(f"/api/actions/{name}/update", timeout=20.0)
    log_event("info", "mediastack", "update_queued", f"Update queued for {name}", result)
    return result


async def job(job_id: str) -> dict[str, Any]:
    return await _get(f"/api/jobs/{job_id}", timeout=10.0)


async def adopt(names: list[str]) -> dict[str, Any]:
    result = await _post("/api/adopt", json_payload={"names": names}, timeout=20.0)
    log_event("info", "mediastack", "adopt", "Containers adopted into MediaStack", {"names": names})
    await refresh_status()
    return result


def _local_minute() -> str:
    # The container receives TZ=Europe/London in the final stack. datetime.now()
    # therefore follows the operator-configured local timezone without needing a
    # second timezone dependency.
    return datetime.now().strftime("%H:%M")


async def _wait_update_job(job_id: str, timeout: int = 900) -> dict[str, Any]:
    started = time.monotonic()
    last: dict[str, Any] = {}
    while time.monotonic() - started < timeout:
        payload = await job(job_id)
        current = payload.get("job") or {}
        last = current
        if current.get("status") in {"complete", "failed"}:
            return current
        await asyncio.sleep(5)
    raise RuntimeError(f"update job {job_id} timed out (last stage={last.get('stage')})")


async def update_scheduler_loop() -> None:
    """Run opt-in sequential automatic updates inside the maintenance window.

    Automatic updates are disabled unless all of these are true:
    - setup mode is configured for automatic updates;
    - the Stack Agent write channel is explicitly enabled;
    - the individual service policy is `automatic`;
    - an image digest change is currently reported.

    One failed service stops the cycle. Zurg/Jellyfin/PostgreSQL/ArrNexus are
    manual by default in stack_setup and must be explicitly overridden.
    """
    await asyncio.sleep(20)
    last_run_date = ""
    while True:
        try:
            setup_state = stack_setup.state()
            if str(setup_state.get("update_mode") or "review") != "automatic":
                await asyncio.sleep(30)
                continue

            if _local_minute() != str(setup_state.get("update_time") or "04:00"):
                await asyncio.sleep(20)
                continue

            today = datetime.now().strftime("%Y-%m-%d")
            if last_run_date == today:
                await asyncio.sleep(30)
                continue

            snapshot = await refresh_status()
            if not snapshot.get("write_enabled"):
                log_event("warning", "mediastack", "auto_update_skipped", "Automatic update window reached but Stack Agent write actions are disabled")
                last_run_date = today
                await asyncio.sleep(60)
                continue

            await refresh_updates()
            snapshot = cached_snapshot()
            policies = stack_setup.service_policies(setup_state)
            candidates = [
                row
                for row in snapshot.get("services") or []
                if row.get("update_available") and policies.get(str(row.get("name") or "")) == "automatic"
            ]
            last_run_date = today
            if not candidates:
                log_event("info", "mediastack", "auto_update", "Automatic update window: no eligible updates")
                await asyncio.sleep(60)
                continue

            log_event(
                "info",
                "mediastack",
                "auto_update_start",
                f"Starting automatic update window for {len(candidates)} service(s)",
                {"services": [row.get("name") for row in candidates]},
            )
            for row in candidates:
                name = str(row.get("name") or "")
                queued = await start_update(name)
                result = await _wait_update_job(str(queued.get("job_id") or ""))
                if result.get("status") != "complete":
                    log_event("error", "mediastack", "auto_update_failed", f"Automatic update failed for {name}; remaining updates were skipped", result)
                    break
                log_event("info", "mediastack", "auto_update_complete", f"Automatic update completed for {name}", result)
                await refresh_status()
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_event("error", "mediastack", "auto_update_error", str(exc))
        await asyncio.sleep(20)
