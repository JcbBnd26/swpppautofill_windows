"""Tenant isolation tests — IR #1: Foundation.

Verifies that the multi-tenant schema is correct and that company data
cannot bleed between tenants at the database layer.  These tests expand
with every subsequent Phase 2 IR as new company-scoped surfaces are added.
"""

from __future__ import annotations

import itertools
import os
import tempfile

# Set data dir before any web.auth imports so the DB path is correct.
# If test_auth.py was imported first (alphabetically it is), the env var
# is already set and web.auth.db is cached — we just reuse the same test DB.
if "TOOLS_DATA_DIR" not in os.environ:
    os.environ["TOOLS_DATA_DIR"] = tempfile.mkdtemp()
os.environ.setdefault("TOOLS_DEV_MODE", "1")

from web.auth import db  # noqa: E402

# Use a high counter offset to avoid display_name collisions with test_auth.py
_seq = itertools.count(9000)


def _u(prefix: str = "U") -> str:
    """Return a unique display name."""
    return f"{prefix}{next(_seq)}"


def _co(prefix: str = "Co") -> str:
    """Return a unique company legal name."""
    return f"{prefix} {next(_seq)} LLC"


# ── Schema ────────────────────────────────────────────────────────────────


class TestSchema:
    def test_companies_table_exists(self):
        db.init_db()
        with db.connect() as conn:
            tables = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "companies" in tables
        assert "company_users" in tables

    def test_users_has_is_platform_admin_column(self):
        db.init_db()
        with db.connect() as conn:
            assert db._column_exists(conn, "users", "is_platform_admin")

    def test_companies_schema_columns(self):
        db.init_db()
        with db.connect() as conn:
            cols = {
                r[1] for r in conn.execute("PRAGMA table_info(companies)").fetchall()
            }
        required = {
            "id",
            "legal_name",
            "display_name",
            "slug",
            "primary_timezone",
            "is_active",
            "created_at",
            "plan",
            "seat_limit",
            "active_until",
            "created_by_platform_admin_id",
        }
        assert required <= cols

    def test_company_users_schema_columns(self):
        db.init_db()
        with db.connect() as conn:
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(company_users)").fetchall()
            }
        assert {"user_id", "company_id", "role", "joined_at", "is_active"} <= cols


# ── Company CRUD ──────────────────────────────────────────────────────────


class TestCompanyCRUD:
    def test_create_and_get_company(self):
        db.init_db()
        name = _co()
        with db.connect() as conn:
            cid = db.create_company(
                conn, legal_name=name, display_name=name, timezone="America/Chicago"
            )
            company = db.get_company(conn, cid)
        assert company is not None
        assert company["legal_name"] == name
        assert company["slug"]  # non-empty slug generated

    def test_slug_generated_lowercase_no_spaces(self):
        db.init_db()
        name = _co("Big Build")
        with db.connect() as conn:
            cid = db.create_company(
                conn, legal_name=name, display_name=name, timezone="America/Chicago"
            )
            company = db.get_company(conn, cid)
        assert " " not in company["slug"]
        assert company["slug"] == company["slug"].lower()

    def test_slug_unique_on_name_collision(self):
        db.init_db()
        name = _co("Dup")
        with db.connect() as conn:
            id1 = db.create_company(
                conn, legal_name=name, display_name=name, timezone="America/Chicago"
            )
            id2 = db.create_company(
                conn, legal_name=name, display_name=name, timezone="America/Chicago"
            )
            c1 = db.get_company(conn, id1)
            c2 = db.get_company(conn, id2)
        assert c1["slug"] != c2["slug"]

    def test_get_company_by_slug(self):
        db.init_db()
        name = _co("Slug")
        with db.connect() as conn:
            cid = db.create_company(
                conn, legal_name=name, display_name=name, timezone="America/Chicago"
            )
            company = db.get_company(conn, cid)
            found = db.get_company_by_slug(conn, company["slug"])
        assert found is not None
        assert found["id"] == cid

    def test_get_all_companies_returns_both(self):
        db.init_db()
        name_a, name_b = _co("CompA"), _co("CompB")
        with db.connect() as conn:
            id_a = db.create_company(
                conn, legal_name=name_a, display_name=name_a, timezone="America/Chicago"
            )
            id_b = db.create_company(
                conn, legal_name=name_b, display_name=name_b, timezone="America/Chicago"
            )
            all_companies = db.get_all_companies(conn)
        ids = {c["id"] for c in all_companies}
        assert id_a in ids
        assert id_b in ids

    def test_get_company_returns_none_for_missing(self):
        db.init_db()
        with db.connect() as conn:
            result = db.get_company(conn, "nonexistent-id-xxxx")
        assert result is None

    def test_get_company_by_slug_returns_none_for_missing(self):
        db.init_db()
        with db.connect() as conn:
            result = db.get_company_by_slug(conn, "no-such-slug-ever")
        assert result is None


# ── Company Membership ────────────────────────────────────────────────────


class TestCompanyMembership:
    def test_add_and_get_company_user(self):
        db.init_db()
        with db.connect() as conn:
            cid = db.create_company(
                conn, legal_name=_co(), display_name=_co(), timezone="America/Chicago"
            )
            uid = db.create_user(conn, _u())
            db.add_company_user(conn, user_id=uid, company_id=cid, role="pm")
            member = db.get_company_user(conn, user_id=uid, company_id=cid)
        assert member is not None
        assert member["role"] == "pm"
        assert member["is_active"] == 1

    def test_all_three_valid_roles_accepted(self):
        db.init_db()
        with db.connect() as conn:
            for role in ("company_admin", "pm", "viewer"):
                cid = db.create_company(
                    conn,
                    legal_name=_co(),
                    display_name=_co(),
                    timezone="America/Chicago",
                )
                uid = db.create_user(conn, _u())
                db.add_company_user(conn, uid, cid, role=role)
                m = db.get_company_user(conn, uid, cid)
                assert m["role"] == role

    def test_invalid_role_raises(self):
        db.init_db()
        import pytest

        with db.connect() as conn:
            cid = db.create_company(
                conn, legal_name=_co(), display_name=_co(), timezone="America/Chicago"
            )
            uid = db.create_user(conn, _u())
            with pytest.raises(ValueError, match="Invalid role"):
                db.add_company_user(conn, uid, cid, role="superuser")

    def test_get_company_members_returns_only_that_company(self):
        db.init_db()
        with db.connect() as conn:
            cid_a = db.create_company(
                conn,
                legal_name=_co("A"),
                display_name=_co("A"),
                timezone="America/Chicago",
            )
            cid_b = db.create_company(
                conn,
                legal_name=_co("B"),
                display_name=_co("B"),
                timezone="America/Chicago",
            )
            uid_a = db.create_user(conn, _u("UserA"))
            uid_b = db.create_user(conn, _u("UserB"))
            db.add_company_user(conn, uid_a, cid_a, role="pm")
            db.add_company_user(conn, uid_b, cid_b, role="pm")
            members_a = db.get_company_members(conn, cid_a)
            members_b = db.get_company_members(conn, cid_b)
        ids_a = {m["user_id"] for m in members_a}
        ids_b = {m["user_id"] for m in members_b}
        # Each company sees only its own member
        assert uid_a in ids_a
        assert uid_b not in ids_a
        assert uid_b in ids_b
        assert uid_a not in ids_b

    def test_get_user_companies_returns_memberships(self):
        db.init_db()
        with db.connect() as conn:
            cid_a = db.create_company(
                conn, legal_name=_co(), display_name=_co(), timezone="America/Chicago"
            )
            cid_b = db.create_company(
                conn, legal_name=_co(), display_name=_co(), timezone="America/Chicago"
            )
            uid = db.create_user(conn, _u())
            db.add_company_user(conn, uid, cid_a, role="pm")
            db.add_company_user(conn, uid, cid_b, role="viewer")
            memberships = db.get_user_companies(conn, uid)
        company_ids = {m["id"] for m in memberships}
        assert cid_a in company_ids
        assert cid_b in company_ids


# ── Tenant Bleed: Cross-Company Isolation ─────────────────────────────────


class TestTenantBleed:
    def test_user_in_company_a_has_no_membership_in_company_b(self):
        """A user added to Company A has no membership record in Company B."""
        db.init_db()
        with db.connect() as conn:
            cid_a = db.create_company(
                conn,
                legal_name=_co("TA"),
                display_name=_co("TA"),
                timezone="America/Chicago",
            )
            cid_b = db.create_company(
                conn,
                legal_name=_co("TB"),
                display_name=_co("TB"),
                timezone="America/Chicago",
            )
            uid = db.create_user(conn, _u("CrossUser"))
            db.add_company_user(conn, uid, cid_a, role="pm")
            # Membership in A exists
            assert db.get_company_user(conn, uid, cid_a) is not None
            # Membership in B must NOT exist
            assert db.get_company_user(conn, uid, cid_b) is None

    def test_company_members_excludes_inactive_records(self):
        """Deactivated company_users rows are excluded from get_company_members."""
        db.init_db()
        with db.connect() as conn:
            cid = db.create_company(
                conn, legal_name=_co(), display_name=_co(), timezone="America/Chicago"
            )
            uid = db.create_user(conn, _u("InactiveUser"))
            db.add_company_user(conn, uid, cid, role="pm")
            # Deactivate membership directly
            conn.execute(
                "UPDATE company_users SET is_active = 0 "
                "WHERE user_id = ? AND company_id = ?",
                (uid, cid),
            )
            members = db.get_company_members(conn, cid)
        assert all(m["user_id"] != uid for m in members)

    def test_two_companies_same_slug_prefix_get_distinct_slugs(self):
        """Companies with identical names receive distinct, collision-safe slugs."""
        db.init_db()
        name = _co("SameSlug")
        with db.connect() as conn:
            id1 = db.create_company(
                conn, legal_name=name, display_name=name, timezone="America/Chicago"
            )
            id2 = db.create_company(
                conn, legal_name=name, display_name=name, timezone="America/Chicago"
            )
            c1 = db.get_company(conn, id1)
            c2 = db.get_company(conn, id2)
        assert c1["slug"] != c2["slug"]
        # Both slugs must start with the same base (derived from the name)
        assert c2["slug"].startswith(c1["slug"])


# ── is_platform_admin Propagation ─────────────────────────────────────────


class TestPlatformAdminFlag:
    def test_admin_user_gets_is_platform_admin(self):
        """Users created with is_admin=True also get is_platform_admin=True."""
        db.init_db()
        with db.connect() as conn:
            uid = db.create_user(conn, _u("PlatAdmin"), is_admin=True)
            user = db.get_user(conn, uid)
        assert user["is_platform_admin"] == 1

    def test_regular_user_has_no_platform_admin(self):
        db.init_db()
        with db.connect() as conn:
            uid = db.create_user(conn, _u("RegUser"))
            user = db.get_user(conn, uid)
        assert user["is_platform_admin"] == 0

    def test_validate_session_includes_is_platform_admin(self):
        """validate_session returns a dict with is_platform_admin for admin users."""
        db.init_db()
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            uid = db.create_user(conn, _u("SessAdmin"), is_admin=True)
            token = db.create_session(conn, uid)
        with db.connect() as conn:
            user = db.validate_session(conn, token)
        assert user is not None
        assert "is_platform_admin" in user
        assert user["is_platform_admin"] == 1

    def test_validate_session_regular_user_platform_admin_false(self):
        db.init_db()
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            uid = db.create_user(conn, _u("RegSessUser"), is_admin=False)
            token = db.create_session(conn, uid)
        with db.connect() as conn:
            user = db.validate_session(conn, token)
        assert user is not None
        assert user["is_platform_admin"] == 0
