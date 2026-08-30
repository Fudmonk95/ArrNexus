from __future__ import annotations
import json
import sqlite3
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


def init_db():
    with sqlite3.connect(settings.db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()


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
            "INSERT INTO jobs(kind,status,total,completed,failed,message,updated_at) VALUES(?,?,?,?,?,?,?)",
            (kind, "queued", len(items), 0, 0, "Queued", _utcnow()),
        )
        jid = int(cur.lastrowid)
        for item in items:
            conn.execute(
                "INSERT INTO job_items(job_id,source_path,display_name,status,stage,destination_key,message,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (jid, item.get("source_path"), item.get("display_name"), "queued", "queued", item.get("destination_key"), "Waiting", _utcnow()),
            )
        return jid


def get_job(job_id: int):
    with db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        items = conn.execute("SELECT * FROM job_items WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    return rowdict(job), [dict(x) for x in items]


def recent_jobs(limit: int = 20):
    with db() as conn:
        return conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def update_job(job_id: int, **fields):
    allowed = {"status", "total", "completed", "failed", "message"}
    pairs = [(k, v) for k, v in fields.items() if k in allowed]
    if not pairs:
        return
    sql = ",".join(f"{k}=?" for k, _ in pairs) + ",updated_at=?"
    vals = [v for _, v in pairs] + [_utcnow(), job_id]
    with db() as conn:
        conn.execute(f"UPDATE jobs SET {sql} WHERE id=?", vals)


def update_job_item(item_id: int, **fields):
    allowed = {"status", "stage", "destination_key", "message"}
    pairs = [(k, v) for k, v in fields.items() if k in allowed]
    if not pairs:
        return
    sql = ",".join(f"{k}=?" for k, _ in pairs) + ",updated_at=?"
    vals = [v for _, v in pairs] + [_utcnow(), item_id]
    with db() as conn:
        conn.execute(f"UPDATE job_items SET {sql} WHERE id=?", vals)


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
