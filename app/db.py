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

CREATE TABLE IF NOT EXISTS recovery_counters (
    media_key TEXT PRIMARY KEY,
    service TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    failures INTEGER NOT NULL DEFAULT 0,
    last_failure_at TEXT,
    cooldown_until TEXT,
    paused INTEGER NOT NULL DEFAULT 0,
    pause_reason TEXT NOT NULL DEFAULT '',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_recovery_paused ON recovery_counters(paused, failures);

CREATE TABLE IF NOT EXISTS orchestrator_items (
    item_key TEXT PRIMARY KEY,
    service TEXT NOT NULL,
    media_type TEXT NOT NULL,
    title TEXT NOT NULL,
    arr_id INTEGER,
    sub_id INTEGER,
    series_id INTEGER,
    season_number INTEGER,
    state TEXT NOT NULL DEFAULT 'detected',
    attempts INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '',
    last_search_at TEXT,
    next_retry_at TEXT,
    last_seen_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_orchestrator_state ON orchestrator_items(state, service, updated_at);

CREATE TABLE IF NOT EXISTS orchestrator_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_key TEXT NOT NULL,
    state TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_orchestrator_events_item ON orchestrator_events(item_key,id DESC);

CREATE TABLE IF NOT EXISTS janitor_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service TEXT NOT NULL,
    queue_id TEXT NOT NULL DEFAULT '',
    media_key TEXT NOT NULL DEFAULT '',
    release_title TEXT NOT NULL DEFAULT '',
    classification TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    dry_run INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_janitor_actions_media ON janitor_actions(media_key,id DESC);
CREATE INDEX IF NOT EXISTS idx_janitor_actions_service ON janitor_actions(service,id DESC);
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
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.executescript(SCHEMA)
        _compat_migrations(conn)
        conn.commit()
    finally:
        conn.close()


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
        now = utcnow()
        changed = not old or tuple(old) != values
        if changed:
            conn.execute(
                """INSERT INTO pipeline_items(item_key,seerr_request_id,media_type,title,external_id,service,arr_id,stage,detail,zurg_path,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(item_key) DO UPDATE SET seerr_request_id=excluded.seerr_request_id,media_type=excluded.media_type,
                   title=excluded.title,external_id=excluded.external_id,service=excluded.service,arr_id=excluded.arr_id,
                   stage=excluded.stage,detail=excluded.detail,zurg_path=excluded.zurg_path,updated_at=excluded.updated_at""",
                (key, *values, now),
            )

        # v11.0.1 stores each observed lifecycle milestone once so later states
        # do not erase the useful path the request took through the stack.
        existing_stages = {
            str(row[0])
            for row in conn.execute("SELECT stage FROM pipeline_events WHERE item_key=?", (key,)).fetchall()
        }
        for milestone in item.get("milestones") or []:
            stage = str((milestone or {}).get("stage") or "").strip()
            detail = str((milestone or {}).get("detail") or "").strip()
            if not stage or stage in existing_stages:
                continue
            conn.execute(
                "INSERT INTO pipeline_events(item_key,stage,detail,created_at) VALUES(?,?,?,?)",
                (key, stage, detail, now),
            )
            existing_stages.add(stage)

        # Preserve the old behaviour for a current-state/detail change that is
        # not already represented by a milestone.
        current_stage = str(item.get("stage") or "")
        if current_stage and current_stage not in existing_stages and (not old or old["stage"] != current_stage or old["detail"] != item.get("detail")):
            conn.execute(
                "INSERT INTO pipeline_events(item_key,stage,detail,created_at) VALUES(?,?,?,?)",
                (key, current_stage, item.get("detail", ""), now),
            )


def pipeline_history(limit: int = 100) -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM pipeline_items ORDER BY updated_at DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def pipeline_events(item_key: str, limit: int = 50) -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM pipeline_events WHERE item_key=? ORDER BY id DESC LIMIT ?", (item_key, int(limit))).fetchall()
    return [dict(r) for r in rows]


def pipeline_recent_events(limit: int = 160) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """SELECT e.id,e.item_key,e.stage,e.detail,e.created_at,
                      i.title,i.media_type,i.service,i.zurg_path
               FROM pipeline_events e
               LEFT JOIN pipeline_items i ON i.item_key=e.item_key
               ORDER BY e.id DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# v12 recovery/orchestration persistence
# ---------------------------------------------------------------------------

def recovery_get(media_key: str) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM recovery_counters WHERE media_key=?", (str(media_key),)).fetchone()
    return dict(row) if row else {
        "media_key": str(media_key), "service": "", "title": "", "failures": 0,
        "last_failure_at": None, "cooldown_until": None, "paused": 0, "pause_reason": "",
    }


def recovery_register_failure(media_key: str, service: str, title: str, cooldown_until: str = "", pause: bool = False, reason: str = "") -> dict:
    now = utcnow()
    with db() as conn:
        row = conn.execute("SELECT failures FROM recovery_counters WHERE media_key=?", (str(media_key),)).fetchone()
        failures = int(row[0] if row else 0) + 1
        conn.execute(
            """INSERT INTO recovery_counters(media_key,service,title,failures,last_failure_at,cooldown_until,paused,pause_reason,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(media_key) DO UPDATE SET service=excluded.service,title=excluded.title,failures=excluded.failures,
               last_failure_at=excluded.last_failure_at,cooldown_until=excluded.cooldown_until,paused=excluded.paused,
               pause_reason=excluded.pause_reason,updated_at=excluded.updated_at""",
            (str(media_key), str(service), str(title), failures, now, cooldown_until or None, int(bool(pause)), str(reason or ""), now),
        )
    return recovery_get(media_key)


def recovery_set_pause(media_key: str, paused: bool, reason: str = "") -> None:
    current = recovery_get(media_key)
    now = utcnow()
    with db() as conn:
        conn.execute(
            """INSERT INTO recovery_counters(media_key,service,title,failures,last_failure_at,cooldown_until,paused,pause_reason,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(media_key) DO UPDATE SET paused=excluded.paused,pause_reason=excluded.pause_reason,updated_at=excluded.updated_at""",
            (str(media_key), current.get("service") or "", current.get("title") or "", int(current.get("failures") or 0),
             current.get("last_failure_at"), current.get("cooldown_until"), int(bool(paused)), str(reason or ""), now),
        )


def recovery_clear(media_key: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM recovery_counters WHERE media_key=?", (str(media_key),))


def recovery_list(limit: int = 300, paused_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM recovery_counters"
    args: list[Any] = []
    if paused_only:
        sql += " WHERE paused=1"
    sql += " ORDER BY paused DESC, failures DESC, updated_at DESC LIMIT ?"
    args.append(int(limit))
    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def orchestrator_upsert(item: dict[str, Any]) -> None:
    now = utcnow()
    key = str(item["item_key"])
    with db() as conn:
        old = conn.execute("SELECT state,detail,attempts FROM orchestrator_items WHERE item_key=?", (key,)).fetchone()
        state = str(item.get("state") or "detected")
        detail = str(item.get("detail") or "")
        attempts = int(item.get("attempts") if item.get("attempts") is not None else (old["attempts"] if old else 0))
        conn.execute(
            """INSERT INTO orchestrator_items(item_key,service,media_type,title,arr_id,sub_id,series_id,season_number,state,attempts,detail,last_search_at,next_retry_at,last_seen_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(item_key) DO UPDATE SET service=excluded.service,media_type=excluded.media_type,title=excluded.title,
               arr_id=excluded.arr_id,sub_id=excluded.sub_id,series_id=excluded.series_id,season_number=excluded.season_number,
               state=excluded.state,attempts=excluded.attempts,detail=excluded.detail,last_search_at=excluded.last_search_at,
               next_retry_at=excluded.next_retry_at,last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at""",
            (key, str(item.get("service") or ""), str(item.get("media_type") or ""), str(item.get("title") or ""),
             item.get("arr_id"), item.get("sub_id"), item.get("series_id"), item.get("season_number"), state, attempts, detail,
             item.get("last_search_at"), item.get("next_retry_at"), item.get("last_seen_at") or now, now),
        )
        if not old or old["state"] != state or old["detail"] != detail:
            conn.execute("INSERT INTO orchestrator_events(item_key,state,detail,created_at) VALUES(?,?,?,?)", (key, state, detail, now))


def orchestrator_list(limit: int = 500, states: list[str] | None = None) -> list[dict]:
    sql = "SELECT * FROM orchestrator_items"
    args: list[Any] = []
    if states:
        marks = ",".join("?" for _ in states)
        sql += f" WHERE state IN ({marks})"
        args.extend(states)
    sql += " ORDER BY CASE state WHEN 'attention' THEN 0 WHEN 'searching' THEN 1 WHEN 'queued' THEN 2 WHEN 'cooldown' THEN 3 ELSE 4 END, attempts ASC, updated_at DESC LIMIT ?"
    args.append(int(limit))
    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def orchestrator_get(item_key: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM orchestrator_items WHERE item_key=?", (str(item_key),)).fetchone()
    return dict(row) if row else None


def orchestrator_events(item_key: str = "", limit: int = 200) -> list[dict]:
    with db() as conn:
        if item_key:
            rows = conn.execute("SELECT * FROM orchestrator_events WHERE item_key=? ORDER BY id DESC LIMIT ?", (str(item_key), int(limit))).fetchall()
        else:
            rows = conn.execute("SELECT * FROM orchestrator_events ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def janitor_log_action(service: str, queue_id: str, media_key: str, release_title: str, classification: str, action: str, outcome: str, detail: str = "", dry_run: bool = True) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO janitor_actions(service,queue_id,media_key,release_title,classification,action,outcome,detail,dry_run) VALUES(?,?,?,?,?,?,?,?,?)",
            (str(service), str(queue_id), str(media_key), str(release_title), str(classification), str(action), str(outcome), str(detail), int(bool(dry_run))),
        )
        return int(cur.lastrowid)


def janitor_history(limit: int = 300) -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM janitor_actions ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rows]
