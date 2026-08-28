from __future__ import annotations

import asyncio
from typing import Any

from .db import setting_get, setting_set, log_event, add_activity
from .instances import discover_instances, ArrInstance
from .router_service import client_for_instance


def _bool(key: str, default: bool = False) -> bool:
    return setting_get(key, "true" if default else "false").lower() in {"1","true","yes","on"}


def settings_state() -> dict[str, Any]:
    try:
        interval = max(15, int(setting_get("selfheal.interval_minutes", "60") or 60))
    except Exception:
        interval = 60
    try:
        max_actions = max(1, min(50, int(setting_get("selfheal.max_actions", "3") or 3)))
    except Exception:
        max_actions = 3
    return {
        "enabled": _bool("selfheal.enabled", False),
        "search_missing": _bool("selfheal.search_missing", True),
        "search_upgrades": _bool("selfheal.search_upgrades", False),
        "interval_minutes": interval,
        "max_actions": max_actions,
        "window_start": setting_get("selfheal.window_start", "02:00"),
        "window_end": setting_get("selfheal.window_end", "06:00"),
    }


def save_settings(*, enabled: bool, search_missing: bool, search_upgrades: bool, interval_minutes: int, max_actions: int, window_start: str, window_end: str):
    setting_set("selfheal.enabled", "true" if enabled else "false")
    setting_set("selfheal.search_missing", "true" if search_missing else "false")
    setting_set("selfheal.search_upgrades", "true" if search_upgrades else "false")
    setting_set("selfheal.interval_minutes", str(max(15, int(interval_minutes or 60))))
    setting_set("selfheal.max_actions", str(max(1, min(50, int(max_actions or 3)))))
    setting_set("selfheal.window_start", window_start or "02:00")
    setting_set("selfheal.window_end", window_end or "06:00")


def _quality_unmet(row: dict) -> bool:
    mf = row.get("movieFile") or row.get("episodeFile") or {}
    return bool(row.get("qualityCutoffNotMet") or mf.get("qualityCutoffNotMet"))


async def inspect_instance(inst: ArrInstance) -> dict[str, Any]:
    client = client_for_instance(inst)
    row: dict[str, Any] = {
        "service": inst.service,
        "instance": inst.instance,
        "destination_key": inst.destination_key or "default",
        "ok": True,
        "missing": [],
        "upgrades": [],
        "queue_issues": [],
        "counts": {"missing":0,"upgrades":0,"queue_issues":0},
    }
    try:
        if inst.service == "radarr":
            items = await client.movies()
            for item in items or []:
                if item.get("monitored", True) and not item.get("hasFile"):
                    row["missing"].append({"id": item.get("id"), "title": item.get("title"), "year": item.get("year")})
                if item.get("hasFile") and _quality_unmet(item):
                    row["upgrades"].append({"id": item.get("id"), "title": item.get("title"), "year": item.get("year")})
        elif inst.service == "sonarr":
            items = await client.series()
            for item in items or []:
                stats = item.get("statistics") or {}
                total = int(stats.get("episodeCount") or 0)
                have = int(stats.get("episodeFileCount") or 0)
                missing = max(0, total - have)
                if item.get("monitored", True) and missing:
                    row["missing"].append({"id": item.get("id"), "title": item.get("title"), "missing": missing, "have": have, "total": total})
        elif inst.service == "lidarr":
            items = await client.artists()
            for item in items or []:
                stats = item.get("statistics") or {}
                total = int(stats.get("trackCount") or 0)
                have = int(stats.get("trackFileCount") or 0)
                missing = max(0, total - have)
                if item.get("monitored", True) and total and missing:
                    row["missing"].append({"id": item.get("id"), "title": item.get("artistName") or item.get("foreignArtistId") or "Artist", "missing": missing, "have": have, "total": total})
        try:
            queue = await client.queue(100)
            records = queue.get("records", []) if isinstance(queue, dict) else queue or []
            for q in records:
                status = str(q.get("status") or q.get("trackedDownloadState") or "").lower()
                message = str((q.get("statusMessages") or [{}])[0].get("messages", [""])[0] if q.get("statusMessages") else q.get("errorMessage") or "")
                if status in {"warning","failed","error"} or "import" in message.lower() or "stalled" in message.lower():
                    row["queue_issues"].append({"title": q.get("title") or q.get("downloadId") or "Queue item", "status": status or "warning", "message": message})
        except Exception:
            pass
    except Exception as exc:
        row["ok"] = False
        row["error"] = str(exc)
    row["counts"] = {"missing":len(row["missing"]),"upgrades":len(row["upgrades"]),"queue_issues":len(row["queue_issues"])}
    return row


async def scan_self_healing() -> list[dict[str, Any]]:
    instances = [i for i in discover_instances() if i.api_key]
    if not instances:
        return []
    results = await asyncio.gather(*(inspect_instance(i) for i in instances), return_exceptions=True)
    rows: list[dict[str, Any]] = []
    for inst, result in zip(instances, results):
        if isinstance(result, Exception):
            rows.append({"service":inst.service,"instance":inst.instance,"destination_key":inst.destination_key or "default","ok":False,"error":str(result),"missing":[],"upgrades":[],"queue_issues":[],"counts":{"missing":0,"upgrades":0,"queue_issues":0}})
        else:
            rows.append(result)
    return rows


def _find_instance(service: str, instance: str) -> ArrInstance:
    found = next((i for i in discover_instances() if i.service == service and i.instance == instance), None)
    if not found:
        raise RuntimeError(f"{service}/{instance} is not currently discovered")
    if not found.api_key:
        raise RuntimeError(f"{service}/{instance} has no usable API key")
    return found


async def trigger_search(service: str, instance: str, kind: str = "missing", limit: int = 10) -> dict[str, Any]:
    inst = _find_instance(service, instance)
    state = await inspect_instance(inst)
    candidates = state.get("missing" if kind == "missing" else "upgrades", [])[:max(1, min(25, int(limit or 10)))]
    client = client_for_instance(inst)
    completed = 0
    errors: list[str] = []
    for item in candidates:
        try:
            item_id = int(item.get("id") or 0)
            if not item_id:
                continue
            if service == "radarr":
                await client.search(item_id)
            elif service == "sonarr":
                await client.search(item_id)
            elif service == "lidarr":
                await client.search_artist(item_id)
            completed += 1
        except Exception as exc:
            errors.append(f"{item.get('title')}: {exc}")
        await asyncio.sleep(0.05)
    log_event("info" if not errors else "warning", "selfheal", "search_triggered", f"{service}/{instance}: {completed} {kind} search(es) triggered", {"errors":errors[:10]})
    add_activity("self-heal", f"{service}/{instance}", f"Triggered {completed} {kind} search(es)")
    return {"completed":completed,"errors":errors,"total":len(candidates)}


async def automatic_cycle() -> dict[str, Any]:
    cfg = settings_state()
    if not cfg["enabled"]:
        return {"ran":False,"reason":"disabled"}
    rows = await scan_self_healing()
    remaining = cfg["max_actions"]
    actions = []
    for row in rows:
        if remaining <= 0 or not row.get("ok"):
            continue
        if cfg["search_missing"] and row.get("missing"):
            n = min(remaining, len(row["missing"]))
            res = await trigger_search(row["service"], row["instance"], "missing", n)
            actions.append({"service":row["service"],"instance":row["instance"],"kind":"missing",**res})
            remaining -= res.get("completed",0)
        if remaining > 0 and cfg["search_upgrades"] and row.get("upgrades"):
            n = min(remaining, len(row["upgrades"]))
            res = await trigger_search(row["service"], row["instance"], "upgrades", n)
            actions.append({"service":row["service"],"instance":row["instance"],"kind":"upgrades",**res})
            remaining -= res.get("completed",0)
    return {"ran":True,"actions":actions}


def _within_window(start: str, end: str) -> bool:
    from datetime import datetime
    now = datetime.now().time()
    try:
        sh, sm = (int(x) for x in start.split(":", 1))
        eh, em = (int(x) for x in end.split(":", 1))
        from datetime import time
        s, e = time(sh, sm), time(eh, em)
        if s <= e:
            return s <= now <= e
        return now >= s or now <= e
    except Exception:
        return True


async def scheduler_loop():
    """Conservative optional scheduler. Disabled by default.

    It wakes every minute, respects the configured maintenance window and never
    triggers more than the configured max_actions per cycle.
    """
    import time
    while True:
        try:
            cfg = settings_state()
            if cfg["enabled"] and _within_window(cfg["window_start"], cfg["window_end"]):
                try:
                    last = float(setting_get("selfheal.last_run_epoch", "0") or 0)
                except Exception:
                    last = 0
                interval = max(15, int(cfg["interval_minutes"])) * 60
                if time.time() - last >= interval:
                    result = await automatic_cycle()
                    setting_set("selfheal.last_run_epoch", str(time.time()))
                    log_event("info", "selfheal", "automatic_cycle", "Automatic self-healing cycle completed", result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                log_event("error", "selfheal", "automatic_cycle_failed", str(exc))
            except Exception:
                pass
        await asyncio.sleep(60)
