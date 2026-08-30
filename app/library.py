from __future__ import annotations
from pathlib import Path
import os
from .config import settings
from .namespace import view_path, logical_from_view
from .importer import all_library_roots


def provider_from_target(target: str) -> str:
    t = target.lower()
    if "/decypharr/" in t:
        return "Real-Debrid"
    if "/nzbdav/" in t or "/.ids/" in t:
        return "Usenet"
    return "Unknown"


def inventory_roots(sample_limit: int = 5) -> list[dict]:
    out = []
    for key, logical in all_library_roots().items():
        try:
            actual = view_path(logical)
            dirs = [p for p in actual.iterdir() if p.is_dir()] if actual.exists() else []
            providers = {"Real-Debrid": 0, "Usenet": 0, "Unknown": 0}
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
    return out


def build_source_link_index(limit: int = 200000) -> dict[str, list[str]]:
    """Map /mnt/debrid/decypharr/__all__/<folder> to symlinks that target it."""
    index: dict[str, list[str]] = {}
    source_prefix = settings.source_root.rstrip("/") + "/"
    seen = 0
    for _, logical_root in all_library_roots().items():
        try:
            root = view_path(logical_root)
            if not root.exists():
                continue
            for p in root.rglob("*"):
                if seen >= limit:
                    return index
                if not p.is_symlink():
                    continue
                seen += 1
                target = os.readlink(p)
                if not target.startswith(source_prefix):
                    continue
                rel = target[len(source_prefix):]
                source_folder = rel.split("/", 1)[0]
                source_path = f"{settings.source_root.rstrip('/')}/{source_folder}"
                index.setdefault(source_path, []).append(str(logical_from_view(p)))
        except Exception:
            continue
    return index
