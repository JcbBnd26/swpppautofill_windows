"""IR #2 tests — Onboarding & Roles.

Covers:
- company_signup_invites table structure
- invite_codes.company_id / role columns
- create_company_signup_invite / get / claim lifecycle
- create_employee_invite / claim with company membership
- claim_company_signup_invite permissions & isolation
- 3-role permission matrix enforcement
- FastAPI endpoints: POST /admin/company-signup-invites,
  GET /auth/signup-invite/{token}, POST /auth/signup,
  POST /companies/{id}/invites, GET /companies/{id}/members
"""

from __future__ import annotations

import itertools
import os
import tempfile
from datetime import datetime, timedelta, timezone

# Set data dir before web imports (mirrors pattern in test_tenant_isolation.py).
if "TOOLS_DATA_DIR" not in os.environ:
    os.environ["TOOLS_DATA_DIR"] = tempfile.mkdtemp()
os.environ.setdefault("TOOLS_DEV_MODE", "1")

import pytest
from fastapi.testclient import TestClient

from web.auth import db
from web.auth.main import app

_seq = itertools.count(8000)


def _u(prefix: str = "U") -> str:
    return f"{prefix}{next(_seq)}"


def _co(prefix: str = "Co") -> str:
    return f"{prefix} {next(_seq)} LLC"


def _email() -> str:
    return f"admin{next(_seq)}@example.com"


def _admin_client() -> tuple[TestClient, str]:
    """Return (TestClient, admin_user_id) with an active session cookie."""
    db.init_db()
    with db.connect() as conn:
        db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
        uid = db.create_user(conn, _u("Admin"), is_admin=True)
        db.set_user_password(conn, uid, "AdminPass1!")
        token = db.create_session(conn, uid)
    client = TestClient(app, raise_server_exceptions=True)
    client.cookies.set("tools_session", token)
    return client, uid


def _company_admin_client(company_id: str) -> tuple[TestClient, str]:
    """Return (TestClient, user_id) for a company_admin of *company_id*."""
    db.init_db()
    with db.connect() as conn:
        db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
        uid = db.create_user(conn, _u("CAdmin"))
        db.set_user_password(conn, uid, "CAdminPass1!")
        db.grant_app_access(conn, uid, "swppp")
        db.add_company_user(conn, uid, company_id, role="company_admin")
        token = db.create_session(conn, uid)
    client = TestClient(app, raise_server_exceptions=True)
    client.cookies.set("tools_session", token)
    return client, uid


# ── Schema ────────────────────────────────────────────────────────────────


class TestIR2Schema:
    def test_company_signup_invites_table_exists(self):
        db.init_db()
        with db.connect() as conn:
            tables = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "company_signup_invites" in tables

    def test_invite_codes_has_company_id_and_role(self):
        db.init_db()
        with db.connect() as conn:
            assert db._column_exists(conn, "invite_codes", "company_id")
            assert db._column_exists(conn, "invite_codes", "role")

    def test_users_has_email_column(self):
        db.init_db()
        with db.connect() as conn:
            assert db._column_exists(conn, "users", "email")


# ── Company Signup Invite Lifecycle ──────────────────────────────────────


class TestCompanySignupInvite:
    def test_create_and_get(self):
        db.init_db()
        with db.connect() as conn:
            platform_uid = db.create_user(conn, _u("PA"), is_admin=True)
            token = db.create_company_signup_invite(
                conn,
                proposed_company_name=_co(),
                admin_email=_email(),
                created_by=platform_uid,
            )
            invite = db.get_company_signup_invite(conn, token)
        assert invite is not None
        assert invite["claimed_at"] is None

    def test_get_all_company_signup_invites(self):
        db.init_db()
        with db.connect() as conn:
            platform_uid = db.create_user(conn, _u("PA2"), is_admin=True)
            t1 = db.create_company_signup_invite(conn, _co(), _email(), platform_uid)
            t2 = db.create_company_signup_invite(conn, _co(), _email(), platform_uid)
            all_invites = db.get_all_company_signup_invites(conn)
        tokens = {i["token"] for i in all_invites}
        assert t1 in tokens
        assert t2 in tokens

    def test_claim_creates_company_and_user(self):
        db.init_db()
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            pa = db.create_user(conn, _u("PA3"), is_admin=True)
            token = db.create_company_signup_invite(
                conn,
                proposed_company_name=_co("ClaimMe"),
                admin_email=_email(),
                created_by=pa,
            )
        name = _co("New")
        with db.connect() as conn:
            result = db.claim_company_signup_invite(
                conn,
                token=token,
                display_name=_u("Owner"),
                password="SomePass1!",
                legal_name=name,
                company_display_name=name,
                tz="America/Chicago",
            )
        assert result is not None
        user_id, company_id, session_token = result
        assert user_id
        assert company_id
        assert session_token
        with db.connect() as conn:
            company = db.get_company(conn, company_id)
            membership = db.get_company_user(conn, user_id, company_id)
        assert company["legal_name"] == name
        assert membership["role"] == "company_admin"

    def test_claim_marks_invite_claimed(self):
        db.init_db()
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            pa = db.create_user(conn, _u("PA4"), is_admin=True)
            token = db.create_company_signup_invite(conn, _co(), _email(), pa)
        with db.connect() as conn:
            db.claim_company_signup_invite(
                conn,
                token=token,
                display_name=_u(),
                password="P@ssword1",
                legal_name=_co(),
                company_display_name=_co(),
            )
            invite = db.get_company_signup_invite(conn, token)
        assert invite["claimed_at"] is not None

    def test_double_claim_returns_none(self):
        db.init_db()
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            pa = db.create_user(conn, _u("PA5"), is_admin=True)
            token = db.create_company_signup_invite(conn, _co(), _email(), pa)
        args = dict(
            display_name=_u(),
            password="P@ssword1",
            legal_name=_co(),
            company_display_name=_co(),
        )
        with db.connect() as conn:
            first = db.claim_company_signup_invite(conn, token=token, **args)
        assert first is not None
        with db.connect() as conn:
            second = db.claim_company_signup_invite(conn, token=token, **args)
        assert second is None

    def test_expired_invite_returns_none(self):
        db.init_db()
        with db.connect() as conn:
            pa = db.create_user(conn, _u("PA6"), is_admin=True)
            token = db.create_company_signup_invite(conn, _co(), _email(), pa)
            # Manually backdate the expiry
            conn.execute(
                "UPDATE company_signup_invites SET expires_at = ? WHERE token = ?",
                ("2000-01-01T00:00:00+00:00", token),
            )
        with db.connect() as conn:
            result = db.claim_company_signup_invite(
                conn,
                token=token,
                display_name=_u(),
                password="P@ssword1",
                legal_name=_co(),
                company_display_name=_co(),
            )
        assert result is None

    def test_get_missing_invite_returns_none(self):
        db.init_db()
        with db.connect() as conn:
            assert db.get_company_signup_invite(conn, "no-such-token") is None


# ── Employee Invite ───────────────────────────────────────────────────────


class TestEmployeeInvite:
    def _setup(self) -> tuple[str, str]:
        """Return (company_id, swppp_app_seeded)."""
        db.init_db()
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            cid = db.create_company(
                conn, legal_name=_co(), display_name=_co(), timezone="America/Chicago"
            )
        return cid

    def test_create_employee_invite_returns_code(self):
        cid = self._setup()
        with db.connect() as conn:
            code = db.create_employee_invite(conn, _u(), cid, "pm", ["swppp"])
        assert code.startswith("TOOLS-")

    def test_claim_employee_invite_adds_to_company(self):
        cid = self._setup()
        with db.connect() as conn:
            code = db.create_employee_invite(conn, _u("Emp"), cid, "pm", ["swppp"])
        with db.connect() as conn:
            result = db.claim_invite_code(conn, code)
        assert result is not None
        user_id, _ = result
        with db.connect() as conn:
            membership = db.get_company_user(conn, user_id, cid)
        assert membership is not None
        assert membership["role"] == "pm"

    def test_invalid_role_on_employee_invite_raises(self):
        cid = self._setup()
        with db.connect() as conn:
            with pytest.raises(ValueError, match="Invalid role"):
                db.create_employee_invite(conn, _u(), cid, "god", ["swppp"])

    def test_both_roles_produce_correct_membership(self):
        db.init_db()
        for role in ("company_admin", "pm"):
            cid = self._setup()
            with db.connect() as conn:
                code = db.create_employee_invite(
                    conn, _u(f"R{role}"), cid, role, ["swppp"]
                )
            with db.connect() as conn:
                uid, _ = db.claim_invite_code(conn, code)
                m = db.get_company_user(conn, uid, cid)
            assert m["role"] == role


# ── FastAPI endpoints ─────────────────────────────────────────────────────


class TestSignupInviteEndpoints:
    def test_platform_admin_can_create_signup_invite(self):
        client, _ = _admin_client()
        res = client.post(
            "/admin/company-signup-invites",
            json={
                "proposed_company_name": _co(),
                "admin_email": _email(),
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert "token" in data
        assert "/signup/" in data["link"]

    def test_non_platform_admin_gets_403(self):
        db.init_db()
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            uid = db.create_user(conn, _u("NonAdmin"), is_admin=False)
            token = db.create_session(conn, uid)
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token)
        res = client.post(
            "/admin/company-signup-invites",
            json={
                "proposed_company_name": _co(),
                "admin_email": _email(),
            },
        )
        assert res.status_code == 403

    def test_unauthenticated_gets_401(self):
        client = TestClient(app, raise_server_exceptions=True)
        res = client.post(
            "/admin/company-signup-invites",
            json={
                "proposed_company_name": _co(),
                "admin_email": _email(),
            },
        )
        assert res.status_code == 401

    def test_list_signup_invites(self):
        client, admin_id = _admin_client()
        client.post(
            "/admin/company-signup-invites",
            json={
                "proposed_company_name": _co("Listed"),
                "admin_email": _email(),
            },
        )
        res = client.get("/admin/company-signup-invites")
        assert res.status_code == 200
        assert "invites" in res.json()

    def test_get_invite_info_valid(self):
        client, _ = _admin_client()
        create_res = client.post(
            "/admin/company-signup-invites",
            json={
                "proposed_company_name": "Test Corp",
                "admin_email": _email(),
            },
        )
        token = create_res.json()["token"]
        info_res = TestClient(app).get(f"/auth/signup-invite/{token}")
        assert info_res.status_code == 200
        assert info_res.json()["proposed_company_name"] == "Test Corp"

    def test_get_invite_info_missing_returns_404(self):
        res = TestClient(app).get("/auth/signup-invite/no-such-token-xyz")
        assert res.status_code == 404

    def test_full_signup_flow(self):
        client, _ = _admin_client()
        create_res = client.post(
            "/admin/company-signup-invites",
            json={
                "proposed_company_name": _co("Full"),
                "admin_email": _email(),
            },
        )
        token = create_res.json()["token"]
        signup_client = TestClient(app, raise_server_exceptions=True)
        name = _co("Claimed")
        res = signup_client.post(
            "/auth/signup",
            json={
                "token": token,
                "display_name": _u("NewAdmin"),
                "password": "NewPass1!",
                "legal_name": name,
                "company_display_name": name,
                "timezone": "America/Chicago",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["company_id"]

    def test_signup_with_bad_token_returns_400(self):
        signup_client = TestClient(app, raise_server_exceptions=True)
        res = signup_client.post(
            "/auth/signup",
            json={
                "token": "bogus-token",
                "display_name": _u(),
                "password": "NewPass1!",
                "legal_name": _co(),
                "company_display_name": _co(),
                "timezone": "America/Chicago",
            },
        )
        assert res.status_code == 400


class TestCompanyMemberEndpoints:
    def _setup_company(self) -> tuple[str, str]:
        """Create company, return (company_id, company_admin_user_id) with session."""
        db.init_db()
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            cid = db.create_company(
                conn, legal_name=_co(), display_name=_co(), timezone="America/Chicago"
            )
        return cid

    def test_company_admin_can_list_members(self):
        cid = self._setup_company()
        client, _ = _company_admin_client(cid)
        res = client.get(f"/companies/{cid}/members")
        assert res.status_code == 200
        assert "members" in res.json()

    def test_non_member_gets_403(self):
        cid = self._setup_company()
        db.init_db()
        with db.connect() as conn:
            uid = db.create_user(conn, _u("Outsider"))
            token = db.create_session(conn, uid)
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token)
        res = client.get(f"/companies/{cid}/members")
        assert res.status_code == 403

    def test_platform_admin_can_list_any_companys_members(self):
        cid = self._setup_company()
        client, _ = _admin_client()
        res = client.get(f"/companies/{cid}/members")
        assert res.status_code == 200

    def test_company_admin_can_create_employee_invite(self):
        cid = self._setup_company()
        client, _ = _company_admin_client(cid)
        res = client.post(
            f"/companies/{cid}/invites",
            json={
                "display_name": _u("NewEmp"),
                "role": "pm",
                "app_permissions": ["swppp"],
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert "code" in data
        assert "link" in data

    def test_pm_cannot_create_employee_invite(self):
        cid = self._setup_company()
        db.init_db()
        with db.connect() as conn:
            uid = db.create_user(conn, _u("PMUser"))
            db.grant_app_access(conn, uid, "swppp")
            db.add_company_user(conn, uid, cid, role="pm")
            token = db.create_session(conn, uid)
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token)
        res = client.post(
            f"/companies/{cid}/invites",
            json={
                "display_name": _u("Attempt"),
                "role": "pm",
                "app_permissions": ["swppp"],
            },
        )
        assert res.status_code == 403

    def test_invalid_role_in_employee_invite_returns_400(self):
        cid = self._setup_company()
        client, _ = _company_admin_client(cid)
        res = client.post(
            f"/companies/{cid}/invites",
            json={
                "display_name": _u("BadRole"),
                "role": "superadmin",
                "app_permissions": ["swppp"],
            },
        )
        assert res.status_code == 400

    def test_update_member_role(self):
        cid = self._setup_company()
        db.init_db()
        with db.connect() as conn:
            target_uid = db.create_user(conn, _u("Target"))
            db.add_company_user(conn, target_uid, cid, role="pm")
        client, _ = _company_admin_client(cid)
        res = client.patch(
            f"/companies/{cid}/members/{target_uid}", json={"role": "company_admin"}
        )
        assert res.status_code == 200
        with db.connect() as conn:
            m = db.get_company_user(conn, target_uid, cid)
        assert m["role"] == "company_admin"

    def test_remove_member(self):
        cid = self._setup_company()
        db.init_db()
        with db.connect() as conn:
            target_uid = db.create_user(conn, _u("ToRemove"))
            db.add_company_user(conn, target_uid, cid, role="pm")
        client, _ = _company_admin_client(cid)
        res = client.delete(f"/companies/{cid}/members/{target_uid}")
        assert res.status_code == 200
        with db.connect() as conn:
            m = db.get_company_user(conn, target_uid, cid)
        assert m is None

    def test_cannot_remove_self(self):
        cid = self._setup_company()
        client, uid = _company_admin_client(cid)
        res = client.delete(f"/companies/{cid}/members/{uid}")
        assert res.status_code == 400


class TestAdminCompanyList:
    def test_platform_admin_sees_all_companies(self):
        db.init_db()
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")
            cid1 = db.create_company(
                conn, legal_name=_co(), display_name=_co(), timezone="America/Chicago"
            )
            cid2 = db.create_company(
                conn, legal_name=_co(), display_name=_co(), timezone="America/Chicago"
            )
        client, _ = _admin_client()
        res = client.get("/admin/companies")
        assert res.status_code == 200
        ids = {c["id"] for c in res.json()["companies"]}
        assert cid1 in ids
        assert cid2 in ids

    def test_non_platform_admin_gets_403(self):
        db.init_db()
        with db.connect() as conn:
            uid = db.create_user(conn, _u("Regular"))
            token = db.create_session(conn, uid)
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token)
        res = client.get("/admin/companies")
        assert res.status_code == 403
