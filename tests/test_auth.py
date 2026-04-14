"""Tests for web.auth — Phase 1 auth system."""

from __future__ import annotations

import json
import os
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


def _admin_client() -> TestClient:
    """Create an admin user via the DB and return an authenticated TestClient."""
    with db.connect() as conn:
        db.seed_app(conn, "swppp", "SWPPP AutoFill", "Generate ODOT PDFs", "/swppp")
        code = db.create_invite(conn, "TestAdmin", ["swppp"], grant_admin=True)
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
        assert data["display_name"] == "TestAdmin"
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
        # Set a password on the admin account
        r = admin.post("/auth/set-password", json={"password": "TestPass123!"})
        assert r.status_code == 200

        # Log out
        admin.post("/auth/logout", follow_redirects=False)

        # Log back in with password
        c = TestClient(app, cookies={})
        r = c.post(
            "/auth/signin",
            json={"display_name": "TestAdmin", "password": "TestPass123!"},
        )
        assert r.status_code == 200
        assert r.json()["success"] is True
        assert "tools_session" in r.cookies

        # Verify session is valid
        me = c.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["display_name"] == "TestAdmin"

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
        c.post("/auth/set-password", json={"password": "NewPassword2"})

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

    def test_deactivated_user_cannot_password_login(self):
        admin = _admin_client()
        code = _make_invite(admin, "DeactPwUser")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "ValidPass1!"})
        me = c.get("/auth/me").json()

        # Deactivate user
        admin.patch(f"/admin/users/{me['user_id']}", json={"is_active": False})

        # Password login should fail
        c2 = TestClient(app, cookies={})
        r = c2.post(
            "/auth/signin",
            json={"display_name": "DeactPwUser", "password": "ValidPass1!"},
        )
        assert r.status_code == 401


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
        assert r.status_code == 404

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
