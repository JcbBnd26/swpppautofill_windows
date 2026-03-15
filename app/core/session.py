from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

SESSION_DIR = Path.home() / ".swppp_autofill"
SESSION_FILE = SESSION_DIR / "session.json"
CURRENT_VERSION = 1

# Allowed characters for session names (filesystem-safe)
_SAFE_NAME_RE = re.compile(r"^[\w\s\-().]+$")


def _session_path(name: str) -> Path:
    """Return the file path for a named session."""
    if not _SAFE_NAME_RE.match(name):
        raise ValueError(f"Invalid session name: {name!r}")
    return SESSION_DIR / f"{name}.json"


def save_session(data: dict, path: Path | None = None) -> None:
    """Serialize *data* as JSON to the session file (atomic write)."""
    dest = path or SESSION_FILE
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(dest.parent), suffix=".tmp", prefix="session_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, dest)
        except BaseException:
            # Clean up the temp file on failure
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        log.exception("Failed to save session to %s", dest)


def load_session(path: Path | None = None) -> dict | None:
    """Read and return the session dict, or *None* on any failure."""
    src = path or SESSION_FILE
    try:
        raw = src.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        log.warning("Corrupt session file at %s — starting fresh", src)
        return None

    if not isinstance(data, dict):
        return None

    version = data.get("version")
    if not isinstance(version, int) or version < 1 or version > CURRENT_VERSION:
        log.warning(
            "Unsupported session version %r in %s — starting fresh", version, src
        )
        return None

    return data


def save_named_session(name: str, data: dict) -> None:
    """Save a session under a user-chosen name."""
    save_session(data, _session_path(name))


def load_named_session(name: str) -> dict | None:
    """Load a previously saved named session."""
    return load_session(_session_path(name))


def list_sessions() -> list[str]:
    """Return sorted names of all saved sessions (excluding the auto-save)."""
    if not SESSION_DIR.is_dir():
        return []
    names = []
    for p in SESSION_DIR.glob("*.json"):
        if p.name == "session.json":
            continue
        names.append(p.stem)
    names.sort(key=str.casefold)
    return names


def delete_session(name: str) -> None:
    """Delete a named session file."""
    path = _session_path(name)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.exception("Failed to delete session %s", path)
