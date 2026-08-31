from __future__ import annotations
import json
import sqlite3
from pathlib import Path
import os
import hashlib
import hmac
import base64
import time
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT NOT NULL,
    source_name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    destination_key TEXT NOT NULL,
    destination_path TEXT NOT NULL,
    arr_name TEXT,
    arr_instance TEXT,
    arr_id INTEGER,
    status TEXT NOT NULL,
    note TEXT,
    created_paths TEXT DEFAULT '[]',
    source_fingerprint TEXT,
    source_quality INTEGER DEFAULT 0,
    undone INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_import_source ON imports(source_path);
CREATE INDEX IF NOT EXISTS idx_import_status ON imports(status);

CREATE TABLE IF NOT EXISTS item_state (
    source_path TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'waiting',
    note TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    total INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    reviewed INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS job_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    source_path TEXT,
    display_name TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT NOT NULL DEFAULT 'queued',
    destination_key TEXT,
    message TEXT,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_job_items_job ON job_items(job_id);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT,
    source_path TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS routing_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_type TEXT NOT NULL,
    field TEXT NOT NULL DEFAULT 'title',
    pattern TEXT NOT NULL,
    destination_key TEXT NOT NULL,
    weight INTEGER NOT NULL DEFAULT 90,
    enabled INTEGER NOT NULL DEFAULT 1,
    learned INTEGER NOT NULL DEFAULT 0,
    hits INTEGER NOT NULL DEFAULT 0,
    UNIQUE(media_type, field, pattern, destination_key)
);

CREATE TABLE IF NOT EXISTS metadata_cache (
    cache_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
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
    theme TEXT NOT NULL DEFAULT 'nexus',
    dashboard_layout TEXT NOT NULL DEFAULT 'default',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    can_request INTEGER NOT NULL DEFAULT 1,
    daily_request_limit INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_type TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    destination_key TEXT,
    arr_instance TEXT,
    arr_id INTEGER,
    status TEXT NOT NULL DEFAULT 'requested',
    status_detail TEXT,
    protocol TEXT,
    user_id INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(media_type, external_id)
);

CREATE TABLE IF NOT EXISTS password_resets (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires_at REAL NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
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

CREATE TABLE IF NOT EXISTS mount_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    logical_path TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'library',
    service TEXT,
    destination_key TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scrape_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_type TEXT NOT NULL,
    title TEXT NOT NULL,
    external_id TEXT,
    arr_service TEXT,
    arr_instance TEXT,
    arr_id INTEGER,
    destination_key TEXT,
    status TEXT NOT NULL DEFAULT 'searching',
    detail TEXT,
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_scrape_jobs_status ON scrape_jobs(status);

CREATE TABLE IF NOT EXISTS ui_preferences (
    user_id INTEGER NOT NULL,
    pref_key TEXT NOT NULL,
    pref_value TEXT NOT NULL DEFAULT '',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, pref_key),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS media_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    media_type TEXT NOT NULL DEFAULT 'mixed',
    movie_destination TEXT NOT NULL DEFAULT 'auto',
    tv_destination TEXT NOT NULL DEFAULT 'auto',
    acquisition_strategy TEXT NOT NULL DEFAULT 'automatic',
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
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection):
    # v0.2 databases only had the first import columns. Add newer columns safely.
    cols = _columns(conn, "imports")
    wanted = {
        "arr_instance": "TEXT",
        "created_paths": "TEXT DEFAULT '[]'",
        "source_fingerprint": "TEXT",
        "source_quality": "INTEGER DEFAULT 0",
        "undone": "INTEGER DEFAULT 0",
        "updated_at": "TEXT",
    }
    for name, decl in wanted.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE imports ADD COLUMN {name} {decl}")
    conn.execute("UPDATE imports SET updated_at=COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)")

    # Older v1/v2 databases may have partial user/request schemas.
    user_cols = _columns(conn, "users")
    for name, decl in {
        "email": "TEXT", "display_name": "TEXT", "role": "TEXT NOT NULL DEFAULT 'admin'",
        "theme": "TEXT NOT NULL DEFAULT 'nexus'", "dashboard_layout": "TEXT NOT NULL DEFAULT 'default'",
        "created_at": "TEXT", "updated_at": "TEXT",
        "can_request": "INTEGER NOT NULL DEFAULT 1", "daily_request_limit": "INTEGER NOT NULL DEFAULT 0"
    }.items():
        if name not in user_cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {decl}")
    req_cols = _columns(conn, "requests")
    if req_cols:
        for name, decl in {"status_detail":"TEXT", "protocol":"TEXT", "updated_at":"TEXT", "user_id":"INTEGER"}.items():
            if name not in req_cols:
                conn.execute(f"ALTER TABLE requests ADD COLUMN {name} {decl}")

    # v10.1 distinguishes controlled Language Guard rejections from real import failures.
    job_cols = _columns(conn, "jobs")
    if job_cols and "rejected" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN rejected INTEGER NOT NULL DEFAULT 0")
    # v10.4.2 reports Manual Review separately from confirmed language rejection.
    job_cols = _columns(conn, "jobs")
    if job_cols and "reviewed" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0")
    # v10.5 cooperative job control persists cancellation requests so worker
    # threads/processes can stop safely without treating the operation as failed.
    job_cols = _columns(conn, "jobs")
    if job_cols and "cancel_requested" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0")
    item_cols = _columns(conn, "job_items")
    if item_cols and "result_json" not in item_cols:
        conn.execute("ALTER TABLE job_items ADD COLUMN result_json TEXT NOT NULL DEFAULT '{}'")


def init_db():
    with sqlite3.connect(settings.db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    # Legacy deployments can still bootstrap from environment variables, but
    # a normal v3/Portainer install uses the first-run /setup wizard instead.
    if user_count() == 0 and settings.bootstrap_password:
        ensure_user(settings.bootstrap_username or 'admin', settings.bootstrap_password)
    seed_default_mounts()


@contextmanager
def db():
    conn = sqlite3.connect(settings.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def rowdict(row):
    return dict(row) if row is not None else None


def recent_imports(limit=100):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM imports ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def latest_success_for_source(source_path: str):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM imports WHERE source_path=? AND status IN ('linked','complete') AND undone=0 ORDER BY id DESC LIMIT 1",
            (source_path,),
        ).fetchone()


def latest_import_by_source() -> dict[str, dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT i.* FROM imports i
            JOIN (SELECT source_path, MAX(id) AS max_id FROM imports GROUP BY source_path) x
              ON x.max_id=i.id
            """
        ).fetchall()
    return {r["source_path"]: dict(r) for r in rows}


def successful_imports_by_source() -> dict[str, dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT i.* FROM imports i
            JOIN (
              SELECT source_path, MAX(id) AS max_id
              FROM imports
              WHERE status IN ('complete','linked') AND undone=0
              GROUP BY source_path
            ) x ON x.max_id=i.id
            """
        ).fetchall()
    return {r["source_path"]: dict(r) for r in rows}


def log_import(**kwargs) -> int:
    cols = [
        "source_path", "source_name", "media_type", "destination_key",
        "destination_path", "arr_name", "arr_instance", "arr_id", "status", "note",
        "created_paths", "source_fingerprint", "source_quality", "undone", "updated_at"
    ]
    values = []
    for c in cols:
        v = kwargs.get(c)
        if c == "created_paths" and isinstance(v, (list, tuple)):
            v = json.dumps(list(v))
        if c == "created_paths" and v is None:
            v = "[]"
        if c == "undone" and v is None:
            v = 0
        if c == "updated_at" and v is None:
            v = _utcnow()
        values.append(v)
    with db() as conn:
        cur = conn.execute(
            f"INSERT INTO imports ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            values,
        )
        return int(cur.lastrowid)


def mark_import_undone(import_id: int, note: str = ""):
    with db() as conn:
        conn.execute(
            "UPDATE imports SET undone=1,status='undone',note=?,updated_at=? WHERE id=?",
            (note, _utcnow(), import_id),
        )


def set_item_state(source_path: str, state: str, note: str = ""):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO item_state(source_path,state,note,updated_at) VALUES(?,?,?,?)
            ON CONFLICT(source_path) DO UPDATE SET state=excluded.state,note=excluded.note,updated_at=excluded.updated_at
            """,
            (source_path, state, note, _utcnow()),
        )


def item_states() -> dict[str, dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM item_state").fetchall()
    return {r["source_path"]: dict(r) for r in rows}


def clear_language_block_states(note: str = "Language Checks OFF - language state no longer blocks import") -> int:
    """Neutralise language-only workflow states while retaining Language Guard cache.

    The master toggle is an import control, not a cache purge.  Old ffprobe
    results and exact-source overrides stay available, but a stale
    language_review/language_rejected row must not keep the Inbox in a re-check
    state once checks are disabled.
    """
    with db() as conn:
        cur = conn.execute(
            """
            UPDATE item_state
               SET state='waiting', note=?, updated_at=?
             WHERE state IN ('language_rejected','language_rejected_removed','language_issue','language_review')
            """,
            (str(note or "Language Checks OFF - language state no longer blocks import"), _utcnow()),
        )
        return int(cur.rowcount or 0)


def add_activity(kind: str, title: str, detail: str = "", source_path: str = ""):
    with db() as conn:
        conn.execute(
            "INSERT INTO activity(kind,title,detail,source_path) VALUES(?,?,?,?)",
            (kind, title, detail, source_path),
        )


def recent_activity(limit: int = 30):
    with db() as conn:
        return conn.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def create_job(kind: str, items: list[dict]) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO jobs(kind,status,total,completed,failed,rejected,reviewed,cancel_requested,message,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (kind, "queued", len(items), 0, 0, 0, 0, 0, "Queued", _utcnow()),
        )
        jid = int(cur.lastrowid)
        for item in items:
            conn.execute(
                "INSERT INTO job_items(job_id,source_path,display_name,status,stage,destination_key,message,result_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (jid, str(item.get("source_path") or ""), str(item.get("display_name") or Path(str(item.get("source_path") or "")).name or "Job item"), "queued", "queued", str(item.get("destination_key") or ""), "Waiting", json.dumps(item.get("result") or {}), _utcnow()),
            )
        return jid


def _job_item_dict(row) -> dict:
    out = dict(row)
    try:
        out["result"] = json.loads(out.get("result_json") or "{}")
    except Exception:
        out["result"] = {}
    return out


def get_job(job_id: int):
    with db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        items = conn.execute("SELECT * FROM job_items WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    return rowdict(job), [_job_item_dict(x) for x in items]


def recent_jobs(limit: int = 20):
    with db() as conn:
        return conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def update_job(job_id: int, **fields):
    allowed = {"status", "total", "completed", "failed", "rejected", "reviewed", "cancel_requested", "message"}
    pairs = [(k, v) for k, v in fields.items() if k in allowed]
    if not pairs:
        return
    sql = ",".join(f"{k}=?" for k, _ in pairs) + ",updated_at=?"
    vals = [v for _, v in pairs] + [_utcnow(), job_id]
    with db() as conn:
        conn.execute(f"UPDATE jobs SET {sql} WHERE id=?", vals)


def update_job_item(item_id: int, **fields):
    allowed = {"status", "stage", "destination_key", "message", "result_json"}
    if "result" in fields:
        fields["result_json"] = json.dumps(fields.pop("result") or {}, sort_keys=True)
    pairs = [(k, v) for k, v in fields.items() if k in allowed]
    if not pairs:
        return
    sql = ",".join(f"{k}=?" for k, _ in pairs) + ",updated_at=?"
    vals = [v for _, v in pairs] + [_utcnow(), item_id]
    with db() as conn:
        conn.execute(f"UPDATE job_items SET {sql} WHERE id=?", vals)



def add_job_item(job_id: int, *, source_path: str = "", display_name: str = "", status: str = "queued", stage: str = "queued", destination_key: str = "", message: str = "", result: dict | None = None) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO job_items(job_id,source_path,display_name,status,stage,destination_key,message,result_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (int(job_id), source_path, display_name, status, stage, destination_key, message, json.dumps(result or {}, sort_keys=True), _utcnow()),
        )
        return int(cur.lastrowid)


def job_cancel_requested(job_id: int) -> bool:
    with db() as conn:
        row = conn.execute("SELECT cancel_requested,status FROM jobs WHERE id=?", (int(job_id),)).fetchone()
    return bool(row and (int(row["cancel_requested"] or 0) or str(row["status"] or "") in {"cancelling", "cancelled"}))


def request_job_cancel(job_id: int) -> bool:
    with db() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id=?", (int(job_id),)).fetchone()
        if not row:
            return False
        status = str(row["status"] or "")
        if status not in {"queued", "running", "cancelling"}:
            return False
        if status == "queued":
            conn.execute("UPDATE jobs SET cancel_requested=1,status='cancelled',message='Cancelled before start',updated_at=? WHERE id=?", (_utcnow(), int(job_id)))
            conn.execute("UPDATE job_items SET status='cancelled',stage='cancelled',message='Cancelled before start',updated_at=? WHERE job_id=? AND status='queued'", (_utcnow(), int(job_id)))
        else:
            conn.execute("UPDATE jobs SET cancel_requested=1,status='cancelling',message='Cancellation requested; stopping safely',updated_at=? WHERE id=?", (_utcnow(), int(job_id)))
        return True


def mark_remaining_job_items_cancelled(job_id: int, message: str = "Cancelled") -> None:
    with db() as conn:
        conn.execute(
            "UPDATE job_items SET status='cancelled',stage='cancelled',message=?,updated_at=? WHERE job_id=? AND status IN ('queued','running')",
            (message, _utcnow(), int(job_id)),
        )


def remove_job(job_id: int) -> bool:
    with db() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id=?", (int(job_id),)).fetchone()
        if not row or str(row["status"] or "") in {"queued", "running", "cancelling"}:
            return False
        conn.execute("DELETE FROM jobs WHERE id=?", (int(job_id),))
        return True


def clear_finished_jobs() -> int:
    with db() as conn:
        cur = conn.execute("DELETE FROM jobs WHERE status NOT IN ('queued','running','cancelling')")
        return int(cur.rowcount or 0)

def list_rules(media_type: str | None = None):
    with db() as conn:
        if media_type:
            return conn.execute("SELECT * FROM routing_rules WHERE media_type=? ORDER BY weight DESC,id", (media_type,)).fetchall()
        return conn.execute("SELECT * FROM routing_rules ORDER BY media_type,weight DESC,id").fetchall()


def save_rule(media_type: str, field: str, pattern: str, destination_key: str, weight: int = 90, learned: int = 0):
    pattern = pattern.strip().lower()
    if not pattern:
        return
    with db() as conn:
        conn.execute(
            """
            INSERT INTO routing_rules(media_type,field,pattern,destination_key,weight,enabled,learned,hits)
            VALUES(?,?,?,?,?,1,?,0)
            ON CONFLICT(media_type,field,pattern,destination_key)
            DO UPDATE SET weight=excluded.weight,enabled=1,learned=MAX(routing_rules.learned,excluded.learned)
            """,
            (media_type, field, pattern, destination_key, int(weight), int(learned)),
        )


def delete_rule(rule_id: int):
    with db() as conn:
        conn.execute("DELETE FROM routing_rules WHERE id=?", (rule_id,))


def increment_rule_hit(rule_id: int):
    with db() as conn:
        conn.execute("UPDATE routing_rules SET hits=hits+1 WHERE id=?", (rule_id,))


def learn_exact_route(media_type: str, title: str, destination_key: str):
    from .scanner import normalize_title
    norm = normalize_title(title)
    if norm:
        save_rule(media_type, "normalized_title", norm, destination_key, 100, learned=1)


def cache_get(key: str):
    with db() as conn:
        row = conn.execute("SELECT payload FROM metadata_cache WHERE cache_key=?", (key,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def cache_set(key: str, payload):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO metadata_cache(cache_key,payload,updated_at) VALUES(?,?,?)
            ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at
            """,
            (key, json.dumps(payload), _utcnow()),
        )


# ---- v2 settings, users and request tracking ---------------------------------

def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    rounds = 240_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"pbkdf2_sha256${rounds}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        alg, rounds, salt64, digest64 = stored.split("$", 3)
        if alg != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt64)
        expected = base64.b64decode(digest64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def ensure_user(username: str, password: str):
    username = (username or "admin").strip() or "admin"
    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users(username,display_name,password_hash,role,theme) VALUES(?,?,?,?,?)",
                (username, username, _hash_password(password or "change-me"), "admin", "nexus"),
            )


def authenticate_user(identity: str, password: str):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE lower(username)=lower(?) OR lower(COALESCE(email,''))=lower(?) LIMIT 1",
            (identity.strip(), identity.strip()),
        ).fetchone()
    if row and _verify_password(password, row["password_hash"]):
        return dict(row)
    return None


def get_user(user_id: int):
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (int(user_id),)).fetchone()
    return dict(row) if row else None


def update_user(user_id: int, username: str, email: str, display_name: str, theme: str, dashboard_layout: str, password: str = ""):
    with db() as conn:
        if password:
            conn.execute(
                "UPDATE users SET username=?,email=?,display_name=?,theme=?,dashboard_layout=?,password_hash=?,updated_at=? WHERE id=?",
                (username.strip(), email.strip() or None, display_name.strip(), theme, dashboard_layout, _hash_password(password), _utcnow(), int(user_id)),
            )
        else:
            conn.execute(
                "UPDATE users SET username=?,email=?,display_name=?,theme=?,dashboard_layout=?,updated_at=? WHERE id=?",
                (username.strip(), email.strip() or None, display_name.strip(), theme, dashboard_layout, _utcnow(), int(user_id)),
            )


def setting_get(key: str, default: str = "") -> str:
    # Connection objects are constructed while the module imports, before the
    # FastAPI startup hook creates a brand-new database. Fresh installs must
    # therefore gracefully fall back to .env until app_settings exists.
    try:
        with db() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return default
        raise


def setting_set(key: str, value: str, secret: bool = False):
    with db() as conn:
        conn.execute(
            "INSERT INTO app_settings(key,value,secret,updated_at) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,secret=excluded.secret,updated_at=excluded.updated_at",
            (key, value or "", int(bool(secret)), _utcnow()),
        )


def setting_delete(key: str):
    with db() as conn:
        conn.execute("DELETE FROM app_settings WHERE key=?", (key,))


def all_settings(mask_secrets: bool = True) -> dict[str, str]:
    with db() as conn:
        rows = conn.execute("SELECT key,value,secret FROM app_settings").fetchall()
    out = {}
    for r in rows:
        out[r["key"]] = "********" if mask_secrets and r["secret"] and r["value"] else r["value"]
    return out


def track_request(media_type: str, external_id: str, title: str, year=None, destination_key="", arr_instance="", arr_id=None, status="requested", user_id: int | None = None):
    if not external_id:
        return
    with db() as conn:
        conn.execute(
            """INSERT INTO requests(media_type,external_id,title,year,destination_key,arr_instance,arr_id,status,user_id,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(media_type,external_id) DO UPDATE SET title=excluded.title,year=excluded.year,destination_key=excluded.destination_key,
               arr_instance=excluded.arr_instance,arr_id=excluded.arr_id,status=excluded.status,user_id=COALESCE(excluded.user_id,requests.user_id),updated_at=excluded.updated_at""",
            (media_type, str(external_id), title, year, destination_key, arr_instance, arr_id, status, user_id, _utcnow()),
        )


def request_map(media_type: str) -> dict[str, dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM requests WHERE media_type=?", (media_type,)).fetchall()
    return {str(r["external_id"]): dict(r) for r in rows}


def activity_by_day(days: int = 7):
    with db() as conn:
        rows = conn.execute(
            "SELECT substr(created_at,1,10) day, count(*) count FROM activity WHERE created_at >= datetime('now', ?) GROUP BY substr(created_at,1,10) ORDER BY day",
            (f'-{int(days)-1} days',),
        ).fetchall()
    return [dict(r) for r in rows]


def list_users():
    with db() as conn:
        rows = conn.execute("SELECT id,username,email,display_name,role,theme,dashboard_layout,can_request,daily_request_limit,created_at,updated_at FROM users ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def create_user(username: str, email: str, display_name: str, password: str, role: str = "user") -> int:
    username = (username or "").strip()
    if not username or not password:
        raise ValueError("Username and password are required")
    role = role if role in {"admin", "user"} else "user"
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO users(username,email,display_name,password_hash,role,theme,dashboard_layout) VALUES(?,?,?,?,?,?,?)",
            (username, (email or "").strip() or None, (display_name or username).strip(), _hash_password(password), role, "nexus", "default"),
        )
        return int(cur.lastrowid)


def delete_user(user_id: int, protect_user_id: int | None = None):
    if protect_user_id and int(user_id) == int(protect_user_id):
        raise ValueError("You cannot delete the account you are currently using")
    with db() as conn:
        admins = conn.execute("SELECT count(*) FROM users WHERE role='admin'").fetchone()[0]
        row = conn.execute("SELECT role FROM users WHERE id=?", (int(user_id),)).fetchone()
        if row and row[0] == "admin" and admins <= 1:
            raise ValueError("At least one administrator must remain")
        conn.execute("DELETE FROM users WHERE id=?", (int(user_id),))


def create_password_reset(email: str, ttl_seconds: int = 1800) -> str | None:
    identity = (email or "").strip().lower()
    if not identity:
        return None
    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE lower(COALESCE(email,''))=? LIMIT 1", (identity,)).fetchone()
        if not row:
            return None
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        conn.execute("UPDATE password_resets SET used=1 WHERE user_id=? AND used=0", (int(row["id"]),))
        conn.execute("INSERT INTO password_resets(token_hash,user_id,expires_at,used) VALUES(?,?,?,0)", (token_hash, int(row["id"]), time.time()+ttl_seconds))
        return token


def consume_password_reset(token: str, new_password: str) -> bool:
    if not token or len(new_password or "") < 8:
        return False
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with db() as conn:
        row = conn.execute("SELECT user_id,expires_at,used FROM password_resets WHERE token_hash=?", (token_hash,)).fetchone()
        if not row or row["used"] or float(row["expires_at"]) < time.time():
            return False
        conn.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (_hash_password(new_password), _utcnow(), int(row["user_id"])))
        conn.execute("UPDATE password_resets SET used=1 WHERE token_hash=?", (token_hash,))
        return True


def update_user_access(user_id: int, role: str, can_request: bool, daily_request_limit: int):
    role = role if role in {"admin", "user"} else "user"
    limit = max(0, int(daily_request_limit or 0))
    with db() as conn:
        conn.execute(
            "UPDATE users SET role=?,can_request=?,daily_request_limit=?,updated_at=? WHERE id=?",
            (role, int(bool(can_request)), limit, _utcnow(), int(user_id)),
        )


def requests_today(user_id: int) -> int:
    if not user_id:
        return 0
    with db() as conn:
        return int(conn.execute(
            "SELECT count(*) FROM requests WHERE user_id=? AND date(created_at)=date('now')",
            (int(user_id),),
        ).fetchone()[0])


def title_timeline(title: str, source_path: str = "", limit: int = 120) -> list[dict]:
    title = (title or "").strip()
    events: list[dict] = []
    with db() as conn:
        if title:
            rows = conn.execute(
                "SELECT created_at,kind AS source,kind AS event,title,detail AS message FROM activity WHERE lower(title)=lower(?) ORDER BY id DESC LIMIT ?",
                (title, int(limit)),
            ).fetchall()
            events.extend(dict(r) for r in rows)
            reqs = conn.execute(
                "SELECT created_at,'request' AS source,status AS event,title,COALESCE(status_detail,'') AS message FROM requests WHERE lower(title)=lower(?) ORDER BY id DESC LIMIT ?",
                (title, int(limit)),
            ).fetchall()
            events.extend(dict(r) for r in reqs)
        if source_path:
            rows = conn.execute(
                "SELECT created_at,'import' AS source,status AS event,source_name AS title,COALESCE(note,'') AS message FROM imports WHERE source_path=? ORDER BY id DESC LIMIT ?",
                (source_path, int(limit)),
            ).fetchall()
            events.extend(dict(r) for r in rows)
        if title:
            scrapes = conn.execute(
                "SELECT started_at AS created_at,'scraping' AS source,status AS event,title,COALESCE(detail,'') AS message FROM scrape_jobs WHERE lower(title)=lower(?) ORDER BY id DESC LIMIT ?",
                (title, int(limit)),
            ).fetchall()
            events.extend(dict(r) for r in scrapes)
    events.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return events[:limit]


def setting_rows() -> list[dict]:
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT key,value,secret,updated_at FROM app_settings ORDER BY key").fetchall()]


def replace_nonsecret_settings(values: dict[str, str]):
    for key, value in (values or {}).items():
        if value == "********":
            continue
        # Never import known credential/token namespaces from a sanitized config file.
        if any(x in key.lower() for x in ("api_key", "password", "secret", "token", "webhook")):
            continue
        setting_set(str(key), str(value), False)


def request_rows(limit: int = 500):
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM requests ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()]


# ---- v3 operational helpers -------------------------------------------------

def user_count() -> int:
    try:
        with db() as conn:
            return int(conn.execute("SELECT count(*) FROM users").fetchone()[0])
    except sqlite3.OperationalError:
        return 0


def log_event(level: str, source: str, event: str, message: str = "", context: dict | None = None):
    level = (level or "info").lower()
    if level not in {"debug","info","warning","error","critical"}:
        level = "info"
    with db() as conn:
        conn.execute(
            "INSERT INTO app_logs(level,source,event,message,context,created_at) VALUES(?,?,?,?,?,?)",
            (level, (source or "app").lower(), event or "event", message or "", json.dumps(context or {}), _utcnow()),
        )


def list_logs(level: str = "all", source: str = "all", q: str = "", limit: int = 300):
    clauses=[]; args=[]
    if level != "all": clauses.append("level=?"); args.append(level)
    if source != "all": clauses.append("source=?"); args.append(source)
    if q:
        clauses.append("(event LIKE ? OR message LIKE ? OR context LIKE ?)")
        like=f"%{q}%"; args.extend([like,like,like])
    where=(" WHERE "+" AND ".join(clauses)) if clauses else ""
    args.append(int(limit))
    with db() as conn:
        rows=conn.execute(f"SELECT * FROM app_logs{where} ORDER BY id DESC LIMIT ?", args).fetchall()
    return [dict(r) for r in rows]


def log_sources():
    with db() as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT source FROM app_logs ORDER BY source").fetchall()]


def seed_default_mounts():
    defaults = [
        ("DMM / Decypharr source", settings.source_root, "source", "decypharr", None),
        ("Movies", settings.radarr_default_root, "movie", "radarr", "default"),
        ("Movies - Kids", settings.radarr_kids_root, "movie", "radarr", "kids"),
        ("Movies - Christmas", settings.radarr_christmas_root, "movie", "radarr", "christmas"),
        ("Movies - Halloween", settings.radarr_halloween_root, "movie", "radarr", "halloween"),
        ("Movies - Easter", settings.radarr_easter_root, "movie", "radarr", "easter"),
        ("TV", settings.sonarr_default_root, "tv", "sonarr", "default"),
        ("TV - Kids", settings.sonarr_kids_root, "tv", "sonarr", "kids"),
        ("TV - Netflix", settings.sonarr_netflix_root, "tv", "sonarr", "netflix"),
        ("TV - Disney+", settings.sonarr_disney_root, "tv", "sonarr", "disney"),
        ("TV - Amazon", settings.sonarr_amazon_root, "tv", "sonarr", "amazon"),
        ("TV - Apple TV+", settings.sonarr_apple_root, "tv", "sonarr", "apple"),
        ("TV - BBC", settings.sonarr_bbc_root, "tv", "sonarr", "bbc"),
        ("Music", settings.lidarr_root, "music", "lidarr", "default"),
    ]
    try:
        with db() as conn:
            for name,path,kind,service,destination in defaults:
                conn.execute(
                    "INSERT OR IGNORE INTO mount_registry(name,logical_path,kind,service,destination_key,enabled) VALUES(?,?,?,?,?,1)",
                    (name,path,kind,service,destination),
                )
    except sqlite3.OperationalError:
        pass


def list_mounts(enabled_only: bool = False):
    with db() as conn:
        sql="SELECT * FROM mount_registry" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY kind,name"
        return [dict(r) for r in conn.execute(sql).fetchall()]


def save_mount(name: str, logical_path: str, kind: str = "library", service: str = "", destination_key: str = "", mount_id: int | None = None):
    name=(name or "").strip(); logical_path=(logical_path or "").strip()
    if not name or not logical_path.startswith("/"):
        raise ValueError("Mount name and absolute logical path are required")
    with db() as conn:
        if mount_id:
            conn.execute("UPDATE mount_registry SET name=?,logical_path=?,kind=?,service=?,destination_key=?,updated_at=? WHERE id=?",
                         (name,logical_path,kind,service or None,destination_key or None,_utcnow(),int(mount_id)))
            return int(mount_id)
        cur=conn.execute("INSERT INTO mount_registry(name,logical_path,kind,service,destination_key,enabled,updated_at) VALUES(?,?,?,?,?,1,?)",
                         (name,logical_path,kind,service or None,destination_key or None,_utcnow()))
        return int(cur.lastrowid)


def delete_mount(mount_id: int):
    with db() as conn:
        conn.execute("DELETE FROM mount_registry WHERE id=?", (int(mount_id),))


def add_scrape(media_type: str, title: str, external_id: str = "", arr_service: str = "", arr_instance: str = "", arr_id: int | None = None, destination_key: str = "", status: str = "searching", detail: str = "") -> int:
    with db() as conn:
        cur=conn.execute("INSERT INTO scrape_jobs(media_type,title,external_id,arr_service,arr_instance,arr_id,destination_key,status,detail,started_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (media_type,title,str(external_id or ""),arr_service,arr_instance,arr_id,destination_key,status,detail,_utcnow(),_utcnow()))
        return int(cur.lastrowid)


def update_scrape(scrape_id: int, status: str | None = None, detail: str | None = None):
    fields=[]; args=[]
    if status is not None: fields.append("status=?"); args.append(status)
    if detail is not None: fields.append("detail=?"); args.append(detail)
    fields.append("updated_at=?"); args.append(_utcnow()); args.append(int(scrape_id))
    with db() as conn:
        conn.execute(f"UPDATE scrape_jobs SET {','.join(fields)} WHERE id=?", args)


def list_scrapes(limit: int = 100, status: str = "all"):
    with db() as conn:
        if status == "all":
            rows=conn.execute("SELECT * FROM scrape_jobs ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        else:
            rows=conn.execute("SELECT * FROM scrape_jobs WHERE status=? ORDER BY id DESC LIMIT ?", (status,int(limit))).fetchall()
    return [dict(r) for r in rows]


def ui_pref_get(user_id: int, key: str, default: str = "") -> str:
    with db() as conn:
        row=conn.execute("SELECT pref_value FROM ui_preferences WHERE user_id=? AND pref_key=?", (int(user_id),key)).fetchone()
    return row[0] if row else default


def ui_pref_set(user_id: int, key: str, value: str):
    with db() as conn:
        conn.execute("INSERT INTO ui_preferences(user_id,pref_key,pref_value,updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id,pref_key) DO UPDATE SET pref_value=excluded.pref_value,updated_at=excluded.updated_at",
                     (int(user_id),key,value or "",_utcnow()))

# ---- v6 acquisition helpers -------------------------------------------------

def update_request_progress(media_type: str, external_id: str, status: str, detail: str = "", protocol: str = ""):
    if not external_id:
        return
    with db() as conn:
        conn.execute(
            "UPDATE requests SET status=?,status_detail=?,protocol=?,updated_at=? WHERE media_type=? AND external_id=?",
            (status or "requested", detail or "", protocol or None, _utcnow(), media_type, str(external_id)),
        )
