#!/usr/bin/env python3
"""Bootstrap script — creates DB, seeds SWPPP app, generates the first admin invite."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is on sys.path so `web.*` imports work.
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

from web.auth.db import DB_PATH, connect, create_invite, init_db, seed_app  # noqa: E402


def main() -> None:
    print(f"Initializing database at {DB_PATH}")
    init_db()

    with connect() as conn:
        # Seed the SWPPP app (idempotent)
        seed_app(
            conn,
            "swppp",
            "SWPPP AutoFill",
            "Generate ODOT stormwater inspection PDFs",
            "/swppp",
        )

        # Skip if an admin user already exists
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE is_admin = 1"
        ).fetchone()
        if row["c"] > 0:
            print("Admin user already exists — skipping invite generation.")
            return

        # Generate admin bootstrap invite
        code = create_invite(conn, "Admin", ["swppp"], grant_admin=True)

    base_url = "http://localhost:8001"
    print()
    print("=" * 54)
    print("  ADMIN INVITE CODE GENERATED")
    print("=" * 54)
    print(f"  Code : {code}")
    print(f"  Link : {base_url}/auth/login?code={code}")
    print()
    print("  Claim this code to create the first admin user.")
    print("=" * 54)


if __name__ == "__main__":
    main()
