from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

log = logging.getLogger(__name__)

DATA_DIR = Path(
    os.environ.get(
        "TOOLS_DATA_DIR",
        str(Path(__file__).resolve().parent.parent / "data"),
    )
)
DB_PATH = DATA_DIR / "swppp_sessions.db"

SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS saved_sessions (
    user_id    TEXT NOT NULL,
    name       TEXT NOT NULL,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, name)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = _open_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def connect() -> Generator[sqlite3.Connection, None, None]:
    conn = _open_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
    log.info("SWPPP sessions DB initialized at %s", DB_PATH)


def list_sessions(conn: sqlite3.Connection, user_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT name, updated_at FROM saved_sessions "
        "WHERE user_id = ? ORDER BY name COLLATE NOCASE",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_session(
    conn: sqlite3.Connection, user_id: str, name: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT data FROM saved_sessions WHERE user_id = ? AND name = ?",
        (user_id, name),
    ).fetchone()
    if not row:
        return None
    return json.loads(row["data"])


def save_session(
    conn: sqlite3.Connection, user_id: str, name: str, data: dict[str, Any]
) -> None:
    now = _now()
    conn.execute(
        "INSERT INTO saved_sessions (user_id, name, data, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, name) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
        (user_id, name, json.dumps(data), now, now),
    )


def delete_session(conn: sqlite3.Connection, user_id: str, name: str) -> None:
    conn.execute(
        "DELETE FROM saved_sessions WHERE user_id = ? AND name = ?",
        (user_id, name),
    )
