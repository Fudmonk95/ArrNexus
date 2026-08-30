import sqlite3
from contextlib import contextmanager
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
    arr_id INTEGER,
    status TEXT NOT NULL,
    note TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_import_source ON imports(source_path);
"""


def init_db():
    with sqlite3.connect(settings.db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def db():
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def recent_imports(limit=100):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM imports ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def log_import(**kwargs):
    cols = [
        "source_path", "source_name", "media_type", "destination_key",
        "destination_path", "arr_name", "arr_id", "status", "note"
    ]
    values = [kwargs.get(c) for c in cols]
    with db() as conn:
        conn.execute(
            f"INSERT INTO imports ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            values,
        )
