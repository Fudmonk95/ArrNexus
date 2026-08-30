from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import os

from .config import settings


class NamespaceError(RuntimeError):
    pass


def _read_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")
    except (OSError, PermissionError):
        return ""


@lru_cache(maxsize=1)
def find_anchor_pid() -> int:
    """Find the DUMB Radarr process whose mount namespace exposes /mnt/debrid."""
    match = settings.radarr_process_match
    data_match = settings.radarr_data_match
    candidates: list[int] = []
    try:
        proc_entries = Path("/proc").iterdir()
    except OSError as exc:
        raise NamespaceError(f"Cannot inspect /proc: {exc}") from exc

    for entry in proc_entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        cmdline = _read_cmdline(pid)
        if not cmdline:
            continue
        if match and match not in cmdline:
            continue
        if data_match and data_match not in cmdline:
            continue
        view = Path(f"/proc/{pid}/root{settings.dumb_root}")
        if view.is_dir():
            candidates.append(pid)

    if not candidates:
        raise NamespaceError(
            "Could not find the DUMB Radarr mount namespace. "
            "The container must run with pid: host and permission to inspect /proc."
        )
    return sorted(candidates)[0]


def refresh_anchor_pid() -> int:
    find_anchor_pid.cache_clear()
    return find_anchor_pid()


def namespace_root() -> Path:
    pid = find_anchor_pid()
    root = Path(f"/proc/{pid}/root{settings.dumb_root}")
    if not root.is_dir():
        pid = refresh_anchor_pid()
        root = Path(f"/proc/{pid}/root{settings.dumb_root}")
    if not root.is_dir():
        raise NamespaceError(f"DUMB root is unavailable through PID {pid}: {root}")
    return root


def logical_path(value: str | Path) -> Path:
    return Path(str(value))


def view_path(value: str | Path) -> Path:
    """Translate a DUMB-visible path (/mnt/debrid/...) to this container's /proc view."""
    logical = logical_path(value)
    dumb_root = Path(settings.dumb_root)
    try:
        rel = logical.relative_to(dumb_root)
    except ValueError as exc:
        raise NamespaceError(f"Path is outside DUMB root {dumb_root}: {logical}") from exc
    return namespace_root() / rel


def logical_from_view(value: str | Path) -> Path:
    view = Path(value)
    root = namespace_root()
    try:
        rel = view.relative_to(root)
    except ValueError as exc:
        raise NamespaceError(f"Path is outside namespace root {root}: {view}") from exc
    return Path(settings.dumb_root) / rel


def is_within_logical(value: str | Path, parent: str | Path) -> bool:
    value_p = Path(os.path.normpath(str(value)))
    parent_p = Path(os.path.normpath(str(parent)))
    try:
        value_p.relative_to(parent_p)
        return True
    except ValueError:
        return False


def namespace_status() -> dict:
    try:
        pid = find_anchor_pid()
        root = namespace_root()
        return {
            "ok": True,
            "pid": pid,
            "root": str(root),
            "dumb_root": settings.dumb_root,
            "source_exists": view_path(settings.source_root).is_dir(),
        }
    except Exception as exc:
        return {
            "ok": False,
            "pid": None,
            "root": None,
            "dumb_root": settings.dumb_root,
            "source_exists": False,
            "error": str(exc),
        }
