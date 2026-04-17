from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import uuid
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
DB_PATH = DATA_DIR / "auth.db"

# Invite-code alphabet: A-Z + 0-9, minus ambiguous O/0/I/1
SAFE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS apps (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL,
    route_prefix  TEXT NOT NULL UNIQUE,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    password_hash TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invite_codes (
    id              TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    claimed_by      TEXT REFERENCES users(id),
    app_permissions TEXT NOT NULL,
    grant_admin     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    claimed_at      TEXT
);

CREATE TABLE IF NOT EXISTS user_app_access (
    user_id    TEXT NOT NULL REFERENCES users(id),
    app_id     TEXT NOT NULL REFERENCES apps(id),
    granted_at TEXT NOT NULL,
    PRIMARY KEY (user_id, app_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    token        TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id),
    device_label TEXT,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """FastAPI dependency — yields a connection with auto-commit/rollback."""
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
    """Context manager for scripts and tests (not a FastAPI dependency)."""
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
    """Create all tables and run pending migrations (idempotent)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.executescript(SCHEMA_SQL)
        _run_migrations(conn)
        conn.commit()
    finally:
        conn.close()
    log.info("Database initialized at %s", DB_PATH)


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a column exists on a table via PRAGMA table_info."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _index_exists(conn: sqlite3.Connection, index_name: str) -> bool:
    """Check if an index exists by name."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    ).fetchone()
    return row is not None


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Run all pending schema migrations."""
    # Migration 1: Add password_hash column to users table.
    if not _column_exists(conn, "users", "password_hash"):
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        log.info("Migration: added password_hash column to users table")

    # Migration 2: Enforce case-insensitive uniqueness on users.display_name.
    if not _index_exists(conn, "ux_users_display_name_nocase"):
        dupes = conn.execute(
            "SELECT LOWER(display_name) AS n, COUNT(*) AS c FROM users "
            "GROUP BY n HAVING c > 1"
        ).fetchall()
        if dupes:
            names = ", ".join(f"{d['n']} (x{d['c']})" for d in dupes)
            raise RuntimeError(
                f"Cannot enforce unique display_name: duplicates exist: {names}. "
                f"Resolve manually before redeploying."
            )
        conn.execute(
            "CREATE UNIQUE INDEX ux_users_display_name_nocase "
            "ON users(display_name COLLATE NOCASE)"
        )
        log.info(
            "Migration: created unique index on users(display_name COLLATE NOCASE)"
        )


# ── Apps ─────────────────────────────────────────────────────────────────


def seed_app(
    conn: sqlite3.Connection,
    app_id: str,
    name: str,
    description: str,
    route_prefix: str,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO apps (id, name, description, route_prefix, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (app_id, name, description, route_prefix, _now()),
    )


def get_all_apps(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM apps ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_app(conn: sqlite3.Connection, app_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM apps WHERE id = ?", (app_id,)).fetchone()
    return dict(row) if row else None


def create_app(
    conn: sqlite3.Connection,
    app_id: str,
    name: str,
    description: str,
    route_prefix: str,
) -> None:
    conn.execute(
        "INSERT INTO apps (id, name, description, route_prefix, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (app_id, name, description, route_prefix, _now()),
    )


def update_app(conn: sqlite3.Connection, app_id: str, **fields: Any) -> None:
    allowed = {"name", "description", "is_active"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return
    for k in updates:
        if isinstance(updates[k], bool):
            updates[k] = int(updates[k])
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [app_id]
    conn.execute(f"UPDATE apps SET {set_clause} WHERE id = ?", values)


# ── Invite Codes ─────────────────────────────────────────────────────────


def generate_invite_code() -> str:
    part1 = "".join(secrets.choice(SAFE_CHARS) for _ in range(4))
    part2 = "".join(secrets.choice(SAFE_CHARS) for _ in range(4))
    return f"TOOLS-{part1}-{part2}"


def create_invite(
    conn: sqlite3.Connection,
    display_name: str,
    app_permissions: list[str],
    *,
    grant_admin: bool = False,
) -> str:
    code = generate_invite_code()
    while conn.execute("SELECT 1 FROM invite_codes WHERE id = ?", (code,)).fetchone():
        code = generate_invite_code()
    conn.execute(
        "INSERT INTO invite_codes "
        "(id, display_name, status, app_permissions, grant_admin, created_at) "
        "VALUES (?, ?, 'pending', ?, ?, ?)",
        (code, display_name, json.dumps(app_permissions), int(grant_admin), _now()),
    )
    return code


def get_invite(conn: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM invite_codes WHERE id = ?", (code,)).fetchone()
    return dict(row) if row else None


def get_all_invites(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM invite_codes ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def revoke_invite(conn: sqlite3.Connection, code: str) -> None:
    conn.execute("UPDATE invite_codes SET status = 'revoked' WHERE id = ?", (code,))


# ── Users ────────────────────────────────────────────────────────────────


def create_user(
    conn: sqlite3.Connection,
    display_name: str,
    is_admin: bool = False,
) -> str:
    user_id = str(uuid.uuid4())
    now = _now()
    conn.execute(
        "INSERT INTO users (id, display_name, is_active, is_admin, created_at, last_seen_at) "
        "VALUES (?, ?, 1, ?, ?, ?)",
        (user_id, display_name, int(is_admin), now, now),
    )
    return user_id


def get_user(conn: sqlite3.Connection, user_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_all_users(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM users ORDER BY display_name COLLATE NOCASE"
    ).fetchall()
    result = []
    for row in rows:
        user = dict(row)
        apps = conn.execute(
            "SELECT app_id FROM user_app_access WHERE user_id = ?", (user["id"],)
        ).fetchall()
        user["apps"] = [a["app_id"] for a in apps]
        result.append(user)
    return result


def update_user(conn: sqlite3.Connection, user_id: str, **fields: Any) -> None:
    allowed = {"is_active", "is_admin"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return
    for k in updates:
        if isinstance(updates[k], bool):
            updates[k] = int(updates[k])
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [user_id]
    conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)


# ── User App Access ──────────────────────────────────────────────────────


def grant_app_access(conn: sqlite3.Connection, user_id: str, app_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO user_app_access (user_id, app_id, granted_at) "
        "VALUES (?, ?, ?)",
        (user_id, app_id, _now()),
    )


def revoke_app_access(conn: sqlite3.Connection, user_id: str, app_id: str) -> None:
    conn.execute(
        "DELETE FROM user_app_access WHERE user_id = ? AND app_id = ?",
        (user_id, app_id),
    )


def get_user_apps(conn: sqlite3.Connection, user_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT app_id FROM user_app_access WHERE user_id = ?", (user_id,)
    ).fetchall()
    return [r["app_id"] for r in rows]


# ── Passwords ────────────────────────────────────────────────────────────


def _hash_password(password: str) -> str:
    """Hash a password with scrypt.  Returns 'salt_hex:hash_hex'."""
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return salt.hex() + ":" + h.hex()


def _verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored 'salt_hex:hash_hex' string."""
    try:
        salt_hex, hash_hex = stored_hash.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    h = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return secrets.compare_digest(h, expected)


def set_user_password(conn: sqlite3.Connection, user_id: str, password: str) -> None:
    """Set (or replace) a user's password."""
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (_hash_password(password), user_id),
    )


def user_has_password(conn: sqlite3.Connection, user_id: str) -> bool:
    """Return True if *user_id* has a password_hash set (i.e. not NULL)."""
    row = conn.execute(
        "SELECT password_hash FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if not row:
        return False
    return bool(row["password_hash"])


def verify_user_password(conn: sqlite3.Connection, user_id: str, password: str) -> bool:
    """Verify *password* against the stored hash for *user_id*."""
    row = conn.execute(
        "SELECT password_hash FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if not row or not row["password_hash"]:
        return False
    return _verify_password(password, row["password_hash"])


def authenticate_user(
    conn: sqlite3.Connection,
    display_name: str,
    password: str,
) -> dict[str, Any] | None:
    """Look up a user by display_name + password.  Returns user dict or None."""
    rows = conn.execute(
        "SELECT id, display_name, password_hash, is_active, is_admin "
        "FROM users WHERE display_name = ? COLLATE NOCASE",
        (display_name,),
    ).fetchall()
    for row in rows:
        user = dict(row)
        if not user["is_active"]:
            continue
        if not user["password_hash"]:
            continue
        if _verify_password(password, user["password_hash"]):
            user["apps"] = get_user_apps(conn, user["id"])
            del user["password_hash"]
            return user
    return None


# ── Sessions ─────────────────────────────────────────────────────────────


def create_session(
    conn: sqlite3.Connection,
    user_id: str,
    device_label: str | None = None,
) -> str:
    token = secrets.token_hex(32)
    now = _now()
    conn.execute(
        "INSERT INTO sessions (token, user_id, device_label, created_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (token, user_id, device_label, now, now),
    )
    return token


def validate_session(conn: sqlite3.Connection, token: str) -> dict[str, Any] | None:
    """Look up session → active user.  Returns user dict with app list, or None."""
    row = conn.execute(
        "SELECT u.id, u.display_name, u.is_active, u.is_admin "
        "FROM sessions s JOIN users u ON s.user_id = u.id "
        "WHERE s.token = ?",
        (token,),
    ).fetchone()
    if not row:
        return None
    user = dict(row)
    if not user["is_active"]:
        return None
    now = _now()
    conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token = ?", (now, token))
    conn.execute("UPDATE users SET last_seen_at = ? WHERE id = ?", (now, user["id"]))
    user["apps"] = get_user_apps(conn, user["id"])
    return user


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def get_user_sessions(conn: sqlite3.Connection, user_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT token, device_label, created_at, last_seen_at "
        "FROM sessions WHERE user_id = ? ORDER BY last_seen_at DESC",
        (user_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["token_prefix"] = d.pop("token")[:8] + "...."
        result.append(d)
    return result


def delete_user_sessions(conn: sqlite3.Connection, user_id: str) -> int:
    cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return cur.rowcount


def delete_session_by_prefix(conn: sqlite3.Connection, token_prefix: str) -> bool:
    """Delete session by 8-char token prefix.  Returns True if exactly one removed."""
    rows = conn.execute(
        "SELECT token FROM sessions WHERE token LIKE ?",
        (token_prefix + "%",),
    ).fetchall()
    if len(rows) != 1:
        return False
    conn.execute("DELETE FROM sessions WHERE token = ?", (rows[0]["token"],))
    return True


# ── Claim Flow ───────────────────────────────────────────────────────────


def claim_invite_code(
    conn: sqlite3.Connection,
    code: str,
    device_label: str | None = None,
) -> tuple[str, str] | None:
    """Claim an invite code.  Returns (user_id, session_token) or None."""
    invite = get_invite(conn, code)
    if not invite or invite["status"] != "pending":
        return None

    is_admin = bool(invite["grant_admin"])
    user_id = create_user(conn, invite["display_name"], is_admin=is_admin)

    app_ids = json.loads(invite["app_permissions"])
    for app_id in app_ids:
        grant_app_access(conn, user_id, app_id)

    now = _now()
    conn.execute(
        "UPDATE invite_codes SET status = 'claimed', claimed_by = ?, claimed_at = ? "
        "WHERE id = ?",
        (user_id, now, code),
    )

    token = create_session(conn, user_id, device_label)
    return user_id, token
