"""IR-6: Platform admin health dashboard tests."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from web.auth import db, main

# ── Test helpers ─────────────────────────────────────────────────────────────


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    for statement in db.SCHEMA_SQL.strip().split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.commit()
    return conn


def _seed_company(conn: sqlite3.Connection) -> dict[str, str]:
    """Create a company with one active project (auto_weekly enabled)."""
    suffix = str(uuid.uuid4())[:8]
    admin_id = db.create_user(conn, display_name=f"Admin {suffix}", is_admin=True)
    company_id = db.create_company(
        conn,
        legal_name=f"Company {suffix}",
        display_name=f"Co {suffix}",
        created_by=admin_id,
    )
    db.add_company_user(
        conn, user_id=admin_id, company_id=company_id, role="company_admin"
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


def _create_platform_admin_session(conn: sqlite3.Connection) -> str:
    user_id = db.create_user(conn, display_name="Platform Admin", is_admin=True)
    conn.execute("UPDATE users SET is_platform_admin=1 WHERE id=?", (user_id,))
    conn.commit()
    return db.create_session(conn, user_id=user_id)


def _create_regular_admin_session(conn: sqlite3.Connection) -> str:
    """is_admin=True but NOT is_platform_admin — should be blocked."""
    user_id = db.create_user(conn, display_name="Regular Admin", is_admin=True)
    # create_user mirrors is_admin → is_platform_admin; clear that flag explicitly
    conn.execute("UPDATE users SET is_platform_admin=0 WHERE id=?", (user_id,))
    conn.commit()
    return db.create_session(conn, user_id=user_id)


# ── TestGetPlatformHealth ─────────────────────────────────────────────────────


class TestGetPlatformHealth:
    def test_platform_admin_gets_200(self):
        conn = _make_conn()
        _seed_company(conn)
        token = _create_platform_admin_session(conn)

        app = main.app
        app.dependency_overrides[db.get_db] = lambda: conn
        client = TestClient(app, raise_server_exceptions=True)
        res = client.get("/admin/platform-health", cookies={"tools_session": token})
        app.dependency_overrides.clear()

        assert res.status_code == 200

    def test_non_platform_admin_gets_403(self):
        conn = _make_conn()
        token = _create_regular_admin_session(conn)

        app = main.app
        app.dependency_overrides[db.get_db] = lambda: conn
        client = TestClient(app, raise_server_exceptions=True)
        res = client.get("/admin/platform-health", cookies={"tools_session": token})
        app.dependency_overrides.clear()

        assert res.status_code == 403

    def test_unauthenticated_gets_401_or_403(self):
        conn = _make_conn()

        app = main.app
        app.dependency_overrides[db.get_db] = lambda: conn
        client = TestClient(app, raise_server_exceptions=True)
        res = client.get("/admin/platform-health")
        app.dependency_overrides.clear()

        assert res.status_code in (401, 403)

    def test_response_shape(self):
        conn = _make_conn()
        _seed_company(conn)
        token = _create_platform_admin_session(conn)

        app = main.app
        app.dependency_overrides[db.get_db] = lambda: conn
        client = TestClient(app, raise_server_exceptions=True)
        res = client.get("/admin/platform-health", cookies={"tools_session": token})
        app.dependency_overrides.clear()

        assert res.status_code == 200
        body = res.json()
        for key in (
            "total_companies",
            "total_active_projects",
            "reports_filed_7d",
            "reports_filed_30d",
            "problem_projects",
            "company_rollup",
        ):
            assert key in body, f"missing key: {key}"
        assert isinstance(body["problem_projects"], list)
        assert isinstance(body["company_rollup"], list)

    def test_total_companies_count(self):
        conn = _make_conn()
        _seed_company(conn)
        _seed_company(conn)
        token = _create_platform_admin_session(conn)

        app = main.app
        app.dependency_overrides[db.get_db] = lambda: conn
        client = TestClient(app, raise_server_exceptions=True)
        res = client.get("/admin/platform-health", cookies={"tools_session": token})
        app.dependency_overrides.clear()

        assert res.json()["total_companies"] == 2

    def test_total_active_projects_count(self):
        conn = _make_conn()
        d = _seed_company(conn)
        # Add a second project to same company
        db.create_project(
            conn,
            company_id=d["company_id"],
            project_number="EXTRA-1",
            project_name="Extra Project",
            site_address="2 Side St",
            timezone="America/Chicago",
            rain_station_code="NRMN",
            created_by_user_id=d["admin_id"],
        )
        token = _create_platform_admin_session(conn)

        app = main.app
        app.dependency_overrides[db.get_db] = lambda: conn
        client = TestClient(app, raise_server_exceptions=True)
        res = client.get("/admin/platform-health", cookies={"tools_session": token})
        app.dependency_overrides.clear()

        # 2 non-archived projects
        assert res.json()["total_active_projects"] == 2

    def test_problem_projects_includes_failing(self):
        conn = _make_conn()
        d = _seed_company(conn)
        conn.execute(
            "UPDATE projects SET last_run_status='failed' WHERE id=?",
            (d["project_id"],),
        )
        conn.commit()
        token = _create_platform_admin_session(conn)

        app = main.app
        app.dependency_overrides[db.get_db] = lambda: conn
        client = TestClient(app, raise_server_exceptions=True)
        res = client.get("/admin/platform-health", cookies={"tools_session": token})
        app.dependency_overrides.clear()

        problems = res.json()["problem_projects"]
        project_ids = [p["project_id"] for p in problems]
        assert d["project_id"] in project_ids
        red = next(p for p in problems if p["project_id"] == d["project_id"])
        assert red["health_flag"] == "red"
        assert red["status_reason"] == "Failing (auto-weekly)"

    def test_problem_projects_excludes_healthy(self):
        conn = _make_conn()
        d = _seed_company(conn)
        # Keep project healthy: status=active, no failure
        conn.execute(
            "UPDATE projects SET last_run_status='ok', last_successful_run_at=? WHERE id=?",
            (date.today().isoformat(), d["project_id"]),
        )
        conn.commit()
        token = _create_platform_admin_session(conn)

        app = main.app
        app.dependency_overrides[db.get_db] = lambda: conn
        client = TestClient(app, raise_server_exceptions=True)
        res = client.get("/admin/platform-health", cookies={"tools_session": token})
        app.dependency_overrides.clear()

        project_ids = [p["project_id"] for p in res.json()["problem_projects"]]
        assert d["project_id"] not in project_ids

    def test_problem_projects_cross_company(self):
        conn = _make_conn()
        d1 = _seed_company(conn)
        d2 = _seed_company(conn)
        # Make both projects fail
        for pid in (d1["project_id"], d2["project_id"]):
            conn.execute(
                "UPDATE projects SET last_run_status='failed' WHERE id=?", (pid,)
            )
        conn.commit()
        token = _create_platform_admin_session(conn)

        app = main.app
        app.dependency_overrides[db.get_db] = lambda: conn
        client = TestClient(app, raise_server_exceptions=True)
        res = client.get("/admin/platform-health", cookies={"tools_session": token})
        app.dependency_overrides.clear()

        project_ids = [p["project_id"] for p in res.json()["problem_projects"]]
        assert d1["project_id"] in project_ids
        assert d2["project_id"] in project_ids

    def test_problem_projects_excludes_archived(self):
        conn = _make_conn()
        d = _seed_company(conn)
        conn.execute(
            "UPDATE projects SET status='archived', last_run_status='failed' WHERE id=?",
            (d["project_id"],),
        )
        conn.commit()
        token = _create_platform_admin_session(conn)

        app = main.app
        app.dependency_overrides[db.get_db] = lambda: conn
        client = TestClient(app, raise_server_exceptions=True)
        res = client.get("/admin/platform-health", cookies={"tools_session": token})
        app.dependency_overrides.clear()

        project_ids = [p["project_id"] for p in res.json()["problem_projects"]]
        assert d["project_id"] not in project_ids

    def test_problem_projects_setup_incomplete_is_yellow(self):
        conn = _make_conn()
        d = _seed_company(conn)
        conn.execute(
            "UPDATE projects SET status='setup_incomplete', auto_weekly_enabled=0 WHERE id=?",
            (d["project_id"],),
        )
        conn.commit()
        token = _create_platform_admin_session(conn)

        app = main.app
        app.dependency_overrides[db.get_db] = lambda: conn
        client = TestClient(app, raise_server_exceptions=True)
        res = client.get("/admin/platform-health", cookies={"tools_session": token})
        app.dependency_overrides.clear()

        problems = res.json()["problem_projects"]
        project_ids = [p["project_id"] for p in problems]
        assert d["project_id"] in project_ids
        yellow = next(p for p in problems if p["project_id"] == d["project_id"])
        assert yellow["health_flag"] == "yellow"
        assert yellow["status_reason"] == "Setup incomplete"

    def test_company_rollup_all_companies_present(self):
        conn = _make_conn()
        d1 = _seed_company(conn)
        d2 = _seed_company(conn)
        token = _create_platform_admin_session(conn)

        app = main.app
        app.dependency_overrides[db.get_db] = lambda: conn
        client = TestClient(app, raise_server_exceptions=True)
        res = client.get("/admin/platform-health", cookies={"tools_session": token})
        app.dependency_overrides.clear()

        rollup_ids = [c["id"] for c in res.json()["company_rollup"]]
        assert d1["company_id"] in rollup_ids
        assert d2["company_id"] in rollup_ids

    def test_company_rollup_counts_correct(self):
        conn = _make_conn()
        d = _seed_company(conn)
        # Add a paused project
        paused_id = db.create_project(
            conn,
            company_id=d["company_id"],
            project_number="PAU-1",
            project_name="Paused Project",
            site_address="3 St",
            timezone="America/Chicago",
            rain_station_code="NRMN",
            created_by_user_id=d["admin_id"],
        )
        conn.execute("UPDATE projects SET status='paused' WHERE id=?", (paused_id,))
        # Mark original project as failing
        conn.execute(
            "UPDATE projects SET last_run_status='failed' WHERE id=?",
            (d["project_id"],),
        )
        conn.commit()
        token = _create_platform_admin_session(conn)

        app = main.app
        app.dependency_overrides[db.get_db] = lambda: conn
        client = TestClient(app, raise_server_exceptions=True)
        res = client.get("/admin/platform-health", cookies={"tools_session": token})
        app.dependency_overrides.clear()

        rollup = res.json()["company_rollup"]
        row = next(c for c in rollup if c["id"] == d["company_id"])
        assert row["total_projects"] == 2
        assert row["active"] == 1
        assert row["failing"] == 1
        assert row["paused"] == 1

    def test_reports_filed_7d_counts_correctly(self):
        conn = _make_conn()
        d = _seed_company(conn)
        today = date.today().isoformat()
        old_date = (date.today() - timedelta(days=10)).isoformat()
        # Recent entry: should count
        db.create_project_run_log(
            conn,
            project_id=d["project_id"],
            run_date=today,
            status="ok",
            error_type=None,
            error_message=None,
            reports_filed=3,
            duration_ms=None,
        )
        # Old entry: should NOT count in 7d
        db.create_project_run_log(
            conn,
            project_id=d["project_id"],
            run_date=old_date,
            status="ok",
            error_type=None,
            error_message=None,
            reports_filed=5,
            duration_ms=None,
        )
        # Force created_at timestamps so 7-day filter works
        conn.execute(
            "UPDATE project_run_log SET created_at=? WHERE run_date=?",
            (today + "T12:00:00", today),
        )
        conn.execute(
            "UPDATE project_run_log SET created_at=? WHERE run_date=?",
            (old_date + "T12:00:00", old_date),
        )
        conn.commit()
        token = _create_platform_admin_session(conn)

        app = main.app
        app.dependency_overrides[db.get_db] = lambda: conn
        client = TestClient(app, raise_server_exceptions=True)
        res = client.get("/admin/platform-health", cookies={"tools_session": token})
        app.dependency_overrides.clear()

        body = res.json()
        assert body["reports_filed_7d"] == 3
        assert body["reports_filed_30d"] == 8  # 3 + 5

    def test_last_run_at_populated(self):
        conn = _make_conn()
        d = _seed_company(conn)
        today = date.today().isoformat()
        db.create_project_run_log(
            conn,
            project_id=d["project_id"],
            run_date=today,
            status="ok",
            error_type=None,
            error_message=None,
            reports_filed=1,
            duration_ms=None,
        )
        conn.commit()
        token = _create_platform_admin_session(conn)

        app = main.app
        app.dependency_overrides[db.get_db] = lambda: conn
        client = TestClient(app, raise_server_exceptions=True)
        res = client.get("/admin/platform-health", cookies={"tools_session": token})
        app.dependency_overrides.clear()

        assert res.json()["last_run_at"] is not None
