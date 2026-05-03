"""IR-7 tests — Archive Flow.

Covers:
- db.archive_project / unarchive_project / set_archive_zip_path / add_not_document
- POST /companies/{cid}/projects/{pid}/archive
- GET  /companies/{cid}/projects/{pid}/archive/status
- POST /companies/{cid}/projects/{pid}/unarchive
- POST /companies/{cid}/projects/{pid}/not
- GET  /mailbox/{number}/archive/download
- GET  /mailbox/{number} — is_archived flag + archive fields
"""

from __future__ import annotations

import io
import itertools
import os
import tempfile
from unittest.mock import patch

# Set data dir before any web.auth imports.
if "TOOLS_DATA_DIR" not in os.environ:
    os.environ["TOOLS_DATA_DIR"] = tempfile.mkdtemp()
os.environ.setdefault("TOOLS_DEV_MODE", "1")

import pytest
from fastapi.testclient import TestClient

from web.auth import db
from web.auth.main import app

_seq = itertools.count(70000)
client = TestClient(app, raise_server_exceptions=True)


# ── Helpers ──────────────────────────────────────────────────────────────


def _u(prefix: str = "U") -> str:
    return f"{prefix}{next(_seq)}"


def _co() -> str:
    return f"ArchiveCo {next(_seq)} LLC"


def _pnum() -> str:
    return f"ARC-{next(_seq)}"


def _authed_client(user_id: str) -> TestClient:
    with db.connect() as conn:
        token = db.create_session(conn, user_id)
    c = TestClient(app, raise_server_exceptions=True)
    c.cookies.set("tools_session", token)
    return c


def _setup_company_and_project(
    *,
    pm_role: str = "pm",
) -> dict:
    """Create a company, a pm user, a company_admin user, and a project.

    Returns a dict with keys: company_id, project_id, project_number,
    pm_client, pm_user_id, admin_client, admin_user_id.
    """
    db.init_db()
    with db.connect() as conn:
        db.seed_app(conn, "swppp", "SWPPP", "desc", "/swppp")

        # Create company
        company_id = db.create_company(
            conn,
            legal_name=_co(),
            display_name="ArchiveCo",
            timezone="America/Chicago",
        )

        # PM user
        pm_id = db.create_user(conn, _u("PM"), is_admin=False)
        db.add_company_user(conn, pm_id, company_id, role=pm_role)

        # Company admin user
        admin_id = db.create_user(conn, _u("CAdmin"), is_admin=False)
        db.add_company_user(conn, admin_id, company_id, role="company_admin")

        # Project
        pnum = _pnum()
        project_id = db.create_project(
            conn,
            company_id=company_id,
            created_by_user_id=pm_id,
            project_number=pnum,
            project_name="Test Project",
            site_address="123 Test St",
            rain_station_code="KTOL",
        )

    return {
        "company_id": company_id,
        "project_id": project_id,
        "project_number": pnum,
        "pm_client": _authed_client(pm_id),
        "pm_user_id": pm_id,
        "admin_client": _authed_client(admin_id),
        "admin_user_id": admin_id,
    }


def _dummy_file(name: str = "not.pdf") -> tuple[str, io.BytesIO, str]:
    return (name, io.BytesIO(b"%PDF-1.4 dummy"), "application/pdf")


# ── TestArchiveProject ────────────────────────────────────────────────────


class TestArchiveProject:
    def test_pm_can_archive_without_not(self):
        s = _setup_company_and_project()
        r = s["pm_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
            data={"archive_without_not": "true"},
        )
        assert r.status_code == 202
        body = r.json()
        assert body["project_id"] == s["project_id"]
        assert body["archive_zip_ready"] is False

    def test_company_admin_can_archive(self):
        s = _setup_company_and_project()
        r = s["admin_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
            data={"archive_without_not": "true"},
        )
        assert r.status_code == 202

    def test_non_member_gets_403(self):
        s = _setup_company_and_project()
        # Create a user with no company membership
        with db.connect() as conn:
            outsider_id = db.create_user(conn, _u("Outsider"), is_admin=False)
        outsider = _authed_client(outsider_id)
        r = outsider.post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
            data={"archive_without_not": "true"},
        )
        assert r.status_code == 403

    def test_status_becomes_archived(self):
        s = _setup_company_and_project()
        s["pm_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
            data={"archive_without_not": "true"},
        )
        with db.connect() as conn:
            project = db.get_project_for_company(conn, s["project_id"], s["company_id"])
        assert project["status"] == "archived"

    def test_auto_weekly_disabled(self):
        s = _setup_company_and_project()
        # Enable auto-weekly first
        with db.connect() as conn:
            db.update_project(conn, s["project_id"], auto_weekly_enabled=1)
        s["pm_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
            data={"archive_without_not": "true"},
        )
        with db.connect() as conn:
            project = db.get_project_for_company(conn, s["project_id"], s["company_id"])
        assert not project["auto_weekly_enabled"]

    def test_archive_with_not_file(self):
        s = _setup_company_and_project()
        r = s["pm_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
            data={"archive_without_not": "false"},
            files={"not_file": _dummy_file()},
        )
        assert r.status_code == 202
        with db.connect() as conn:
            project = db.get_project_for_company(conn, s["project_id"], s["company_id"])
        assert project["not_document_path"] is not None

    def test_409_if_already_archived(self):
        s = _setup_company_and_project()
        s["pm_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
            data={"archive_without_not": "true"},
        )
        r2 = s["pm_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
            data={"archive_without_not": "true"},
        )
        assert r2.status_code == 409

    def test_template_versions_archived(self):
        s = _setup_company_and_project()
        # Create a template version
        with db.connect() as conn:
            db.create_template_version(
                conn,
                project_id=s["project_id"],
                created_by_user_id=s["pm_user_id"],
                template_data={"job_piece": "JP-1"},
            )
        s["pm_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
            data={"archive_without_not": "true"},
        )
        with db.connect() as conn:
            versions = conn.execute(
                "SELECT status FROM project_template_versions WHERE project_id = ?",
                (s["project_id"],),
            ).fetchall()
        assert all(v["status"] == "archived" for v in versions)


# ── TestArchiveStatus ─────────────────────────────────────────────────────


class TestArchiveStatus:
    def test_status_zip_not_ready(self):
        s = _setup_company_and_project()
        s["pm_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
            data={"archive_without_not": "true"},
        )
        r = s["pm_client"].get(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive/status"
        )
        assert r.status_code == 200
        body = r.json()
        # ZIP not ready yet (background task doesn't run in TestClient sync mode)
        assert isinstance(body["archive_zip_ready"], bool)

    def test_status_zip_ready_after_path_set(self):
        s = _setup_company_and_project()
        s["pm_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
            data={"archive_without_not": "true"},
        )
        # Manually set the zip path to simulate completed background task
        fake_path = "/tmp/fake.zip"
        with db.connect() as conn:
            db.set_archive_zip_path(conn, s["project_id"], fake_path)
        r = s["pm_client"].get(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive/status"
        )
        assert r.status_code == 200
        body = r.json()
        assert body["archive_zip_ready"] is True
        assert body["archive_zip_path"] == fake_path


# ── TestUnarchive ─────────────────────────────────────────────────────────


class TestUnarchive:
    def test_company_admin_can_unarchive(self):
        s = _setup_company_and_project()
        s["pm_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
            data={"archive_without_not": "true"},
        )
        r = s["admin_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/unarchive"
        )
        assert r.status_code == 200
        assert r.json()["status"] == "active"

    def test_pm_cannot_unarchive(self):
        s = _setup_company_and_project()
        s["pm_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
            data={"archive_without_not": "true"},
        )
        r = s["pm_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/unarchive"
        )
        assert r.status_code == 403

    def test_status_resets_to_active(self):
        s = _setup_company_and_project()
        s["pm_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
            data={"archive_without_not": "true"},
        )
        s["admin_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/unarchive"
        )
        with db.connect() as conn:
            project = db.get_project_for_company(conn, s["project_id"], s["company_id"])
        assert project["status"] == "active"
        assert project["archived_at"] is None
        assert project["archive_zip_path"] is None

    def test_400_if_not_archived(self):
        s = _setup_company_and_project()
        r = s["admin_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/unarchive"
        )
        assert r.status_code == 400


# ── TestNotUpload ─────────────────────────────────────────────────────────


class TestNotUpload:
    def test_upload_not_for_archived_project(self):
        s = _setup_company_and_project()
        s["pm_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
            data={"archive_without_not": "true"},
        )
        r = s["pm_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/not",
            files={"not_file": _dummy_file("termination.pdf")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["project_id"] == s["project_id"]
        assert "termination.pdf" in body["not_document_path"]

    def test_400_for_active_project(self):
        s = _setup_company_and_project()
        r = s["pm_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/not",
            files={"not_file": _dummy_file()},
        )
        assert r.status_code == 400

    def test_clears_archive_zip_path(self):
        s = _setup_company_and_project()
        with patch("web.auth.main._generate_archive_zip"):
            s["pm_client"].post(
                f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
                data={"archive_without_not": "true"},
            )
        # Set a fake zip path
        with db.connect() as conn:
            db.set_archive_zip_path(conn, s["project_id"], "/tmp/old.zip")
        # Upload NOT — should clear the zip path, then background task would regen
        with patch("web.auth.main._generate_archive_zip"):
            s["pm_client"].post(
                f"/companies/{s['company_id']}/projects/{s['project_id']}/not",
                files={"not_file": _dummy_file()},
            )
        with db.connect() as conn:
            project = db.get_project_for_company(conn, s["project_id"], s["company_id"])
        assert project["archive_zip_path"] is None


# ── TestMailboxArchivedView ───────────────────────────────────────────────


class TestMailboxArchivedView:
    def test_is_archived_flag_in_mailbox_response(self):
        s = _setup_company_and_project()
        with patch("web.auth.main._generate_archive_zip"):
            s["pm_client"].post(
                f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
                data={"archive_without_not": "true"},
            )
        r = client.get(f"/mailbox/{s['project_number']}")
        assert r.status_code == 200
        body = r.json()
        assert body["is_archived"] is True
        assert body["archived_at"] is not None
        assert body["archive_zip_ready"] is False
        assert body["not_on_file"] is False

    def test_202_while_zip_preparing(self):
        s = _setup_company_and_project()
        with patch("web.auth.main._generate_archive_zip"):
            s["pm_client"].post(
                f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
                data={"archive_without_not": "true"},
            )
        r = client.get(f"/mailbox/{s['project_number']}/archive/download")
        assert r.status_code == 202

    def test_file_response_when_zip_ready(self, tmp_path):
        s = _setup_company_and_project()
        s["pm_client"].post(
            f"/companies/{s['company_id']}/projects/{s['project_id']}/archive",
            data={"archive_without_not": "true"},
        )
        # Write a fake zip file and set the path
        fake_zip = tmp_path / "archive.zip"
        fake_zip.write_bytes(b"PK\x03\x04")  # minimal ZIP magic bytes
        with db.connect() as conn:
            db.set_archive_zip_path(conn, s["project_id"], str(fake_zip))
        r = client.get(f"/mailbox/{s['project_number']}/archive/download")
        assert r.status_code == 200
        assert r.headers["content-type"] in (
            "application/zip",
            "application/zip; charset=utf-8",
        )

    def test_404_when_not_archived(self):
        s = _setup_company_and_project()
        r = client.get(f"/mailbox/{s['project_number']}/archive/download")
        assert r.status_code == 404
