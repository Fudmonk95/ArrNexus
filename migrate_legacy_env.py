#!/usr/bin/env python3
"""One-time ArrNexus v2 -> v3 connection migration.

Usage:
  python3 migrate_legacy_env.py /path/to/old/.env /path/to/new/data/router.db

This script uses only Python's standard library. It copies supported connection
values from an old .env file into ArrNexus's SQLite settings store so the v3
container no longer needs the environment file at runtime.
"""
from __future__ import annotations
import sqlite3, sys
from pathlib import Path

MAPPING = {
    "RADARR_URL": ("connection.radarr.main.url", 0),
    "RADARR_API_KEY": ("connection.radarr.main.api_key", 1),
    "SONARR_URL": ("connection.sonarr.main.url", 0),
    "SONARR_API_KEY": ("connection.sonarr.main.api_key", 1),
    "LIDARR_URL": ("connection.lidarr.main.url", 0),
    "LIDARR_API_KEY": ("connection.lidarr.main.api_key", 1),
    "PROWLARR_URL": ("connection.prowlarr.main.url", 0),
    "PROWLARR_API_KEY": ("connection.prowlarr.main.api_key", 1),
    "JELLYFIN_URL": ("connection.jellyfin.main.url", 0),
    "JELLYFIN_API_KEY": ("connection.jellyfin.main.api_key", 1),
    "SEERR_URL": ("connection.seerr.main.url", 0),
    "SEERR_API_KEY": ("connection.seerr.main.api_key", 1),
    "DUMB_ROOT": ("paths.dumb_root", 0),
}


def parse_env(path: Path) -> dict[str, str]:
    out = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        out[key.strip()] = value
    return out


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: migrate_legacy_env.py OLD_ENV NEW_ROUTER_DB")
    env_path, db_path = map(Path, sys.argv[1:])
    if not env_path.is_file():
        raise SystemExit(f"Old env file not found: {env_path}")
    if not db_path.is_file():
        raise SystemExit(f"ArrNexus database not found: {db_path}")
    values = parse_env(env_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY,value TEXT NOT NULL DEFAULT '',secret INTEGER NOT NULL DEFAULT 0,updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        copied = 0
        for env_key, (db_key, secret) in MAPPING.items():
            value = values.get(env_key, "").strip()
            if not value:
                continue
            conn.execute("INSERT INTO app_settings(key,value,secret,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=excluded.value,secret=excluded.secret,updated_at=CURRENT_TIMESTAMP", (db_key, value, secret))
            copied += 1
            # Main DUMB instance is normally named nzbdav. Save the same value
            # there so both generic clients and discovered-instance cards work.
            if env_key.startswith(("RADARR_", "SONARR_", "LIDARR_")):
                service = env_key.split("_",1)[0].lower()
                suffix = "url" if env_key.endswith("_URL") else "api_key"
                conn.execute("INSERT INTO app_settings(key,value,secret,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=excluded.value,secret=excluded.secret,updated_at=CURRENT_TIMESTAMP", (f"connection.{service}.nzbdav.{suffix}", value, secret))
        conn.commit()
    finally:
        conn.close()
    print(f"Migrated {copied} legacy setting(s) into {db_path}")
    print("The old .env is no longer required by ArrNexus v3 after verification.")

if __name__ == "__main__":
    main()
