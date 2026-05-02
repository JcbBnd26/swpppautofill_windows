from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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

# Session lifetime: 90-day sliding window
SESSION_LIFETIME_DAYS = 90

# Valid company-user roles.
COMPANY_ROLES = frozenset({"company_admin", "pm", "viewer"})

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
    id                   TEXT PRIMARY KEY,
    display_name         TEXT NOT NULL,
    password_hash        TEXT,
    is_active            INTEGER NOT NULL DEFAULT 1,
    is_admin             INTEGER NOT NULL DEFAULT 0,
    is_platform_admin    INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL
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
    last_seen_at TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    id                           TEXT PRIMARY KEY,
    legal_name                   TEXT NOT NULL,
    display_name                 TEXT NOT NULL,
    slug                         TEXT NOT NULL UNIQUE,
    primary_timezone             TEXT NOT NULL DEFAULT 'America/Chicago',
    address                      TEXT,
    phone                        TEXT,
    logo_path                    TEXT,
    website                      TEXT,
    is_active                    INTEGER NOT NULL DEFAULT 1,
    paused_at                    TEXT,
    paused_reason                TEXT,
    plan                         TEXT,
    seat_limit                   INTEGER,
    active_until                 TEXT,
    settings_json                TEXT,
    created_at                   TEXT NOT NULL,
    created_by_platform_admin_id TEXT REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS company_users (
    user_id    TEXT NOT NULL REFERENCES users(id),
    company_id TEXT NOT NULL REFERENCES companies(id),
    role       TEXT NOT NULL DEFAULT 'pm',
    joined_at  TEXT NOT NULL,
    is_active  INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, company_id)
);

CREATE TABLE IF NOT EXISTS company_signup_invites (
    token                         TEXT PRIMARY KEY,
    proposed_company_name         TEXT NOT NULL,
    admin_email                   TEXT NOT NULL,
    created_at                    TEXT NOT NULL,
    expires_at                    TEXT NOT NULL,
    claimed_at                    TEXT,
    claimed_by_user_id            TEXT REFERENCES users(id),
    created_by_platform_admin_id  TEXT REFERENCES users(id)
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
    conn.row_factory = sqlite3.Row
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

    # Migration 3: Add expires_at column to sessions table.
    if not _column_exists(conn, "sessions", "expires_at"):
        conn.execute("ALTER TABLE sessions ADD COLUMN expires_at TEXT")
        # Populate existing sessions: extend from last_seen_at by SESSION_LIFETIME_DAYS.
        # This gives active sessions a fair runway rather than expiring them immediately.
        conn.execute(
            "UPDATE sessions SET expires_at = "
            "datetime(last_seen_at, '+90 days') "
            "WHERE expires_at IS NULL"
        )
        log.info("Migration 3: added expires_at column to sessions table")

    # Migration 4: Add is_platform_admin column to users.
    if not _column_exists(conn, "users", "is_platform_admin"):
        conn.execute(
            "ALTER TABLE users ADD COLUMN is_platform_admin INTEGER NOT NULL DEFAULT 0"
        )
        # Backfill: existing admins become platform admins.
        conn.execute("UPDATE users SET is_platform_admin = is_admin")
        log.info("Migration 4: added is_platform_admin column to users")

    # Migration 5: Add email column to users.
    if not _column_exists(conn, "users", "email"):
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
        log.info("Migration 5: added email column to users")

    # Migration 6: Add company_id and role columns to invite_codes (employee invite extension).
    if not _column_exists(conn, "invite_codes", "company_id"):
        conn.execute(
            "ALTER TABLE invite_codes ADD COLUMN company_id TEXT REFERENCES companies(id)"
        )
        conn.execute("ALTER TABLE invite_codes ADD COLUMN role TEXT")
        log.info("Migration 6: added company_id and role columns to invite_codes")


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


def generate_password() -> str:
    """Generate a readable 12-char password: XXXX-XXXX-XXXX (SAFE_CHARS)."""
    parts = ["".join(secrets.choice(SAFE_CHARS) for _ in range(4)) for _ in range(3)]
    return "-".join(parts)


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
        "INSERT INTO users "
        "(id, display_name, is_active, is_admin, is_platform_admin, created_at, last_seen_at) "
        "VALUES (?, ?, 1, ?, ?, ?, ?)",
        (user_id, display_name, int(is_admin), int(is_admin), now, now),
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
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=SESSION_LIFETIME_DAYS)
    ).isoformat()
    conn.execute(
        "INSERT INTO sessions "
        "(token, user_id, device_label, created_at, last_seen_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (token, user_id, device_label, now, now, expires_at),
    )
    return token


def validate_session(conn: sqlite3.Connection, token: str) -> dict[str, Any] | None:
    """Look up session → active user.  Returns user dict with app list, or None."""
    now = _now()
    row = conn.execute(
        "SELECT u.id, u.display_name, u.is_active, u.is_admin, u.is_platform_admin "
        "FROM sessions s JOIN users u ON s.user_id = u.id "
        "WHERE s.token = ? AND (s.expires_at IS NULL OR s.expires_at > ?)",
        (token, now),
    ).fetchone()
    if not row:
        return None
    user = dict(row)
    if not user["is_active"]:
        return None
    new_expires_at = (
        datetime.now(timezone.utc) + timedelta(days=SESSION_LIFETIME_DAYS)
    ).isoformat()
    conn.execute(
        "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token = ?",
        (now, new_expires_at, token),
    )
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


def delete_sessions_except(
    conn: sqlite3.Connection,
    user_id: str,
    exclude_token: str,
) -> int:
    """Delete all sessions for user_id except the one matching exclude_token.

    Used when a user changes their own password: their current session
    stays alive so they are not immediately logged out, but all other
    sessions (e.g., on other devices, or stolen sessions) are revoked.
    Returns the count of deleted sessions.
    """
    cur = conn.execute(
        "DELETE FROM sessions WHERE user_id = ? AND token != ?",
        (user_id, exclude_token),
    )
    return cur.rowcount


# ── Companies ─────────────────────────────────────────────────────────────


def _slugify(name: str) -> str:
    """Convert a company name to a URL-safe lowercase slug."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "company"


def create_company(
    conn: sqlite3.Connection,
    *,
    legal_name: str,
    display_name: str,
    timezone: str = "America/Chicago",
    address: str | None = None,
    phone: str | None = None,
    logo_path: str | None = None,
    website: str | None = None,
    created_by: str | None = None,
) -> str:
    """Create a new company record.  Returns the new company id."""
    company_id = str(uuid.uuid4())
    base_slug = _slugify(legal_name)
    slug = base_slug
    suffix = 2
    while conn.execute("SELECT 1 FROM companies WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    conn.execute(
        "INSERT INTO companies "
        "(id, legal_name, display_name, slug, primary_timezone, "
        "address, phone, logo_path, website, created_at, created_by_platform_admin_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            company_id,
            legal_name,
            display_name,
            slug,
            timezone,
            address,
            phone,
            logo_path,
            website,
            _now(),
            created_by,
        ),
    )
    log.info("Company created: id=%s slug=%s", company_id, slug)
    return company_id


def get_company(conn: sqlite3.Connection, company_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    return dict(row) if row else None


def get_company_by_slug(conn: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM companies WHERE slug = ?", (slug,)).fetchone()
    return dict(row) if row else None


def get_all_companies(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM companies ORDER BY legal_name COLLATE NOCASE"
    ).fetchall()
    return [dict(r) for r in rows]


def update_company(conn: sqlite3.Connection, company_id: str, **fields: Any) -> None:
    allowed = {
        "legal_name",
        "display_name",
        "primary_timezone",
        "address",
        "phone",
        "logo_path",
        "website",
        "is_active",
        "paused_at",
        "paused_reason",
        "plan",
        "seat_limit",
        "active_until",
        "settings_json",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    for k in list(updates):
        if isinstance(updates[k], bool):
            updates[k] = int(updates[k])
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [company_id]
    conn.execute(f"UPDATE companies SET {set_clause} WHERE id = ?", values)


# ── Company Users ──────────────────────────────────────────────────────────


def add_company_user(
    conn: sqlite3.Connection,
    user_id: str,
    company_id: str,
    role: str = "pm",
) -> None:
    """Add or re-activate a user in a company with the given role."""
    if role not in COMPANY_ROLES:
        raise ValueError(
            f"Invalid role '{role}'. Must be one of {sorted(COMPANY_ROLES)}"
        )
    conn.execute(
        "INSERT OR REPLACE INTO company_users "
        "(user_id, company_id, role, joined_at, is_active) "
        "VALUES (?, ?, ?, ?, 1)",
        (user_id, company_id, role, _now()),
    )


def get_company_user(
    conn: sqlite3.Connection,
    user_id: str,
    company_id: str,
) -> dict[str, Any] | None:
    """Return the active membership record for a user in a company, or None."""
    row = conn.execute(
        "SELECT * FROM company_users "
        "WHERE user_id = ? AND company_id = ? AND is_active = 1",
        (user_id, company_id),
    ).fetchone()
    return dict(row) if row else None


def get_company_members(
    conn: sqlite3.Connection,
    company_id: str,
) -> list[dict[str, Any]]:
    """Return all active members of a company with their role and display name."""
    rows = conn.execute(
        "SELECT cu.user_id, cu.role, cu.joined_at, u.display_name "
        "FROM company_users cu "
        "JOIN users u ON cu.user_id = u.id "
        "WHERE cu.company_id = ? AND cu.is_active = 1 "
        "ORDER BY u.display_name COLLATE NOCASE",
        (company_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_user_companies(
    conn: sqlite3.Connection,
    user_id: str,
) -> list[dict[str, Any]]:
    """Return all active company memberships for a user, including company details."""
    rows = conn.execute(
        "SELECT c.id, c.legal_name, c.display_name, c.slug, cu.role "
        "FROM company_users cu "
        "JOIN companies c ON cu.company_id = c.id "
        "WHERE cu.user_id = ? AND cu.is_active = 1 AND c.is_active = 1 "
        "ORDER BY c.legal_name COLLATE NOCASE",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


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

    # If this is a company employee invite, add the user to the company.
    company_id = invite.get("company_id")
    role = invite.get("role")
    if company_id and role:
        add_company_user(conn, user_id, company_id, role=role)

    now = _now()
    conn.execute(
        "UPDATE invite_codes SET status = 'claimed', claimed_by = ?, claimed_at = ? "
        "WHERE id = ?",
        (user_id, now, code),
    )

    token = create_session(conn, user_id, device_label)
    return user_id, token


# ── Employee Invites ─────────────────────────────────────────────────────────


def create_employee_invite(
    conn: sqlite3.Connection,
    display_name: str,
    company_id: str,
    role: str,
    app_permissions: list[str],
) -> str:
    """Create an invite code that also adds the claimed user to a company with a role."""
    if role not in COMPANY_ROLES:
        raise ValueError(f"Invalid role '{role}'. Must be one of {sorted(COMPANY_ROLES)}")
    code = generate_invite_code()
    while conn.execute("SELECT 1 FROM invite_codes WHERE id = ?", (code,)).fetchone():
        code = generate_invite_code()
    conn.execute(
        "INSERT INTO invite_codes "
        "(id, display_name, status, app_permissions, grant_admin, created_at, company_id, role) "
        "VALUES (?, ?, 'pending', ?, 0, ?, ?, ?)",
        (code, display_name, json.dumps(app_permissions), _now(), company_id, role),
    )
    return code


# ── Company Signup Invites ────────────────────────────────────────────────────


SIGNUP_INVITE_LIFETIME_DAYS = 30


def create_company_signup_invite(
    conn: sqlite3.Connection,
    proposed_company_name: str,
    admin_email: str,
    created_by: str,
) -> str:
    """Create a company signup invite for a new tenant.  Returns the token."""
    token = secrets.token_urlsafe(32)
    now_dt = datetime.now(timezone.utc)
    expires_at = (now_dt + timedelta(days=SIGNUP_INVITE_LIFETIME_DAYS)).isoformat()
    conn.execute(
        "INSERT INTO company_signup_invites "
        "(token, proposed_company_name, admin_email, created_at, expires_at, "
        "created_by_platform_admin_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (token, proposed_company_name, admin_email, now_dt.isoformat(), expires_at, created_by),
    )
    log.info(
        "Company signup invite created: proposed=%s email=%s by=%s",
        proposed_company_name, admin_email, created_by,
    )
    return token


def get_company_signup_invite(
    conn: sqlite3.Connection,
    token: str,
) -> dict[str, Any] | None:
    """Return a company signup invite by token, or None if not found."""
    row = conn.execute(
        "SELECT * FROM company_signup_invites WHERE token = ?", (token,)
    ).fetchone()
    return dict(row) if row else None


def get_all_company_signup_invites(
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM company_signup_invites ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def claim_company_signup_invite(
    conn: sqlite3.Connection,
    token: str,
    display_name: str,
    password: str,
    legal_name: str,
    company_display_name: str,
    tz: str = "America/Chicago",
    address: str | None = None,
    phone: str | None = None,
    website: str | None = None,
    device_label: str | None = None,
) -> tuple[str, str, str] | None:
    """Claim a company signup invite.

    Creates the company, the first company_admin user, and a session.
    Returns (user_id, company_id, session_token) or None on failure.
    """
    now_dt = datetime.now(timezone.utc)
    invite = get_company_signup_invite(conn, token)
    if not invite:
        return None
    if invite["claimed_at"] is not None:
        return None
    if invite["expires_at"] < now_dt.isoformat():
        return None

    company_id = create_company(
        conn,
        legal_name=legal_name,
        display_name=company_display_name,
        timezone=tz,
        address=address,
        phone=phone,
        website=website,
    )

    user_id = create_user(conn, display_name, is_admin=False)
    set_user_password(conn, user_id, password)

    # Grant SWPPP app access if the app exists.
    if conn.execute("SELECT 1 FROM apps WHERE id = 'swppp'").fetchone():
        grant_app_access(conn, user_id, "swppp")

    add_company_user(conn, user_id, company_id, role="company_admin")

    conn.execute(
        "UPDATE company_signup_invites "
        "SET claimed_at = ?, claimed_by_user_id = ? WHERE token = ?",
        (now_dt.isoformat(), user_id, token),
    )

    log.info(
        "Company signup claimed: company_id=%s user_id=%s",
        company_id, user_id,
    )
    session_token = create_session(conn, user_id, device_label)
    return user_id, company_id, session_token
