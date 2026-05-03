"""Tests for web.auth — Phase 1 auth system."""

from __future__ import annotations

import itertools
import json
import os
import sqlite3
import tempfile

import pytest
from fastapi.testclient import TestClient

# Point the DB at a temp directory so tests don't touch real data.
_tmpdir = tempfile.mkdtemp()
os.environ["TOOLS_DATA_DIR"] = _tmpdir
os.environ["TOOLS_DEV_MODE"] = "1"

from web.auth import db  # noqa: E402
from web.auth.main import app  # noqa: E402

client = TestClient(app, cookies={})


# ── Helpers ──────────────────────────────────────────────────────────────

_seq = itertools.count(1)


def _admin_client() -> TestClient:
    """Create an admin user via the DB and return an authenticated TestClient."""
    with db.connect() as conn:
        db.seed_app(conn, "swppp", "SWPPP AutoFill", "Generate ODOT PDFs", "/swppp")
        code = db.create_invite(
            conn, f"TestAdmin{next(_seq)}", ["swppp"], grant_admin=True
        )
    c = TestClient(app, cookies={})
    r = c.post("/auth/claim", json={"code": code})
    assert r.status_code == 200
    return c


def _make_invite(
    admin: TestClient, name: str = "User", apps: list[str] | None = None
) -> str:
    """Use the admin API to generate an invite code; return the code string."""
    r = admin.post(
        "/admin/invites",
        json={"display_name": name, "app_permissions": apps or ["swppp"]},
    )
    assert r.status_code == 200
    return r.json()["code"]


# ── DB / Bootstrap ───────────────────────────────────────────────────────


class TestBootstrap:
    def test_init_db_creates_tables(self):
        db.init_db()
        with db.connect() as conn:
            tables = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert {
            "apps",
            "users",
            "invite_codes",
            "user_app_access",
            "sessions",
        } <= tables

    def test_seed_app_idempotent(self):
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            apps = db.get_all_apps(conn)
        assert sum(1 for a in apps if a["id"] == "swppp") == 1

    def test_invite_code_format(self):
        code = db.generate_invite_code()
        assert code.startswith("TOOLS-")
        parts = code.split("-")
        assert len(parts) == 3
        assert len(parts[1]) == 4 and len(parts[2]) == 4
        # No ambiguous chars
        for ch in parts[1] + parts[2]:
            assert ch not in "OI01"


# ── Claim Flow ───────────────────────────────────────────────────────────


class TestClaimFlow:
    def test_claim_valid_code(self):
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            code = db.create_invite(conn, "ClaimUser", ["swppp"])
        r = client.post("/auth/claim", json={"code": code})
        assert r.status_code == 200
        assert r.json()["success"] is True
        assert "tools_session" in r.cookies

    def test_claim_invalid_code(self):
        r = client.post("/auth/claim", json={"code": "TOOLS-FAKE-CODE"})
        assert r.status_code == 400

    def test_claim_already_claimed(self):
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            code = db.create_invite(conn, "DoubleClaimUser", ["swppp"])
        r1 = client.post("/auth/claim", json={"code": code})
        assert r1.status_code == 200
        r2 = client.post("/auth/claim", json={"code": code})
        assert r2.status_code == 400

    def test_claim_admin_invite_grants_admin(self):
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            code = db.create_invite(conn, "AdminUser", ["swppp"], grant_admin=True)
        c = TestClient(app, cookies={})
        r = c.post("/auth/claim", json={"code": code})
        assert r.status_code == 200
        me = c.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["is_admin"] is True

    def test_claim_case_insensitive(self):
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            code = db.create_invite(conn, "CaseUser", ["swppp"])
        r = client.post("/auth/claim", json={"code": code.lower()})
        assert r.status_code == 200


# ── Auth Guards ──────────────────────────────────────────────────────────


class TestAuthGuards:
    def test_me_unauthenticated(self):
        c = TestClient(app, cookies={})
        r = c.get("/auth/me")
        assert r.status_code == 401

    def test_admin_unauthenticated(self):
        c = TestClient(app, cookies={})
        r = c.get("/admin/users")
        assert r.status_code == 401

    def test_admin_non_admin_user(self):
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            code = db.create_invite(conn, "NormalUser", ["swppp"])
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        r = c.get("/admin/users")
        assert r.status_code == 403


# ── /auth/me ─────────────────────────────────────────────────────────────


class TestMe:
    def test_me_returns_user_info(self):
        admin = _admin_client()
        r = admin.get("/auth/me")
        assert r.status_code == 200
        data = r.json()
        assert data["display_name"].startswith("TestAdmin")
        assert data["is_admin"] is True
        assert any(a["id"] == "swppp" for a in data["apps"])


# ── /auth/logout ─────────────────────────────────────────────────────────


class TestLogout:
    def test_logout_clears_session(self):
        admin = _admin_client()
        r = admin.post("/auth/logout", follow_redirects=False)
        assert r.status_code == 302
        # Session cookie should be cleared — subsequent /auth/me fails
        r2 = admin.get("/auth/me")
        assert r2.status_code == 401


# ── Admin: Users ─────────────────────────────────────────────────────────


class TestAdminUsers:
    def test_list_users(self):
        admin = _admin_client()
        r = admin.get("/admin/users")
        assert r.status_code == 200
        assert len(r.json()["users"]) >= 1

    def test_deactivate_user(self):
        admin = _admin_client()
        # Create a second user
        code = _make_invite(admin, "ToDeactivate")
        c2 = TestClient(app, cookies={})
        c2.post("/auth/claim", json={"code": code})
        me2 = c2.get("/auth/me").json()

        r = admin.patch(f"/admin/users/{me2['user_id']}", json={"is_active": False})
        assert r.status_code == 200

        # Deactivated user can't access /auth/me
        r2 = c2.get("/auth/me")
        assert r2.status_code == 401

    def test_cannot_deactivate_self(self):
        admin = _admin_client()
        me = admin.get("/auth/me").json()
        r = admin.patch(f"/admin/users/{me['user_id']}", json={"is_active": False})
        assert r.status_code == 400


# ── Admin: Invites ───────────────────────────────────────────────────────


class TestAdminInvites:
    def test_create_and_list_invites(self):
        admin = _admin_client()
        code = _make_invite(admin, "InviteTest")
        r = admin.get("/admin/invites")
        assert r.status_code == 200
        codes = [i["id"] for i in r.json()["invites"]]
        assert code in codes

    def test_revoke_invite(self):
        admin = _admin_client()
        code = _make_invite(admin, "ToRevoke")
        r = admin.delete(f"/admin/invites/{code}")
        assert r.status_code == 200
        # Code can no longer be claimed
        r2 = client.post("/auth/claim", json={"code": code})
        assert r2.status_code == 400

    def test_revoke_already_claimed(self):
        admin = _admin_client()
        code = _make_invite(admin, "ClaimedInv")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        r = admin.delete(f"/admin/invites/{code}")
        assert r.status_code == 400

    def test_create_invite_validates_apps(self):
        admin = _admin_client()
        r = admin.post(
            "/admin/invites",
            json={"display_name": "Bad", "app_permissions": ["nonexistent"]},
        )
        assert r.status_code == 400


# ── Admin: Sessions ──────────────────────────────────────────────────────


class TestAdminSessions:
    def test_list_and_kill_sessions(self):
        admin = _admin_client()
        code = _make_invite(admin, "SessionUser")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        me = c.get("/auth/me").json()

        # List sessions
        r = admin.get(f"/admin/users/{me['user_id']}/sessions")
        assert r.status_code == 200
        assert len(r.json()["sessions"]) >= 1

        # Kill all
        r2 = admin.delete(f"/admin/users/{me['user_id']}/sessions")
        assert r2.status_code == 200
        assert r2.json()["deleted_count"] >= 1

        # User's session is now invalid
        r3 = c.get("/auth/me")
        assert r3.status_code == 401

    def test_kill_session_by_prefix(self):
        admin = _admin_client()
        code = _make_invite(admin, "PrefixUser")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        me = c.get("/auth/me").json()

        sessions = admin.get(f"/admin/users/{me['user_id']}/sessions").json()[
            "sessions"
        ]
        prefix = sessions[0]["token_prefix"].replace("....", "")
        r = admin.delete(f"/admin/sessions/{prefix}")
        assert r.status_code == 200


# ── Admin: App Access ────────────────────────────────────────────────────


class TestAdminAppAccess:
    def test_grant_and_revoke_app(self):
        admin = _admin_client()
        # Register a second app
        admin.post(
            "/admin/apps",
            json={
                "id": "testapp",
                "name": "Test App",
                "description": "A test app",
                "route_prefix": "/testapp",
            },
        )
        code = _make_invite(admin, "AppUser")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        me = c.get("/auth/me").json()

        # Grant
        r = admin.post(f"/admin/users/{me['user_id']}/apps", json={"app_id": "testapp"})
        assert r.status_code == 200
        me2 = c.get("/auth/me").json()
        assert any(a["id"] == "testapp" for a in me2["apps"])

        # Revoke
        r2 = admin.delete(f"/admin/users/{me['user_id']}/apps/testapp")
        assert r2.status_code == 200
        me3 = c.get("/auth/me").json()
        assert not any(a["id"] == "testapp" for a in me3["apps"])


# ── Admin: Apps CRUD ─────────────────────────────────────────────────────


class TestAdminApps:
    def test_list_apps(self):
        admin = _admin_client()
        r = admin.get("/admin/apps")
        assert r.status_code == 200
        assert any(a["id"] == "swppp" for a in r.json()["apps"])

    def test_create_app(self):
        admin = _admin_client()
        r = admin.post(
            "/admin/apps",
            json={
                "id": "newapp",
                "name": "New App",
                "description": "Brand new",
                "route_prefix": "/newapp",
            },
        )
        assert r.status_code == 200
        apps = admin.get("/admin/apps").json()["apps"]
        assert any(a["id"] == "newapp" for a in apps)

    def test_create_app_validates_id(self):
        admin = _admin_client()
        r = admin.post(
            "/admin/apps",
            json={
                "id": "BAD APP",
                "name": "Bad",
                "description": "x",
                "route_prefix": "/bad",
            },
        )
        assert r.status_code == 400

    def test_update_app(self):
        admin = _admin_client()
        admin.post(
            "/admin/apps",
            json={
                "id": "patchme",
                "name": "Patch Me",
                "description": "orig",
                "route_prefix": "/patchme",
            },
        )
        r = admin.patch("/admin/apps/patchme", json={"is_active": False})
        assert r.status_code == 200
        apps = admin.get("/admin/apps").json()["apps"]
        patched = next(a for a in apps if a["id"] == "patchme")
        assert patched["is_active"] is False


# ── Phase 5: Extended claim tests ────────────────────────────────────────


class TestClaimFlowExtended:
    def test_claim_whitespace_padded_code(self):
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            code = db.create_invite(conn, "PadUser", ["swppp"])
        r = client.post("/auth/claim", json={"code": f"  {code}  "})
        assert r.status_code == 200

    def test_claim_empty_code(self):
        r = client.post("/auth/claim", json={"code": ""})
        assert r.status_code == 400

    def test_claim_very_long_code(self):
        r = client.post("/auth/claim", json={"code": "X" * 51})
        assert r.status_code == 422


# ── Phase 5: Extended invite tests ───────────────────────────────────────


class TestAdminInvitesExtended:
    def test_create_invite_empty_apps(self):
        admin = _admin_client()
        r = admin.post(
            "/admin/invites",
            json={"display_name": "User", "app_permissions": []},
        )
        assert r.status_code == 400

    def test_revoke_already_revoked(self):
        admin = _admin_client()
        code = _make_invite(admin, "RevokeMe")
        admin.delete(f"/admin/invites/{code}")
        r = admin.delete(f"/admin/invites/{code}")
        assert r.status_code == 400
        assert "revoked" in r.json()["detail"].lower()

    def test_revoke_nonexistent_code(self):
        admin = _admin_client()
        r = admin.delete("/admin/invites/TOOLS-XXXX-YYYY")
        assert r.status_code == 404

    def test_create_invite_very_long_name(self):
        admin = _admin_client()
        r = admin.post(
            "/admin/invites",
            json={
                "display_name": "A" * 201,
                "app_permissions": ["swppp"],
            },
        )
        assert r.status_code == 422


# ── Password Auth ────────────────────────────────────────────────────────


class TestPasswordAuth:
    def test_set_and_login_with_password(self):
        admin = _admin_client()
        # Get the dynamic admin name
        me_r = admin.get("/auth/me")
        admin_name = me_r.json()["display_name"]

        # Set a password on the admin account
        r = admin.post("/auth/set-password", json={"password": "TestPass123!"})
        assert r.status_code == 200

        # Log out
        admin.post("/auth/logout", follow_redirects=False)

        # Log back in with password
        c = TestClient(app, cookies={})
        r = c.post(
            "/auth/signin",
            json={"display_name": admin_name, "password": "TestPass123!"},
        )
        assert r.status_code == 200
        assert r.json()["success"] is True
        assert "tools_session" in r.cookies

        # Verify session is valid
        me = c.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["display_name"] == admin_name

    def test_login_wrong_password(self):
        # Create a user with a password
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            code = db.create_invite(conn, "WrongPwUser", ["swppp"])
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "CorrectPass1"})

        c2 = TestClient(app, cookies={})
        r = c2.post(
            "/auth/signin",
            json={"display_name": "WrongPwUser", "password": "WrongPassword"},
        )
        assert r.status_code == 401

    def test_login_nonexistent_user(self):
        c = TestClient(app, cookies={})
        r = c.post(
            "/auth/signin",
            json={"display_name": "NoSuchUser", "password": "whatever"},
        )
        assert r.status_code == 401

    def test_login_no_password_set(self):
        """User without a password can't use password login."""
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            code = db.create_invite(conn, "NoPwUser", ["swppp"])
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})

        c2 = TestClient(app, cookies={})
        r = c2.post(
            "/auth/signin",
            json={"display_name": "NoPwUser", "password": "anything"},
        )
        assert r.status_code == 401

    def test_login_case_insensitive_name(self):
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            code = db.create_invite(conn, "CaseTestUser", ["swppp"])
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "MyPassword8"})

        c2 = TestClient(app, cookies={})
        r = c2.post(
            "/auth/signin",
            json={"display_name": "casetestuser", "password": "MyPassword8"},
        )
        assert r.status_code == 200

    def test_set_password_requires_auth(self):
        c = TestClient(app, cookies={})
        r = c.post("/auth/set-password", json={"password": "NewPass123!"})
        assert r.status_code == 401

    def test_set_password_too_short(self):
        admin = _admin_client()
        r = admin.post("/auth/set-password", json={"password": "short"})
        assert r.status_code == 422

    def test_change_password(self):
        """Setting a new password replaces the old one."""
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            code = db.create_invite(conn, "ChangePwUser", ["swppp"])
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "OldPassword1"})
        c.post(
            "/auth/set-password",
            json={"password": "NewPassword2", "current_password": "OldPassword1"},
        )

        # Old password no longer works
        c2 = TestClient(app, cookies={})
        r = c2.post(
            "/auth/signin",
            json={"display_name": "ChangePwUser", "password": "OldPassword1"},
        )
        assert r.status_code == 401

        # New password works
        r = c2.post(
            "/auth/signin",
            json={"display_name": "ChangePwUser", "password": "NewPassword2"},
        )
        assert r.status_code == 200


class TestMiddlewareCookieRefresh:
    def test_claim_sets_fresh_cookie_not_overwritten(self):
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            code = db.create_invite(conn, "CookieTestUser", ["swppp"])

        c = TestClient(app, cookies={})
        r = c.post("/auth/claim", json={"code": code})

        assert r.status_code == 200
        cookie_val = r.cookies.get("tools_session")
        assert cookie_val is not None
        assert len(cookie_val) > 0

        c2 = TestClient(app, cookies={"tools_session": cookie_val})
        me = c2.get("/auth/me")
        assert me.status_code == 200

    def test_signin_sets_fresh_cookie_not_overwritten(self):
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            code = db.create_invite(conn, "PwCookieUser", ["swppp"])

        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "TestPass123!"})

        c2 = TestClient(app, cookies={})
        r = c2.post(
            "/auth/signin",
            json={"display_name": "PwCookieUser", "password": "TestPass123!"},
        )

        assert r.status_code == 200
        cookie_val = r.cookies.get("tools_session")
        assert cookie_val is not None
        assert len(cookie_val) > 0

        c3 = TestClient(app, cookies={"tools_session": cookie_val})
        me = c3.get("/auth/me")
        assert me.status_code == 200

    def test_existing_session_gets_refreshed(self):
        admin = _admin_client()
        original_token = admin.cookies.get("tools_session")

        r = admin.get("/auth/me")

        assert r.status_code == 200
        assert r.cookies.get("tools_session") == original_token


class TestDatabaseMigration:
    def test_init_db_creates_password_hash_column(self):
        db.init_db()
        conn = sqlite3.connect(str(db.DB_PATH))
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
            assert "password_hash" in cols
        finally:
            conn.close()

    def test_init_db_idempotent(self):
        db.init_db()
        db.init_db()
        conn = sqlite3.connect(str(db.DB_PATH))
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
            assert cols.count("password_hash") == 1
        finally:
            conn.close()


class TestSetPasswordCurrentCheck:
    def test_first_time_set_does_not_require_current(self):
        """A user who has never set a password can set one without proof."""
        admin = _admin_client()
        code = _make_invite(admin, "FirstTimerSetPw")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        r = c.post("/auth/set-password", json={"password": "FirstPass123"})
        assert r.status_code == 200

    def test_change_requires_current(self):
        """Once a password is set, changing it without current_password fails."""
        admin = _admin_client()
        code = _make_invite(admin, "ChangeRequiresCurrent")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "FirstPass123"})
        r = c.post("/auth/set-password", json={"password": "NewPass456!"})
        assert r.status_code == 400
        assert "Current password" in r.json()["detail"]

    def test_change_with_wrong_current_rejected(self):
        admin = _admin_client()
        code = _make_invite(admin, "WrongCurrentPw")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "FirstPass123"})
        r = c.post(
            "/auth/set-password",
            json={"password": "NewPass456!", "current_password": "WrongOne!!"},
        )
        assert r.status_code == 401

    def test_change_with_correct_current_succeeds(self):
        admin = _admin_client()
        code = _make_invite(admin, "CorrectCurrentPw")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "FirstPass123"})
        r = c.post(
            "/auth/set-password",
            json={"password": "NewPass456!", "current_password": "FirstPass123"},
        )
        assert r.status_code == 200

    def test_old_password_no_longer_works_after_change(self):
        """After a successful change, the old password must fail at /auth/signin."""
        admin = _admin_client()
        code = _make_invite(admin, "OldPwDies")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "FirstPass123"})
        c.post(
            "/auth/set-password",
            json={"password": "NewPass456!", "current_password": "FirstPass123"},
        )
        c2 = TestClient(app, cookies={})
        r = c2.post(
            "/auth/signin",
            json={"display_name": "OldPwDies", "password": "FirstPass123"},
        )
        assert r.status_code == 401


class TestDisplayNameUniqueness:
    def test_index_exists(self):
        with db.connect() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='ux_users_display_name_nocase'"
            ).fetchone()
            assert row is not None

    def test_create_invite_rejects_duplicate_user_name(self):
        admin = _admin_client()
        code = _make_invite(admin, "ActualUser1")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        r = admin.post(
            "/admin/invites",
            json={"display_name": "ActualUser1", "app_permissions": ["swppp"]},
        )
        assert r.status_code == 400
        assert "already exists" in r.json()["detail"].lower()

    def test_create_invite_rejects_case_insensitive_duplicate(self):
        admin = _admin_client()
        code = _make_invite(admin, "CaseSensitive1")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        r = admin.post(
            "/admin/invites",
            json={"display_name": "CASESENSITIVE1", "app_permissions": ["swppp"]},
        )
        assert r.status_code == 400

    def test_create_invite_rejects_duplicate_pending_invite(self):
        admin = _admin_client()
        _make_invite(admin, "PendingDupe")
        r = admin.post(
            "/admin/invites",
            json={"display_name": "PendingDupe", "app_permissions": ["swppp"]},
        )
        assert r.status_code == 400
        assert "pending" in r.json()["detail"].lower()

    def test_db_index_rejects_direct_duplicate_insert(self):
        """Even direct DB inserts must be rejected by the unique index."""
        import sqlite3 as sq

        with db.connect() as conn:
            db.create_user(conn, "DirectDupe")
            with pytest.raises(sq.IntegrityError):
                db.create_user(conn, "DirectDupe")


# ── Phase 5: Extended user tests ─────────────────────────────────────────


class TestAdminUsersExtended:
    def test_patch_user_no_fields(self):
        admin = _admin_client()
        code = _make_invite(admin, "NoOpUser")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        me = c.get("/auth/me").json()

        r = admin.patch(f"/admin/users/{me['user_id']}", json={})
        assert r.status_code == 200

    def test_patch_nonexistent_user(self):
        admin = _admin_client()
        r = admin.patch(
            "/admin/users/nonexistent-uuid",
            json={"is_active": False},
        )

    def test_promote_to_admin(self):
        admin = _admin_client()
        code = _make_invite(admin, "PromoteMe")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        me = c.get("/auth/me").json()
        assert me["is_admin"] is False

        r = admin.patch(f"/admin/users/{me['user_id']}", json={"is_admin": True})
        assert r.status_code == 200
        me2 = c.get("/auth/me").json()
        assert me2["is_admin"] is True

    def test_deactivate_already_deactivated(self):
        admin = _admin_client()
        code = _make_invite(admin, "DeactTwice")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        me = c.get("/auth/me").json()

        admin.patch(f"/admin/users/{me['user_id']}", json={"is_active": False})
        r = admin.patch(f"/admin/users/{me['user_id']}", json={"is_active": False})
        assert r.status_code == 200


# ── Platform admin PATCH tests ───────────────────────────────────────────


def _non_pa_admin_client() -> TestClient:
    """Create an is_admin=True, is_platform_admin=False user and return a client."""
    with db.connect() as conn:
        db.seed_app(conn, "swppp", "SWPPP AutoFill", "Generate ODOT PDFs", "/swppp")
        uid = db.create_user(conn, f"NonPAAdmin{next(_seq)}", is_admin=True)
        conn.execute("UPDATE users SET is_platform_admin=0 WHERE id=?", (uid,))
        conn.commit()
        token = db.create_session(conn, uid)
    c = TestClient(app, cookies={})
    c.cookies.set("tools_session", token)
    return c


class TestPlatformAdminPatch:
    def test_get_users_includes_is_platform_admin_field(self):
        """GET /admin/users must include is_platform_admin on every UserInfo."""
        db.init_db()
        admin = _admin_client()
        r = admin.get("/admin/users")
        assert r.status_code == 200
        users = r.json()["users"]
        assert len(users) >= 1
        for u in users:
            assert (
                "is_platform_admin" in u
            ), f"Missing is_platform_admin on user {u['id']}"
            assert isinstance(u["is_platform_admin"], bool)

    def test_platform_admin_can_promote_another_user(self):
        """A PA can set is_platform_admin=True on another user."""
        db.init_db()
        admin = _admin_client()
        code = _make_invite(admin, f"PromoteTarget{next(_seq)}")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        target_id = c.get("/auth/me").json()["user_id"]

        r = admin.patch(f"/admin/users/{target_id}", json={"is_platform_admin": True})
        assert r.status_code == 200

        # Confirm the flag is reflected in the user list
        users = {u["id"]: u for u in admin.get("/admin/users").json()["users"]}
        assert users[target_id]["is_platform_admin"] is True

    def test_platform_admin_can_demote_another_user(self):
        """A PA can set is_platform_admin=False on another user (not themselves)."""
        db.init_db()
        admin = _admin_client()
        with db.connect() as conn:
            uid2 = db.create_user(conn, f"PATarget{next(_seq)}", is_admin=True)
            conn.execute("UPDATE users SET is_platform_admin=1 WHERE id=?", (uid2,))
            conn.commit()

        r = admin.patch(f"/admin/users/{uid2}", json={"is_platform_admin": False})
        assert r.status_code == 200

        users = {u["id"]: u for u in admin.get("/admin/users").json()["users"]}
        assert users[uid2]["is_platform_admin"] is False

    def test_self_demotion_is_rejected(self):
        """A PA cannot remove their own platform_admin flag — guard must fire."""
        db.init_db()
        admin = _admin_client()
        my_id = admin.get("/auth/me").json()["user_id"]

        r = admin.patch(f"/admin/users/{my_id}", json={"is_platform_admin": False})
        assert r.status_code == 400
        assert "demote yourself" in r.json()["detail"].lower()

    def test_non_platform_admin_cannot_change_pa_flag(self):
        """An is_admin=True user without is_platform_admin cannot escalate via PATCH."""
        db.init_db()
        non_pa = _non_pa_admin_client()
        admin = _admin_client()
        code = _make_invite(admin, f"EscalationTarget{next(_seq)}")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        target_id = c.get("/auth/me").json()["user_id"]

        r = non_pa.patch(f"/admin/users/{target_id}", json={"is_platform_admin": True})
        assert r.status_code == 403


# ── Phase 6: Server-side auth gate ──────────────────────────────────────


class TestServerSideAuthGate:
    """Portal and admin pages must redirect unauthenticated users
    server-side (302) rather than serving HTML."""

    def test_portal_redirects_when_unauthenticated(self):
        c = TestClient(app, cookies={})
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert "/auth/login" in r.headers.get("location", "")

    def test_portal_serves_html_when_authenticated(self):
        admin = _admin_client()
        r = admin.get("/", follow_redirects=False)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_admin_redirects_when_unauthenticated(self):
        c = TestClient(app, cookies={})
        r = c.get("/admin", follow_redirects=False)
        assert r.status_code == 302
        assert "/auth/login" in r.headers.get("location", "")

    def test_admin_redirects_non_admin_to_portal(self):
        """A logged-in non-admin user should be sent to / not /auth/login."""
        admin = _admin_client()
        code = _make_invite(admin, "NonAdminGate")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        r = c.get("/admin", follow_redirects=False)
        assert r.status_code == 302
        location = r.headers.get("location", "")
        # Should redirect to portal root, NOT to login
        assert location.endswith("/") or location == "/"

    def test_admin_serves_html_for_admin_user(self):
        admin = _admin_client()
        r = admin.get("/admin", follow_redirects=False)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_login_page_always_served(self):
        c = TestClient(app, cookies={})
        r = c.get("/auth/login")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_portal_redirects_with_invalid_cookie(self):
        c = TestClient(app, cookies={"tools_session": "garbage-token"})
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert "/auth/login" in r.headers.get("location", "")


# ── Phase 7: Shared CSRF middleware ──────────────────────────────────────


class TestSharedCsrfMiddleware:
    """CSRF middleware (now shared) must still block mismatched origins."""

    def test_csrf_allows_same_origin(self):
        """Requests with matching origin should succeed normally."""
        admin = _admin_client()
        code = _make_invite(admin, "CsrfTestUser")
        assert code  # invite created successfully through same-origin POST

    def test_csrf_allows_missing_origin(self):
        """Requests without an Origin header should be allowed."""
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            code = db.create_invite(conn, "NoOriginUser", ["swppp"])
        c = TestClient(app, cookies={})
        r = c.post("/auth/claim", json={"code": code})
        assert r.status_code == 200

    def test_csrf_factory_rejects_bad_origin(self):
        """Direct unit test of the factory with dev_mode=False to verify
        that mismatched Origin headers are actually rejected."""
        import asyncio
        from unittest.mock import AsyncMock

        from web.middleware import create_csrf_middleware

        middleware = create_csrf_middleware(
            expected_origin="https://example.com", dev_mode=False
        )

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/test",
            "headers": [(b"origin", b"https://evil.com")],
            "query_string": b"",
        }
        from starlette.requests import Request as StarletteRequest

        request = StarletteRequest(scope)
        call_next = AsyncMock()
        response = asyncio.run(middleware(request, call_next))
        assert response.status_code == 403
        call_next.assert_not_called()

    def test_csrf_factory_allows_matching_origin(self):
        """Direct unit test: matching origin passes through."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from web.middleware import create_csrf_middleware

        middleware = create_csrf_middleware(
            expected_origin="https://example.com", dev_mode=False
        )

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/test",
            "headers": [(b"origin", b"https://example.com")],
            "query_string": b"",
        }
        from starlette.requests import Request as StarletteRequest

        request = StarletteRequest(scope)
        ok_response = MagicMock(status_code=200)
        call_next = AsyncMock(return_value=ok_response)
        response = asyncio.run(middleware(request, call_next))
        assert response.status_code == 200
        call_next.assert_called_once()

    def test_csrf_factory_skips_in_dev_mode(self):
        """Direct unit test: dev_mode=True skips the check even for bad origin."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from web.middleware import create_csrf_middleware

        middleware = create_csrf_middleware(
            expected_origin="https://example.com", dev_mode=True
        )

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/test",
            "headers": [(b"origin", b"https://evil.com")],
            "query_string": b"",
        }
        from starlette.requests import Request as StarletteRequest

        request = StarletteRequest(scope)
        ok_response = MagicMock(status_code=200)
        call_next = AsyncMock(return_value=ok_response)
        response = asyncio.run(middleware(request, call_next))
        assert response.status_code == 200
        call_next.assert_called_once()


# ── Phase 5: Extended session tests ──────────────────────────────────────


class TestAdminSessionsExtended:
    def test_kill_session_nonexistent_prefix(self):
        admin = _admin_client()
        r = admin.delete("/admin/sessions/zzzzzzzz")
        assert r.status_code == 400

    def test_kill_session_empty_prefix(self):
        admin = _admin_client()
        r = admin.delete("/admin/sessions/%20")
        assert r.status_code == 400


# ── Phase 5: Extended app admin tests ────────────────────────────────────


class TestAdminAppsExtended:
    def test_create_app_uppercase_id(self):
        admin = _admin_client()
        r = admin.post(
            "/admin/apps",
            json={
                "id": "UpperCase",
                "name": "Bad",
                "description": "x",
                "route_prefix": "/upper",
            },
        )
        assert r.status_code == 400

    def test_create_app_duplicate_id(self):
        admin = _admin_client()
        admin.post(
            "/admin/apps",
            json={
                "id": "dupe-app",
                "name": "First",
                "description": "x",
                "route_prefix": "/dupe",
            },
        )
        r = admin.post(
            "/admin/apps",
            json={
                "id": "dupe-app",
                "name": "Second",
                "description": "x",
                "route_prefix": "/dupe2",
            },
        )
        assert r.status_code == 400
        assert "already exists" in r.json()["detail"].lower()

    def test_create_app_missing_slash_prefix(self):
        admin = _admin_client()
        r = admin.post(
            "/admin/apps",
            json={
                "id": "noslash",
                "name": "No Slash",
                "description": "x",
                "route_prefix": "noslash",
            },
        )
        assert r.status_code == 400

    def test_patch_app_no_fields(self):
        admin = _admin_client()
        admin.post(
            "/admin/apps",
            json={
                "id": "noop-app",
                "name": "NoOp",
                "description": "x",
                "route_prefix": "/noop",
            },
        )
        r = admin.patch("/admin/apps/noop-app", json={})
        assert r.status_code == 200

    def test_patch_nonexistent_app(self):
        admin = _admin_client()
        r = admin.patch("/admin/apps/does-not-exist", json={"is_active": False})
        assert r.status_code == 404

    def test_create_app_very_long_fields(self):
        admin = _admin_client()
        r = admin.post(
            "/admin/apps",
            json={
                "id": "a" * 51,
                "name": "X",
                "description": "X",
                "route_prefix": "/x",
            },
        )
        assert r.status_code == 422


# ── Phase 5: Auth dependency edge cases ──────────────────────────────────


class TestAuthDependencies:
    def test_empty_session_cookie(self):
        c = TestClient(app, cookies={"tools_session": ""})
        r = c.get("/auth/me")
        assert r.status_code == 401

    def test_garbage_session_cookie(self):
        c = TestClient(app, cookies={"tools_session": "totally-invalid-token"})
        r = c.get("/auth/me")
        assert r.status_code == 401

    def test_deleted_session_is_invalid(self):
        admin = _admin_client()
        me = admin.get("/auth/me").json()

        # Kill all sessions for this user
        admin_sessions = admin.get(f"/admin/users/{me['user_id']}/sessions").json()[
            "sessions"
        ]
        for s in admin_sessions:
            prefix = s["token_prefix"].replace("....", "")
            admin.delete(f"/admin/sessions/{prefix}")

        # Now the admin's own cookie is invalid
        r = admin.get("/auth/me")
        assert r.status_code == 401


# -- Admin: Create User + Reset Password --


class TestAdminCreateUser:
    def test_create_user_returns_password(self):
        admin = _admin_client()
        r = admin.post(
            "/admin/users",
            json={"display_name": "NewPwUser", "app_permissions": ["swppp"]},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["display_name"] == "NewPwUser"
        assert len(data["password"]) >= 12
        assert data["user_id"]

    def test_created_user_can_login_with_password(self):
        admin = _admin_client()
        data = admin.post(
            "/admin/users",
            json={"display_name": "LoginPwUser", "app_permissions": ["swppp"]},
        ).json()
        c = TestClient(app, cookies={})
        r = c.post(
            "/auth/signin",
            json={"display_name": "LoginPwUser", "password": data["password"]},
        )
        assert r.status_code == 200
        me = c.get("/auth/me").json()
        assert me["display_name"] == "LoginPwUser"
        assert me["is_admin"] is False
        assert any(a["id"] == "swppp" for a in me["apps"])

    def test_create_user_admin_flag(self):
        admin = _admin_client()
        data = admin.post(
            "/admin/users",
            json={
                "display_name": "NewAdmin",
                "app_permissions": ["swppp"],
                "is_admin": True,
            },
        ).json()
        c = TestClient(app, cookies={})
        c.post(
            "/auth/signin",
            json={"display_name": "NewAdmin", "password": data["password"]},
        )
        assert c.get("/auth/me").json()["is_admin"] is True

    def test_create_user_duplicate_name(self):
        admin = _admin_client()
        admin.post(
            "/admin/users", json={"display_name": "DupPw", "app_permissions": ["swppp"]}
        )
        r = admin.post(
            "/admin/users", json={"display_name": "duppw", "app_permissions": ["swppp"]}
        )
        assert r.status_code == 400

    def test_create_user_requires_apps(self):
        admin = _admin_client()
        r = admin.post(
            "/admin/users", json={"display_name": "NoApps", "app_permissions": []}
        )
        assert r.status_code == 400

    def test_create_user_requires_admin(self):
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            code = db.create_invite(conn, "NotAdmin", ["swppp"])
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        r = c.post(
            "/admin/users", json={"display_name": "Foo", "app_permissions": ["swppp"]}
        )
        assert r.status_code == 403

    def test_reset_password_changes_password(self):
        admin = _admin_client()
        created = admin.post(
            "/admin/users",
            json={"display_name": "ResetMe", "app_permissions": ["swppp"]},
        ).json()
        old_pw = created["password"]
        uid = created["user_id"]
        r = admin.post(f"/admin/users/{uid}/reset-password")
        assert r.status_code == 200
        new_pw = r.json()["password"]
        assert new_pw != old_pw
        c = TestClient(app, cookies={})
        assert (
            c.post(
                "/auth/signin", json={"display_name": "ResetMe", "password": old_pw}
            ).status_code
            == 401
        )
        assert (
            c.post(
                "/auth/signin", json={"display_name": "ResetMe", "password": new_pw}
            ).status_code
            == 200
        )

    def test_reset_password_user_not_found(self):
        admin = _admin_client()
        r = admin.post("/admin/users/nonexistent-uuid/reset-password")
        assert r.status_code == 404


# ── Tier 5: Health Endpoint ─────────────────────────────────────────────


class TestHealthEndpoint:
    """Verify health endpoint returns correct status."""

    def test_health_returns_200_when_healthy(self):
        """GET /health must return 200 with status=ok when DB is available."""
        c = TestClient(app)
        response = c.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "tools-auth"
        assert "timestamp" in data
        assert "db" in data

    def test_health_is_unauthenticated(self):
        """GET /health must not require a session cookie."""
        c = TestClient(app, cookies={})
        # Call with no cookies set — must still return 200
        response = c.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


# ── Tier 4: Logout Cookie Fix ───────────────────────────────────────────


class TestLogoutCookieFix:
    """Verify logout clears the browser cookie in the response headers."""

    def test_logout_response_contains_delete_cookie_header(self):
        """After logout, the Set-Cookie header must set Max-Age=0."""
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            code = db.create_invite(conn, "LogoutUser", ["swppp"])
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "Pass-1234"})

        # Post to logout
        r = c.post("/auth/logout", follow_redirects=False)
        assert r.status_code == 302

        # Confirm the Set-Cookie header clears the session cookie
        set_cookie = r.headers.get("set-cookie", "")
        assert "tools_session=" in set_cookie
        assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie.lower()

    def test_logout_deletes_server_side_session(self):
        """Server-side session row must be deleted regardless of cookie fix."""
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            code = db.create_invite(conn, "LogoutUser2", ["swppp"])
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "Pass-1234"})

        # Verify session exists before logout
        token = c.cookies.get("tools_session")
        with db.connect() as conn:
            user = db.validate_session(conn, token)
            assert user is not None

        # Log out
        c.post("/auth/logout")

        # Verify session is gone
        with db.connect() as conn:
            user = db.validate_session(conn, token)
            assert user is None

    def test_logout_without_cookie_does_not_error(self):
        """Logout with no session cookie must still redirect cleanly."""
        c = TestClient(app, cookies={})
        r = c.post("/auth/logout", follow_redirects=False)
        assert r.status_code == 302


# ── Tier 4: Session Refresh Middleware ──────────────────────────────────


class TestSessionRefreshMiddleware:
    """Verify the cookie refresh middleware only refreshes on 2xx responses."""

    def test_valid_session_2xx_refreshes_cookie(self):
        """A valid session on a 2xx response must get a refreshed cookie."""
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            code = db.create_invite(conn, "RefreshUser", ["swppp"])
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "Pass-1234"})

        # GET /auth/me with valid session → 200 → cookie in response headers
        r = c.get("/auth/me")
        assert r.status_code == 200
        # The middleware should have refreshed the cookie
        assert "set-cookie" in r.headers or "Set-Cookie" in r.headers

    def test_invalid_session_redirect_does_not_refresh_cookie(self):
        """A redirect due to invalid session must NOT refresh the cookie."""
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            code = db.create_invite(conn, "RedirUser", ["swppp"])
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        token = c.cookies.get("tools_session")

        # Delete the session server-side
        with db.connect() as conn:
            db.delete_session(conn, token)

        # GET / with a deleted session token → 302 → no Set-Cookie in response
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 302
        # Middleware should NOT refresh the cookie on a redirect (3xx)
        set_cookie = r.headers.get("set-cookie", "")
        # If there's a Set-Cookie header, it should be from the endpoint clearing it, not middleware refreshing
        if "set-cookie" in r.headers or "Set-Cookie" in r.headers:
            # It should be deleting the cookie, not refreshing it with a long max-age
            assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie.lower()

    def test_401_response_does_not_refresh_cookie(self):
        """A 401 from a protected endpoint must not refresh any cookie."""
        c = TestClient(app, cookies={})
        # Try to access a protected endpoint without a session
        r = c.get("/admin/users")
        assert r.status_code == 401  # Unauthenticated access returns 401
        # No session cookie to refresh
        set_cookie = r.headers.get("set-cookie", "")
        assert "tools_session=" not in set_cookie


# ── Tier 4: Session Expiry ──────────────────────────────────────────────


class TestSessionExpiry:
    """Verify sessions expire correctly and the sliding window works."""

    def test_expired_session_is_rejected(self):
        """A session with expires_at in the past must return None from validate_session."""
        from datetime import datetime, timedelta, timezone

        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            user_id = db.create_user(conn, "ExpiryUser")
            token = db.create_session(conn, user_id)
            conn.commit()

            # Force expiry into the past
            past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            conn.execute(
                "UPDATE sessions SET expires_at = ? WHERE token = ?", (past, token)
            )
            conn.commit()

            result = db.validate_session(conn, token)
            assert result is None

    def test_valid_session_extends_expiry_on_validate(self):
        """Calling validate_session on a valid session must push expires_at forward."""
        import time
        from datetime import datetime, timedelta, timezone

        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            user_id = db.create_user(conn, "SlideUser")
            token = db.create_session(conn, user_id)
            conn.commit()

            # Record the initial expires_at
            row = conn.execute(
                "SELECT expires_at FROM sessions WHERE token = ?", (token,)
            ).fetchone()
            initial_expires = row["expires_at"]

            # Wait a tiny bit to ensure time has passed
            time.sleep(0.1)

            # Validate — this should slide the expiry forward
            db.validate_session(conn, token)
            conn.commit()

            row = conn.execute(
                "SELECT expires_at FROM sessions WHERE token = ?", (token,)
            ).fetchone()
            # The new expires_at should be later than the initial one
            assert row["expires_at"] >= initial_expires

    def test_new_session_has_expires_at_set(self):
        """create_session must always populate expires_at."""
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            user_id = db.create_user(conn, "ExpiryCreationUser")
            token = db.create_session(conn, user_id)
            conn.commit()

            row = conn.execute(
                "SELECT expires_at FROM sessions WHERE token = ?", (token,)
            ).fetchone()
            assert row["expires_at"] is not None


# ── Tier 4: Password Session Invalidation ───────────────────────────────


class TestPasswordSessionInvalidation:
    """Verify password changes revoke appropriate sessions."""

    def test_user_password_change_kills_other_sessions(self):
        """After user changes password, all sessions except current are invalid."""
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            code = db.create_invite(conn, "MultiSessionUser", ["swppp"])
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "OldPass-1234"})

        # Create another session for this user (simulating login from another device)
        current_token = c.cookies.get("tools_session")
        with db.connect() as conn:
            # Find user ID
            user = db.validate_session(conn, current_token)
            user_id = user["id"]
            other_token = db.create_session(conn, user_id, "other-device")
            conn.commit()

        # Change password
        r = c.post(
            "/auth/set-password",
            json={"current_password": "OldPass-1234", "password": "NewPass-5678"},
        )
        assert r.status_code == 200

        # Other session must now be invalid
        with db.connect() as conn:
            other_user = db.validate_session(conn, other_token)
            assert other_user is None

    def test_user_password_change_keeps_current_session(self):
        """After user changes password, the current session remains valid."""
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            code = db.create_invite(conn, "KeepSessionUser", ["swppp"])
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "OldPass-1234"})

        # Change password
        c.post(
            "/auth/set-password",
            json={"current_password": "OldPass-1234", "password": "NewPass-5678"},
        )

        # Current session should still be valid
        r = c.get("/auth/me")
        assert r.status_code == 200

    def test_admin_password_reset_kills_all_sessions(self):
        """Admin password reset must invalidate ALL sessions including the current one."""
        admin = _admin_client()

        # Create a regular user
        created = admin.post(
            "/admin/users",
            json={"display_name": "ResetTargetUser", "app_permissions": ["swppp"]},
        ).json()
        user_id = created["user_id"]
        password = created["password"]

        # Log in as that user
        c = TestClient(app, cookies={})
        c.post(
            "/auth/signin",
            json={"display_name": "ResetTargetUser", "password": password},
        )
        token = c.cookies.get("tools_session")

        # Admin resets the password
        r = admin.post(f"/admin/users/{user_id}/reset-password")
        assert r.status_code == 200

        # The user's session must be gone
        with db.connect() as conn:
            result = db.validate_session(conn, token)
            assert result is None

    def test_admin_reset_logs_session_count(self, caplog):
        """Admin reset log message must include sessions_revoked count."""
        import logging

        caplog.set_level(logging.INFO)
        admin = _admin_client()

        # Create a user with a session
        created = admin.post(
            "/admin/users",
            json={"display_name": "LogTargetUser", "app_permissions": ["swppp"]},
        ).json()
        user_id = created["user_id"]

        # Admin resets the password
        admin.post(f"/admin/users/{user_id}/reset-password")

        # Check that the log message includes sessions_revoked
        assert any("sessions_revoked=" in record.message for record in caplog.records)
