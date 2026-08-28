from __future__ import annotations

import io
import json
import os
import platform
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from .db import all_settings, list_logs, list_mounts, list_users, recent_jobs, recent_imports

BACKUP_DIR = Path(settings.db_path).resolve().parent / "backups"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")


def create_database_backup(reason: str = "manual", retention: int = 10) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"arrnexus-{_stamp()}-{reason}.db"
    source = sqlite3.connect(settings.db_path)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    backups = sorted(BACKUP_DIR.glob("arrnexus-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[max(1, retention):]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def list_backups(limit: int = 20) -> list[dict]:
    try:
        rows = sorted(BACKUP_DIR.glob("arrnexus-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    except OSError:
        rows = []
    return [{"name": p.name, "path": str(p), "size": p.stat().st_size, "mtime": p.stat().st_mtime} for p in rows]


def sanitized_config() -> dict:
    return {
        "format": "arrnexus-config-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "settings": all_settings(mask_secrets=True),
        "mounts": list_mounts(False),
        "users": [
            {k: u.get(k) for k in ("username", "email", "display_name", "role", "theme", "dashboard_layout", "can_request", "daily_request_limit")}
            for u in list_users()
        ],
    }


def diagnostics_zip(extra: dict | None = None) -> bytes:
    payloads = {
        "summary.json": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "hostname": platform.node(),
            "db_path": settings.db_path,
            **(extra or {}),
        },
        "settings-sanitized.json": sanitized_config(),
        "logs.json": list_logs("all", "all", "", 300),
        "jobs.json": [dict(x) for x in recent_jobs(100)],
        "imports.json": [dict(x) for x in recent_imports(200)],
    }
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for name, obj in payloads.items():
            z.writestr(name, json.dumps(obj, indent=2, default=str))
        z.writestr("README.txt", "ArrNexus diagnostics bundle. Secrets are masked/omitted. Review before sharing publicly.\n")
    return mem.getvalue()
