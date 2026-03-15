from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.session import (
    CURRENT_VERSION,
    delete_session,
    list_sessions,
    load_named_session,
    load_session,
    save_named_session,
    save_session,
)


@pytest.fixture()
def session_path(tmp_path: Path) -> Path:
    return tmp_path / ".swppp_autofill" / "session.json"


def _sample_data() -> dict:
    return {
        "version": CURRENT_VERSION,
        "project_fields": {"job_piece": "JP-101"},
        "checkbox_states": {"Erosion": {"Q1": "YES"}},
        "notes_texts": {"Erosion": "Slope needs work."},
        "generator_settings": {
            "year": "2026",
            "months": [3],
            "custom_dates_enabled": False,
            "custom_start_date": "",
            "custom_end_date": "",
            "rain_enabled": True,
            "rain_station": "NRMN - Norman",
        },
    }


def test_save_creates_file(session_path: Path) -> None:
    save_session(_sample_data(), session_path)
    assert session_path.exists()
    data = json.loads(session_path.read_text(encoding="utf-8"))
    assert data["version"] == CURRENT_VERSION


def test_load_returns_saved_data(session_path: Path) -> None:
    original = _sample_data()
    save_session(original, session_path)
    loaded = load_session(session_path)
    assert loaded == original


def test_load_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_session(tmp_path / "does_not_exist.json") is None


def test_load_corrupt_json_returns_none(session_path: Path) -> None:
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_bytes(b"\x00garbage{{{not json")
    assert load_session(session_path) is None


def test_load_future_version_returns_none(session_path: Path) -> None:
    data = _sample_data()
    data["version"] = 99
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(json.dumps(data), encoding="utf-8")
    assert load_session(session_path) is None


def test_load_missing_version_returns_none(session_path: Path) -> None:
    data = _sample_data()
    del data["version"]
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(json.dumps(data), encoding="utf-8")
    assert load_session(session_path) is None


def test_save_atomic_write(session_path: Path) -> None:
    """After a successful save, no .tmp file should remain."""
    save_session(_sample_data(), session_path)
    tmp_files = list(session_path.parent.glob("session_*.tmp"))
    assert tmp_files == []
    # The final file must be valid JSON
    data = json.loads(session_path.read_text(encoding="utf-8"))
    assert data["version"] == CURRENT_VERSION


def test_save_creates_directory(tmp_path: Path) -> None:
    deep_path = tmp_path / "a" / "b" / "c" / "session.json"
    save_session(_sample_data(), deep_path)
    assert deep_path.exists()
    data = json.loads(deep_path.read_text(encoding="utf-8"))
    assert data["project_fields"]["job_piece"] == "JP-101"


# ---- Named session tests ----


def test_named_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.session.SESSION_DIR", tmp_path)
    original = _sample_data()
    save_named_session("Highway 66 Bridge", original)
    loaded = load_named_session("Highway 66 Bridge")
    assert loaded == original


def test_list_sessions_sorted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.session.SESSION_DIR", tmp_path)
    save_named_session("Zulu Project", _sample_data())
    save_named_session("Alpha Job", _sample_data())
    save_named_session("Mike Bridge", _sample_data())
    # Also write session.json (auto-save) — should be excluded
    save_session(_sample_data(), tmp_path / "session.json")
    names = list_sessions()
    assert names == ["Alpha Job", "Mike Bridge", "Zulu Project"]


def test_list_sessions_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.session.SESSION_DIR", tmp_path)
    assert list_sessions() == []


def test_delete_session_removes_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.core.session.SESSION_DIR", tmp_path)
    save_named_session("Temp Job", _sample_data())
    assert (tmp_path / "Temp Job.json").exists()
    delete_session("Temp Job")
    assert not (tmp_path / "Temp Job.json").exists()


def test_delete_nonexistent_session_no_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.core.session.SESSION_DIR", tmp_path)
    delete_session("Does Not Exist")  # should not raise
