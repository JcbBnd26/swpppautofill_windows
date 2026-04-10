from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import Cookie, Depends, HTTPException

from web.auth.db import get_db, validate_session

log = logging.getLogger(__name__)


def get_current_user(
    tools_session: str | None = Cookie(default=None),
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Resolve session cookie → active user dict.  Raises 401 on failure."""
    if not tools_session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = validate_session(db, tools_session)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user


def require_admin(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Raises 403 if `user` is not an admin."""
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_app(app_id: str):
    """Dependency factory — returns a checker that verifies app access."""

    def _check(
        user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        if app_id not in user.get("apps", []):
            raise HTTPException(status_code=403, detail=f"No access to app '{app_id}'")
        return user

    return _check
