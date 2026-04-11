from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from web.auth import db
from web.auth.dependencies import get_current_user, require_admin
from web.auth.models import (
    AppCreateRequest,
    AppFullInfo,
    AppInfo,
    AppListResponse,
    ClaimRequest,
    ClaimResponse,
    DeleteSessionsResponse,
    GrantAppRequest,
    InviteCreateRequest,
    InviteCreateResponse,
    InviteInfo,
    InviteListResponse,
    MeResponse,
    PatchAppRequest,
    PatchUserRequest,
    SessionInfo,
    SessionListResponse,
    SuccessResponse,
    UserInfo,
    UserListResponse,
)

log = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "portal"
DEV_MODE = os.environ.get("TOOLS_DEV_MODE", "0") == "1"
BASE_URL = os.environ.get("TOOLS_BASE_URL", "http://localhost:8001")
COOKIE_MAX_AGE = 90 * 24 * 60 * 60  # 90 days


@asynccontextmanager
async def _lifespan(application: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Tools Auth Service", lifespan=_lifespan)


# ── Middleware: silent cookie refresh ────────────────────────────────────


@app.middleware("http")
async def refresh_session_cookie(request: Request, call_next):
    response = await call_next(request)
    # Don't re-stamp if the endpoint already set/deleted the cookie
    if request.url.path in ("/auth/claim", "/auth/logout"):
        return response
    token = request.cookies.get("tools_session")
    if token and 200 <= response.status_code < 400:
        response.set_cookie(
            key="tools_session",
            value=token,
            httponly=True,
            secure=not DEV_MODE,
            samesite="lax",
            path="/",
            max_age=COOKIE_MAX_AGE,
        )
    return response


# ── Middleware: CSRF origin check ────────────────────────────────────────

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def csrf_origin_check(request: Request, call_next):
    if request.method in _UNSAFE_METHODS:
        origin = request.headers.get("origin")
        if origin and not DEV_MODE:
            expected = BASE_URL.rstrip("/")
            if not origin.rstrip("/") == expected:
                log.warning(
                    "CSRF origin mismatch: expected=%s got=%s path=%s",
                    expected,
                    origin,
                    request.url.path,
                )
                return Response(
                    content='{"detail":"Origin mismatch"}',
                    status_code=403,
                    media_type="application/json",
                )
    return await call_next(request)


# ── Public Endpoints ─────────────────────────────────────────────────────


@app.get("/auth/login")
def login_page():
    html_path = FRONTEND_DIR / "login.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return HTMLResponse("<h1>Login page not found</h1>", status_code=500)


@app.post("/auth/claim")
def claim_code(
    body: ClaimRequest,
    request: Request,
    response: Response,
    conn: sqlite3.Connection = Depends(db.get_db),
):
    device_label = (request.headers.get("User-Agent") or "")[:200] or None
    code = body.code.strip().upper()
    result = db.claim_invite_code(conn, code, device_label)
    if not result:
        log.warning(
            "Failed invite claim attempt: code=%s ip=%s",
            code,
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=400, detail="Invalid or expired invite code")
    user_id, token = result
    log.info("Invite claimed: code=%s user_id=%s", code, user_id)
    response.set_cookie(
        key="tools_session",
        value=token,
        httponly=True,
        secure=not DEV_MODE,
        samesite="lax",
        path="/",
        max_age=COOKIE_MAX_AGE,
    )
    return ClaimResponse(success=True, redirect="/")


@app.post("/auth/logout")
def logout(
    request: Request,
    response: Response,
    conn: sqlite3.Connection = Depends(db.get_db),
):
    token = request.cookies.get("tools_session")
    if token:
        db.delete_session(conn, token)
    response.delete_cookie("tools_session", path="/")
    return RedirectResponse(url="/auth/login", status_code=302)


# ── Session-Required ─────────────────────────────────────────────────────


@app.get("/auth/me")
def me(
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    app_list = []
    for app_id in user.get("apps", []):
        info = db.get_app(conn, app_id)
        if info and info["is_active"]:
            app_list.append(
                AppInfo(
                    id=info["id"],
                    name=info["name"],
                    description=info["description"],
                    route_prefix=info["route_prefix"],
                )
            )
    return MeResponse(
        user_id=user["id"],
        display_name=user["display_name"],
        is_admin=bool(user["is_admin"]),
        apps=app_list,
    )


# ── Admin: Users ─────────────────────────────────────────────────────────


@app.get("/admin/users")
def list_users(
    _admin: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    users = db.get_all_users(conn)
    return UserListResponse(
        users=[
            UserInfo(
                id=u["id"],
                display_name=u["display_name"],
                is_active=bool(u["is_active"]),
                is_admin=bool(u["is_admin"]),
                created_at=u["created_at"],
                last_seen_at=u["last_seen_at"],
                apps=u["apps"],
            )
            for u in users
        ]
    )


@app.patch("/admin/users/{user_id}")
def update_user_endpoint(
    user_id: str,
    body: PatchUserRequest,
    admin: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    if body.is_active is False and user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    if not db.get_user(conn, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    db.update_user(conn, user_id, is_active=body.is_active, is_admin=body.is_admin)
    if body.is_active is False:
        log.info("User deactivated: user_id=%s by admin=%s", user_id, admin["id"])
    if body.is_admin is not None:
        log.info(
            "User admin flag changed: user_id=%s is_admin=%s by admin=%s",
            user_id,
            body.is_admin,
            admin["id"],
        )
    return SuccessResponse()


# ── Admin: Sessions ──────────────────────────────────────────────────────


@app.get("/admin/users/{user_id}/sessions")
def list_user_sessions(
    user_id: str,
    _admin: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    sessions = db.get_user_sessions(conn, user_id)
    return SessionListResponse(sessions=[SessionInfo(**s) for s in sessions])


@app.delete("/admin/users/{user_id}/sessions")
def delete_all_user_sessions(
    user_id: str,
    _admin: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    count = db.delete_user_sessions(conn, user_id)
    log.info(
        "All sessions killed: user_id=%s count=%d by admin=%s",
        user_id,
        count,
        _admin["id"],
    )
    return DeleteSessionsResponse(success=True, deleted_count=count)


@app.delete("/admin/sessions/{token_prefix}")
def delete_session_by_prefix(
    token_prefix: str,
    _admin: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    if not db.delete_session_by_prefix(conn, token_prefix):
        raise HTTPException(
            status_code=400, detail="No unique session matches that prefix"
        )
    log.info(
        "Session killed by prefix: prefix=%s by admin=%s", token_prefix, _admin["id"]
    )
    return SuccessResponse()


# ── Admin: Invites ───────────────────────────────────────────────────────


@app.post("/admin/invites")
def create_invite(
    body: InviteCreateRequest,
    _admin: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    if not body.display_name.strip():
        raise HTTPException(status_code=400, detail="Display name is required")
    if not body.app_permissions:
        raise HTTPException(status_code=400, detail="At least one app must be selected")
    for aid in body.app_permissions:
        if not db.get_app(conn, aid):
            raise HTTPException(status_code=400, detail=f"Unknown app: {aid}")
    code = db.create_invite(conn, body.display_name.strip(), body.app_permissions)
    log.info(
        "Invite created: code=%s name=%s apps=%s by admin=%s",
        code,
        body.display_name.strip(),
        body.app_permissions,
        _admin["id"],
    )
    link = f"{BASE_URL.rstrip('/')}/auth/login?code={code}"
    return InviteCreateResponse(code=code, link=link)


@app.get("/admin/invites")
def list_invites(
    _admin: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    invites = db.get_all_invites(conn)
    return InviteListResponse(
        invites=[
            InviteInfo(
                id=inv["id"],
                display_name=inv["display_name"],
                status=inv["status"],
                app_permissions=json.loads(inv["app_permissions"]),
                created_at=inv["created_at"],
                claimed_at=inv["claimed_at"],
                claimed_by=inv["claimed_by"],
            )
            for inv in invites
        ]
    )


@app.delete("/admin/invites/{code_id}")
def revoke_invite(
    code_id: str,
    _admin: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    invite = db.get_invite(conn, code_id)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite["status"] == "claimed":
        raise HTTPException(
            status_code=400,
            detail="Code already claimed — manage access through the user",
        )
    if invite["status"] == "revoked":
        raise HTTPException(status_code=400, detail="Code already revoked")
    db.revoke_invite(conn, code_id)
    log.info("Invite revoked: code=%s by admin=%s", code_id, _admin["id"])
    return SuccessResponse()


# ── Admin: App Access ────────────────────────────────────────────────────


@app.post("/admin/users/{user_id}/apps")
def grant_app(
    user_id: str,
    body: GrantAppRequest,
    _admin: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    if not db.get_user(conn, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    if not db.get_app(conn, body.app_id):
        raise HTTPException(status_code=400, detail=f"Unknown app: {body.app_id}")
    db.grant_app_access(conn, user_id, body.app_id)
    log.info(
        "App access granted: user_id=%s app=%s by admin=%s",
        user_id,
        body.app_id,
        _admin["id"],
    )
    return SuccessResponse()


@app.delete("/admin/users/{user_id}/apps/{app_id}")
def revoke_app(
    user_id: str,
    app_id: str,
    _admin: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    db.revoke_app_access(conn, user_id, app_id)
    log.info(
        "App access revoked: user_id=%s app=%s by admin=%s",
        user_id,
        app_id,
        _admin["id"],
    )
    return SuccessResponse()


# ── Admin: Apps ──────────────────────────────────────────────────────────


@app.get("/admin/apps")
def list_apps(
    _admin: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    apps = db.get_all_apps(conn)
    return AppListResponse(
        apps=[
            AppFullInfo(
                id=a["id"],
                name=a["name"],
                description=a["description"],
                route_prefix=a["route_prefix"],
                is_active=bool(a["is_active"]),
                created_at=a["created_at"],
            )
            for a in apps
        ]
    )


@app.post("/admin/apps")
def create_app_endpoint(
    body: AppCreateRequest,
    _admin: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    if not re.match(r"^[a-z0-9-]+$", body.id):
        raise HTTPException(
            status_code=400,
            detail="App ID must be lowercase alphanumeric + hyphens only",
        )
    if not body.route_prefix.startswith("/"):
        raise HTTPException(status_code=400, detail="Route prefix must start with /")
    if db.get_app(conn, body.id):
        raise HTTPException(status_code=400, detail=f"App '{body.id}' already exists")
    for a in db.get_all_apps(conn):
        if a["route_prefix"] == body.route_prefix:
            raise HTTPException(
                status_code=400,
                detail=f"Route prefix '{body.route_prefix}' already taken",
            )
    db.create_app(conn, body.id, body.name, body.description, body.route_prefix)
    return SuccessResponse()


@app.patch("/admin/apps/{app_id}")
def update_app_endpoint(
    app_id: str,
    body: PatchAppRequest,
    _admin: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    if not db.get_app(conn, app_id):
        raise HTTPException(status_code=404, detail="App not found")
    db.update_app(
        conn,
        app_id,
        name=body.name,
        description=body.description,
        is_active=body.is_active,
    )
    return SuccessResponse()


# ── Dev-mode: serve portal HTML ──────────────────────────────────────────

if DEV_MODE:

    @app.get("/")
    def portal_index():
        html_path = FRONTEND_DIR / "index.html"
        if html_path.exists():
            return FileResponse(html_path, media_type="text/html")
        return HTMLResponse("<h1>Portal not found</h1>", status_code=500)

    @app.get("/admin")
    def admin_page():
        html_path = FRONTEND_DIR / "admin.html"
        if html_path.exists():
            return FileResponse(html_path, media_type="text/html")
        return HTMLResponse("<h1>Admin page not found</h1>", status_code=500)
