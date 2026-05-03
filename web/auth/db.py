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
COMPANY_ROLES = frozenset({"company_admin", "pm"})

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

CREATE TABLE IF NOT EXISTS projects (
    id                          TEXT PRIMARY KEY,
    company_id                  TEXT NOT NULL REFERENCES companies(id),
    project_number              TEXT NOT NULL,
    project_name                TEXT NOT NULL,
    site_address                TEXT NOT NULL,
    timezone                    TEXT NOT NULL DEFAULT 'America/Chicago',
    rain_station_code           TEXT NOT NULL,
    project_start_date          TEXT,
    project_end_date            TEXT,
    re_odot_contact_1           TEXT,
    re_odot_contact_2           TEXT,
    contractor_name             TEXT,
    contract_id                 TEXT,
    notes                       TEXT,
    auto_weekly_enabled         INTEGER NOT NULL DEFAULT 0,
    schedule_day_of_week        INTEGER NOT NULL DEFAULT 5,
    rain_threshold_inches       REAL NOT NULL DEFAULT 0.5,
    notify_on_success           INTEGER NOT NULL DEFAULT 0,
    notify_on_failure           INTEGER NOT NULL DEFAULT 1,
    notification_emails         TEXT,
    template_review_cadence     TEXT NOT NULL DEFAULT 'quarterly',
    auto_pause_on_missed_review INTEGER NOT NULL DEFAULT 0,
    template_promote_mode       TEXT NOT NULL DEFAULT 'auto',
    status                      TEXT NOT NULL DEFAULT 'setup_incomplete',
    active_template_version_id  TEXT,
    paused_until                TEXT,
    last_successful_run_at      TEXT,
    last_run_status             TEXT,
    last_run_at                 TEXT,
    template_last_reviewed_at   TEXT,
    last_preview_generated_at   TEXT,
    archive_zip_path            TEXT,
    archived_at                 TEXT,
    archived_by_user_id         TEXT REFERENCES users(id),
    not_document_path           TEXT,
    not_uploaded_at             TEXT,
    not_uploaded_by             TEXT REFERENCES users(id),
    cloud_sync_status           TEXT,
    created_at                  TEXT NOT NULL,
    created_by_user_id          TEXT NOT NULL REFERENCES users(id),
    UNIQUE(company_id, project_number)
);

CREATE TABLE IF NOT EXISTS project_template_versions (
    id                      TEXT PRIMARY KEY,
    project_id              TEXT NOT NULL REFERENCES projects(id),
    version_number          INTEGER NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'draft',
    template_data           TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    created_by_user_id      TEXT NOT NULL REFERENCES users(id),
    promoted_at             TEXT,
    promoted_by_user_id     TEXT REFERENCES users(id),
    superseded_at           TEXT,
    UNIQUE(project_id, version_number)
);

CREATE TABLE IF NOT EXISTS mailbox_entries (
    id                      TEXT PRIMARY KEY,
    project_id              TEXT NOT NULL REFERENCES projects(id),
    company_id              TEXT NOT NULL REFERENCES companies(id),
    report_date             TEXT NOT NULL,
    report_type             TEXT NOT NULL,
    generation_mode         TEXT NOT NULL DEFAULT 'scheduled',
    file_path               TEXT NOT NULL,
    file_size_bytes         INTEGER,
    template_version_id     TEXT REFERENCES project_template_versions(id),
    rain_data_json          TEXT,
    created_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mailbox_project
    ON mailbox_entries(project_id, report_date DESC);

CREATE TABLE IF NOT EXISTS project_run_log (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id),
    run_date        TEXT NOT NULL,
    status          TEXT NOT NULL,
    error_type      TEXT,
    error_message   TEXT,
    reports_filed   INTEGER NOT NULL DEFAULT 0,
    duration_ms     INTEGER,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_log_project
    ON project_run_log(project_id, run_date DESC);
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
    allowed = {"is_active", "is_admin", "is_platform_admin"}
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
        raise ValueError(
            f"Invalid role '{role}'. Must be one of {sorted(COMPANY_ROLES)}"
        )
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
        (
            token,
            proposed_company_name,
            admin_email,
            now_dt.isoformat(),
            expires_at,
            created_by,
        ),
    )
    log.info(
        "Company signup invite created: proposed=%s email=%s by=%s",
        proposed_company_name,
        admin_email,
        created_by,
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
        company_id,
        user_id,
    )
    session_token = create_session(conn, user_id, device_label)
    return user_id, company_id, session_token


# ── Projects ───────────────────────────────────────────────────────────────


def create_project(
    conn: sqlite3.Connection,
    company_id: str,
    created_by_user_id: str,
    **fields: Any,
) -> str:
    """Create a new project. Returns project id."""
    project_id = str(uuid.uuid4())
    required = {
        "project_number",
        "project_name",
        "site_address",
        "rain_station_code",
    }
    for r in required:
        if r not in fields:
            raise ValueError(f"Missing required field: {r}")

    # Allowed optional fields with their defaults already in schema
    allowed_optional = {
        "timezone",
        "project_start_date",
        "project_end_date",
        "re_odot_contact_1",
        "re_odot_contact_2",
        "contractor_name",
        "contract_id",
        "notes",
    }

    all_fields = {**fields}
    all_fields["id"] = project_id
    all_fields["company_id"] = company_id
    all_fields["created_by_user_id"] = created_by_user_id
    all_fields["created_at"] = _now()

    columns = list(all_fields.keys())
    placeholders = ", ".join("?" * len(columns))
    col_names = ", ".join(columns)
    values = [all_fields[c] for c in columns]

    try:
        conn.execute(
            f"INSERT INTO projects ({col_names}) VALUES ({placeholders})",
            values,
        )
    except sqlite3.IntegrityError as e:
        if "UNIQUE constraint failed" in str(e):
            raise ValueError("Project number already exists in this company") from e
        raise

    log.info(
        "Project created: id=%s company_id=%s number=%s",
        project_id,
        company_id,
        fields["project_number"],
    )
    return project_id


def get_project(conn: sqlite3.Connection, project_id: str) -> dict[str, Any] | None:
    """Get a project by id (no company filter)."""
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return dict(row) if row else None


def get_project_by_number(
    conn: sqlite3.Connection, project_number: str
) -> dict[str, Any] | None:
    """Get a project by project number (for Mailbox lookup — no company filter)."""
    row = conn.execute(
        "SELECT * FROM projects WHERE project_number = ?",
        (project_number,),
    ).fetchone()
    return dict(row) if row else None


def get_company_projects(
    conn: sqlite3.Connection, company_id: str
) -> list[dict[str, Any]]:
    """Get all projects for a company, ordered by created_at DESC."""
    rows = conn.execute(
        "SELECT * FROM projects WHERE company_id = ? ORDER BY created_at DESC",
        (company_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_project_for_company(
    conn: sqlite3.Connection,
    project_id: str,
    company_id: str,
) -> dict[str, Any] | None:
    """Tenant-safe lookup — returns project only if it belongs to company."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ? AND company_id = ?",
        (project_id, company_id),
    ).fetchone()
    return dict(row) if row else None


def update_project(conn: sqlite3.Connection, project_id: str, **fields: Any) -> None:
    """Update a project with allowed fields."""
    allowed = {
        "project_name",
        "site_address",
        "timezone",
        "rain_station_code",
        "project_start_date",
        "project_end_date",
        "re_odot_contact_1",
        "re_odot_contact_2",
        "contractor_name",
        "contract_id",
        "notes",
        "auto_weekly_enabled",
        "schedule_day_of_week",
        "rain_threshold_inches",
        "notify_on_success",
        "notify_on_failure",
        "notification_emails",
        "template_review_cadence",
        "auto_pause_on_missed_review",
        "template_promote_mode",
        "status",
        "active_template_version_id",
        "paused_until",
        "last_successful_run_at",
        "last_run_status",
        "last_run_at",
        "template_last_reviewed_at",
        "last_preview_generated_at",
        "archive_zip_path",
        "archived_at",
        "archived_by_user_id",
        "not_document_path",
        "not_uploaded_at",
        "not_uploaded_by",
        "cloud_sync_status",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return

    # Convert bools to ints for SQLite
    for k in list(updates):
        if isinstance(updates[k], bool):
            updates[k] = int(updates[k])

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [project_id]
    conn.execute(f"UPDATE projects SET {set_clause} WHERE id = ?", values)


# ── Project Template Versions ──────────────────────────────────────────────


def create_template_version(
    conn: sqlite3.Connection,
    project_id: str,
    created_by_user_id: str,
    template_data: dict,
) -> str:
    """Create a new template version for a project.

    Auto-increments version_number. If project's template_promote_mode is 'auto',
    immediately promotes the new version to active. Otherwise leaves as draft.
    Returns version id.
    """
    version_id = str(uuid.uuid4())

    # Get next version number
    max_row = conn.execute(
        "SELECT MAX(version_number) as max_ver FROM project_template_versions WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    next_version = (max_row["max_ver"] or 0) + 1

    # Get project's promote mode
    project = conn.execute(
        "SELECT template_promote_mode FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    if not project:
        raise ValueError(f"Project {project_id} not found")

    promote_mode = project["template_promote_mode"]

    # Insert new version
    conn.execute(
        """INSERT INTO project_template_versions
           (id, project_id, version_number, status, template_data, created_at, created_by_user_id)
           VALUES (?, ?, ?, 'draft', ?, ?, ?)""",
        (
            version_id,
            project_id,
            next_version,
            json.dumps(template_data),
            _now(),
            created_by_user_id,
        ),
    )

    log.info(
        "Template version created: id=%s project_id=%s version=%d mode=%s",
        version_id,
        project_id,
        next_version,
        promote_mode,
    )

    # Auto-promote if configured
    if promote_mode == "auto":
        promote_template_version(conn, version_id, created_by_user_id)

    return version_id


def get_template_version(
    conn: sqlite3.Connection, version_id: str
) -> dict[str, Any] | None:
    """Get a specific template version by id."""
    row = conn.execute(
        "SELECT * FROM project_template_versions WHERE id = ?",
        (version_id,),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["template_data"] = json.loads(result["template_data"])
    return result


def get_template_versions(
    conn: sqlite3.Connection, project_id: str
) -> list[dict[str, Any]]:
    """Get all template versions for a project, ordered by version_number DESC."""
    rows = conn.execute(
        "SELECT * FROM project_template_versions WHERE project_id = ? ORDER BY version_number DESC",
        (project_id,),
    ).fetchall()
    results = []
    for row in rows:
        r = dict(row)
        r["template_data"] = json.loads(r["template_data"])
        results.append(r)
    return results


def get_active_template_version(
    conn: sqlite3.Connection, project_id: str
) -> dict[str, Any] | None:
    """Get the active template version for a project."""
    row = conn.execute(
        "SELECT * FROM project_template_versions WHERE project_id = ? AND status = 'active'",
        (project_id,),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["template_data"] = json.loads(result["template_data"])
    return result


def promote_template_version(
    conn: sqlite3.Connection, version_id: str, promoted_by_user_id: str
) -> None:
    """Promote a template version to active status.

    Supersedes any previously active version, updates projects.active_template_version_id,
    and transitions project status from 'setup_incomplete' to 'active' on first promote.
    """
    # Get the version to promote
    version = conn.execute(
        "SELECT * FROM project_template_versions WHERE id = ?",
        (version_id,),
    ).fetchone()
    if not version:
        raise ValueError(f"Template version {version_id} not found")

    # Check if already active
    if version["status"] == "active":
        raise ValueError(f"Template version {version_id} is already active")

    project_id = version["project_id"]
    now = _now()

    # Supersede any currently active version
    conn.execute(
        """UPDATE project_template_versions
           SET status = 'superseded', superseded_at = ?
           WHERE project_id = ? AND status = 'active'""",
        (now, project_id),
    )

    # Promote this version
    conn.execute(
        """UPDATE project_template_versions
           SET status = 'active', promoted_at = ?, promoted_by_user_id = ?
           WHERE id = ?""",
        (now, promoted_by_user_id, version_id),
    )

    # Update project's active_template_version_id
    conn.execute(
        "UPDATE projects SET active_template_version_id = ? WHERE id = ?",
        (version_id, project_id),
    )

    # If project status is 'setup_incomplete', change to 'active'
    conn.execute(
        """UPDATE projects SET status = 'active'
           WHERE id = ? AND status = 'setup_incomplete'""",
        (project_id,),
    )

    log.info(
        "Template version promoted: version_id=%s project_id=%s",
        version_id,
        project_id,
    )


def archive_template_versions_for_project(
    conn: sqlite3.Connection, project_id: str
) -> None:
    """Archive all template versions for a project.

    Called when a project is archived.
    """
    conn.execute(
        "UPDATE project_template_versions SET status = 'archived' WHERE project_id = ?",
        (project_id,),
    )
    log.info("Template versions archived for project: project_id=%s", project_id)


# ── Mailbox Entries (IR-3) ──────────────────────────────────────────────


def create_mailbox_entry(
    conn: sqlite3.Connection,
    project_id: str,
    company_id: str,
    report_date: str,
    report_type: str,
    file_path: str,
    **kwargs: Any,
) -> str:
    """Create a new mailbox entry.

    Args:
        project_id: Project ID
        company_id: Company ID (for index/query optimization)
        report_date: ISO date string (YYYY-MM-DD)
        report_type: 'auto_weekly', 'auto_rain_event', 'manual_upload'
        file_path: Relative path to PDF file
        **kwargs: Optional fields (generation_mode, file_size_bytes, template_version_id, rain_data_json)

    Returns:
        Mailbox entry ID (UUID)
    """
    entry_id = str(uuid.uuid4())
    now = _now()

    generation_mode = kwargs.get("generation_mode", "scheduled")
    file_size_bytes = kwargs.get("file_size_bytes")
    template_version_id = kwargs.get("template_version_id")
    rain_data_json = kwargs.get("rain_data_json")

    conn.execute(
        """INSERT INTO mailbox_entries
           (id, project_id, company_id, report_date, report_type, generation_mode,
            file_path, file_size_bytes, template_version_id, rain_data_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            entry_id,
            project_id,
            company_id,
            report_date,
            report_type,
            generation_mode,
            file_path,
            file_size_bytes,
            template_version_id,
            rain_data_json,
            now,
        ),
    )

    log.info(
        "Mailbox entry created: entry_id=%s project_id=%s report_date=%s type=%s",
        entry_id,
        project_id,
        report_date,
        report_type,
    )
    return entry_id


def get_mailbox_entry(conn: sqlite3.Connection, entry_id: str) -> dict[str, Any] | None:
    """Get a single mailbox entry by ID."""
    row = conn.execute(
        "SELECT * FROM mailbox_entries WHERE id = ?",
        (entry_id,),
    ).fetchone()
    return dict(row) if row else None


def get_mailbox_entries(
    conn: sqlite3.Connection, project_id: str, sort_order: str = "desc"
) -> list[dict[str, Any]]:
    """Get all mailbox entries for a project.

    Args:
        project_id: Project ID
        sort_order: 'desc' (newest first, default) or 'asc' (oldest first)

    Returns:
        List of mailbox entries sorted by report_date
    """
    if sort_order not in ("desc", "asc"):
        raise ValueError(f"Invalid sort_order '{sort_order}'. Must be 'desc' or 'asc'")

    order_clause = "DESC" if sort_order == "desc" else "ASC"

    rows = conn.execute(
        f"SELECT * FROM mailbox_entries WHERE project_id = ? ORDER BY report_date {order_clause}",
        (project_id,),
    ).fetchall()

    return [dict(row) for row in rows]


def get_mailbox_entry_count(conn: sqlite3.Connection, project_id: str) -> int:
    """Get count of mailbox entries for a project."""
    row = conn.execute(
        "SELECT COUNT(*) as count FROM mailbox_entries WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return row["count"] if row else 0


# ── Scheduler / Project Run Log ──────────────────────────────────────────


def get_projects_due_for_run(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all active projects with auto_weekly_enabled=1 that are not expired and not paused."""
    today = _now()[:10]  # YYYY-MM-DD
    rows = conn.execute(
        """
        SELECT * FROM projects
        WHERE auto_weekly_enabled = 1
          AND status = 'active'
          AND (project_end_date IS NULL OR project_end_date >= ?)
          AND (paused_until IS NULL OR paused_until < ?)
        """,
        (today, today),
    ).fetchall()
    return [dict(row) for row in rows]


def create_project_run_log(
    conn: sqlite3.Connection,
    project_id: str,
    run_date: str,
    status: str,
    *,
    error_type: str | None = None,
    error_message: str | None = None,
    reports_filed: int = 0,
    duration_ms: int | None = None,
) -> str:
    """Insert a project_run_log row and return its UUID."""
    import uuid

    entry_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO project_run_log
            (id, project_id, run_date, status, error_type, error_message,
             reports_filed, duration_ms, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry_id,
            project_id,
            run_date,
            status,
            error_type,
            error_message,
            reports_filed,
            duration_ms,
            _now(),
        ),
    )
    conn.commit()
    return entry_id


def get_project_run_log(
    conn: sqlite3.Connection, project_id: str, limit: int = 30
) -> list[dict[str, Any]]:
    """Return the most recent run log entries for a project, newest first."""
    rows = conn.execute(
        "SELECT * FROM project_run_log WHERE project_id = ? ORDER BY run_date DESC LIMIT ?",
        (project_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def update_project_run_state(
    conn: sqlite3.Connection,
    project_id: str,
    last_run_at: str,
    last_run_status: str,
    last_successful_run_at: str | None = None,
) -> None:
    """Update last_run_at, last_run_status (and optionally last_successful_run_at) on the project row."""
    if last_successful_run_at is not None:
        conn.execute(
            """
            UPDATE projects
               SET last_run_at = ?,
                   last_run_status = ?,
                   last_successful_run_at = ?
             WHERE id = ?
            """,
            (last_run_at, last_run_status, last_successful_run_at, project_id),
        )
    else:
        conn.execute(
            """
            UPDATE projects
               SET last_run_at = ?,
                   last_run_status = ?
             WHERE id = ?
            """,
            (last_run_at, last_run_status, project_id),
        )
    conn.commit()


def get_company_dashboard(conn: sqlite3.Connection, company_id: str) -> dict:
    """Return project health counts and recent failures for a company dashboard."""
    # Count projects by status bucket
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_projects,
            SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,
            SUM(CASE WHEN status = 'paused' THEN 1 ELSE 0 END) AS paused,
            SUM(CASE WHEN status = 'setup_incomplete' THEN 1 ELSE 0 END) AS setup_incomplete,
            SUM(
                CASE
                    WHEN auto_weekly_enabled = 1
                         AND last_run_status IN ('failed', 'partial_failure')
                    THEN 1 ELSE 0
                END
            ) AS failing
          FROM projects
         WHERE company_id = ?
           AND (status IS NULL OR status != 'archived')
        """,
        (company_id,),
    ).fetchone()

    counts = {
        "total_projects": row["total_projects"] or 0,
        "active": row["active"] or 0,
        "failing": row["failing"] or 0,
        "paused": row["paused"] or 0,
        "setup_incomplete": row["setup_incomplete"] or 0,
    }

    # Fetch last 5 failed run log entries for the company
    failures = conn.execute(
        """
        SELECT rl.id, rl.run_date, rl.error_message, rl.error_type,
               p.id AS project_id, p.project_number, p.project_name
          FROM project_run_log rl
          JOIN projects p ON p.id = rl.project_id
         WHERE p.company_id = ?
           AND rl.status IN ('failed', 'partial_failure')
         ORDER BY rl.run_date DESC
         LIMIT 5
        """,
        (company_id,),
    ).fetchall()

    counts["recent_failures"] = [
        {
            "project_id": f["project_id"],
            "project_number": f["project_number"],
            "project_name": f["project_name"],
            "run_date": f["run_date"],
            "error_message": f["error_message"],
        }
        for f in failures
    ]

    return counts


def get_platform_dashboard(conn: sqlite3.Connection) -> dict:
    """Return cross-company health summary for the platform admin dashboard."""
    # ── Metrics ────────────────────────────────────────────────────────────
    metrics = conn.execute("""
        SELECT
            (SELECT COUNT(*) FROM companies WHERE is_active = 1) AS total_companies,
            (SELECT COUNT(*) FROM projects WHERE status != 'archived') AS total_active_projects,
            COALESCE((
                SELECT SUM(reports_filed)
                  FROM project_run_log
                 WHERE date(created_at) >= date('now', '-7 days')
            ), 0) AS reports_filed_7d,
            COALESCE((
                SELECT SUM(reports_filed)
                  FROM project_run_log
                 WHERE date(created_at) >= date('now', '-30 days')
            ), 0) AS reports_filed_30d,
            (SELECT MAX(created_at) FROM project_run_log) AS last_run_at
        """).fetchone()

    result: dict = {
        "total_companies": metrics["total_companies"] or 0,
        "total_active_projects": metrics["total_active_projects"] or 0,
        "reports_filed_7d": metrics["reports_filed_7d"] or 0,
        "reports_filed_30d": metrics["reports_filed_30d"] or 0,
        "last_run_at": metrics["last_run_at"],
    }

    # ── Problem projects (red = failing, yellow = setup_incomplete or stale) ──
    problem_rows = conn.execute("""
        SELECT
            c.display_name AS company_name,
            p.id AS project_id,
            p.project_number,
            p.project_name,
            p.last_successful_run_at,
            p.status,
            p.auto_weekly_enabled,
            p.last_run_status,
            COALESCE((
                SELECT SUM(rl.reports_filed)
                  FROM project_run_log rl
                 WHERE rl.project_id = p.id
                   AND rl.status IN ('failed', 'partial_failure')
                   AND date(rl.created_at) >= date('now', '-7 days')
            ), 0) AS failure_count_7d,
            CASE
                WHEN p.auto_weekly_enabled = 1
                     AND p.last_run_status IN ('failed', 'partial_failure')
                THEN 'red'
                ELSE 'yellow'
            END AS health_flag
          FROM projects p
          JOIN companies c ON c.id = p.company_id
         WHERE p.status != 'archived'
           AND (
               -- red: auto-weekly failing
               (p.auto_weekly_enabled = 1
                AND p.last_run_status IN ('failed', 'partial_failure'))
               OR
               -- yellow: setup incomplete
               p.status = 'setup_incomplete'
               OR
               -- yellow: active+auto but stale >8 days
               (p.auto_weekly_enabled = 1
                AND p.status = 'active'
                AND (p.last_successful_run_at IS NULL
                     OR julianday('now') - julianday(p.last_successful_run_at) > 8))
           )
         ORDER BY
             CASE WHEN p.auto_weekly_enabled = 1
                       AND p.last_run_status IN ('failed', 'partial_failure')
                  THEN 0 ELSE 1 END,
             c.display_name COLLATE NOCASE
        """).fetchall()

    result["problem_projects"] = [dict(r) for r in problem_rows]

    # ── Company rollup ─────────────────────────────────────────────────────
    rollup_rows = conn.execute("""
        SELECT
            c.id,
            c.display_name,
            COUNT(p.id) AS total_projects,
            SUM(CASE WHEN p.status = 'active' THEN 1 ELSE 0 END) AS active,
            SUM(CASE WHEN p.auto_weekly_enabled = 1
                          AND p.last_run_status IN ('failed', 'partial_failure')
                     THEN 1 ELSE 0 END) AS failing,
            SUM(CASE WHEN p.status = 'paused' THEN 1 ELSE 0 END) AS paused,
            SUM(CASE WHEN p.status = 'setup_incomplete' THEN 1 ELSE 0 END) AS setup_incomplete,
            MAX(p.last_run_at) AS last_activity,
            (SELECT u.display_name
               FROM company_users cu
               JOIN users u ON u.id = cu.user_id
              WHERE cu.company_id = c.id
                AND cu.role = 'company_admin'
              ORDER BY cu.joined_at ASC
              LIMIT 1) AS admin_name
          FROM companies c
          LEFT JOIN projects p
            ON p.company_id = c.id AND p.status != 'archived'
         WHERE c.is_active = 1
         GROUP BY c.id, c.display_name
         ORDER BY failing DESC, c.display_name COLLATE NOCASE
        """).fetchall()

    result["company_rollup"] = [dict(r) for r in rollup_rows]

    return result


# ── Archive Flow (IR-7) ──────────────────────────────────────────────────


def archive_project(
    conn: sqlite3.Connection,
    project_id: str,
    user_id: str,
    not_document_path: str | None = None,
) -> None:
    """Set project status to 'archived', disable auto-weekly, record audit fields.

    Also archives all template versions for the project.
    """
    now = _now()
    conn.execute(
        """UPDATE projects SET
               status = 'archived',
               auto_weekly_enabled = 0,
               archived_at = ?,
               archived_by_user_id = ?,
               not_document_path = ?
           WHERE id = ?""",
        (now, user_id, not_document_path, project_id),
    )
    archive_template_versions_for_project(conn, project_id)
    log.info("Project archived: project_id=%s by_user=%s", project_id, user_id)


def unarchive_project(conn: sqlite3.Connection, project_id: str) -> None:
    """Restore an archived project to 'active' status.

    Clears all archive tracking fields.  Does NOT re-enable auto_weekly —
    the PM must explicitly turn that back on.
    """
    conn.execute(
        """UPDATE projects SET
               status = 'active',
               archived_at = NULL,
               archived_by_user_id = NULL,
               archive_zip_path = NULL
           WHERE id = ?""",
        (project_id,),
    )
    log.info("Project unarchived: project_id=%s", project_id)


def set_archive_zip_path(
    conn: sqlite3.Connection, project_id: str, zip_path: str
) -> None:
    """Record the path of the completed archive ZIP file."""
    conn.execute(
        "UPDATE projects SET archive_zip_path = ? WHERE id = ?",
        (zip_path, project_id),
    )
    log.info("Archive ZIP path set: project_id=%s path=%s", project_id, zip_path)


def add_not_document(
    conn: sqlite3.Connection,
    project_id: str,
    user_id: str,
    not_path: str,
) -> None:
    """Record a newly-uploaded Notice of Termination document.

    Clears archive_zip_path so the BackgroundTask knows to regenerate the ZIP.
    """
    now = _now()
    conn.execute(
        """UPDATE projects SET
               not_document_path = ?,
               not_uploaded_at = ?,
               not_uploaded_by = ?,
               archive_zip_path = NULL
           WHERE id = ?""",
        (not_path, now, user_id, project_id),
    )
    log.info(
        "NOT document added: project_id=%s path=%s by=%s", project_id, not_path, user_id
    )
