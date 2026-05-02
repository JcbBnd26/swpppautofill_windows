from __future__ import annotations

import sqlite3
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from web.auth import db
from web.auth.main import app

# ── In-memory DB helpers ─────────────────────────────────────────────────


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    for statement in db.SCHEMA_SQL.strip().split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.commit()
    return conn


def _seed(conn: sqlite3.Connection) -> dict[str, str]:
    """Create company + one active project with auto_weekly enabled."""
    suffix = str(uuid.uuid4())[:8]
    admin_id = db.create_user(conn, display_name=f"Admin {suffix}", is_admin=True)
    company_id = db.create_company(
        conn,
        legal_name=f"Company {suffix}",
        display_name=f"Co {suffix}",
        created_by=admin_id,
    )
    project_id = db.create_project(
        conn,
        company_id=company_id,
        project_number=f"P{suffix}",
        project_name=f"Project {suffix}",
        site_address="1 Main St",
        timezone="America/Chicago",
        rain_station_code="NRMN",
        created_by_user_id=admin_id,
    )
    conn.execute(
        "UPDATE projects SET auto_weekly_enabled=1, status='active', project_start_date=? WHERE id=?",
        ("2026-04-01", project_id),
    )
    conn.commit()
    return {
        "admin_id": admin_id,
        "company_id": company_id,
        "project_id": project_id,
        "project_number": f"P{suffix}",
    }


def _create_company_member_session(
    conn: sqlite3.Connection, company_id: str, role: str = "pm"
) -> str:
    """Create a company member with the given role and return a session token."""
    user_id = db.create_user(conn, display_name=f"Member {role}", is_admin=False)
    db.add_company_user(conn, user_id=user_id, company_id=company_id, role=role)
    return db.create_session(conn, user_id=user_id)


def _create_platform_admin_session(conn: sqlite3.Connection) -> str:
    user_id = db.create_user(conn, display_name="Platform Admin", is_admin=True)
    conn.execute("UPDATE users SET is_platform_admin=1 WHERE id=?", (user_id,))
    conn.commit()
    return db.create_session(conn, user_id=user_id)


def _create_outsider_session(conn: sqlite3.Connection) -> str:
    """Create a user with no company membership."""
    user_id = db.create_user(conn, display_name="Outsider", is_admin=False)
    return db.create_session(conn, user_id=user_id)


# ── TestGetCompanyDashboard ───────────────────────────────────────────────


class TestGetCompanyDashboard:
    def test_member_gets_200(self):
        conn = _make_conn()
        data = _seed(conn)
        token = _create_company_member_session(conn, data["company_id"])
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(
                f"/companies/{data['company_id']}/dashboard",
                cookies={"tools_session": token},
            )
            assert res.status_code == 200
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_non_member_gets_403(self):
        conn = _make_conn()
        data = _seed(conn)
        token = _create_outsider_session(conn)
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(
                f"/companies/{data['company_id']}/dashboard",
                cookies={"tools_session": token},
            )
            assert res.status_code == 403
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_unauthenticated_gets_401_or_403(self):
        conn = _make_conn()
        data = _seed(conn)
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(f"/companies/{data['company_id']}/dashboard")
            assert res.status_code in (401, 403)
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_platform_admin_bypasses_membership(self):
        conn = _make_conn()
        data = _seed(conn)
        token = _create_platform_admin_session(conn)
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(
                f"/companies/{data['company_id']}/dashboard",
                cookies={"tools_session": token},
            )
            assert res.status_code == 200
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_response_shape(self):
        conn = _make_conn()
        data = _seed(conn)
        token = _create_company_member_session(conn, data["company_id"])
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(
                f"/companies/{data['company_id']}/dashboard",
                cookies={"tools_session": token},
            )
            body = res.json()
            for key in ("total_projects", "active", "failing", "paused", "setup_incomplete", "recent_failures"):
                assert key in body, f"Missing key: {key}"
            assert isinstance(body["recent_failures"], list)
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_counts_active_correctly(self):
        conn = _make_conn()
        data = _seed(conn)
        # data already has 1 active project
        token = _create_company_member_session(conn, data["company_id"])
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(
                f"/companies/{data['company_id']}/dashboard",
                cookies={"tools_session": token},
            )
            body = res.json()
            assert body["total_projects"] == 1
            assert body["active"] == 1
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_failing_definition(self):
        """failing = auto_weekly_enabled=1 AND last_run_status IN ('failed','partial_failure')."""
        conn = _make_conn()
        data = _seed(conn)
        # Mark the project as failed
        conn.execute(
            "UPDATE projects SET last_run_status='failed', last_run_at='2026-05-01' WHERE id=?",
            (data["project_id"],),
        )
        conn.commit()
        token = _create_company_member_session(conn, data["company_id"])
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(
                f"/companies/{data['company_id']}/dashboard",
                cookies={"tools_session": token},
            )
            body = res.json()
            assert body["failing"] == 1
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_recent_failures_populated(self):
        conn = _make_conn()
        data = _seed(conn)
        # Add a failed run log entry
        db.create_project_run_log(
            conn,
            project_id=data["project_id"],
            run_date="2026-05-01",
            status="failed",
            error_type="ValueError",
            error_message="test error",
        )
        token = _create_company_member_session(conn, data["company_id"])
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(
                f"/companies/{data['company_id']}/dashboard",
                cookies={"tools_session": token},
            )
            body = res.json()
            assert len(body["recent_failures"]) == 1
            f = body["recent_failures"][0]
            assert f["project_id"] == data["project_id"]
            assert f["run_date"] == "2026-05-01"
            assert f["error_message"] == "test error"
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_archived_excluded_from_totals(self):
        conn = _make_conn()
        data = _seed(conn)
        # Add an archived project
        suffix = str(uuid.uuid4())[:8]
        archived_id = db.create_project(
            conn,
            company_id=data["company_id"],
            project_number=f"ARC{suffix}",
            project_name="Archived Project",
            site_address="2 Main St",
            timezone="America/Chicago",
            rain_station_code="NRMN",
            created_by_user_id=data["admin_id"],
        )
        conn.execute("UPDATE projects SET status='archived' WHERE id=?", (archived_id,))
        conn.commit()
        token = _create_company_member_session(conn, data["company_id"])
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(
                f"/companies/{data['company_id']}/dashboard",
                cookies={"tools_session": token},
            )
            body = res.json()
            # total_projects excludes archived
            assert body["total_projects"] == 1
        finally:
            app.dependency_overrides.clear()
            conn.close()


# ── TestGetProjectRunLog ──────────────────────────────────────────────────


class TestGetProjectRunLog:
    def test_member_gets_200(self):
        conn = _make_conn()
        data = _seed(conn)
        token = _create_company_member_session(conn, data["company_id"])
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(
                f"/companies/{data['company_id']}/projects/{data['project_id']}/run-log",
                cookies={"tools_session": token},
            )
            assert res.status_code == 200
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_non_member_gets_403(self):
        conn = _make_conn()
        data = _seed(conn)
        token = _create_outsider_session(conn)
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(
                f"/companies/{data['company_id']}/projects/{data['project_id']}/run-log",
                cookies={"tools_session": token},
            )
            assert res.status_code == 403
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_wrong_company_gets_404(self):
        """Member of company A cannot see projects from company B."""
        conn = _make_conn()
        data_a = _seed(conn)
        data_b = _seed(conn)  # separate company
        # Member of company A tries to fetch company B's project
        token = _create_company_member_session(conn, data_a["company_id"])
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(
                f"/companies/{data_a['company_id']}/projects/{data_b['project_id']}/run-log",
                cookies={"tools_session": token},
            )
            assert res.status_code == 404
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_bad_project_id_gets_404(self):
        conn = _make_conn()
        data = _seed(conn)
        token = _create_company_member_session(conn, data["company_id"])
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(
                f"/companies/{data['company_id']}/projects/nonexistent-id/run-log",
                cookies={"tools_session": token},
            )
            assert res.status_code == 404
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_empty_log_returns_empty_list(self):
        conn = _make_conn()
        data = _seed(conn)
        token = _create_company_member_session(conn, data["company_id"])
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(
                f"/companies/{data['company_id']}/projects/{data['project_id']}/run-log",
                cookies={"tools_session": token},
            )
            assert res.status_code == 200
            assert res.json()["entries"] == []
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_entries_returned_newest_first(self):
        conn = _make_conn()
        data = _seed(conn)
        db.create_project_run_log(conn, project_id=data["project_id"], run_date="2026-04-01", status="ok")
        db.create_project_run_log(conn, project_id=data["project_id"], run_date="2026-04-08", status="ok")
        db.create_project_run_log(conn, project_id=data["project_id"], run_date="2026-04-15", status="failed")
        token = _create_company_member_session(conn, data["company_id"])
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(
                f"/companies/{data['company_id']}/projects/{data['project_id']}/run-log",
                cookies={"tools_session": token},
            )
            entries = res.json()["entries"]
            dates = [e["run_date"] for e in entries]
            assert dates == sorted(dates, reverse=True)
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_limit_param_respected(self):
        conn = _make_conn()
        data = _seed(conn)
        for i in range(5):
            db.create_project_run_log(
                conn,
                project_id=data["project_id"],
                run_date=f"2026-04-{i + 1:02d}",
                status="ok",
            )
        token = _create_company_member_session(conn, data["company_id"])
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(
                f"/companies/{data['company_id']}/projects/{data['project_id']}/run-log?limit=2",
                cookies={"tools_session": token},
            )
            assert len(res.json()["entries"]) == 2
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_limit_capped_at_100(self):
        conn = _make_conn()
        data = _seed(conn)
        token = _create_company_member_session(conn, data["company_id"])
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(
                f"/companies/{data['company_id']}/projects/{data['project_id']}/run-log?limit=999",
                cookies={"tools_session": token},
            )
            # FastAPI Query(le=100) should reject > 100 with 422
            assert res.status_code == 422
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_error_fields_surfaced(self):
        conn = _make_conn()
        data = _seed(conn)
        db.create_project_run_log(
            conn,
            project_id=data["project_id"],
            run_date="2026-05-01",
            status="failed",
            error_type="RuntimeError",
            error_message="something went wrong",
        )
        token = _create_company_member_session(conn, data["company_id"])
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.get(
                f"/companies/{data['company_id']}/projects/{data['project_id']}/run-log",
                cookies={"tools_session": token},
            )
            entry = res.json()["entries"][0]
            assert entry["error_type"] == "RuntimeError"
            assert entry["error_message"] == "something went wrong"
        finally:
            app.dependency_overrides.clear()
            conn.close()


# ── TestRunCompanyReports ─────────────────────────────────────────────────


class TestRunCompanyReports:
    def test_pm_can_trigger(self):
        conn = _make_conn()
        data = _seed(conn)
        token = _create_company_member_session(conn, data["company_id"], role="pm")
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            with patch("web.scheduler.run_due_reports.run_due_reports") as mock_run:
                mock_run.return_value = {
                    "projects_processed": 1,
                    "reports_filed": 2,
                    "failures": 0,
                    "skipped": 0,
                }
                res = client.post(
                    f"/companies/{data['company_id']}/run-due-reports",
                    json={"force": False},
                    cookies={"tools_session": token},
                )
            assert res.status_code == 200
            body = res.json()
            assert body["projects_processed"] == 1
            assert "duration_ms" in body
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_company_admin_can_trigger(self):
        conn = _make_conn()
        data = _seed(conn)
        token = _create_company_member_session(conn, data["company_id"], role="company_admin")
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            with patch("web.scheduler.run_due_reports.run_due_reports") as mock_run:
                mock_run.return_value = {
                    "projects_processed": 0,
                    "reports_filed": 0,
                    "failures": 0,
                    "skipped": 0,
                }
                res = client.post(
                    f"/companies/{data['company_id']}/run-due-reports",
                    json={"force": False},
                    cookies={"tools_session": token},
                )
            assert res.status_code == 200
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_non_member_gets_403(self):
        conn = _make_conn()
        data = _seed(conn)
        token = _create_outsider_session(conn)
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            res = client.post(
                f"/companies/{data['company_id']}/run-due-reports",
                json={"force": False},
                cookies={"tools_session": token},
            )
            assert res.status_code == 403
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_company_id_passed_to_engine(self):
        """Verify company_id is forwarded so the engine filters to this company only."""
        conn = _make_conn()
        data = _seed(conn)
        token = _create_company_member_session(conn, data["company_id"], role="pm")
        client = TestClient(app)

        def override():
            yield conn

        app.dependency_overrides[db.get_db] = override
        try:
            with patch("web.scheduler.run_due_reports.run_due_reports") as mock_run:
                mock_run.return_value = {
                    "projects_processed": 0,
                    "reports_filed": 0,
                    "failures": 0,
                    "skipped": 0,
                }
                client.post(
                    f"/companies/{data['company_id']}/run-due-reports",
                    json={"force": False},
                    cookies={"tools_session": token},
                )
                _, kwargs = mock_run.call_args
                assert kwargs.get("company_id") == data["company_id"]
        finally:
            app.dependency_overrides.clear()
            conn.close()
