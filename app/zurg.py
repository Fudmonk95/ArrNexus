from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any

import httpx

from .config import settings
from .db import setting_get

_STATUS_CACHE: dict[str, Any] = {
    "updated_monotonic": 0.0,
    "updated_at": "",
    "value": None,
    "error": "",
}
_INDEX_CACHE: dict[str, Any] = {
    "updated_monotonic": 0.0,
    "updated_at": "",
    "data": {"movies": [], "shows": [], "downloads": [], "magic": [], "nzb": []},
    "error": "",
    "building": False,
}
_STORAGE_CACHE: dict[str, Any] = {
    "updated_monotonic": 0.0,
    "updated_at": "",
    "value": None,
    "error": "",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def root() -> Path:
    return Path(setting_get("zurg.root", settings.zurg_root) or settings.zurg_root)


def cache_root() -> Path:
    configured = setting_get("zurg.cache_path", "") or getattr(settings, "zurg_cache_path", "")
    return Path(configured or "/host/zurg-rclone-cache")


def _count_children(path: Path) -> int:
    try:
        return sum(1 for _ in os.scandir(path))
    except OSError:
        return 0


def _version_text(base: Path) -> str:
    path = base / "version.txt"
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()[:4000]
    except OSError:
        return ""


def _version_line(text: str) -> str:
    match = re.search(r"(?im)^version:\s*(.+)$", text or "")
    return match.group(1).strip() if match else ""


def filesystem_status() -> dict[str, Any]:
    base = root()
    required = ("__all__", "__magic__", "movies", "shows")
    exists = base.exists()
    readable = False
    try:
        readable = exists and os.access(base, os.R_OK) and any(True for _ in os.scandir(base))
    except OSError:
        readable = False

    folders = {name: (base / name).exists() for name in required}
    count_names = ("movies", "shows", "music", "__all__", "__downloads__", "__magic__", "__nzb__", "__unplayable__")
    counts = {name: _count_children(base / name) for name in count_names}
    version = _version_text(base)
    return {
        "ok": bool(readable and all(folders.values())),
        "root": str(base),
        "exists": exists,
        "readable": readable,
        "folders": folders,
        "counts": counts,
        "version": version,
        "version_line": _version_line(version),
    }


async def endpoint_status() -> dict[str, Any]:
    url = (setting_get("zurg.url", settings.zurg_url) or settings.zurg_url).rstrip("/")
    if not url:
        return {
            "configured": False,
            "reachable": False,
            "ok": True,
            "url": "",
            "status": None,
            "state": "Not configured",
            "auth_required": False,
        }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0), follow_redirects=True) as client:
            response = await client.get(url + "/")
        status = int(response.status_code)
        auth_required = status in {401, 403}
        reachable = status < 500
        if auth_required:
            state = "Authentication required"
        elif 200 <= status < 400:
            state = "Reachable"
        elif status < 500:
            state = f"Reachable (HTTP {status})"
        else:
            state = f"Server error (HTTP {status})"
        return {
            "configured": True,
            "reachable": reachable,
            "ok": reachable,
            "url": url,
            "status": status,
            "state": state,
            "auth_required": auth_required,
        }
    except Exception as exc:
        return {
            "configured": True,
            "reachable": False,
            "ok": False,
            "url": url,
            "status": None,
            "state": "Unreachable",
            "auth_required": False,
            "error": str(exc),
        }


def _human_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    amount = float(max(0, int(value)))
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} PiB"


def _storage_probe() -> dict[str, Any]:
    path = cache_root()
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": None,
        "size_human": "—",
        "filesystem_total_bytes": None,
        "filesystem_used_bytes": None,
        "filesystem_free_bytes": None,
        "filesystem_total_human": "—",
        "filesystem_used_human": "—",
        "filesystem_free_human": "—",
        "size_state": "unavailable",
    }
    if not path.exists():
        return result

    try:
        usage = shutil.disk_usage(path)
        result.update({
            "filesystem_total_bytes": usage.total,
            "filesystem_used_bytes": usage.used,
            "filesystem_free_bytes": usage.free,
            "filesystem_total_human": _human_bytes(usage.total),
            "filesystem_used_human": _human_bytes(usage.used),
            "filesystem_free_human": _human_bytes(usage.free),
        })
    except OSError:
        pass

    try:
        completed = subprocess.run(
            ["du", "-sb", "--apparent-size", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=4,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            size = int(completed.stdout.split()[0])
            result["size_bytes"] = size
            result["size_human"] = _human_bytes(size)
            result["size_state"] = "measured"
        else:
            result["size_state"] = "measurement failed"
    except subprocess.TimeoutExpired:
        result["size_state"] = "measurement timed out"
    except Exception as exc:
        result["size_state"] = f"measurement error: {exc}"
    return result


async def refresh_storage() -> dict[str, Any]:
    try:
        value = await asyncio.to_thread(_storage_probe)
        _STORAGE_CACHE.update({
            "updated_monotonic": time.monotonic(),
            "updated_at": _now_iso(),
            "value": value,
            "error": "",
        })
        return value
    except Exception as exc:
        _STORAGE_CACHE["error"] = str(exc)
        raise


def cached_storage() -> dict[str, Any]:
    value = _STORAGE_CACHE.get("value")
    if value:
        out = copy.deepcopy(value)
    else:
        path = cache_root()
        out = {
            "path": str(path), "exists": path.exists(), "size_bytes": None, "size_human": "warming up",
            "filesystem_total_bytes": None, "filesystem_used_bytes": None, "filesystem_free_bytes": None,
            "filesystem_total_human": "—", "filesystem_used_human": "—", "filesystem_free_human": "—",
            "size_state": "warming up",
        }
    out["updated_at"] = _STORAGE_CACHE.get("updated_at") or ""
    out["error"] = _STORAGE_CACHE.get("error") or ""
    return out


async def status() -> dict[str, Any]:
    fs, endpoint = await asyncio.gather(
        asyncio.to_thread(filesystem_status),
        endpoint_status(),
    )
    # The mounted filesystem is the source of truth. A protected Zurg HTTP
    # endpoint returning 401/403 is still reachable and should not turn the
    # whole application red.
    return {
        **fs,
        "endpoint": endpoint,
        "storage": cached_storage(),
        "ok": bool(fs["ok"]),
        "attention": bool(not fs["ok"] or (endpoint.get("configured") and not endpoint.get("reachable"))),
    }


async def refresh_status() -> dict[str, Any]:
    value = await status()
    _STATUS_CACHE.update({
        "updated_monotonic": time.monotonic(),
        "updated_at": _now_iso(),
        "value": value,
        "error": "",
    })
    return value


def cached_status() -> dict[str, Any]:
    value = _STATUS_CACHE.get("value")
    if value:
        out = copy.deepcopy(value)
    else:
        configured_url = setting_get("zurg.url", settings.zurg_url) or settings.zurg_url
        out = {
            "ok": False,
            "attention": False,
            "root": str(root()),
            "exists": False,
            "readable": False,
            "folders": {name: False for name in ("__all__", "__magic__", "movies", "shows")},
            "counts": {name: 0 for name in ("movies", "shows", "music", "__all__", "__downloads__", "__magic__", "__nzb__", "__unplayable__")},
            "version": "",
            "version_line": "",
            "endpoint": {
                "configured": bool(configured_url),
                "reachable": False,
                "ok": True,
                "url": configured_url,
                "status": None,
                "state": "Health check warming up",
                "auth_required": False,
            },
            "storage": cached_storage(),
        }
    out["cache_updated_at"] = _STATUS_CACHE.get("updated_at") or ""
    out["cache_error"] = _STATUS_CACHE.get("error") or ""
    return out


async def status_loop(interval: float = 15.0) -> None:
    while True:
        try:
            await refresh_status()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _STATUS_CACHE["error"] = str(exc)
        await asyncio.sleep(interval)


async def storage_loop(interval: float = 300.0) -> None:
    while True:
        try:
            await refresh_storage()
            # Refresh status so the dashboard picks up the latest storage block.
            if _STATUS_CACHE.get("value"):
                _STATUS_CACHE["value"]["storage"] = cached_storage()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _STORAGE_CACHE["error"] = str(exc)
        await asyncio.sleep(interval)


def recent_entries(limit: int = 30) -> list[dict[str, Any]]:
    base = root()
    rows: list[dict[str, Any]] = []
    for section in ("movies", "shows", "music", "__downloads__", "__magic__", "__nzb__", "__unplayable__"):
        path = base / section
        try:
            for entry in os.scandir(path):
                try:
                    stat = entry.stat(follow_symlinks=False)
                    rows.append({
                        "section": section,
                        "name": entry.name,
                        "path": entry.path,
                        "mtime": stat.st_mtime,
                        "updated": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    })
                except OSError:
                    continue
        except OSError:
            continue
    rows.sort(key=lambda x: x["mtime"], reverse=True)
    return rows[: max(1, min(200, int(limit)))]


def _norm(text: str) -> str:
    text = str(text or "").casefold()
    text = re.sub(r"\b(19|20)\d{2}\b", " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _scan_section(start: Path, max_depth: int, limit: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not start.exists():
        return rows
    try:
        for current, dirs, files in os.walk(start):
            try:
                depth = len(Path(current).relative_to(start).parts)
            except ValueError:
                depth = 0
            if depth >= max_depth:
                dirs[:] = []
            for name in dirs + files:
                path = str(Path(current) / name)
                rows.append({"path": path, "text": _norm(path)})
                if len(rows) >= limit:
                    return rows
    except OSError:
        return rows
    return rows


def _scan_library_top(start: Path, limit: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    try:
        for entry in os.scandir(start):
            path = entry.path
            rows.append({"path": path, "text": _norm(entry.name)})
            if len(rows) >= limit:
                break
    except OSError:
        pass
    return rows


def _build_index() -> dict[str, list[dict[str, str]]]:
    base = root()
    # Movies and shows are indexed only at their title root. Working views are
    # shallow-scanned. This keeps correlation cheap even with thousands of titles.
    return {
        "movies": _scan_library_top(base / "movies", 12000),
        "shows": _scan_library_top(base / "shows", 12000),
        "downloads": _scan_section(base / "__downloads__", 2, 12000),
        "magic": _scan_section(base / "__magic__", 3, 18000),
        "nzb": _scan_section(base / "__nzb__", 3, 12000),
    }


async def rebuild_index() -> dict[str, list[dict[str, str]]]:
    if _INDEX_CACHE.get("building"):
        return _INDEX_CACHE.get("data") or {}
    _INDEX_CACHE["building"] = True
    try:
        data = await asyncio.to_thread(_build_index)
        _INDEX_CACHE.update({
            "updated_monotonic": time.monotonic(),
            "updated_at": _now_iso(),
            "data": data,
            "error": "",
        })
        return data
    except Exception as exc:
        _INDEX_CACHE["error"] = str(exc)
        raise
    finally:
        _INDEX_CACHE["building"] = False


async def index_loop(interval: float = 45.0) -> None:
    while True:
        try:
            await rebuild_index()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(interval)


def index_state() -> dict[str, Any]:
    data = _INDEX_CACHE.get("data") or {}
    updated = float(_INDEX_CACHE.get("updated_monotonic") or 0)
    return {
        "ready": bool(updated),
        "building": bool(_INDEX_CACHE.get("building")),
        "updated_at": _INDEX_CACHE.get("updated_at") or "",
        "age_seconds": max(0.0, time.monotonic() - updated) if updated else None,
        "error": _INDEX_CACHE.get("error") or "",
        "counts": {k: len(v) for k, v in data.items()},
    }


def title_matches(title: str, year: int | None = None, limit: int = 5) -> dict[str, list[str]]:
    wanted = _norm(title)
    if not wanted:
        return {"movies": [], "shows": [], "downloads": [], "magic": [], "nzb": [], "working": []}
    tokens = [x for x in wanted.split() if len(x) >= 2][:6]
    data = _INDEX_CACHE.get("data") or {}
    out: dict[str, list[str]] = {}
    for section in ("movies", "shows", "downloads", "magic", "nzb"):
        matches: list[str] = []
        for row in data.get(section, []):
            text = str(row.get("text") or "")
            if tokens and all(token in text for token in tokens):
                path = str(row.get("path") or "")
                # Year is useful for movie discrimination but remains optional.
                if year and section == "movies" and str(year) not in path:
                    pass
                matches.append(path)
                if len(matches) >= limit:
                    break
        out[section] = matches
    out["working"] = (out["downloads"] + out["magic"] + out["nzb"])[:limit]
    return out


def working_entries(limit: int = 80) -> list[dict[str, str]]:
    data = _INDEX_CACHE.get("data") or {}
    base = root()
    rows: list[dict[str, str]] = []
    generic = {
        "tv", "show", "shows", "movie", "movies", "music", "kids", "anime",
        "other", "downloads", "download", "nzb", "realdebrid", "season",
    }
    section_roots = {
        "downloads": base / "__downloads__",
        "magic": base / "__magic__",
        "nzb": base / "__nzb__",
    }
    seen_names: set[tuple[str, str]] = set()
    for section, stage in (("downloads", "working"), ("magic", "scraping"), ("nzb", "scraping")):
        start = section_roots[section]
        for item in data.get(section, []):
            path = str(item.get("path") or "")
            if not path:
                continue
            try:
                rel = Path(path).relative_to(start)
                parts = rel.parts
            except Exception:
                parts = Path(path).parts
            if section in {"magic", "nzb"} and len(parts) < 2:
                continue
            name = Path(path).name.strip()
            low = _norm(name)
            if not low or low in generic or re.fullmatch(r"season\s*\d+", low):
                continue
            # Prefer title-like directories over leaf media files when both are
            # present. os.walk yields directories first, so this keeps one useful
            # row instead of generic roots such as 'tv'/'kids'.
            if Path(name).suffix.lower() in {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".mp3", ".flac"}:
                parent_name = Path(path).parent.name.strip()
                parent_norm = _norm(parent_name)
                if parent_norm and parent_norm not in generic and not re.fullmatch(r"season\s*\d+", parent_norm):
                    name = parent_name
                    low = parent_norm
            key = (section, low)
            if key in seen_names:
                continue
            seen_names.add(key)
            rows.append({
                "section": section,
                "stage": stage,
                "path": path,
                "name": name,
            })
            if len(rows) >= limit:
                return rows
    return rows
