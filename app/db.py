from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    secret INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    email TEXT UNIQUE,
    display_name TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL DEFAULT 'info',
    source TEXT NOT NULL DEFAULT 'app',
    event TEXT NOT NULL,
    message TEXT,
    context TEXT DEFAULT '{}',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_app_logs_source ON app_logs(source);
CREATE INDEX IF NOT EXISTS idx_app_logs_level ON app_logs(level);

CREATE TABLE IF NOT EXISTS metadata_cache (
    cache_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS media_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    media_type TEXT NOT NULL DEFAULT 'mixed',
    movie_destination TEXT NOT NULL DEFAULT 'auto',
    tv_destination TEXT NOT NULL DEFAULT 'auto',
    acquisition_strategy TEXT NOT NULL DEFAULT 'arr_native',
    monitor INTEGER NOT NULL DEFAULT 1,
    search_automatically INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 0,
    sync_interval_hours INTEGER NOT NULL DEFAULT 12,
    last_sync_at TEXT,
    last_error TEXT,
    last_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_media_lists_enabled ON media_lists(enabled);

CREATE TABLE IF NOT EXISTS media_list_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'complete',
    preview INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    existing_count INTEGER NOT NULL DEFAULT 0,
    added_count INTEGER NOT NULL DEFAULT 0,
    unmatched_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(list_id) REFERENCES media_lists(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_media_list_runs_list ON media_list_runs(list_id, created_at DESC);

CREATE TABLE IF NOT EXISTS media_automations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT 'mixed',
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT NOT NULL DEFAULT '',
    definition_json TEXT NOT NULL DEFAULT '{}',
    engine TEXT NOT NULL DEFAULT 'jellyfin',
    enabled INTEGER NOT NULL DEFAULT 1,
    schedule_hours INTEGER NOT NULL DEFAULT 24,
    acquire_missing INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_media_automations_enabled ON media_automations(enabled);

CREATE TABLE IF NOT EXISTS media_automation_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    automation_id INTEGER NOT NULL,
    server_type TEXT NOT NULL DEFAULT 'jellyfin',
    library_name TEXT NOT NULL DEFAULT '',
    collection_name TEXT NOT NULL DEFAULT '',
    engine TEXT NOT NULL DEFAULT 'jellyfin',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_collection_id TEXT NOT NULL DEFAULT '',
    last_status TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    last_sync_at TEXT,
    FOREIGN KEY(automation_id) REFERENCES media_automations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_media_automation_targets_definition ON media_automation_targets(automation_id);

CREATE TABLE IF NOT EXISTS media_automation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    automation_id INTEGER NOT NULL,
    preview INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    FOREIGN KEY(automation_id) REFERENCES media_automations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_media_automation_runs_definition ON media_automation_runs(automation_id,id DESC);

CREATE TABLE IF NOT EXISTS pipeline_items (
    item_key TEXT PRIMARY KEY,
    seerr_request_id INTEGER,
    media_type TEXT NOT NULL,
    title TEXT NOT NULL,
    external_id TEXT,
    service TEXT,
    arr_id INTEGER,
    stage TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    zurg_path TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pipeline_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_key TEXT NOT NULL,
    stage TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pipeline_events_item ON pipeline_events(item_key,id DESC);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _compat_migrations(conn: sqlite3.Connection) -> None:
    # Existing v10 databases are reused in-place. Extra historical tables are
    # harmless; v11 simply stops reading or writing them.
    if _columns(conn, "users"):
        for name, decl in {
            "email": "TEXT", "display_name": "TEXT", "role": "TEXT NOT NULL DEFAULT 'admin'",
            "created_at": "TEXT", "updated_at": "TEXT",
        }.items():
            if name not in _columns(conn, "users"):
                conn.execute(f"ALTER TABLE users ADD COLUMN {name} {decl}")

    # Remove old media-automation targets that are not part of the v11 Jellyfin-only UI.
    if _columns(conn, "media_automation_targets"):
        conn.execute("DELETE FROM media_automation_targets WHERE lower(server_type) <> 'jellyfin'")


def init_db() -> None:
    with sqlite3.connect(settings.db_path) as conn:
        conn.executescript(SCHEMA)
        _compat_migrations(conn)
        conn.commit()


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 250_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def user_count() -> int:
    with db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])


def create_user(username: str, email: str, display_name: str, password: str, role: str = "admin") -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO users(username,email,display_name,password_hash,role,updated_at) VALUES(?,?,?,?,?,?)",
            (username.strip(), email.strip() or None, display_name.strip() or username.strip(), _hash_password(password), role, utcnow()),
        )
        return int(cur.lastrowid)


def authenticate_user(identity: str, password: str) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE lower(username)=lower(?) OR lower(email)=lower(?) LIMIT 1",
            (identity.strip(), identity.strip()),
        ).fetchone()
    if not row or not _verify_password(password, row["password_hash"]):
        return None
    return dict(row)


def get_user(user_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (int(user_id),)).fetchone()
    return dict(row) if row else None


def update_user(user_id: int, username: str, email: str, display_name: str, password: str = "") -> None:
    with db() as conn:
        if password:
            conn.execute(
                "UPDATE users SET username=?,email=?,display_name=?,password_hash=?,updated_at=? WHERE id=?",
                (username.strip(), email.strip() or None, display_name.strip(), _hash_password(password), utcnow(), int(user_id)),
            )
        else:
            conn.execute(
                "UPDATE users SET username=?,email=?,display_name=?,updated_at=? WHERE id=?",
                (username.strip(), email.strip() or None, display_name.strip(), utcnow(), int(user_id)),
            )


def setting_get(key: str, default: str = "") -> str:
    with db() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else default


def setting_set(key: str, value: str, secret: bool = False) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO app_settings(key,value,secret,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,secret=excluded.secret,updated_at=excluded.updated_at",
            (key, str(value or ""), int(bool(secret)), utcnow()),
        )


def setting_delete(key: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM app_settings WHERE key=?", (key,))


def all_settings(mask_secrets: bool = True) -> dict[str, str]:
    with db() as conn:
        rows = conn.execute("SELECT key,value,secret FROM app_settings ORDER BY key").fetchall()
    return {r["key"]: ("********" if mask_secrets and r["secret"] and r["value"] else r["value"]) for r in rows}


def cache_get(key: str, max_age_seconds: int = 3600) -> Any | None:
    with db() as conn:
        row = conn.execute("SELECT payload,updated_at FROM metadata_cache WHERE cache_key=?", (key,)).fetchone()
    if not row:
        return None
    try:
        updated = datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00"))
        if (datetime.now(timezone.utc) - updated).total_seconds() > max_age_seconds:
            return None
        return json.loads(row["payload"])
    except Exception:
        return None


def cache_set(key: str, payload: Any) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO metadata_cache(cache_key,payload,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
            (key, json.dumps(payload, ensure_ascii=False), utcnow()),
        )


def log_event(level: str, source: str, event: str, message: str = "", context: dict | None = None) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO app_logs(level,source,event,message,context) VALUES(?,?,?,?,?)",
            (level, source, event, message, json.dumps(context or {}, ensure_ascii=False)),
        )


def list_logs(level: str = "all", source: str = "all", q: str = "", limit: int = 300) -> list[dict]:
    sql = "SELECT * FROM app_logs WHERE 1=1"
    args: list[Any] = []
    if level != "all":
        sql += " AND level=?"; args.append(level)
    if source != "all":
        sql += " AND source=?"; args.append(source)
    if q:
        sql += " AND (message LIKE ? OR event LIKE ?)"; args.extend([f"%{q}%", f"%{q}%"])
    sql += " ORDER BY id DESC LIMIT ?"; args.append(int(limit))
    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def pipeline_upsert(item: dict[str, Any]) -> None:
    key = str(item["key"])
    with db() as conn:
        old = conn.execute(
            "SELECT seerr_request_id,media_type,title,external_id,service,arr_id,stage,detail,zurg_path FROM pipeline_items WHERE item_key=?",
            (key,),
        ).fetchone()
        values = (
            item.get("request_id"), item.get("media_type"), item.get("title"), item.get("external_id"),
            item.get("service"), item.get("arr_id"), item.get("stage"), item.get("detail", ""), item.get("zurg_path", ""),
        )
        if old and tuple(old) == values:
            return
        now = utcnow()
        conn.execute(
            """INSERT INTO pipeline_items(item_key,seerr_request_id,media_type,title,external_id,service,arr_id,stage,detail,zurg_path,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(item_key) DO UPDATE SET seerr_request_id=excluded.seerr_request_id,media_type=excluded.media_type,
               title=excluded.title,external_id=excluded.external_id,service=excluded.service,arr_id=excluded.arr_id,
               stage=excluded.stage,detail=excluded.detail,zurg_path=excluded.zurg_path,updated_at=excluded.updated_at""",
            (key, *values, now),
        )
        if not old or old["stage"] != item.get("stage") or old["detail"] != item.get("detail"):
            conn.execute(
                "INSERT INTO pipeline_events(item_key,stage,detail,created_at) VALUES(?,?,?,?)",
                (key, item.get("stage"), item.get("detail", ""), now),
            )


def pipeline_history(limit: int = 100) -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM pipeline_items ORDER BY updated_at DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def pipeline_events(item_key: str, limit: int = 50) -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM pipeline_events WHERE item_key=? ORDER BY id DESC LIMIT ?", (item_key, int(limit))).fetchall()
    return [dict(r) for r in rows]
