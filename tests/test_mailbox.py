from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from web.auth import db
from web.auth.main import app

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def db_conn():
    """Fresh in-memory database for each test."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    db.SCHEMA_SQL_LINES = db.SCHEMA_SQL.strip().split(";")
    for statement in db.SCHEMA_SQL_LINES:
        if statement.strip():
            conn.execute(statement)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def setup_mailbox_data(db_conn):
    """Create test data for mailbox tests."""
    suffix = str(uuid.uuid4())[:8]

    # Create admin user
    admin_id = db.create_user(db_conn, display_name=f"Admin {suffix}", is_admin=True)

    # Create company
    company_id = db.create_company(
        db_conn,
        legal_name=f"Test Company {suffix}",
        display_name=f"Test Co {suffix}",
        created_by=admin_id,
    )

    # Create project
    project_id = db.create_project(
        db_conn,
        company_id=company_id,
        project_number=f"P{suffix}",
        project_name=f"Test Project {suffix}",
        site_address="123 Test St",
        timezone="America/Chicago",
        rain_station_code="MESONET_STATION",
        created_by_user_id=admin_id,
    )

    db_conn.commit()

    return {
        "admin_id": admin_id,
        "company_id": company_id,
        "project_id": project_id,
        "project_number": f"P{suffix}",
    }


# ── Schema Tests ────────────────────────────────────────────────────────


class TestMailboxSchema:
    """Test mailbox_entries table schema and indexes."""

    def test_table_exists(self, db_conn):
        """Verify mailbox_entries table exists."""
        result = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mailbox_entries'"
        ).fetchone()
        assert result is not None

    def test_table_columns(self, db_conn):
        """Verify all required columns exist."""
        columns = db_conn.execute("PRAGMA table_info(mailbox_entries)").fetchall()
        column_names = [col["name"] for col in columns]

        expected_columns = [
            "id",
            "project_id",
            "company_id",
            "report_date",
            "report_type",
            "generation_mode",
            "file_path",
            "file_size_bytes",
            "template_version_id",
            "rain_data_json",
            "created_at",
        ]

        for col in expected_columns:
            assert col in column_names, f"Missing column: {col}"

    def test_foreign_keys(self, db_conn):
        """Verify foreign key constraints."""
        fks = db_conn.execute("PRAGMA foreign_key_list(mailbox_entries)").fetchall()

        # Should have 3 foreign keys: project_id, company_id, template_version_id
        assert len(fks) >= 2, "Missing foreign key constraints"

        fk_columns = [fk["from"] for fk in fks]
        assert "project_id" in fk_columns
        assert "company_id" in fk_columns

    def test_index_exists(self, db_conn):
        """Verify idx_mailbox_project index exists."""
        indexes = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_mailbox_project'"
        ).fetchall()
        assert len(indexes) == 1


# ── CRUD Function Tests ─────────────────────────────────────────────────


class TestMailboxCRUD:
    """Test mailbox database CRUD operations."""

    def test_create_mailbox_entry(self, db_conn, setup_mailbox_data):
        """Create a mailbox entry."""
        data = setup_mailbox_data

        entry_id = db.create_mailbox_entry(
            db_conn,
            project_id=data["project_id"],
            company_id=data["company_id"],
            report_date="2026-05-01",
            report_type="auto_weekly",
            file_path="company123/project456/2026-05-01.pdf",
            file_size_bytes=123456,
        )

        assert entry_id is not None

        # Verify entry exists
        entry = db_conn.execute(
            "SELECT * FROM mailbox_entries WHERE id = ?", (entry_id,)
        ).fetchone()

        assert entry is not None
        assert entry["project_id"] == data["project_id"]
        assert entry["company_id"] == data["company_id"]
        assert entry["report_date"] == "2026-05-01"
        assert entry["report_type"] == "auto_weekly"
        assert entry["file_size_bytes"] == 123456

    def test_get_mailbox_entry(self, db_conn, setup_mailbox_data):
        """Get a single mailbox entry."""
        data = setup_mailbox_data

        entry_id = db.create_mailbox_entry(
            db_conn,
            project_id=data["project_id"],
            company_id=data["company_id"],
            report_date="2026-05-01",
            report_type="auto_weekly",
            file_path="test.pdf",
        )

        entry = db.get_mailbox_entry(db_conn, entry_id)

        assert entry is not None
        assert entry["id"] == entry_id
        assert entry["report_date"] == "2026-05-01"

    def test_get_mailbox_entry_not_found(self, db_conn):
        """Get non-existent entry returns None."""
        entry = db.get_mailbox_entry(db_conn, "nonexistent")
        assert entry is None

    def test_get_mailbox_entries_empty(self, db_conn, setup_mailbox_data):
        """Get entries for project with no entries."""
        data = setup_mailbox_data
        entries = db.get_mailbox_entries(db_conn, data["project_id"])
        assert entries == []

    def test_get_mailbox_entries_multiple(self, db_conn, setup_mailbox_data):
        """Get multiple entries sorted by date."""
        data = setup_mailbox_data

        # Create entries in random order
        db.create_mailbox_entry(
            db_conn,
            project_id=data["project_id"],
            company_id=data["company_id"],
            report_date="2026-05-15",
            report_type="auto_weekly",
            file_path="test3.pdf",
        )
        db.create_mailbox_entry(
            db_conn,
            project_id=data["project_id"],
            company_id=data["company_id"],
            report_date="2026-05-01",
            report_type="auto_weekly",
            file_path="test1.pdf",
        )
        db.create_mailbox_entry(
            db_conn,
            project_id=data["project_id"],
            company_id=data["company_id"],
            report_date="2026-05-08",
            report_type="auto_weekly",
            file_path="test2.pdf",
        )

        entries = db.get_mailbox_entries(db_conn, data["project_id"], sort_order="desc")

        assert len(entries) == 3
        # Should be sorted newest first
        assert entries[0]["report_date"] == "2026-05-15"
        assert entries[1]["report_date"] == "2026-05-08"
        assert entries[2]["report_date"] == "2026-05-01"

    def test_get_mailbox_entries_sort_asc(self, db_conn, setup_mailbox_data):
        """Get entries sorted ascending."""
        data = setup_mailbox_data

        db.create_mailbox_entry(
            db_conn,
            project_id=data["project_id"],
            company_id=data["company_id"],
            report_date="2026-05-15",
            report_type="auto_weekly",
            file_path="test2.pdf",
        )
        db.create_mailbox_entry(
            db_conn,
            project_id=data["project_id"],
            company_id=data["company_id"],
            report_date="2026-05-01",
            report_type="auto_weekly",
            file_path="test1.pdf",
        )

        entries = db.get_mailbox_entries(db_conn, data["project_id"], sort_order="asc")

        assert len(entries) == 2
        assert entries[0]["report_date"] == "2026-05-01"
        assert entries[1]["report_date"] == "2026-05-15"

    def test_get_mailbox_entry_count(self, db_conn, setup_mailbox_data):
        """Get count of mailbox entries."""
        data = setup_mailbox_data

        # Initially zero
        count = db.get_mailbox_entry_count(db_conn, data["project_id"])
        assert count == 0

        # Create entries
        db.create_mailbox_entry(
            db_conn,
            project_id=data["project_id"],
            company_id=data["company_id"],
            report_date="2026-05-01",
            report_type="auto_weekly",
            file_path="test1.pdf",
        )
        db.create_mailbox_entry(
            db_conn,
            project_id=data["project_id"],
            company_id=data["company_id"],
            report_date="2026-05-08",
            report_type="auto_weekly",
            file_path="test2.pdf",
        )

        count = db.get_mailbox_entry_count(db_conn, data["project_id"])
        assert count == 2


# ── Endpoint Tests ──────────────────────────────────────────────────────


class TestMailboxEndpoints:
    """Test public mailbox API endpoints (no auth required)."""

    def test_get_mailbox_project_not_found(self):
        """GET /mailbox/{project_number} - project not found."""
        client = TestClient(app)

        # Create in-memory DB for this test
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        for statement in db.SCHEMA_SQL.strip().split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.commit()

        def override_get_db():
            yield conn

        app.dependency_overrides[db.get_db] = override_get_db

        try:
            response = client.get("/mailbox/NONEXISTENT")
            assert response.status_code == 404
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_get_mailbox_project_no_entries(self, db_conn, setup_mailbox_data):
        """GET /mailbox/{project_number} - project with no entries."""
        data = setup_mailbox_data
        client = TestClient(app)

        # Create new connection with check_same_thread=False for TestClient
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        for statement in db.SCHEMA_SQL.strip().split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.commit()

        # Recreate test data in the new connection
        suffix = str(uuid.uuid4())[:8]
        admin_id = db.create_user(conn, display_name=f"Admin {suffix}", is_admin=True)
        company_id = db.create_company(
            conn,
            legal_name=f"Test Company {suffix}",
            display_name=f"Test Co {suffix}",
            created_by=admin_id,
        )
        project_id = db.create_project(
            conn,
            company_id=company_id,
            project_number=f"P{suffix}",
            project_name=f"Test Project {suffix}",
            site_address="123 Test St",
            timezone="America/Chicago",
            rain_station_code="MESONET_STATION",
            created_by_user_id=admin_id,
        )
        conn.commit()

        def override_get_db():
            yield conn

        app.dependency_overrides[db.get_db] = override_get_db

        try:
            response = client.get(f"/mailbox/P{suffix}")
            assert response.status_code == 200

            body = response.json()
            assert body["project_number"] == f"P{suffix}"
            assert body["entry_count"] == 0
            assert body["entries"] == []
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_get_mailbox_project_with_entries(self, db_conn, setup_mailbox_data):
        """GET /mailbox/{project_number} - project with entries."""
        data = setup_mailbox_data

        # Create new connection with check_same_thread=False for TestClient
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        for statement in db.SCHEMA_SQL.strip().split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.commit()

        # Recreate test data in the new connection
        suffix = str(uuid.uuid4())[:8]
        admin_id = db.create_user(conn, display_name=f"Admin {suffix}", is_admin=True)
        company_id = db.create_company(
            conn,
            legal_name=f"Test Company {suffix}",
            display_name=f"Test Co {suffix}",
            created_by=admin_id,
        )
        project_id = db.create_project(
            conn,
            company_id=company_id,
            project_number=f"P{suffix}",
            project_name=f"Test Project {suffix}",
            site_address="123 Test St",
            timezone="America/Chicago",
            rain_station_code="MESONET_STATION",
            created_by_user_id=admin_id,
        )

        # Create entries
        db.create_mailbox_entry(
            conn,
            project_id=project_id,
            company_id=company_id,
            report_date="2026-05-01",
            report_type="auto_weekly",
            file_path="test.pdf",
            file_size_bytes=123456,
        )
        conn.commit()

        client = TestClient(app)

        def override_get_db():
            yield conn

        app.dependency_overrides[db.get_db] = override_get_db

        try:
            response = client.get(f"/mailbox/P{suffix}")
            assert response.status_code == 200

            body = response.json()
            assert body["project_number"] == f"P{suffix}"
            assert body["entry_count"] == 1
            assert len(body["entries"]) == 1
            assert body["entries"][0]["report_date"] == "2026-05-01"
            assert body["entries"][0]["report_type"] == "auto_weekly"
            assert body["entries"][0]["file_size_bytes"] == 123456
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_download_mailbox_entry_not_found(self, db_conn, setup_mailbox_data):
        """GET /mailbox/{project_number}/download/{entry_id} - entry not found."""
        data = setup_mailbox_data

        # Create new connection with check_same_thread=False for TestClient
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        for statement in db.SCHEMA_SQL.strip().split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.commit()

        # Recreate test data in the new connection
        suffix = str(uuid.uuid4())[:8]
        admin_id = db.create_user(conn, display_name=f"Admin {suffix}", is_admin=True)
        company_id = db.create_company(
            conn,
            legal_name=f"Test Company {suffix}",
            display_name=f"Test Co {suffix}",
            created_by=admin_id,
        )
        project_id = db.create_project(
            conn,
            company_id=company_id,
            project_number=f"P{suffix}",
            project_name=f"Test Project {suffix}",
            site_address="123 Test St",
            timezone="America/Chicago",
            rain_station_code="MESONET_STATION",
            created_by_user_id=admin_id,
        )
        conn.commit()

        client = TestClient(app)

        def override_get_db():
            yield conn

        app.dependency_overrides[db.get_db] = override_get_db

        try:
            response = client.get(f"/mailbox/P{suffix}/download/nonexistent")
            assert response.status_code == 404
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_serve_mailbox_html(self):
        """GET /mailbox - serve HTML frontend."""
        client = TestClient(app)
        response = client.get("/mailbox")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert b"SWPPP Mailbox" in response.content

    def test_serve_not_endpoint_not_implemented(self):
        """GET /not - NOT endpoint not yet implemented."""
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row

        def override_get_db():
            yield conn

        client = TestClient(app)
        app.dependency_overrides[db.get_db] = override_get_db

        try:
            response = client.get("/not")
            assert response.status_code == 501
        finally:
            app.dependency_overrides.clear()
            conn.close()


# ── Security Tests ──────────────────────────────────────────────────────


class TestMailboxSecurity:
    """Test security and tenant isolation for mailbox."""

    def test_tenant_isolation_download(self, db_conn):
        """Verify entries cannot be accessed across projects."""
        # Create new connection with check_same_thread=False for TestClient
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        for statement in db.SCHEMA_SQL.strip().split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.commit()

        suffix1 = str(uuid.uuid4())[:8]
        suffix2 = str(uuid.uuid4())[:8]

        # Create two companies and projects
        admin_id = db.create_user(conn, display_name="Admin", is_admin=True)

        company1_id = db.create_company(
            conn,
            legal_name=f"Company1 {suffix1}",
            display_name="C1",
            created_by=admin_id,
        )
        project1_id = db.create_project(
            conn,
            company_id=company1_id,
            project_number=f"P{suffix1}",
            project_name="Project 1",
            site_address="Address 1",
            timezone="America/Chicago",
            rain_station_code="STATION1",
            created_by_user_id=admin_id,
        )

        company2_id = db.create_company(
            conn,
            legal_name=f"Company2 {suffix2}",
            display_name="C2",
            created_by=admin_id,
        )
        project2_id = db.create_project(
            conn,
            company_id=company2_id,
            project_number=f"P{suffix2}",
            project_name="Project 2",
            site_address="Address 2",
            timezone="America/Chicago",
            rain_station_code="STATION2",
            created_by_user_id=admin_id,
        )

        # Create entry for project 1
        entry1_id = db.create_mailbox_entry(
            conn,
            project_id=project1_id,
            company_id=company1_id,
            report_date="2026-05-01",
            report_type="auto_weekly",
            file_path="test1.pdf",
        )

        conn.commit()

        # Try to access project1's entry via project2's endpoint
        client = TestClient(app)

        def override_get_db():
            yield conn

        app.dependency_overrides[db.get_db] = override_get_db

        try:
            response = client.get(f"/mailbox/P{suffix2}/download/{entry1_id}")
            # Should return 404 because entry doesn't belong to project2
            assert response.status_code == 404
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_no_auth_required_for_mailbox(self, db_conn, setup_mailbox_data):
        """Verify mailbox endpoints work without authentication."""
        # Create new connection with check_same_thread=False for TestClient
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        for statement in db.SCHEMA_SQL.strip().split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.commit()

        # Recreate test data in the new connection
        suffix = str(uuid.uuid4())[:8]
        admin_id = db.create_user(conn, display_name=f"Admin {suffix}", is_admin=True)
        company_id = db.create_company(
            conn,
            legal_name=f"Test Company {suffix}",
            display_name=f"Test Co {suffix}",
            created_by=admin_id,
        )
        project_id = db.create_project(
            conn,
            company_id=company_id,
            project_number=f"P{suffix}",
            project_name=f"Test Project {suffix}",
            site_address="123 Test St",
            timezone="America/Chicago",
            rain_station_code="MESONET_STATION",
            created_by_user_id=admin_id,
        )

        # Create entry
        db.create_mailbox_entry(
            conn,
            project_id=project_id,
            company_id=company_id,
            report_date="2026-05-01",
            report_type="auto_weekly",
            file_path="test.pdf",
        )
        conn.commit()

        client = TestClient(app)

        def override_get_db():
            yield conn

        app.dependency_overrides[db.get_db] = override_get_db

        try:
            # No auth headers - should still work
            response = client.get(f"/mailbox/P{suffix}")
            assert response.status_code == 200
        finally:
            app.dependency_overrides.clear()
            conn.close()

    def test_project_number_globally_unique(self, db_conn):
        """Verify project_number lookup works across companies."""
        # Create new connection with check_same_thread=False for TestClient
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        for statement in db.SCHEMA_SQL.strip().split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.commit()

        suffix = str(uuid.uuid4())[:8]

        # Create two companies with different projects
        admin_id = db.create_user(conn, display_name="Admin", is_admin=True)

        company1_id = db.create_company(
            conn,
            legal_name=f"Company1 {suffix}",
            display_name="C1",
            created_by=admin_id,
        )
        project1_id = db.create_project(
            conn,
            company_id=company1_id,
            project_number=f"UNIQUE{suffix}",
            project_name="Project 1",
            site_address="Address 1",
            timezone="America/Chicago",
            rain_station_code="STATION1",
            created_by_user_id=admin_id,
        )

        db.create_mailbox_entry(
            conn,
            project_id=project1_id,
            company_id=company1_id,
            report_date="2026-05-01",
            report_type="auto_weekly",
            file_path="test.pdf",
        )

        conn.commit()

        client = TestClient(app)

        def override_get_db():
            yield conn

        app.dependency_overrides[db.get_db] = override_get_db

        try:
            # Should find project regardless of company context
            response = client.get(f"/mailbox/UNIQUE{suffix}")
            assert response.status_code == 200
            body = response.json()
            assert body["project_number"] == f"UNIQUE{suffix}"
        finally:
            app.dependency_overrides.clear()
            conn.close()


# ── File Serving Tests ──────────────────────────────────────────────────


class TestMailboxFileServing:
    """Test file path resolution and ZIP generation."""

    def test_resolve_file_path_basic(self):
        """Test basic file path resolution."""
        from web.auth.main import _resolve_mailbox_file_path

        path = _resolve_mailbox_file_path("company123/project456/2026-05-01.pdf")
        assert "mailbox" in str(path)
        assert "company123" in str(path)
        assert "2026-05-01.pdf" in str(path)

    def test_resolve_file_path_prevents_traversal(self):
        """Test directory traversal prevention."""
        from web.auth.main import _resolve_mailbox_file_path

        # Attempt directory traversal
        with pytest.raises(ValueError):
            _resolve_mailbox_file_path("../../etc/passwd")

    def test_generate_batch_zip_empty(self, db_conn, setup_mailbox_data, tmp_path):
        """Test ZIP generation with no valid files."""
        from web.auth.main import _generate_batch_zip

        data = setup_mailbox_data

        # Create entry with non-existent file
        entry = {
            "id": str(uuid.uuid4()),
            "file_path": "nonexistent/file.pdf",
            "report_date": "2026-05-01",
        }

        # Should not raise, but ZIP will be empty
        zip_bytes = _generate_batch_zip([entry], data["project_number"])
        assert len(zip_bytes) > 0

        # Verify ZIP structure
        zip_buffer = BytesIO(zip_bytes)
        with ZipFile(zip_buffer, "r") as zipf:
            assert len(zipf.namelist()) == 0  # No files added
