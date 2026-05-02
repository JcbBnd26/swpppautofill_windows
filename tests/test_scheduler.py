from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from web.auth import db
from web.auth.main import app
from web.scheduler.run_due_reports import (
    _get_rain_event_dates,
    _get_scheduled_dates,
    run_due_reports,
)

# ── Shared in-memory DB helpers ──────────────────────────────────────────


def _make_conn(check_same_thread: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    for statement in db.SCHEMA_SQL.strip().split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.commit()
    return conn


def _seed(conn: sqlite3.Connection) -> dict[str, str]:
    """Create admin, company, project with auto_weekly_enabled=1 and status='active'."""
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
    # Enable auto_weekly, set status=active, start date in the past.
    conn.execute(
        """UPDATE projects SET auto_weekly_enabled=1, status='active',
           project_start_date=?, schedule_day_of_week=4, rain_threshold_inches=0.5
           WHERE id=?""",
        ("2026-04-01", project_id),
    )
    conn.commit()
    return {
        "admin_id": admin_id,
        "company_id": company_id,
        "project_id": project_id,
        "project_number": f"P{suffix}",
    }


def _add_template_version(conn: sqlite3.Connection, project_id: str, admin_id: str) -> str:
    """Add an active template version for a project.

    template_promote_mode defaults to 'auto', so create_template_version
    automatically promotes the new version — do NOT call promote_template_version
    manually or it will raise 'already active'.
    """
    template_data = {
        "project_number": "TEST-001",
        "job_piece": "1",
        "contract_id": "C-001",
        "inspection_type": "Weekly",
        "inspected_by": "Jane Inspector",
        "reviewed_by": "",
        "location_description_1": "Northbound lane",
        "location_description_2": "",
        "re_odot_contact_1": "Bob ODOT",
        "re_odot_contact_2": "",
        "checkboxes": {},
        "extra_fields": {},
    }
    version_id = db.create_template_version(
        conn, project_id=project_id, created_by_user_id=admin_id, template_data=template_data
    )
    return version_id


# ── TestProjectRunLogSchema ───────────────────────────────────────────────


class TestProjectRunLogSchema:
    def test_table_exists(self):
        conn = _make_conn()
        result = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='project_run_log'"
        ).fetchone()
        assert result is not None
        conn.close()

    def test_required_columns(self):
        conn = _make_conn()
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(project_run_log)").fetchall()]
        for col in ("id", "project_id", "run_date", "status", "error_type",
                    "error_message", "reports_filed", "duration_ms", "created_at"):
            assert col in cols, f"Missing column: {col}"
        conn.close()

    def test_index_exists(self):
        conn = _make_conn()
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_run_log_project'"
        ).fetchone()
        assert idx is not None
        conn.close()

    def test_reports_filed_default_zero(self):
        conn = _make_conn()
        data = _seed(conn)
        entry_id = db.create_project_run_log(
            conn, project_id=data["project_id"], run_date="2026-05-01", status="ok"
        )
        row = conn.execute(
            "SELECT reports_filed FROM project_run_log WHERE id=?", (entry_id,)
        ).fetchone()
        assert row["reports_filed"] == 0
        conn.close()


# ── TestProjectRunLogCRUD ─────────────────────────────────────────────────


class TestProjectRunLogCRUD:
    def test_create_run_log(self):
        conn = _make_conn()
        data = _seed(conn)
        entry_id = db.create_project_run_log(
            conn,
            project_id=data["project_id"],
            run_date="2026-05-01",
            status="ok",
            reports_filed=3,
            duration_ms=420,
        )
        assert entry_id is not None
        row = conn.execute(
            "SELECT * FROM project_run_log WHERE id=?", (entry_id,)
        ).fetchone()
        assert row["status"] == "ok"
        assert row["reports_filed"] == 3
        assert row["duration_ms"] == 420
        conn.close()

    def test_create_run_log_with_error(self):
        conn = _make_conn()
        data = _seed(conn)
        entry_id = db.create_project_run_log(
            conn,
            project_id=data["project_id"],
            run_date="2026-05-01",
            status="failed",
            error_type="ValueError",
            error_message="something exploded",
        )
        row = conn.execute(
            "SELECT * FROM project_run_log WHERE id=?", (entry_id,)
        ).fetchone()
        assert row["status"] == "failed"
        assert row["error_type"] == "ValueError"
        assert row["error_message"] == "something exploded"
        conn.close()

    def test_get_project_run_log_empty(self):
        conn = _make_conn()
        data = _seed(conn)
        rows = db.get_project_run_log(conn, data["project_id"])
        assert rows == []
        conn.close()

    def test_get_project_run_log_ordered(self):
        conn = _make_conn()
        data = _seed(conn)
        db.create_project_run_log(conn, data["project_id"], "2026-04-01", "ok")
        db.create_project_run_log(conn, data["project_id"], "2026-05-01", "ok")
        db.create_project_run_log(conn, data["project_id"], "2026-03-01", "failed")
        rows = db.get_project_run_log(conn, data["project_id"])
        dates = [r["run_date"] for r in rows]
        assert dates == sorted(dates, reverse=True)
        conn.close()

    def test_get_project_run_log_limit(self):
        conn = _make_conn()
        data = _seed(conn)
        for i in range(35):
            db.create_project_run_log(
                conn, data["project_id"], f"2026-{(i % 12) + 1:02d}-01", "ok"
            )
        rows = db.get_project_run_log(conn, data["project_id"], limit=10)
        assert len(rows) == 10
        conn.close()

    def test_update_project_run_state_basic(self):
        conn = _make_conn()
        data = _seed(conn)
        db.update_project_run_state(conn, data["project_id"], "2026-05-01T06:00:00Z", "ok")
        row = conn.execute(
            "SELECT last_run_at, last_run_status FROM projects WHERE id=?",
            (data["project_id"],),
        ).fetchone()
        assert row["last_run_at"] == "2026-05-01T06:00:00Z"
        assert row["last_run_status"] == "ok"
        conn.close()

    def test_update_project_run_state_with_success_ts(self):
        conn = _make_conn()
        data = _seed(conn)
        db.update_project_run_state(
            conn,
            data["project_id"],
            "2026-05-01T06:00:00Z",
            "ok",
            last_successful_run_at="2026-05-01T06:00:00Z",
        )
        row = conn.execute(
            "SELECT last_successful_run_at FROM projects WHERE id=?",
            (data["project_id"],),
        ).fetchone()
        assert row["last_successful_run_at"] == "2026-05-01T06:00:00Z"
        conn.close()


# ── TestGetProjectsDueForRun ─────────────────────────────────────────────


class TestGetProjectsDueForRun:
    def test_returns_active_auto_enabled(self):
        conn = _make_conn()
        data = _seed(conn)
        rows = db.get_projects_due_for_run(conn)
        ids = [r["id"] for r in rows]
        assert data["project_id"] in ids
        conn.close()

    def test_excludes_disabled_project(self):
        conn = _make_conn()
        data = _seed(conn)
        conn.execute(
            "UPDATE projects SET auto_weekly_enabled=0 WHERE id=?", (data["project_id"],)
        )
        conn.commit()
        rows = db.get_projects_due_for_run(conn)
        ids = [r["id"] for r in rows]
        assert data["project_id"] not in ids
        conn.close()

    def test_excludes_inactive_project(self):
        conn = _make_conn()
        data = _seed(conn)
        conn.execute(
            "UPDATE projects SET status='archived' WHERE id=?", (data["project_id"],)
        )
        conn.commit()
        rows = db.get_projects_due_for_run(conn)
        ids = [r["id"] for r in rows]
        assert data["project_id"] not in ids
        conn.close()

    def test_excludes_expired_project(self):
        conn = _make_conn()
        data = _seed(conn)
        conn.execute(
            "UPDATE projects SET project_end_date='2025-01-01' WHERE id=?",
            (data["project_id"],),
        )
        conn.commit()
        rows = db.get_projects_due_for_run(conn)
        ids = [r["id"] for r in rows]
        assert data["project_id"] not in ids
        conn.close()

    def test_excludes_paused_project(self):
        conn = _make_conn()
        data = _seed(conn)
        # paused_until in the future
        future = (date.today() + timedelta(days=30)).isoformat()
        conn.execute(
            "UPDATE projects SET paused_until=? WHERE id=?",
            (future, data["project_id"]),
        )
        conn.commit()
        rows = db.get_projects_due_for_run(conn)
        ids = [r["id"] for r in rows]
        assert data["project_id"] not in ids
        conn.close()

    def test_includes_past_paused_expiry(self):
        conn = _make_conn()
        data = _seed(conn)
        # paused_until in the past — should be included
        past = (date.today() - timedelta(days=1)).isoformat()
        conn.execute(
            "UPDATE projects SET paused_until=? WHERE id=?",
            (past, data["project_id"]),
        )
        conn.commit()
        rows = db.get_projects_due_for_run(conn)
        ids = [r["id"] for r in rows]
        assert data["project_id"] in ids
        conn.close()


# ── TestReconciliationWeekly ─────────────────────────────────────────────


class TestReconciliationWeekly:
    """Unit tests for _get_scheduled_dates logic."""

    def _project(self, start_date: str, dow: int) -> dict[str, Any]:
        return {
            "id": "proj-1",
            "project_start_date": start_date,
            "schedule_day_of_week": dow,
        }

    def test_returns_empty_no_start_date(self):
        project = {"id": "p", "schedule_day_of_week": 4}
        result = _get_scheduled_dates(project, set(), date(2026, 5, 1))
        assert result == []

    def test_single_date_same_day(self):
        # start on a Friday (dow=4), today is Friday -> one date
        today = date(2026, 5, 1)  # May 1, 2026 is a Friday
        project = self._project("2026-05-01", 4)
        result = _get_scheduled_dates(project, set(), today)
        assert today in result

    def test_skips_filed_dates(self):
        today = date(2026, 5, 8)  # Friday
        project = self._project("2026-04-17", 4)  # Fridays
        filed = {"2026-04-25", "2026-05-01"}
        result = _get_scheduled_dates(project, filed, today)
        result_strs = {d.isoformat() for d in result}
        assert "2026-04-25" not in result_strs
        assert "2026-05-01" not in result_strs
        assert "2026-05-08" in result_strs

    def test_correct_day_of_week(self):
        # schedule_day_of_week=2 (Wednesday)
        today = date(2026, 5, 6)  # Wednesday
        project = self._project("2026-04-01", 2)
        result = _get_scheduled_dates(project, set(), today)
        for d in result:
            assert d.weekday() == 2, f"{d} is not a Wednesday"

    def test_no_future_dates(self):
        today = date(2026, 5, 1)
        project = self._project("2026-05-01", 4)
        result = _get_scheduled_dates(project, set(), today)
        for d in result:
            assert d <= today

    def test_all_filed_returns_empty(self):
        today = date(2026, 5, 8)  # Friday
        project = self._project("2026-05-01", 4)
        # File all Fridays
        filed = {"2026-05-01", "2026-05-08"}
        result = _get_scheduled_dates(project, filed, today)
        assert result == []

    def test_invalid_start_date_returns_empty(self):
        project = self._project("not-a-date", 4)
        result = _get_scheduled_dates(project, set(), date(2026, 5, 1))
        assert result == []


# ── TestReconciliationRainEvent ───────────────────────────────────────────


class TestReconciliationRainEvent:
    """Unit tests for _get_rain_event_dates logic."""

    def _project(self, station: str = "NRMN", threshold: float = 0.5) -> dict[str, Any]:
        return {
            "id": "proj-rain",
            "rain_station_code": station,
            "rain_threshold_inches": threshold,
        }

    def _rain_day(self, d: str, inches: float):
        from app.core.mesonet import RainDay

        return RainDay(date=date.fromisoformat(d), rainfall_inches=inches)

    def test_returns_empty_no_station(self):
        project = {"id": "p", "rain_station_code": "", "rain_threshold_inches": 0.5}
        result = _get_rain_event_dates(project, set(), date(2026, 5, 1))
        assert result == []

    def test_qualifying_rain_days_returned(self):
        from app.core.mesonet import FetchResult

        today = date(2026, 5, 1)
        mock_result = FetchResult(
            days=[
                self._rain_day("2026-04-28", 0.6),
                self._rain_day("2026-04-29", 0.1),
                self._rain_day("2026-04-30", 1.2),
            ],
            failed=0,
            missing=0,
        )
        with patch("app.core.mesonet.fetch_rainfall", return_value=mock_result):
            result = _get_rain_event_dates(self._project(), set(), today)

        result_strs = {d.isoformat() for d in result}
        assert "2026-04-28" in result_strs
        assert "2026-04-30" in result_strs
        assert "2026-04-29" not in result_strs  # below threshold

    def test_skips_filed_rain_dates(self):
        from app.core.mesonet import FetchResult

        today = date(2026, 5, 1)
        mock_result = FetchResult(
            days=[self._rain_day("2026-04-28", 0.8)],
            failed=0,
            missing=0,
        )
        with patch("app.core.mesonet.fetch_rainfall", return_value=mock_result):
            result = _get_rain_event_dates(self._project(), {"2026-04-28"}, today)
        assert result == []

    def test_missing_values_skipped(self):
        from app.core.mesonet import FetchResult

        today = date(2026, 5, 1)
        mock_result = FetchResult(
            days=[
                self._rain_day("2026-04-28", -999.0),  # missing sentinel
            ],
            failed=0,
            missing=1,
        )
        with patch("app.core.mesonet.fetch_rainfall", return_value=mock_result):
            result = _get_rain_event_dates(self._project(), set(), today)
        assert result == []

    def test_fetch_error_returns_empty(self):
        with patch("app.core.mesonet.fetch_rainfall", side_effect=RuntimeError("network error")):
            result = _get_rain_event_dates(self._project(), set(), date(2026, 5, 1))
        assert result == []


# ── TestConfirmationGate ─────────────────────────────────────────────────


class TestConfirmationGate:
    """Test the >10 missing dates safety gate."""

    def test_gate_blocks_large_backlog(self):
        conn = _make_conn()
        data = _seed(conn)
        # Start date 3 months ago → ~12 Fridays → exceeds gate
        start = (date.today() - timedelta(weeks=16)).isoformat()
        conn.execute(
            "UPDATE projects SET project_start_date=? WHERE id=?",
            (start, data["project_id"]),
        )
        conn.commit()

        with patch("app.core.mesonet.fetch_rainfall") as mock_fetch:
            from app.core.mesonet import FetchResult
            mock_fetch.return_value = FetchResult(days=[], failed=0, missing=0)
            result = run_due_reports(conn, dry_run=False, force=False)

        assert result["skipped"] >= 1
        assert result["reports_filed"] == 0
        conn.close()

    def test_force_overrides_gate(self):
        """With --force, gate is bypassed (will attempt generation but fails w/o real template)."""
        conn = _make_conn()
        data = _seed(conn)
        _add_template_version(conn, data["project_id"], data["admin_id"])
        start = (date.today() - timedelta(weeks=16)).isoformat()
        conn.execute(
            "UPDATE projects SET project_start_date=? WHERE id=?",
            (start, data["project_id"]),
        )
        conn.commit()

        with patch("app.core.mesonet.fetch_rainfall") as mock_fetch, \
             patch("web.scheduler.run_due_reports._generate_pdf_for_date", return_value=None):
            from app.core.mesonet import FetchResult
            mock_fetch.return_value = FetchResult(days=[], failed=0, missing=0)
            result = run_due_reports(conn, dry_run=False, force=True)

        # Gate bypassed → skipped==0
        assert result["skipped"] == 0
        conn.close()


# ── TestPerProjectIsolation ───────────────────────────────────────────────


class TestPerProjectIsolation:
    def test_one_project_failure_does_not_block_others(self):
        conn = _make_conn()
        data1 = _seed(conn)
        data2 = _seed(conn)

        for d in (data1, data2):
            _add_template_version(conn, d["project_id"], d["admin_id"])

        call_count = 0

        def fake_generate(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first project explodes")
            return None  # second project: generates nothing

        with patch("app.core.mesonet.fetch_rainfall") as mock_fetch, \
             patch("web.scheduler.run_due_reports._generate_pdf_for_date", side_effect=fake_generate):
            from app.core.mesonet import FetchResult
            mock_fetch.return_value = FetchResult(days=[], failed=0, missing=0)
            result = run_due_reports(conn, dry_run=False, force=True)

        # Both projects were attempted
        assert result["projects_processed"] == 2
        conn.close()


# ── TestRunSummary ────────────────────────────────────────────────────────


class TestRunSummary:
    def test_no_projects_returns_zeros(self):
        conn = _make_conn()
        result = run_due_reports(conn, dry_run=False)
        assert result == {
            "projects_processed": 0,
            "reports_filed": 0,
            "failures": 0,
            "skipped": 0,
        }
        conn.close()

    def test_project_without_template_counted_as_skipped(self):
        conn = _make_conn()
        data = _seed(conn)
        with patch("app.core.mesonet.fetch_rainfall") as mock_fetch:
            from app.core.mesonet import FetchResult
            mock_fetch.return_value = FetchResult(days=[], failed=0, missing=0)
            result = run_due_reports(conn, dry_run=False)
        # No template version → skipped
        assert result["projects_processed"] == 1
        assert result["skipped"] == 1
        conn.close()

    def test_dry_run_counts_filed_not_written(self):
        conn = _make_conn()
        data = _seed(conn)
        _add_template_version(conn, data["project_id"], data["admin_id"])
        # One week back so there's exactly 1 Friday due
        start = (date.today() - timedelta(days=6)).isoformat()
        dow = date.today().weekday()  # schedule today's dow so there's ≤1 date
        conn.execute(
            "UPDATE projects SET project_start_date=?, schedule_day_of_week=? WHERE id=?",
            (start, dow, data["project_id"]),
        )
        conn.commit()

        with patch("app.core.mesonet.fetch_rainfall") as mock_fetch:
            from app.core.mesonet import FetchResult
            mock_fetch.return_value = FetchResult(days=[], failed=0, missing=0)
            result = run_due_reports(conn, dry_run=True)

        # dry_run reports "filed" without touching the filesystem
        assert result["failures"] == 0
        # No real mailbox entries created
        entries = conn.execute("SELECT id FROM mailbox_entries").fetchall()
        assert len(entries) == 0
        conn.close()

    def test_summary_keys_present(self):
        conn = _make_conn()
        result = run_due_reports(conn, dry_run=False)
        for key in ("projects_processed", "reports_filed", "failures", "skipped"):
            assert key in result
        conn.close()


# ── TestAdminRunEndpoint ──────────────────────────────────────────────────


def _make_platform_admin_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    for statement in db.SCHEMA_SQL.strip().split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.commit()
    return conn


def _create_platform_admin_session(conn: sqlite3.Connection) -> str:
    """Create a platform admin user and a valid session token."""
    user_id = db.create_user(conn, display_name="Platform Admin", is_admin=True)
    conn.execute(
        "UPDATE users SET is_platform_admin=1 WHERE id=?", (user_id,)
    )
    conn.commit()
    token = db.create_session(conn, user_id=user_id)
    return token


class TestAdminRunEndpoint:
    def test_requires_platform_admin(self):
        conn = _make_platform_admin_conn()
        client = TestClient(app)

        def override_get_db():
            yield conn

        app.dependency_overrides[db.get_db] = override_get_db
        try:
            # No session cookie → 401/403
            response = client.post(
                "/admin/run-due-reports",
                json={"force": False},
            )
            assert response.status_code in (401, 403)
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_platform_admin_can_trigger(self):
        conn = _make_platform_admin_conn()
        token = _create_platform_admin_session(conn)
        client = TestClient(app)

        def override_get_db():
            yield conn

        app.dependency_overrides[db.get_db] = override_get_db
        try:
            with patch("web.scheduler.run_due_reports.run_due_reports") as mock_run:
                mock_run.return_value = {
                    "projects_processed": 0,
                    "reports_filed": 0,
                    "failures": 0,
                    "skipped": 0,
                }
                response = client.post(
                    "/admin/run-due-reports",
                    json={"force": False},
                    cookies={"tools_session": token},
                )
            assert response.status_code == 200
            body = response.json()
            assert "projects_processed" in body
            assert "duration_ms" in body
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_force_flag_passed_through(self):
        conn = _make_platform_admin_conn()
        token = _create_platform_admin_session(conn)
        client = TestClient(app)

        def override_get_db():
            yield conn

        app.dependency_overrides[db.get_db] = override_get_db
        try:
            with patch("web.scheduler.run_due_reports.run_due_reports") as mock_run:
                mock_run.return_value = {
                    "projects_processed": 0,
                    "reports_filed": 0,
                    "failures": 0,
                    "skipped": 0,
                }
                client.post(
                    "/admin/run-due-reports",
                    json={"force": True},
                    cookies={"tools_session": token},
                )
                # Verify force=True was forwarded to the engine
                _, kwargs = mock_run.call_args
                assert kwargs.get("force") is True
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_response_schema(self):
        conn = _make_platform_admin_conn()
        token = _create_platform_admin_session(conn)
        client = TestClient(app)

        def override_get_db():
            yield conn

        app.dependency_overrides[db.get_db] = override_get_db
        try:
            with patch("web.scheduler.run_due_reports.run_due_reports") as mock_run:
                mock_run.return_value = {
                    "projects_processed": 2,
                    "reports_filed": 5,
                    "failures": 0,
                    "skipped": 1,
                }
                response = client.post(
                    "/admin/run-due-reports",
                    json={"force": False},
                    cookies={"tools_session": token},
                )
            body = response.json()
            assert body["projects_processed"] == 2
            assert body["reports_filed"] == 5
            assert body["failures"] == 0
            assert body["skipped"] == 1
            assert isinstance(body["duration_ms"], int)
        finally:
            app.dependency_overrides.clear()
            conn.close()


# ── TestDryRun ────────────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_no_db_writes_for_mailbox(self):
        conn = _make_conn()
        data = _seed(conn)
        _add_template_version(conn, data["project_id"], data["admin_id"])
        conn.execute(
            "UPDATE projects SET project_start_date=?, schedule_day_of_week=? WHERE id=?",
            ((date.today() - timedelta(days=6)).isoformat(), date.today().weekday(), data["project_id"]),
        )
        conn.commit()

        with patch("app.core.mesonet.fetch_rainfall") as mock_fetch:
            from app.core.mesonet import FetchResult
            mock_fetch.return_value = FetchResult(days=[], failed=0, missing=0)
            run_due_reports(conn, dry_run=True)

        entries = conn.execute("SELECT id FROM mailbox_entries").fetchall()
        assert len(entries) == 0
        conn.close()

    def test_dry_run_still_writes_run_log(self):
        """Even in dry_run, we log the run (as 'ok' or 'skipped')."""
        conn = _make_conn()
        data = _seed(conn)
        _add_template_version(conn, data["project_id"], data["admin_id"])
        conn.execute(
            "UPDATE projects SET project_start_date=?, schedule_day_of_week=? WHERE id=?",
            ((date.today() - timedelta(days=6)).isoformat(), date.today().weekday(), data["project_id"]),
        )
        conn.commit()

        with patch("app.core.mesonet.fetch_rainfall") as mock_fetch:
            from app.core.mesonet import FetchResult
            mock_fetch.return_value = FetchResult(days=[], failed=0, missing=0)
            run_due_reports(conn, dry_run=True)

        run_logs = db.get_project_run_log(conn, data["project_id"])
        assert len(run_logs) >= 1
        conn.close()


# ── TestHeartbeat ─────────────────────────────────────────────────────────


class TestHeartbeat:
    def test_heartbeat_sent_when_env_set(self, monkeypatch):
        monkeypatch.setenv("HEALTHCHECKS_URL", "https://hc-ping.example.com/uuid")
        conn = _make_conn()

        with patch("requests.get") as mock_get:
            run_due_reports(conn, dry_run=False)
            mock_get.assert_called_once()
            called_url = mock_get.call_args[0][0]
            assert "hc-ping.example.com" in called_url
        conn.close()

    def test_heartbeat_not_sent_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("HEALTHCHECKS_URL", raising=False)
        conn = _make_conn()

        with patch("requests.get") as mock_get:
            run_due_reports(conn, dry_run=False)
            mock_get.assert_not_called()
        conn.close()

    def test_heartbeat_failure_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("HEALTHCHECKS_URL", "https://hc-ping.example.com/uuid")
        conn = _make_conn()

        with patch("requests.get", side_effect=RuntimeError("timeout")):
            # Must not raise
            run_due_reports(conn, dry_run=False)
        conn.close()
