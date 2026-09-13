from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import os
import time
from typing import Any

import httpx

AGENT_URL = os.getenv("MEDIASTACK_AGENT_URL", "http://arrnexus-stack-agent:8787").rstrip("/")
FAST_INTERVAL = max(5.0, float(os.getenv("MEDIASTACK_REFRESH_SECONDS", "10")))
UPDATE_INTERVAL = max(60.0, float(os.getenv("MEDIASTACK_UPDATE_SECONDS", "1800")))
CONFIG_INTERVAL = max(30.0, float(os.getenv("MEDIASTACK_CONFIG_SECONDS", "300")))

_CACHE: dict[str, Any] = {
    "agent_url": AGENT_URL,
    "connected": False,
    "updated_at": "",
    "updated_monotonic": 0.0,
    "error": "",
    "health": {},
    "system": {},
    "services": [],
    "updates": {},
    "configs": {"roots": {}, "files": []},
    "updates_checked_at": "",
    "configs_checked_at": "",
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

    update = update_map.get(str(item.get("name") or ""), {})
    item.update(
        {
            "status_class": status_class,
            "status_text": status_text,
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
    attention = sum(1 for row in services if row.get("status_class") in {"warn", "bad"})
    cpu_total = sum(float(row.get("cpu_percent") or 0) for row in services)
    memory_used = sum(int(row.get("memory_used") or 0) for row in services)
    system = out.get("system") or {}
    memory_total = int(system.get("memory_total") or 0)

    out["summary"] = {
        "total": len(services),
        "running": running,
        "healthy": healthy,
        "attention": attention,
        "updates": updates,
        "cpu_percent": round(cpu_total, 2),
        "memory_used": memory_used,
        "memory_used_human": _human_bytes(memory_used),
        "memory_total": memory_total,
        "memory_total_human": _human_bytes(memory_total),
        "memory_percent": round((memory_used / memory_total) * 100.0, 1) if memory_total else 0.0,
    }
    return out


async def _get(path: str, *, params: dict[str, Any] | None = None, timeout: float = 12.0) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(timeout, 4.0))) as client:
        response = await client.get(f"{AGENT_URL}{path}", params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected MediaStack response from {path}")
        return payload


async def refresh_status() -> dict[str, Any]:
    async with _LOCK:
        try:
            health, system, containers = await asyncio.gather(
                _get("/health", timeout=5.0),
                _get("/api/system", timeout=8.0),
                _get("/api/containers", timeout=15.0),
            )
            _CACHE.update(
                {
                    "connected": bool(health.get("ok")),
                    "health": health,
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
        payload = await _get("/api/updates", timeout=45.0)
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


async def refresh_all() -> dict[str, Any]:
    await refresh_status()
    await asyncio.gather(refresh_updates(), refresh_configs())
    return cached_snapshot()


def cached_snapshot() -> dict[str, Any]:
    out = _decorate_snapshot(_CACHE)
    updated = float(_CACHE.get("updated_monotonic") or 0)
    out["age_seconds"] = max(0.0, time.monotonic() - updated) if updated else None
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
            jobs = []
            if not last_updates or now - last_updates >= UPDATE_INTERVAL:
                jobs.append(refresh_updates())
                last_updates = now
            if not last_configs or now - last_configs >= CONFIG_INTERVAL:
                jobs.append(refresh_configs())
                last_configs = now
            if jobs:
                await asyncio.gather(*jobs)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(30.0)


async def logs(name: str, tail: int = 250) -> dict[str, Any]:
    safe_tail = max(1, min(2000, int(tail)))
    return await _get(f"/api/logs/{name}", params={"tail": safe_tail}, timeout=15.0)


async def config_file(root: str, path: str) -> dict[str, Any]:
    return await _get("/api/config", params={"root": root, "path": path}, timeout=15.0)
