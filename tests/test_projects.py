from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from web.auth import db
from web.auth.main import app


def _u(prefix: str = "test-user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _co(prefix: str = "test-company") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _pn(prefix: str = "PRJ") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"


# ── Test Schema ──────────────────────────────────────────────────────────


class TestProjectSchema:
    def test_projects_table_exists(self):
        db.init_db()
        with db.connect() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='projects'"
            ).fetchone()
        assert row is not None

    def test_projects_unique_constraint_on_company_project_number(self):
        db.init_db()
        with db.connect() as conn:
            # Check constraint exists via PRAGMA index_list.
            indexes = conn.execute("PRAGMA index_list(projects)").fetchall()
            # UNIQUE constraint creates an automatic index.
            unique_indexes = [idx for idx in indexes if idx["unique"] == 1]
            assert len(unique_indexes) > 0

    def test_projects_required_columns_present(self):
        db.init_db()
        with db.connect() as conn:
            cols = conn.execute("PRAGMA table_info(projects)").fetchall()
            col_names = {c["name"] for c in cols}
        required = {
            "id",
            "company_id",
            "project_number",
            "project_name",
            "site_address",
            "timezone",
            "rain_station_code",
            "status",
            "created_at",
            "created_by_user_id",
        }
        assert required.issubset(col_names)

    def test_projects_default_status_is_setup_incomplete(self):
        db.init_db()
        with db.connect() as conn:
            cols = conn.execute("PRAGMA table_info(projects)").fetchall()
            status_col = next(c for c in cols if c["name"] == "status")
        # Default should be 'setup_incomplete' (as a string literal in schema).
        assert "'setup_incomplete'" in status_col["dflt_value"]


# ── Test Project CRUD ────────────────────────────────────────────────────


class TestProjectCRUD:
    def test_create_project_returns_id(self):
        db.init_db()
        with db.connect() as conn:
            cid = db.create_company(
                conn,
                legal_name=_co(),
                display_name=_co(),
                timezone="America/Chicago",
            )
            uid = db.create_user(conn, _u())
            pid = db.create_project(
                conn,
                company_id=cid,
                created_by_user_id=uid,
                project_number=_pn(),
                project_name="Test Project",
                site_address="123 Main St",
                rain_station_code="MEML",
            )
        assert pid is not None
        assert len(pid) == 36  # UUID format

    def test_get_project_returns_record(self):
        db.init_db()
        with db.connect() as conn:
            cid = db.create_company(
                conn,
                legal_name=_co(),
                display_name=_co(),
                timezone="America/Chicago",
            )
            uid = db.create_user(conn, _u())
            pnum = _pn()
            pid = db.create_project(
                conn,
                company_id=cid,
                created_by_user_id=uid,
                project_number=pnum,
                project_name="Test Project",
                site_address="123 Main St",
                rain_station_code="MEML",
            )
            project = db.get_project(conn, pid)
        assert project is not None
        assert project["id"] == pid
        assert project["project_number"] == pnum
        assert project["status"] == "setup_incomplete"

    def test_get_project_by_number_returns_record(self):
        db.init_db()
        with db.connect() as conn:
            cid = db.create_company(
                conn,
                legal_name=_co(),
                display_name=_co(),
                timezone="America/Chicago",
            )
            uid = db.create_user(conn, _u())
            pnum = _pn()
            db.create_project(
                conn,
                company_id=cid,
                created_by_user_id=uid,
                project_number=pnum,
                project_name="Test Project",
                site_address="123 Main St",
                rain_station_code="MEML",
            )
            project = db.get_project_by_number(conn, pnum)
        assert project is not None
        assert project["project_number"] == pnum

    def test_get_company_projects_returns_list(self):
        db.init_db()
        with db.connect() as conn:
            cid = db.create_company(
                conn,
                legal_name=_co(),
                display_name=_co(),
                timezone="America/Chicago",
            )
            uid = db.create_user(conn, _u())
            db.create_project(
                conn,
                company_id=cid,
                created_by_user_id=uid,
                project_number=_pn(),
                project_name="Project 1",
                site_address="123 Main St",
                rain_station_code="MEML",
            )
            db.create_project(
                conn,
                company_id=cid,
                created_by_user_id=uid,
                project_number=_pn(),
                project_name="Project 2",
                site_address="456 Oak Ave",
                rain_station_code="MEML",
            )
            projects = db.get_company_projects(conn, cid)
        assert len(projects) == 2

    def test_get_project_for_company_tenant_safe(self):
        db.init_db()
        with db.connect() as conn:
            cid_a = db.create_company(
                conn,
                legal_name=_co(),
                display_name=_co(),
                timezone="America/Chicago",
            )
            cid_b = db.create_company(
                conn,
                legal_name=_co(),
                display_name=_co(),
                timezone="America/Chicago",
            )
            uid = db.create_user(conn, _u())
            pid = db.create_project(
                conn,
                company_id=cid_a,
                created_by_user_id=uid,
                project_number=_pn(),
                project_name="Project A",
                site_address="123 Main St",
                rain_station_code="MEML",
            )
            # Should return project when correct company.
            p1 = db.get_project_for_company(conn, pid, cid_a)
            assert p1 is not None
            # Should return None when wrong company.
            p2 = db.get_project_for_company(conn, pid, cid_b)
            assert p2 is None

    def test_update_project_modifies_fields(self):
        db.init_db()
        with db.connect() as conn:
            cid = db.create_company(
                conn,
                legal_name=_co(),
                display_name=_co(),
                timezone="America/Chicago",
            )
            uid = db.create_user(conn, _u())
            pid = db.create_project(
                conn,
                company_id=cid,
                created_by_user_id=uid,
                project_number=_pn(),
                project_name="Original Name",
                site_address="123 Main St",
                rain_station_code="MEML",
            )
            db.update_project(conn, pid, project_name="Updated Name", status="active")
            project = db.get_project(conn, pid)
        assert project["project_name"] == "Updated Name"
        assert project["status"] == "active"

    def test_create_project_missing_required_field_raises(self):
        db.init_db()
        with db.connect() as conn:
            cid = db.create_company(
                conn,
                legal_name=_co(),
                display_name=_co(),
                timezone="America/Chicago",
            )
            uid = db.create_user(conn, _u())
            with pytest.raises(ValueError, match="Missing required field"):
                db.create_project(
                    conn,
                    company_id=cid,
                    created_by_user_id=uid,
                    project_number=_pn(),
                    # Missing project_name, site_address, rain_station_code
                )


# ── Test Project Endpoints ───────────────────────────────────────────────


def _create_company_and_user(role: str = "pm"):
    """Helper: create a company, user, grant SWPPP access, add to company."""
    db.init_db()
    with db.connect() as conn:
        cid = db.create_company(
            conn,
            legal_name=_co(),
            display_name=_co(),
            timezone="America/Chicago",
        )
        uid = db.create_user(conn, _u())
        db.grant_app_access(conn, uid, "swppp")
        db.add_company_user(conn, uid, cid, role=role)
        token = db.create_session(conn, uid)
    return cid, uid, token


class TestProjectEndpoints:
    def test_create_project_success(self):
        cid, uid, token = _create_company_and_user(role="pm")
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token)
        pnum = _pn()
        res = client.post(
            f"/companies/{cid}/projects",
            json={
                "project_number": pnum,
                "project_name": "Test Project",
                "site_address": "123 Main St",
                "rain_station_code": "MEML",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert "id" in data

    def test_create_project_requires_auth(self):
        cid, _, _ = _create_company_and_user()
        client = TestClient(app, raise_server_exceptions=True)
        res = client.post(
            f"/companies/{cid}/projects",
            json={
                "project_number": _pn(),
                "project_name": "Test Project",
                "site_address": "123 Main St",
                "rain_station_code": "MEML",
            },
        )
        assert res.status_code == 401

    def test_create_project_requires_company_membership(self):
        cid_a, _, _ = _create_company_and_user()
        # Create a second company with a different user.
        cid_b, _, token_b = _create_company_and_user()
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token_b)
        # Try to create project in company A with company B user.
        res = client.post(
            f"/companies/{cid_a}/projects",
            json={
                "project_number": _pn(),
                "project_name": "Test Project",
                "site_address": "123 Main St",
                "rain_station_code": "MEML",
            },
        )
        assert res.status_code == 403

    def test_list_projects_returns_company_projects(self):
        cid, uid, token = _create_company_and_user()
        # Create two projects.
        with db.connect() as conn:
            db.create_project(
                conn,
                company_id=cid,
                created_by_user_id=uid,
                project_number=_pn(),
                project_name="Project 1",
                site_address="123 Main St",
                rain_station_code="MEML",
            )
            db.create_project(
                conn,
                company_id=cid,
                created_by_user_id=uid,
                project_number=_pn(),
                project_name="Project 2",
                site_address="456 Oak Ave",
                rain_station_code="MEML",
            )
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token)
        res = client.get(f"/companies/{cid}/projects")
        assert res.status_code == 200
        data = res.json()
        assert len(data["projects"]) == 2

    def test_get_project_detail_success(self):
        cid, uid, token = _create_company_and_user()
        with db.connect() as conn:
            pid = db.create_project(
                conn,
                company_id=cid,
                created_by_user_id=uid,
                project_number=_pn(),
                project_name="Test Project",
                site_address="123 Main St",
                rain_station_code="MEML",
            )
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token)
        res = client.get(f"/companies/{cid}/projects/{pid}")
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == pid

    def test_update_project_success(self):
        cid, uid, token = _create_company_and_user(role="pm")
        with db.connect() as conn:
            pid = db.create_project(
                conn,
                company_id=cid,
                created_by_user_id=uid,
                project_number=_pn(),
                project_name="Original",
                site_address="123 Main St",
                rain_station_code="MEML",
            )
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token)
        res = client.patch(
            f"/companies/{cid}/projects/{pid}",
            json={"project_name": "Updated"},
        )
        assert res.status_code == 200
        # Verify update.
        with db.connect() as conn:
            project = db.get_project(conn, pid)
        assert project["project_name"] == "Updated"


# ── Test Tenant Isolation ────────────────────────────────────────────────


class TestProjectTenantIsolation:
    def test_company_a_user_cannot_see_company_b_projects(self):
        cid_a, uid_a, token_a = _create_company_and_user()
        cid_b, uid_b, _ = _create_company_and_user()
        # Create project in company B.
        with db.connect() as conn:
            pid_b = db.create_project(
                conn,
                company_id=cid_b,
                created_by_user_id=uid_b,
                project_number=_pn(),
                project_name="Company B Project",
                site_address="123 Main St",
                rain_station_code="MEML",
            )
        # User A tries to access Company B's project.
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token_a)
        res = client.get(f"/companies/{cid_b}/projects/{pid_b}")
        # Should get 403 (not a member of company B).
        assert res.status_code == 403

    def test_get_project_detail_cross_company_returns_404(self):
        cid_a, uid_a, token_a = _create_company_and_user()
        cid_b, uid_b, _ = _create_company_and_user()
        # Create project in company B.
        with db.connect() as conn:
            pid_b = db.create_project(
                conn,
                company_id=cid_b,
                created_by_user_id=uid_b,
                project_number=_pn(),
                project_name="Company B Project",
                site_address="123 Main St",
                rain_station_code="MEML",
            )
        # User A tries to access project via company A endpoint (tenant bleed attempt).
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token_a)
        res = client.get(f"/companies/{cid_a}/projects/{pid_b}")
        # Should return 404 (project doesn't belong to company A).
        assert res.status_code == 404

    def test_list_projects_only_shows_own_company(self):
        cid_a, uid_a, token_a = _create_company_and_user()
        cid_b, uid_b, _ = _create_company_and_user()
        # Create projects in both companies.
        with db.connect() as conn:
            db.create_project(
                conn,
                company_id=cid_a,
                created_by_user_id=uid_a,
                project_number=_pn(),
                project_name="Company A Project",
                site_address="123 Main St",
                rain_station_code="MEML",
            )
            db.create_project(
                conn,
                company_id=cid_b,
                created_by_user_id=uid_b,
                project_number=_pn(),
                project_name="Company B Project",
                site_address="456 Oak Ave",
                rain_station_code="MEML",
            )
        # User A lists projects — should only see Company A.
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token_a)
        res = client.get(f"/companies/{cid_a}/projects")
        assert res.status_code == 200
        data = res.json()
        assert len(data["projects"]) == 1
        assert data["projects"][0]["company_id"] == cid_a


# ── Test Duplicate Project Number ────────────────────────────────────────


class TestProjectDuplicateNumber:
    def test_duplicate_project_number_within_company_returns_409(self):
        cid, uid, token = _create_company_and_user()
        pnum = _pn()
        # Create first project.
        with db.connect() as conn:
            db.create_project(
                conn,
                company_id=cid,
                created_by_user_id=uid,
                project_number=pnum,
                project_name="Project 1",
                site_address="123 Main St",
                rain_station_code="MEML",
            )
        # Try to create second project with same number.
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token)
        res = client.post(
            f"/companies/{cid}/projects",
            json={
                "project_number": pnum,
                "project_name": "Project 2",
                "site_address": "456 Oak Ave",
                "rain_station_code": "MEML",
            },
        )
        assert res.status_code == 409
        assert "already exists" in res.json()["detail"]

    def test_same_project_number_in_different_companies_allowed(self):
        cid_a, uid_a, token_a = _create_company_and_user()
        cid_b, uid_b, token_b = _create_company_and_user()
        pnum = _pn("SHARED")
        # Create project in company A.
        client_a = TestClient(app, raise_server_exceptions=True)
        client_a.cookies.set("tools_session", token_a)
        res_a = client_a.post(
            f"/companies/{cid_a}/projects",
            json={
                "project_number": pnum,
                "project_name": "Project A",
                "site_address": "123 Main St",
                "rain_station_code": "MEML",
            },
        )
        assert res_a.status_code == 200
        # Create project with same number in company B — should succeed.
        client_b = TestClient(app, raise_server_exceptions=True)
        client_b.cookies.set("tools_session", token_b)
        res_b = client_b.post(
            f"/companies/{cid_b}/projects",
            json={
                "project_number": pnum,
                "project_name": "Project B",
                "site_address": "456 Oak Ave",
                "rain_station_code": "MEML",
            },
        )
        assert res_b.status_code == 200


# ── Test Project Validation ──────────────────────────────────────────────


class TestProjectValidation:
    def test_missing_project_number_returns_422(self):
        cid, _, token = _create_company_and_user()
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token)
        res = client.post(
            f"/companies/{cid}/projects",
            json={
                # Missing project_number
                "project_name": "Test",
                "site_address": "123 Main St",
                "rain_station_code": "MEML",
            },
        )
        assert res.status_code == 422

    def test_missing_project_name_returns_422(self):
        cid, _, token = _create_company_and_user()
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token)
        res = client.post(
            f"/companies/{cid}/projects",
            json={
                "project_number": _pn(),
                # Missing project_name
                "site_address": "123 Main St",
                "rain_station_code": "MEML",
            },
        )
        assert res.status_code == 422

    def test_missing_site_address_returns_422(self):
        cid, _, token = _create_company_and_user()
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token)
        res = client.post(
            f"/companies/{cid}/projects",
            json={
                "project_number": _pn(),
                "project_name": "Test",
                # Missing site_address
                "rain_station_code": "MEML",
            },
        )
        assert res.status_code == 422

    def test_missing_rain_station_code_returns_422(self):
        cid, _, token = _create_company_and_user()
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token)
        res = client.post(
            f"/companies/{cid}/projects",
            json={
                "project_number": _pn(),
                "project_name": "Test",
                "site_address": "123 Main St",
                # Missing rain_station_code
            },
        )
        assert res.status_code == 422

    def test_optional_fields_not_required(self):
        cid, _, token = _create_company_and_user()
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token)
        res = client.post(
            f"/companies/{cid}/projects",
            json={
                "project_number": _pn(),
                "project_name": "Minimal Project",
                "site_address": "123 Main St",
                "rain_station_code": "MEML",
                # All optional fields omitted
            },
        )
        assert res.status_code == 200

    def test_project_number_max_length_enforced(self):
        cid, _, token = _create_company_and_user()
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("tools_session", token)
        res = client.post(
            f"/companies/{cid}/projects",
            json={
                "project_number": "X" * 101,  # Exceeds max_length=100
                "project_name": "Test",
                "site_address": "123 Main St",
                "rain_station_code": "MEML",
            },
        )
        assert res.status_code == 422
