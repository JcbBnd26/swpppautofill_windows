"""Tests for web.swppp_api — Phase 2 SWPPP API."""

from __future__ import annotations

import itertools
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Point BOTH DBs at a temp directory.
_tmpdir = tempfile.mkdtemp()
os.environ["TOOLS_DATA_DIR"] = _tmpdir
os.environ["TOOLS_DEV_MODE"] = "1"

from web.auth import db as auth_db  # noqa: E402
from web.auth.main import app as auth_app  # noqa: E402
from web.swppp_api import db as session_db  # noqa: E402
from web.swppp_api.main import app as swppp_app  # noqa: E402

auth_client = TestClient(auth_app, cookies={})


# ── Helpers ──────────────────────────────────────────────────────────────

_seq = itertools.count(1)


def _authed_client() -> TestClient:
    """Create a user with swppp access and return an authenticated TestClient
    for the SWPPP API app."""
    auth_db.init_db()
    session_db.init_db()
    with auth_db.connect() as conn:
        auth_db.seed_app(
            conn, "swppp", "SWPPP AutoFill", "Generate ODOT PDFs", "/swppp"
        )
        code = auth_db.create_invite(
            conn, f"TestUser{next(_seq)}", ["swppp"], grant_admin=False
        )

    # Claim the invite on the auth app to get a session cookie
    r = auth_client.post("/auth/claim", json={"code": code})
    assert r.status_code == 200
    session_cookie = r.cookies.get("tools_session")
    assert session_cookie

    # Return a TestClient for the swppp app with the same session cookie
    return TestClient(swppp_app, cookies={"tools_session": session_cookie})


def _no_access_client() -> TestClient:
    """Create a user WITHOUT swppp access."""
    auth_db.init_db()
    with auth_db.connect() as conn:
        # Create a different app so the user has something, just not swppp
        auth_db.seed_app(conn, "other", "Other App", "desc", "/other")
        code = auth_db.create_invite(
            conn, f"NoAccess{next(_seq)}", ["other"], grant_admin=False
        )

    r = auth_client.post("/auth/claim", json={"code": code})
    assert r.status_code == 200
    session_cookie = r.cookies.get("tools_session")
    return TestClient(swppp_app, cookies={"tools_session": session_cookie})


# ── Auth Guard ───────────────────────────────────────────────────────────


class TestAuthGuard:
    def test_unauthenticated_returns_401(self):
        c = TestClient(swppp_app, cookies={})
        r = c.get("/swppp/api/form-schema")
        assert r.status_code == 401

    def test_no_swppp_access_returns_403(self):
        c = _no_access_client()
        r = c.get("/swppp/api/form-schema")
        assert r.status_code == 403


# ── Form Schema ──────────────────────────────────────────────────────────


class TestFormSchema:
    def test_returns_fields_and_groups(self):
        c = _authed_client()
        r = c.get("/swppp/api/form-schema")
        assert r.status_code == 200
        data = r.json()

        assert "fields" in data
        assert "checkbox_groups" in data
        assert len(data["fields"]) == 10
        assert len(data["checkbox_groups"]) == 7

    def test_checkbox_group_structure(self):
        c = _authed_client()
        r = c.get("/swppp/api/form-schema")
        data = r.json()

        # Find Erosion Minimization group (6 questions)
        erosion = next(
            g for g in data["checkbox_groups"] if g["key"] == "Erosion_Minimization"
        )
        assert erosion["label"] == "Erosion Minimization"
        assert erosion["has_notes"] is True
        assert len(erosion["questions"]) == 6

    def test_total_questions(self):
        c = _authed_client()
        r = c.get("/swppp/api/form-schema")
        data = r.json()
        total = sum(len(g["questions"]) for g in data["checkbox_groups"])
        assert total == 38


# ── Stations ─────────────────────────────────────────────────────────────


class TestStations:
    def test_returns_station_list(self):
        c = _authed_client()
        r = c.get("/swppp/api/stations")
        assert r.status_code == 200
        data = r.json()
        assert len(data["stations"]) > 100

    def test_station_structure(self):
        c = _authed_client()
        r = c.get("/swppp/api/stations")
        item = r.json()["stations"][0]
        assert "code" in item
        assert "name" in item
        assert "display" in item
        assert " - " in item["display"]


# ── Session CRUD ─────────────────────────────────────────────────────────


class TestSessionCRUD:
    def test_list_empty(self):
        c = _authed_client()
        r = c.get("/swppp/api/sessions")
        assert r.status_code == 200
        assert r.json()["sessions"] == []

    def test_save_and_get(self):
        c = _authed_client()
        payload = {"project_fields": {"job_piece": "123"}, "start_date": "2025-01-01"}
        r = c.put("/swppp/api/sessions/test-session", json=payload)
        assert r.status_code == 200
        assert r.json()["success"] is True

        r = c.get("/swppp/api/sessions/test-session")
        assert r.status_code == 200
        assert r.json()["project_fields"]["job_piece"] == "123"

    def test_list_after_save(self):
        c = _authed_client()
        c.put("/swppp/api/sessions/my-session", json={"data": "test"})
        r = c.get("/swppp/api/sessions")
        names = [s["name"] for s in r.json()["sessions"]]
        assert "my-session" in names

    def test_delete(self):
        c = _authed_client()
        c.put("/swppp/api/sessions/del-me", json={"data": "test"})
        r = c.delete("/swppp/api/sessions/del-me")
        assert r.status_code == 200

        r = c.get("/swppp/api/sessions/del-me")
        assert r.status_code == 404

    def test_get_nonexistent_returns_404(self):
        c = _authed_client()
        r = c.get("/swppp/api/sessions/nope")
        assert r.status_code == 404

    def test_export_session(self):
        c = _authed_client()
        c.put("/swppp/api/sessions/export-me", json={"key": "value"})
        r = c.get("/swppp/api/sessions/export-me/export")
        assert r.status_code == 200
        assert "application/json" in r.headers["content-type"]

    def test_import_session_no_save(self):
        c = _authed_client()
        content = json.dumps({"session_name": "imported", "data": 42}).encode()
        r = c.post(
            "/swppp/api/sessions/import",
            files={"file": ("session.json", content, "application/json")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["saved"] is False
        assert body["name"] == "imported"

    def test_import_session_with_save(self):
        c = _authed_client()
        content = json.dumps({"session_name": "saved-import", "x": 1}).encode()
        r = c.post(
            "/swppp/api/sessions/import?save=true",
            files={"file": ("session.json", content, "application/json")},
        )
        assert r.status_code == 200
        assert r.json()["saved"] is True

        # Verify it was actually saved
        r = c.get("/swppp/api/sessions/saved-import")
        assert r.status_code == 200


# ── Generate ─────────────────────────────────────────────────────────────


class TestGenerate:
    def test_generate_zip(self):
        c = _authed_client()
        payload = {
            "project_fields": {
                "job_piece": "JP-001",
                "project_number": "PRJ-123",
                "contract_id": "C-456",
                "inspection_type": "Weekly",
            },
            "start_date": "2025-01-06",
            "end_date": "2025-01-20",
        }
        r = c.post("/swppp/api/generate", json=payload)
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert len(r.content) > 0

    def test_generate_no_dates_returns_400(self):
        c = _authed_client()
        payload = {
            "project_fields": {"job_piece": "JP-001"},
            "start_date": "2025-01-20",
            "end_date": "2025-01-06",  # end before start
        }
        r = c.post("/swppp/api/generate", json=payload)
        assert r.status_code == 400

    def test_generate_with_checkboxes(self):
        c = _authed_client()
        payload = {
            "project_fields": {
                "job_piece": "JP-001",
                "inspection_type": "Weekly",
            },
            "checkbox_states": {
                "Erosion_Minimization": {
                    "BMPs are in place to minimize erosion?": "YES",
                },
            },
            "start_date": "2025-01-06",
            "end_date": "2025-01-06",
        }
        r = c.post("/swppp/api/generate", json=payload)
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"


# ── Rain CSV Parse ───────────────────────────────────────────────────────


class TestRainParseCsv:
    def test_invalid_file_returns_400(self):
        c = _authed_client()
        r = c.post(
            "/swppp/api/rain/parse-csv",
            files={"file": ("bad.csv", b"not,valid,csv,data\n", "text/csv")},
        )
        # Should either parse (with 0 results) or return 400
        assert r.status_code in (200, 400)

    def test_parse_csv_empty_file(self):
        c = _authed_client()
        r = c.post(
            "/swppp/api/rain/parse-csv",
            files={"file": ("empty.csv", b"", "text/csv")},
        )
        assert r.status_code in (200, 400)

    def test_parse_csv_oversized_file(self):
        c = _authed_client()
        big = b"x" * (5 * 1024 * 1024 + 1)
        r = c.post(
            "/swppp/api/rain/parse-csv",
            files={"file": ("huge.csv", big, "text/csv")},
        )
        assert r.status_code == 413

    def test_parse_csv_binary_content(self):
        c = _authed_client()
        r = c.post(
            "/swppp/api/rain/parse-csv",
            files={"file": ("img.csv", b"\x89PNG\r\n\x1a\n\x00", "text/csv")},
        )
        assert r.status_code == 400

    def test_parse_csv_negative_threshold(self):
        c = _authed_client()
        r = c.post(
            "/swppp/api/rain/parse-csv?threshold=-1",
            files={"file": ("f.csv", b"date,rain\n", "text/csv")},
        )
        assert r.status_code == 422


# ── Rain Fetch (mocked) ─────────────────────────────────────────────────


@dataclass
class _MockRainDay:
    date: date
    rainfall_inches: float


@dataclass
class _MockFetchResult:
    days: list[_MockRainDay]
    failed: int
    missing: int


class TestRainFetch:
    def test_rain_fetch_invalid_station(self):
        c = _authed_client()
        r = c.post(
            "/swppp/api/rain/fetch",
            json={
                "station": "",
                "start_date": "2025-01-01",
                "end_date": "2025-01-07",
            },
        )
        assert r.status_code == 400

    def test_rain_fetch_invalid_date_format(self):
        c = _authed_client()
        r = c.post(
            "/swppp/api/rain/fetch",
            json={
                "station": "NRMN",
                "start_date": "2025/01/01",
                "end_date": "2025-01-07",
            },
        )
        assert r.status_code == 400

    def test_rain_fetch_end_before_start(self):
        c = _authed_client()
        with patch("app.core.mesonet_stations.parse_station_code", return_value="NRMN"):
            r = c.post(
                "/swppp/api/rain/fetch",
                json={
                    "station": "NRMN",
                    "start_date": "2025-01-07",
                    "end_date": "2025-01-01",
                },
            )
        assert r.status_code == 400
        assert "precede" in r.json()["detail"].lower()

    def test_rain_fetch_negative_threshold(self):
        c = _authed_client()
        r = c.post(
            "/swppp/api/rain/fetch",
            json={
                "station": "NRMN",
                "start_date": "2025-01-01",
                "end_date": "2025-01-07",
                "threshold": -1.0,
            },
        )
        assert r.status_code == 422

    def test_rain_fetch_threshold_too_high(self):
        c = _authed_client()
        r = c.post(
            "/swppp/api/rain/fetch",
            json={
                "station": "NRMN",
                "start_date": "2025-01-01",
                "end_date": "2025-01-07",
                "threshold": 99.0,
            },
        )
        assert r.status_code == 422

    @patch("app.core.mesonet.filter_rain_events", return_value=[])
    @patch(
        "app.core.mesonet.fetch_rainfall",
        return_value=_MockFetchResult(days=[], failed=0, missing=0),
    )
    @patch("app.core.mesonet_stations.parse_station_code", return_value="NRMN")
    def test_rain_fetch_valid(self, _p1, _p2, _p3):
        c = _authed_client()
        r = c.post(
            "/swppp/api/rain/fetch",
            json={
                "station": "NRMN",
                "start_date": "2025-01-01",
                "end_date": "2025-01-07",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert "all_days" in data
        assert "rain_events" in data
        assert data["station"] == "NRMN"

    @patch(
        "app.core.mesonet.fetch_rainfall",
        side_effect=Exception("Connection timed out"),
    )
    @patch("app.core.mesonet_stations.parse_station_code", return_value="NRMN")
    def test_rain_fetch_network_error(self, _p1, _p2):
        c = _authed_client()
        r = c.post(
            "/swppp/api/rain/fetch",
            json={
                "station": "NRMN",
                "start_date": "2025-01-01",
                "end_date": "2025-01-07",
            },
        )
        assert r.status_code == 502
        assert "fetch failed" in r.json()["detail"].lower()

    @patch("app.core.mesonet.filter_rain_events")
    @patch("app.core.mesonet.fetch_rainfall")
    @patch("app.core.mesonet_stations.parse_station_code", return_value="NRMN")
    def test_rain_fetch_returns_events(self, _p1, mock_fetch, mock_filter):
        mock_day = _MockRainDay(date=date(2025, 1, 3), rainfall_inches=0.75)
        mock_fetch.return_value = _MockFetchResult(days=[mock_day], failed=0, missing=0)
        mock_filter.return_value = [mock_day]
        c = _authed_client()
        r = c.post(
            "/swppp/api/rain/fetch",
            json={
                "station": "NRMN",
                "start_date": "2025-01-01",
                "end_date": "2025-01-07",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["all_days"]) == 1
        assert len(data["rain_events"]) == 1
        assert data["rain_events"][0]["rainfall_inches"] == 0.75


# ── Generate (extended) ─────────────────────────────────────────────────


class TestGenerateExtended:
    def test_generate_missing_start_date(self):
        c = _authed_client()
        r = c.post(
            "/swppp/api/generate",
            json={"project_fields": {"job_piece": "JP-001"}, "end_date": "2025-01-20"},
        )
        assert r.status_code == 422

    def test_generate_missing_end_date(self):
        c = _authed_client()
        r = c.post(
            "/swppp/api/generate",
            json={
                "project_fields": {"job_piece": "JP-001"},
                "start_date": "2025-01-06",
            },
        )
        assert r.status_code == 422

    def test_generate_malformed_rain_day_date(self):
        c = _authed_client()
        payload = {
            "project_fields": {"job_piece": "JP-001", "inspection_type": "Weekly"},
            "start_date": "2025-01-06",
            "end_date": "2025-01-06",
            "rain_days": [{"date": "not-a-date", "rainfall_inches": 0.5}],
        }
        r = c.post("/swppp/api/generate", json=payload)
        assert r.status_code == 400
        assert "rain day date" in r.json()["detail"].lower()

    def test_generate_empty_rain_days_list(self):
        c = _authed_client()
        payload = {
            "project_fields": {"job_piece": "JP-001", "inspection_type": "Weekly"},
            "start_date": "2025-01-06",
            "end_date": "2025-01-06",
            "rain_days": [],
        }
        r = c.post("/swppp/api/generate", json=payload)
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"

    def test_generate_with_rain_days(self):
        c = _authed_client()
        payload = {
            "project_fields": {"job_piece": "JP-001", "inspection_type": "Weekly"},
            "start_date": "2025-01-06",
            "end_date": "2025-01-06",
            "rain_days": [{"date": "2025-01-05", "rainfall_inches": 0.75}],
            "original_inspection_type": "Weekly",
        }
        r = c.post("/swppp/api/generate", json=payload)
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert len(r.content) > 0

    def test_generate_with_all_checkbox_groups(self):
        c = _authed_client()
        schema = c.get("/swppp/api/form-schema").json()
        checkbox_states = {}
        for group in schema["checkbox_groups"]:
            checkbox_states[group["key"]] = {
                q["text"]: "YES" for q in group["questions"]
            }
        payload = {
            "project_fields": {"job_piece": "JP-001", "inspection_type": "Weekly"},
            "checkbox_states": checkbox_states,
            "start_date": "2025-01-06",
            "end_date": "2025-01-06",
        }
        r = c.post("/swppp/api/generate", json=payload)
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"

    def test_generate_with_notes(self):
        c = _authed_client()
        payload = {
            "project_fields": {"job_piece": "JP-001", "inspection_type": "Weekly"},
            "notes_texts": {"Erosion_Minimization": "Some inspection notes here."},
            "start_date": "2025-01-06",
            "end_date": "2025-01-06",
        }
        r = c.post("/swppp/api/generate", json=payload)
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"

    def test_generate_unknown_checkbox_group_ignored(self):
        c = _authed_client()
        payload = {
            "project_fields": {"job_piece": "JP-001", "inspection_type": "Weekly"},
            "checkbox_states": {"Nonexistent_Group": {"Q?": "YES"}},
            "start_date": "2025-01-06",
            "end_date": "2025-01-06",
        }
        r = c.post("/swppp/api/generate", json=payload)
        assert r.status_code == 200

    def test_generate_very_long_field_values(self):
        c = _authed_client()
        payload = {
            "project_fields": {
                "job_piece": "X" * 500,
                "inspection_type": "Weekly",
            },
            "start_date": "2025-01-06",
            "end_date": "2025-01-06",
        }
        r = c.post("/swppp/api/generate", json=payload)
        assert r.status_code == 200

    def test_generate_negative_rain_amount_rejected(self):
        c = _authed_client()
        payload = {
            "project_fields": {"job_piece": "JP-001", "inspection_type": "Weekly"},
            "start_date": "2025-01-06",
            "end_date": "2025-01-06",
            "rain_days": [{"date": "2025-01-05", "rainfall_inches": -1.0}],
        }
        r = c.post("/swppp/api/generate", json=payload)
        assert r.status_code == 422


# ── Session name validation ──────────────────────────────────────────────


class TestSessionNameValidation:
    def test_reject_illegal_characters(self):
        c = _authed_client()
        r = c.put(
            "/swppp/api/sessions/has<something>",
            json={"foo": "bar"},
        )
        assert r.status_code == 400

    def test_reject_slash_in_name(self):
        c = _authed_client()
        r = c.put(
            "/swppp/api/sessions/has%2Fslash",
            json={"foo": "bar"},
        )
        assert r.status_code in (400, 404)

    def test_accept_typical_names(self):
        c = _authed_client()
        for name in ["project_2026-04", "Site.A.1", "My Session 7"]:
            r = c.put(
                f"/swppp/api/sessions/{name}",
                json={"foo": "bar"},
            )
            assert r.status_code == 200, f"rejected valid name: {name}"


# ── Session CRUD (extended) ──────────────────────────────────────────────


class TestSessionCRUDExtended:
    def test_save_empty_body(self):
        c = _authed_client()
        r = c.put("/swppp/api/sessions/empty-body", json={})
        assert r.status_code == 200
        assert r.json()["success"] is True

    def test_save_very_long_name(self):
        c = _authed_client()
        name = "x" * 201
        r = c.put(f"/swppp/api/sessions/{name}", json={"data": "test"})
        assert r.status_code == 400
        assert "too long" in r.json()["detail"].lower()

    def test_get_very_long_name(self):
        c = _authed_client()
        name = "x" * 201
        r = c.get(f"/swppp/api/sessions/{name}")
        assert r.status_code == 400

    def test_save_special_chars_in_name(self):
        c = _authed_client()
        # Spaces, hyphens, underscores, dots are allowed
        r = c.put("/swppp/api/sessions/my%20session_v1.2-final", json={"data": "test"})
        assert r.status_code == 200

    def test_delete_nonexistent_session(self):
        c = _authed_client()
        r = c.delete("/swppp/api/sessions/does-not-exist")
        assert r.status_code == 200

    def test_export_nonexistent_returns_404(self):
        c = _authed_client()
        r = c.get("/swppp/api/sessions/nope/export")
        assert r.status_code == 404

    def test_import_missing_session_name(self):
        c = _authed_client()
        content = json.dumps({"data": 42}).encode()
        r = c.post(
            "/swppp/api/sessions/import",
            files={"file": ("myfile.json", content, "application/json")},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "myfile"

    def test_import_oversized_file(self):
        c = _authed_client()
        big = b"{" + b'"x":1,' * (1024 * 1024) + b'"y":2}'
        r = c.post(
            "/swppp/api/sessions/import",
            files={"file": ("huge.json", big, "application/json")},
        )
        assert r.status_code == 413

    def test_import_non_json_content(self):
        c = _authed_client()
        r = c.post(
            "/swppp/api/sessions/import",
            files={"file": ("bad.json", b"not json at all", "application/json")},
        )
        assert r.status_code == 400

    def test_import_json_array_not_dict(self):
        c = _authed_client()
        content = json.dumps([1, 2, 3]).encode()
        r = c.post(
            "/swppp/api/sessions/import",
            files={"file": ("arr.json", content, "application/json")},
        )
        assert r.status_code == 400
        assert "object" in r.json()["detail"].lower()

    def test_import_save_and_verify(self):
        c = _authed_client()
        content = json.dumps(
            {"session_name": "round-trip", "job_piece": "JP-999"}
        ).encode()
        r = c.post(
            "/swppp/api/sessions/import?save=true",
            files={"file": ("rt.json", content, "application/json")},
        )
        assert r.status_code == 200
        assert r.json()["saved"] is True

        r2 = c.get("/swppp/api/sessions/round-trip")
        assert r2.status_code == 200
        assert r2.json()["job_piece"] == "JP-999"


# ── Dev-mode routes ──────────────────────────────────────────────────────


class TestDevRoutes:
    def test_swppp_index_serves_html(self):
        c = TestClient(swppp_app)
        r = c.get("/swppp/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_swppp_index_contains_alpine(self):
        c = TestClient(swppp_app)
        r = c.get("/swppp/")
        assert r.status_code == 200
        assert "alpine" in r.text.lower()


# ── Tier 5: Health Endpoint ─────────────────────────────────────────────


class TestSwpppHealthEndpoint:
    """Verify SWPPP service health endpoint."""

    def test_health_returns_200_when_healthy(self):
        """GET /swppp/api/health must return 200 when all checks pass."""
        c = TestClient(swppp_app)
        response = c.get("/swppp/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "tools-swppp"
        assert "timestamp" in data
        assert "db" in data

    def test_health_is_unauthenticated(self):
        """GET /swppp/api/health must not require authentication."""
        c = TestClient(swppp_app, cookies={})
        response = c.get("/swppp/api/health")
        assert response.status_code == 200

    def test_health_fails_when_template_missing(self, monkeypatch, tmp_path):
        """Health check must return 503 when template file is missing."""
        import web.swppp_api.main as swppp_main

        # Point TEMPLATE_PDF to a nonexistent file
        monkeypatch.setattr(swppp_main, "TEMPLATE_PDF", tmp_path / "nonexistent.pdf")

        c = TestClient(swppp_app)
        response = c.get("/swppp/api/health")
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["status"] == "unhealthy"
        assert any("template" in issue.lower() for issue in detail["issues"])

    def test_health_fails_when_mapping_missing(self, monkeypatch, tmp_path):
        """Health check must return 503 when mapping file is missing."""
        import web.swppp_api.main as swppp_main

        # Point MAPPING_YAML to a nonexistent file
        monkeypatch.setattr(swppp_main, "MAPPING_YAML", tmp_path / "nonexistent.yaml")

        c = TestClient(swppp_app)
        response = c.get("/swppp/api/health")
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["status"] == "unhealthy"
        assert any("mapping" in issue.lower() for issue in detail["issues"])


# ── Tier 5: Startup Validation ──────────────────────────────────────────


class TestStartupValidation:
    """Verify service refuses to start if critical files are missing."""

    def test_lifespan_raises_if_template_missing(self, tmp_path, monkeypatch):
        """lifespan() must raise RuntimeError when TEMPLATE_PDF does not exist."""
        import asyncio

        import web.swppp_api.main as swppp_main

        monkeypatch.setattr(swppp_main, "TEMPLATE_PDF", tmp_path / "nonexistent.pdf")

        async def _run():
            async with swppp_main.lifespan(swppp_main.app):
                pass

        with pytest.raises(RuntimeError, match="required files missing"):
            asyncio.run(_run())

    def test_lifespan_raises_if_mapping_missing(self, tmp_path, monkeypatch):
        """lifespan() must raise RuntimeError when MAPPING_YAML does not exist."""
        import asyncio

        import web.swppp_api.main as swppp_main

        monkeypatch.setattr(swppp_main, "MAPPING_YAML", tmp_path / "nonexistent.yaml")

        async def _run():
            async with swppp_main.lifespan(swppp_main.app):
                pass

        with pytest.raises(RuntimeError, match="required files missing"):
            asyncio.run(_run())


# ── Tier 5: Session Error Logging ───────────────────────────────────────


class TestSessionErrorLogging:
    """Verify session CRUD routes log errors with full tracebacks."""

    def test_session_list_db_error_logged(self, monkeypatch, caplog):
        """list_sessions must log DB errors with exc_info=True before returning 500."""
        import logging

        from web.swppp_api import db as session_db

        def _broken_connect():
            raise RuntimeError("DB connection failed")

        monkeypatch.setattr(session_db, "connect", _broken_connect)

        c = _authed_client()
        with caplog.at_level(logging.ERROR, logger="web.swppp_api.main"):
            r = c.get("/swppp/api/sessions")

        assert r.status_code == 500
        assert "Failed to retrieve sessions" in r.json()["detail"]
        # Verify error was logged
        assert any("Session list failed" in record.message for record in caplog.records)
        # Verify exc_info was captured (traceback present)
        assert any(record.exc_info is not None for record in caplog.records)

    def test_session_save_db_error_logged(self, monkeypatch, caplog):
        """save_session must log DB errors with exc_info=True before returning 500."""
        import logging

        from web.swppp_api import db as session_db

        # Let connect work but make save_session fail
        original_connect = session_db.connect

        class BrokenConnection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def execute(self, *args):
                raise RuntimeError("Save failed")

        def _connect_broken():
            return BrokenConnection()

        monkeypatch.setattr(session_db, "connect", _connect_broken)
        monkeypatch.setattr(
            session_db,
            "save_session",
            lambda *args: (_ for _ in ()).throw(RuntimeError("Save failed")),
        )

        c = _authed_client()
        with caplog.at_level(logging.ERROR, logger="web.swppp_api.main"):
            r = c.put("/swppp/api/sessions/test", json={"data": "test"})

        assert r.status_code == 500
        assert "Failed to save session" in r.json()["detail"]
        assert any("Session save failed" in record.message for record in caplog.records)
        assert any(record.exc_info is not None for record in caplog.records)
