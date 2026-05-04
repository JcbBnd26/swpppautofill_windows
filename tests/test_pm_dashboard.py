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
            for key in (
                "total_projects",
                "active",
                "failing",
                "paused",
                "setup_incomplete",
                "recent_failures",
            ):
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


# ── TestCompanyDashboardOverview (IR-9) ───────────────────────────────────


class TestCompanyDashboardOverview:
    """Tests for IR-9: Dashboard as Projects Overview - new fields."""

    def test_response_includes_new_fields(self):
        """Confirm all four new IR-9 fields are present in the response."""
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
            assert "reports_filed_this_week" in body
            assert "recent_activity" in body
            assert "upcoming_this_week" in body
            assert "templates_due_for_review" in body
            assert isinstance(body["reports_filed_this_week"], int)
            assert isinstance(body["recent_activity"], list)
            assert isinstance(body["upcoming_this_week"], list)
            assert isinstance(body["templates_due_for_review"], list)
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_reports_filed_this_week_counts_correctly(self):
        """Reports filed this week are counted based on created_at >= Monday 00:00 in company timezone."""
        conn = _make_conn()
        data = _seed(conn)

        # Seed mailbox entries: one from this week, one from last week
        from datetime import datetime, timezone, timedelta

        # This week entry (use current date/time)
        this_week = datetime.now(timezone.utc).isoformat()
        db.create_mailbox_entry(
            conn,
            project_id=data["project_id"],
            company_id=data["company_id"],
            report_date="2026-05-03",
            report_type="auto_weekly",
            file_path="report_this_week.pdf",
        )
        # Set created_at to this week
        conn.execute(
            "UPDATE mailbox_entries SET created_at=? WHERE report_date='2026-05-03'",
            (this_week,),
        )

        # Last week entry
        last_week = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        db.create_mailbox_entry(
            conn,
            project_id=data["project_id"],
            company_id=data["company_id"],
            report_date="2026-04-20",
            report_type="auto_weekly",
            file_path="report_last_week.pdf",
        )
        conn.execute(
            "UPDATE mailbox_entries SET created_at=? WHERE report_date='2026-04-20'",
            (last_week,),
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
            # Should only count the one from this week
            assert body["reports_filed_this_week"] >= 1
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_recent_activity_returns_max_10(self):
        """Recent activity is capped at 10 entries even if more exist."""
        conn = _make_conn()
        data = _seed(conn)

        # Seed 15 mailbox entries
        for i in range(15):
            db.create_mailbox_entry(
                conn,
                project_id=data["project_id"],
                company_id=data["company_id"],
                report_date=f"2026-05-{str(i+1).zfill(2)}",
                report_type="auto_weekly",
                file_path=f"report_{i}.pdf",
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
            assert len(body["recent_activity"]) == 10
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_recent_activity_ordered_newest_first(self):
        """Recent activity is ordered by created_at DESC."""
        conn = _make_conn()
        data = _seed(conn)

        # Seed 3 mailbox entries with different timestamps
        db.create_mailbox_entry(
            conn,
            project_id=data["project_id"],
            company_id=data["company_id"],
            report_date="2026-05-01",
            report_type="auto_weekly",
            file_path="report1.pdf",
        )
        db.create_mailbox_entry(
            conn,
            project_id=data["project_id"],
            company_id=data["company_id"],
            report_date="2026-05-03",
            report_type="auto_weekly",
            file_path="report3.pdf",
        )
        db.create_mailbox_entry(
            conn,
            project_id=data["project_id"],
            company_id=data["company_id"],
            report_date="2026-05-02",
            report_type="auto_weekly",
            file_path="report2.pdf",
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
            activities = body["recent_activity"]
            assert len(activities) == 3
            # Verify DESC order by created_at (most recent first)
            for i in range(len(activities) - 1):
                assert activities[i]["created_at"] >= activities[i + 1]["created_at"]
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_upcoming_this_week_includes_due_projects(self):
        """Projects with schedule_day_of_week within next 7 days appear in upcoming_this_week."""
        conn = _make_conn()
        data = _seed(conn)

        # Get today's weekday (0=Monday, 6=Sunday)
        from datetime import datetime

        today = datetime.now()
        today_weekday = today.weekday()

        # Set project to be due in 2 days
        due_in_2_days = (today_weekday + 2) % 7
        conn.execute(
            "UPDATE projects SET schedule_day_of_week=? WHERE id=?",
            (due_in_2_days, data["project_id"]),
        )

        # Create another project due in 10 days (should not appear)
        suffix = str(uuid.uuid4())[:8]
        future_project_id = db.create_project(
            conn,
            company_id=data["company_id"],
            project_number=f"FUT{suffix}",
            project_name="Future Project",
            site_address="10 Main St",
            timezone="America/Chicago",
            rain_station_code="NRMN",
            created_by_user_id=data["admin_id"],
        )
        due_in_10_days = (today_weekday - 3) % 7  # This will be more than 7 days away
        conn.execute(
            "UPDATE projects SET auto_weekly_enabled=1, status='active', schedule_day_of_week=? WHERE id=?",
            (due_in_10_days, future_project_id),
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
            upcoming = body["upcoming_this_week"]
            # The project due in 2 days should appear
            project_ids = [u["project_id"] for u in upcoming]
            assert data["project_id"] in project_ids
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_upcoming_excludes_paused_projects(self):
        """Projects with paused_until in the future are excluded from upcoming_this_week."""
        conn = _make_conn()
        data = _seed(conn)

        # Set paused_until to a future date
        conn.execute(
            "UPDATE projects SET paused_until='2026-12-31' WHERE id=?",
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
            upcoming = body["upcoming_this_week"]
            # The paused project should NOT appear
            project_ids = [u["project_id"] for u in upcoming]
            assert data["project_id"] not in project_ids
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_templates_due_for_review_respects_cadence(self):
        """Templates due for review respects monthly (30 days) and quarterly (90 days) thresholds."""
        conn = _make_conn()
        data = _seed(conn)

        # Set the project to quarterly review, last reviewed 95 days ago (overdue)
        from datetime import datetime, timedelta, timezone

        old_review = (datetime.now(timezone.utc) - timedelta(days=95)).isoformat()
        conn.execute(
            "UPDATE projects SET template_review_cadence='quarterly', template_last_reviewed_at=? WHERE id=?",
            (old_review, data["project_id"]),
        )

        # Create another project with quarterly review, last reviewed 30 days ago (NOT overdue)
        suffix = str(uuid.uuid4())[:8]
        recent_project_id = db.create_project(
            conn,
            company_id=data["company_id"],
            project_number=f"REC{suffix}",
            project_name="Recent Review Project",
            site_address="5 Main St",
            timezone="America/Chicago",
            rain_station_code="NRMN",
            created_by_user_id=data["admin_id"],
        )
        recent_review = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn.execute(
            "UPDATE projects SET template_review_cadence='quarterly', template_last_reviewed_at=?, status='active' WHERE id=?",
            (recent_review, recent_project_id),
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
            templates_due = body["templates_due_for_review"]
            project_ids = [t["project_id"] for t in templates_due]
            # The 95-day-old project should appear
            assert data["project_id"] in project_ids
            # The 30-day-old project should NOT appear
            assert recent_project_id not in project_ids
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_templates_never_cadence_excluded(self):
        """Projects with template_review_cadence='never' never appear in templates_due_for_review."""
        conn = _make_conn()
        data = _seed(conn)

        # Set project to 'never' review cadence
        conn.execute(
            "UPDATE projects SET template_review_cadence='never', template_last_reviewed_at=NULL WHERE id=?",
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
            templates_due = body["templates_due_for_review"]
            project_ids = [t["project_id"] for t in templates_due]
            # The project with 'never' cadence should NOT appear
            assert data["project_id"] not in project_ids
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
        db.create_project_run_log(
            conn, project_id=data["project_id"], run_date="2026-04-01", status="ok"
        )
        db.create_project_run_log(
            conn, project_id=data["project_id"], run_date="2026-04-08", status="ok"
        )
        db.create_project_run_log(
            conn, project_id=data["project_id"], run_date="2026-04-15", status="failed"
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
        token = _create_company_member_session(
            conn, data["company_id"], role="company_admin"
        )
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
