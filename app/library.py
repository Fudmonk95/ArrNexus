from __future__ import annotations
from pathlib import Path
import copy
import os
import threading
import time
from .config import settings
from .paths import source_root
from .db import setting_get
from .namespace import view_path, logical_from_view
from .importer import all_library_roots


_CACHE_LOCK = threading.RLock()
_INVENTORY_CACHE: dict[int, tuple[float, list[dict]]] = {}
_LINK_CACHE_AT = 0.0
_LINK_CACHE: dict[str, list[str]] = {}
_INVENTORY_TTL = 45.0
_LINK_TTL = 30.0


def invalidate_library_cache() -> None:
    global _LINK_CACHE_AT, _LINK_CACHE
    with _CACHE_LOCK:
        _INVENTORY_CACHE.clear()
        _LINK_CACHE_AT = 0.0
        _LINK_CACHE = {}


def provider_from_target(target: str) -> str:
    t = target.lower()
    recovery = (setting_get("archive_recovery.root", "/mnt/debrid/arrnexus-extracted") or "/mnt/debrid/arrnexus-extracted").rstrip("/").lower()
    if t == recovery or t.startswith(recovery + "/"):
        return "ArrNexus Recovery"
    if "/decypharr/" in t:
        return "Real-Debrid"
    if "/nzbdav/" in t or "/.ids/" in t:
        return "Usenet"
    return "Unknown"


def inventory_roots(sample_limit: int = 5, force: bool = False) -> list[dict]:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _INVENTORY_CACHE.get(int(sample_limit))
        if cached and not force and now - cached[0] < _INVENTORY_TTL:
            return copy.deepcopy(cached[1])
    out = []
    for key, logical in all_library_roots().items():
        try:
            actual = view_path(logical)
            dirs = [p for p in actual.iterdir() if p.is_dir()] if actual.exists() else []
            providers = {"Real-Debrid": 0, "Usenet": 0, "ArrNexus Recovery": 0, "Unknown": 0}
            symlinks = 0
            for p in actual.rglob("*") if actual.exists() else []:
                if p.is_symlink():
                    symlinks += 1
                    providers[provider_from_target(os.readlink(p))] += 1
            out.append({
                "key": key,
                "path": logical,
                "exists": actual.exists(),
                "count": len(dirs),
                "symlinks": symlinks,
                "providers": providers,
                "samples": [p.name for p in sorted(dirs, key=lambda x: x.name.lower())[:sample_limit]],
            })
        except Exception as exc:
            out.append({"key": key, "path": logical, "exists": False, "count": 0, "symlinks": 0, "providers": {}, "samples": [], "error": str(exc)})
    with _CACHE_LOCK:
        _INVENTORY_CACHE[int(sample_limit)] = (time.monotonic(), copy.deepcopy(out))
    return out


def _managed_source_roots() -> list[str]:
    provider = source_root().rstrip("/")
    recovery = (setting_get("archive_recovery.root", "/mnt/debrid/arrnexus-extracted") or "/mnt/debrid/arrnexus-extracted").strip().rstrip("/")
    return list(dict.fromkeys(x for x in (provider, recovery) if x))


def build_source_link_index(limit: int = 200000, force: bool = False) -> dict[str, list[str]]:
    """Map provider *and recovered* source packs to their library symlinks.

    v10.4.4 only indexed the normal DMM/provider root, so valid symlinks whose
    targets lived under ``/mnt/debrid/arrnexus-extracted`` left Inbox cards in
    Waiting forever. v10.5 treats both logical namespaces identically.
    """
    global _LINK_CACHE_AT, _LINK_CACHE
    now = time.monotonic()
    with _CACHE_LOCK:
        if _LINK_CACHE and not force and now - _LINK_CACHE_AT < _LINK_TTL:
            return copy.deepcopy(_LINK_CACHE)
    index: dict[str, list[str]] = {}
    source_roots = _managed_source_roots()
    prefixes = [(root, root + "/") for root in source_roots]
    seen = 0
    for _, logical_root in all_library_roots().items():
        try:
            root = view_path(logical_root)
            if not root.exists():
                continue
            for p in root.rglob("*"):
                if seen >= limit:
                    with _CACHE_LOCK:
                        _LINK_CACHE_AT = time.monotonic(); _LINK_CACHE = copy.deepcopy(index)
                    return index
                if not p.is_symlink():
                    continue
                seen += 1
                target = os.readlink(p)
                matched_root = matched_prefix = ""
                for source_base, prefix in prefixes:
                    if target.startswith(prefix):
                        matched_root, matched_prefix = source_base, prefix
                        break
                if not matched_prefix:
                    continue
                rel = target[len(matched_prefix):]
                source_folder = rel.split("/", 1)[0]
                if not source_folder:
                    continue
                source_path = f"{matched_root}/{source_folder}"
                index.setdefault(source_path, []).append(str(logical_from_view(p)))
        except Exception:
            continue
    with _CACHE_LOCK:
        _LINK_CACHE_AT = time.monotonic(); _LINK_CACHE = copy.deepcopy(index)
    return index
