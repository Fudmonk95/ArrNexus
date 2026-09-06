from __future__ import annotations

import asyncio
from datetime import datetime
import os
from pathlib import Path
import re
import time
from typing import Any

import httpx

from .config import settings
from .db import setting_get

_INDEX: tuple[float, dict[str, list[str]]] = (0.0, {})
_INDEX_TTL = 20.0


def root() -> Path:
    return Path(setting_get("zurg.root", settings.zurg_root) or settings.zurg_root)


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
    counts = {name: _count_children(base / name) for name in ("movies", "shows", "__all__", "__downloads__", "__unplayable__")}
    return {
        "ok": bool(readable and all(folders.values())),
        "root": str(base),
        "exists": exists,
        "readable": readable,
        "folders": folders,
        "counts": counts,
        "version": _version_text(base),
    }


async def endpoint_status() -> dict[str, Any]:
    url = (setting_get("zurg.url", settings.zurg_url) or settings.zurg_url).rstrip("/")
    if not url:
        return {"configured": False, "ok": False, "url": "", "status": None}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0), follow_redirects=True) as client:
            response = await client.get(url + "/")
        return {"configured": True, "ok": response.status_code < 500, "url": url, "status": response.status_code}
    except Exception as exc:
        return {"configured": True, "ok": False, "url": url, "status": None, "error": str(exc)}


async def status() -> dict[str, Any]:
    fs, endpoint = await asyncio.gather(asyncio.to_thread(filesystem_status), endpoint_status())
    return {**fs, "endpoint": endpoint, "ok": bool(fs["ok"] and (endpoint["ok"] or not endpoint["configured"]))}


def recent_entries(limit: int = 30) -> list[dict[str, Any]]:
    base = root()
    rows: list[dict[str, Any]] = []
    for section in ("movies", "shows", "__downloads__", "__unplayable__"):
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


def _build_index() -> dict[str, list[str]]:
    base = root()
    index: dict[str, list[str]] = {"movies": [], "shows": [], "working": []}
    sections = (("movies", "movies"), ("shows", "shows"), ("__downloads__", "working"), ("__magic__", "working"), ("__nzb__", "working"))
    for dirname, key in sections:
        start = base / dirname
        if not start.exists():
            continue
        seen = 0
        for current, dirs, files in os.walk(start):
            depth = len(Path(current).relative_to(start).parts)
            if depth >= 3:
                dirs[:] = []
            for name in dirs + files:
                index[key].append(str(Path(current) / name))
                seen += 1
                if seen >= 30000:
                    dirs[:] = []
                    break
            if seen >= 30000:
                break
    return index


def title_matches(title: str, year: int | None = None, limit: int = 5) -> dict[str, list[str]]:
    global _INDEX
    now = time.monotonic()
    if now - _INDEX[0] > _INDEX_TTL:
        _INDEX = (now, _build_index())
    wanted = _norm(title)
    if not wanted:
        return {"movies": [], "shows": [], "working": []}
    tokens = [x for x in wanted.split() if len(x) >= 2]
    out: dict[str, list[str]] = {}
    for section, paths in _INDEX[1].items():
        matches = []
        for path in paths:
            text = _norm(Path(path).name)
            if tokens and all(token in text for token in tokens[:5]):
                if year and str(year) not in path and section == "movies":
                    # Year is a useful movie discriminator but not mandatory.
                    pass
                matches.append(path)
                if len(matches) >= limit:
                    break
        out[section] = matches
    return out
