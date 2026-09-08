from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .config import settings
from .connections import SERVICES, get_connection
from .db import all_settings
from .zurg import filesystem_status


def diagnostics() -> dict[str, Any]:
    db_path = Path(settings.db_path)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": {
            "path": str(db_path),
            "exists": db_path.exists(),
            "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        },
        "zurg": filesystem_status(),
        "connections": {
            name: {
                "url": get_connection(name).url,
                "configured": bool(get_connection(name).url and get_connection(name).api_key),
            }
            for name in SERVICES
        },
        "settings": all_settings(mask_secrets=True),
    }


def database_backup(destination_dir: str | Path | None = None) -> Path:
    source = Path(settings.db_path)
    if not source.exists():
        raise FileNotFoundError(f"Database does not exist: {source}")
    destination = Path(destination_dir or source.parent / "backups")
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = destination / f"arrnexus-{stamp}.db"
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    return target


def write_diagnostics(destination_dir: str | Path | None = None) -> Path:
    base = Path(destination_dir or Path(settings.db_path).parent / "diagnostics")
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = base / f"arrnexus-diagnostics-{stamp}.json"
    target.write_text(json.dumps(diagnostics(), indent=2, ensure_ascii=False), encoding="utf-8")
    return target
