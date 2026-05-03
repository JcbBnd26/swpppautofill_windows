from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from web.auth import db
from web.auth.dependencies import (
    get_current_user,
    require_admin,
    require_platform_admin,
)
from web.auth.models import (
    AppCreateRequest,
    AppFullInfo,
    AppInfo,
    AppListResponse,
    ClaimRequest,
    ClaimResponse,
    CompanyClaimRequest,
    CompanyClaimResponse,
    CompanyInfo,
    CompanyListResponse,
    CompanySignupInviteInfo,
    CompanySignupInviteListResponse,
    CompanySignupInviteRequest,
    CompanySignupInviteResponse,
    CompanyUserInfo,
    CreateUserRequest,
    CreateUserResponse,
    DeleteSessionsResponse,
    EmployeeInviteRequest,
    EmployeeInviteResponse,
    GrantAppRequest,
    InviteCreateRequest,
    InviteCreateResponse,
    InviteInfo,
    InviteListResponse,
    LoginRequest,
    LoginResponse,
    MailboxEntryPublic,
    MailboxProjectView,
    MeResponse,
    PatchAppRequest,
    PatchUserRequest,
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectInfo,
    ProjectListResponse,
    ProjectUpdateRequest,
    ResetPasswordResponse,
    RunDueReportsRequest,
    RunDueReportsResponse,
    SessionInfo,
    SessionListResponse,
    SetPasswordRequest,
    SuccessResponse,
    TemplatePromoteModeRequest,
    TemplateSaveRequest,
    TemplateVersionDetail,
    TemplateVersionInfo,
    TemplateVersionListResponse,
    UserInfo,
    UserListResponse,
    CompanyDashboardResponse,
    ProjectFailureSummary,
    RunLogEntry,
    RunLogResponse,
    PlatformDashboardResponse,
    ProblemProjectRow,
    CompanyHealthRow,
    ProjectArchiveRequest,
    ProjectArchiveResponse,
    ProjectArchiveStatusResponse,
    NotUploadResponse,
    CompanyRef,
    CompanyCreateRequest,
    CompanyAdminView,
    CompanyAdminListResponse,
)
from web.log_config import configure_logging

# ── Logging configuration ─────────────────────────────────────────────
# JSON-formatted logs for automated tooling (Tier 6 Fix 6C).
# Reads TOOLS_LOG_LEVEL from environment so dev (DEBUG) and prod (INFO)
# can differ without code changes.

_LOG_LEVEL = os.environ.get("TOOLS_LOG_LEVEL", "INFO")
configure_logging(_LOG_LEVEL)

log = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "portal"
DEV_MODE = os.environ.get("TOOLS_DEV_MODE", "0") == "1"
BASE_URL = os.environ.get("TOOLS_BASE_URL", "http://localhost:8001")
COOKIE_MAX_AGE = 90 * 24 * 60 * 60  # 90 days


# ── Helper Functions ──────────────────────────────────────────────────


def _resolve_mailbox_file_path(relative_path: str) -> Path:
    """Resolve a mailbox file path from relative to absolute.

    Args:
        relative_path: Relative path from mailbox entries table (e.g. "company123/project456/2026-05-01.pdf")

    Returns:
        Absolute path to the file
    """
    # Get data directory from environment or use default.
    data_dir = Path(os.environ.get("TOOLS_DATA_DIR", "web/data"))
    mailbox_root = data_dir / "mailbox"

    # Resolve relative path safely (prevent directory traversal).
    file_path = (mailbox_root / relative_path).resolve()

    # Ensure resolved path is still within mailbox root.
    if not str(file_path).startswith(str(mailbox_root.resolve())):
        raise ValueError(f"Invalid file path: {relative_path}")

    return file_path


def _generate_batch_zip(entries: list[dict[str, Any]], project_number: str) -> bytes:
    """Generate a ZIP file containing multiple mailbox entries.

    Args:
        entries: List of mailbox entry dicts
        project_number: Project number for filename generation

    Returns:
        ZIP file as bytes (in-memory)
    """
    from io import BytesIO
    from zipfile import ZipFile, ZIP_DEFLATED

    zip_buffer = BytesIO()

    with ZipFile(zip_buffer, "w", ZIP_DEFLATED) as zipf:
        for entry in entries:
            file_path = _resolve_mailbox_file_path(entry["file_path"])

            if not file_path.exists():
                log.warning(
                    "Mailbox file not found in batch: path=%s entry_id=%s",
                    file_path,
                    entry["id"],
                )
                continue

            # Use report_date for filename.
            filename = f"swppp_{entry['report_date']}.pdf"

            # Add file to ZIP.
            zipf.write(file_path, arcname=filename)

    zip_buffer.seek(0)
    return zip_buffer.read()


@asynccontextmanager
async def _lifespan(application: FastAPI):
    log.info("Auth service starting: dev_mode=%s base_url=%s", DEV_MODE, BASE_URL)
    db.init_db()
    log.info("Database initialized: path=%s", db.DB_PATH)
    yield
    log.info("Auth service shutting down")


app = FastAPI(title="Tools Auth Service", lifespan=_lifespan)


# ── Middleware: silent cookie refresh ────────────────────────────────────


@app.middleware("http")
async def refresh_session_cookie(request: Request, call_next):
    response = await call_next(request)

    # Respect any downstream decision to set or clear the session cookie.
    has_session_cookie = any(
        name == b"set-cookie" and b"tools_session=" in value
        for name, value in response.headers.raw
    )
    if has_session_cookie:
        return response

    token = request.cookies.get("tools_session")
    if token and 200 <= response.status_code < 300:
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

from web.middleware import create_csrf_middleware

_csrf_check = create_csrf_middleware(expected_origin=BASE_URL, dev_mode=DEV_MODE)


@app.middleware("http")
async def csrf_origin_check(request: Request, call_next):
    return await _csrf_check(request, call_next)


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
    conn: sqlite3.Connection = Depends(db.get_db),
):
    token = request.cookies.get("tools_session")
    if token:
        db.delete_session(conn, token)
    redirect = RedirectResponse(url="/auth/login", status_code=302)
    redirect.delete_cookie(
        key="tools_session",
        path="/",
        httponly=True,
        samesite="lax",
        secure=not DEV_MODE,
    )
    return redirect


@app.post("/auth/signin")
def login_password(
    body: LoginRequest,
    request: Request,
    response: Response,
    conn: sqlite3.Connection = Depends(db.get_db),
):
    device_label = (request.headers.get("User-Agent") or "")[:200] or None
    user = db.authenticate_user(conn, body.display_name.strip(), body.password)
    if not user:
        log.warning(
            "Failed password login attempt: name=%s ip=%s",
            body.display_name.strip(),
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=401, detail="Invalid name or password")
    token = db.create_session(conn, user["id"], device_label)
    log.info("Password login: user_id=%s name=%s", user["id"], user["display_name"])
    response.set_cookie(
        key="tools_session",
        value=token,
        httponly=True,
        secure=not DEV_MODE,
        samesite="lax",
        path="/",
        max_age=COOKIE_MAX_AGE,
    )
    return LoginResponse(success=True, redirect="/")


@app.post("/auth/set-password")
def set_password(
    body: SetPasswordRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    if db.user_has_password(conn, user["id"]):
        if not body.current_password:
            log.warning(
                "Set-password without current_password: user_id=%s ip=%s",
                user["id"],
                request.client.host if request.client else "unknown",
            )
            raise HTTPException(status_code=400, detail="Current password is required")
        if not db.verify_user_password(conn, user["id"], body.current_password):
            log.warning(
                "Set-password with wrong current_password: user_id=%s ip=%s",
                user["id"],
                request.client.host if request.client else "unknown",
            )
            raise HTTPException(status_code=401, detail="Current password is incorrect")

    db.set_user_password(conn, user["id"], body.password)

    # Invalidate all other sessions. The current session stays alive so
    # the user is not immediately logged out on their own device.
    current_token = request.cookies.get("tools_session", "")
    killed = db.delete_sessions_except(conn, user["id"], current_token)
    log.info(
        "Password changed: user_id=%s other_sessions_revoked=%d",
        user["id"],
        killed,
    )
    return SuccessResponse()


# ── Health Check ─────────────────────────────────────────────────────────


@app.get("/health")
def health_check(conn: sqlite3.Connection = Depends(db.get_db)):
    """Unauthenticated health check. Verifies DB connectivity.
    Returns 200 if healthy, 503 if the database is unreachable.
    """
    try:
        conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        log.error("Health check: DB connectivity failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=f"Database unreachable: {exc}",
        )
    return {
        "status": "ok",
        "service": "tools-auth",
        "db": str(db.DB_PATH),
        "timestamp": db._now(),
    }


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
    user_companies = db.get_user_companies(conn, user["id"])
    company_list = [
        CompanyRef(id=c["id"], display_name=c["display_name"], role=c["role"])
        for c in user_companies
    ]
    return MeResponse(
        user_id=user["id"],
        display_name=user["display_name"],
        is_admin=bool(user["is_admin"]),
        is_platform_admin=bool(user.get("is_platform_admin", 0)),
        apps=app_list,
        companies=company_list,
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
                is_platform_admin=bool(u.get("is_platform_admin", 0)),
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
    if body.is_platform_admin is False and user_id == admin["id"]:
        raise HTTPException(
            status_code=400, detail="Cannot demote yourself from platform admin"
        )
    if body.is_platform_admin is not None and not admin.get("is_platform_admin"):
        raise HTTPException(
            status_code=403,
            detail="Only platform admins can change platform_admin flag",
        )
    if not db.get_user(conn, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    db.update_user(
        conn,
        user_id,
        is_active=body.is_active,
        is_admin=body.is_admin,
        is_platform_admin=body.is_platform_admin,
    )
    if body.is_active is False:
        log.info("User deactivated: user_id=%s by admin=%s", user_id, admin["id"])
    if body.is_admin is not None:
        log.info(
            "User admin flag changed: user_id=%s is_admin=%s by admin=%s",
            user_id,
            body.is_admin,
            admin["id"],
        )
    if body.is_platform_admin is not None:
        log.info(
            "User platform_admin flag changed: user_id=%s is_platform_admin=%s by admin=%s",
            user_id,
            body.is_platform_admin,
            admin["id"],
        )
    return SuccessResponse()


@app.post("/admin/users")
def create_user_endpoint(
    body: CreateUserRequest,
    _admin: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    name = body.display_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Display name is required")
    if not body.app_permissions:
        raise HTTPException(status_code=400, detail="At least one app must be selected")
    for aid in body.app_permissions:
        if not db.get_app(conn, aid):
            raise HTTPException(status_code=400, detail=f"Unknown app: {aid}")
    existing = conn.execute(
        "SELECT 1 FROM users WHERE display_name = ? COLLATE NOCASE LIMIT 1",
        (name,),
    ).fetchone()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"A user named '{name}' already exists",
        )
    user_id = db.create_user(conn, name, is_admin=body.is_admin)
    for aid in body.app_permissions:
        db.grant_app_access(conn, user_id, aid)
    password = db.generate_password()
    db.set_user_password(conn, user_id, password)
    log.info(
        "User created by admin: user_id=%s name=%s is_admin=%s apps=%s by admin=%s",
        user_id,
        name,
        body.is_admin,
        body.app_permissions,
        _admin["id"],
    )
    return CreateUserResponse(user_id=user_id, display_name=name, password=password)


@app.post("/admin/users/{user_id}/reset-password")
def reset_user_password(
    user_id: str,
    _admin: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    user = db.get_user(conn, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    password = db.generate_password()
    db.set_user_password(conn, user_id, password)

    # Kill ALL sessions for the target user.
    # An admin-initiated reset is a security action; the user must re-authenticate
    # on all devices with the new credential.
    killed = db.delete_user_sessions(conn, user_id)
    log.info(
        "Password reset by admin: user_id=%s by admin=%s sessions_revoked=%d",
        user_id,
        _admin["id"],
        killed,
    )
    return ResetPasswordResponse(
        user_id=user_id,
        display_name=user["display_name"],
        password=password,
    )


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
    existing = conn.execute(
        "SELECT 1 FROM users WHERE display_name = ? COLLATE NOCASE LIMIT 1",
        (body.display_name.strip(),),
    ).fetchone()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"A user named '{body.display_name.strip()}' already exists",
        )
    existing_invite = conn.execute(
        "SELECT 1 FROM invite_codes "
        "WHERE display_name = ? COLLATE NOCASE AND status = 'pending' LIMIT 1",
        (body.display_name.strip(),),
    ).fetchone()
    if existing_invite:
        raise HTTPException(
            status_code=400,
            detail=f"A pending invite for '{body.display_name.strip()}' already exists",
        )
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


# ── Portal Pages (server-side auth gate) ─────────────────────────────────
#
# These routes check the session cookie server-side and either serve the
# HTML or redirect to /auth/login.  This prevents unauthenticated users
# from seeing the page source and eliminates the "Loading..." flash.
#
# NOTE: We do NOT use the get_current_user dependency here because it
# raises HTTPException(401) on failure, which returns JSON.  For page
# routes we need a 302 redirect instead, so we check the cookie manually.


@app.get("/")
def portal_index(
    request: Request,
    conn: sqlite3.Connection = Depends(db.get_db),
):
    token = request.cookies.get("tools_session")
    if not token:
        return RedirectResponse(url="/auth/login", status_code=302)
    user = db.validate_session(conn, token)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=302)
    html_path = FRONTEND_DIR / "index.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return HTMLResponse("<h1>Portal not found</h1>", status_code=500)


@app.get("/admin")
def admin_page(
    request: Request,
    conn: sqlite3.Connection = Depends(db.get_db),
):
    token = request.cookies.get("tools_session")
    if not token:
        return RedirectResponse(url="/auth/login", status_code=302)
    user = db.validate_session(conn, token)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=302)
    if not user.get("is_admin"):
        return RedirectResponse(url="/", status_code=302)
    html_path = FRONTEND_DIR / "admin.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return HTMLResponse("<h1>Admin page not found</h1>", status_code=500)


@app.get("/signup/{token}")
def signup_page(token: str):
    html_path = FRONTEND_DIR / "signup.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return HTMLResponse("<h1>Signup page not found</h1>", status_code=500)


# ── Platform Admin: Company Signup Invites ───────────────────────────────


@app.post("/admin/company-signup-invites")
def create_company_signup_invite(
    body: CompanySignupInviteRequest,
    admin: dict[str, Any] = Depends(require_platform_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    name = body.proposed_company_name.strip()
    email = body.admin_email.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Company name is required")
    if not email:
        raise HTTPException(status_code=400, detail="Admin email is required")
    token = db.create_company_signup_invite(conn, name, email, created_by=admin["id"])
    link = f"{BASE_URL.rstrip('/')}/signup/{token}"
    log.info(
        "Company signup invite created: proposed=%s email=%s by=%s",
        name,
        email,
        admin["id"],
    )
    return CompanySignupInviteResponse(token=token, link=link)


@app.get("/admin/companies")
def list_companies_admin(
    _admin: dict[str, Any] = Depends(require_platform_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    rows = conn.execute("""
        SELECT
            c.id,
            c.display_name,
            c.legal_name,
            c.primary_timezone AS timezone,
            c.created_at,
            COUNT(DISTINCT cu.user_id) AS member_count,
            COUNT(DISTINCT p.id)       AS project_count
        FROM companies c
        LEFT JOIN company_users cu ON cu.company_id = c.id AND cu.is_active = 1
        LEFT JOIN projects p       ON p.company_id  = c.id
        WHERE c.is_active = 1
        GROUP BY c.id
        ORDER BY c.display_name
        """).fetchall()
    return CompanyAdminListResponse(
        companies=[
            CompanyAdminView(
                id=r["id"],
                display_name=r["display_name"],
                legal_name=r["legal_name"],
                timezone=r["timezone"] or "America/Chicago",
                created_at=r["created_at"],
                member_count=r["member_count"],
                project_count=r["project_count"],
            )
            for r in rows
        ]
    )


@app.post("/admin/companies", status_code=201)
def create_company_direct(
    body: CompanyCreateRequest,
    admin: dict[str, Any] = Depends(require_platform_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Platform admin directly creates a company and is added as company_admin."""
    legal_name = body.legal_name.strip()
    display_name = body.display_name.strip()
    if not legal_name:
        raise HTTPException(status_code=400, detail="legal_name is required")
    if not display_name:
        raise HTTPException(status_code=400, detail="display_name is required")
    company_id = db.create_company(
        conn,
        legal_name=legal_name,
        display_name=display_name,
        timezone=body.timezone,
        created_by=admin["id"],
    )
    db.add_company_user(conn, admin["id"], company_id, role="company_admin")
    log.info(
        "Company created directly by platform admin: id=%s name=%s admin=%s",
        company_id,
        legal_name,
        admin["id"],
    )
    return {"id": company_id, "display_name": display_name}


@app.get("/admin/company-signup-invites")
def list_company_signup_invites(
    _admin: dict[str, Any] = Depends(require_platform_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    invites = db.get_all_company_signup_invites(conn)
    return CompanySignupInviteListResponse(
        invites=[
            CompanySignupInviteInfo(
                token=inv["token"],
                proposed_company_name=inv["proposed_company_name"],
                admin_email=inv["admin_email"],
                created_at=inv["created_at"],
                expires_at=inv["expires_at"],
                claimed_at=inv["claimed_at"],
            )
            for inv in invites
        ]
    )


# ── Public: Company Signup Claim ─────────────────────────────────────────


@app.get("/auth/signup-invite/{token}")
def get_signup_invite_info(
    token: str,
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Return minimal info about a signup invite so the page can pre-fill the company name."""
    invite = db.get_company_signup_invite(conn, token)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite["claimed_at"] is not None:
        raise HTTPException(status_code=410, detail="This invite has already been used")
    now_dt = datetime.now(timezone.utc)
    if invite["expires_at"] < now_dt.isoformat():
        raise HTTPException(status_code=410, detail="This invite has expired")
    return {
        "proposed_company_name": invite["proposed_company_name"],
        "admin_email": invite["admin_email"],
    }


@app.post("/auth/signup")
def claim_company_signup(
    body: CompanyClaimRequest,
    request: Request,
    response: Response,
    conn: sqlite3.Connection = Depends(db.get_db),
):
    device_label = (request.headers.get("User-Agent") or "")[:200] or None
    result = db.claim_company_signup_invite(
        conn,
        token=body.token,
        display_name=body.display_name.strip(),
        password=body.password,
        legal_name=body.legal_name.strip(),
        company_display_name=body.company_display_name.strip(),
        tz=body.timezone,
        address=body.address,
        phone=body.phone,
        website=body.website,
        device_label=device_label,
    )
    if not result:
        log.warning(
            "Company signup claim failed: token=%s ip=%s",
            body.token,
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=400, detail="Invalid, expired, or already-used signup invite"
        )
    user_id, company_id, session_token = result
    log.info("Company signup complete: company_id=%s user_id=%s", company_id, user_id)
    response.set_cookie(
        key="tools_session",
        value=session_token,
        httponly=True,
        secure=not DEV_MODE,
        samesite="lax",
        path="/",
        max_age=COOKIE_MAX_AGE,
    )
    return CompanyClaimResponse(success=True, company_id=company_id, redirect="/")


# ── Company Admin: Employee Invites ──────────────────────────────────────


@app.post("/companies/{company_id}/invites")
def create_employee_invite(
    company_id: str,
    body: EmployeeInviteRequest,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    # Only company_admin (or platform admin) may invite employees.
    if not user.get("is_platform_admin"):
        membership = db.get_company_user(conn, user["id"], company_id)
        if not membership or membership["role"] != "company_admin":
            raise HTTPException(status_code=403, detail="Company admin access required")
    company = db.get_company(conn, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if body.role not in db.COMPANY_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role. Must be one of: {sorted(db.COMPANY_ROLES)}",
        )
    name = body.display_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Display name is required")
    if not body.app_permissions:
        raise HTTPException(status_code=400, detail="At least one app must be selected")
    for aid in body.app_permissions:
        if not db.get_app(conn, aid):
            raise HTTPException(status_code=400, detail=f"Unknown app: {aid}")
    existing = conn.execute(
        "SELECT 1 FROM users WHERE display_name = ? COLLATE NOCASE LIMIT 1", (name,)
    ).fetchone()
    if existing:
        raise HTTPException(
            status_code=400, detail=f"A user named '{name}' already exists"
        )
    code = db.create_employee_invite(
        conn, name, company_id, body.role, body.app_permissions
    )
    link = f"{BASE_URL.rstrip('/')}/auth/login?code={code}"
    log.info(
        "Employee invite created: code=%s name=%s role=%s company=%s by=%s",
        code,
        name,
        body.role,
        company_id,
        user["id"],
    )
    return EmployeeInviteResponse(code=code, link=link)


# ── Company Admin: Member Management ─────────────────────────────────────


@app.get("/companies/{company_id}/members")
def list_company_members(
    company_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    if not user.get("is_platform_admin"):
        membership = db.get_company_user(conn, user["id"], company_id)
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this company")
    company = db.get_company(conn, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    members = db.get_company_members(conn, company_id)
    return {
        "company_id": company_id,
        "members": [
            CompanyUserInfo(
                user_id=m["user_id"],
                display_name=m["display_name"],
                role=m["role"],
                joined_at=m["joined_at"],
            )
            for m in members
        ],
    }


@app.patch("/companies/{company_id}/members/{user_id}")
def update_member_role(
    company_id: str,
    user_id: str,
    body: dict[str, Any],
    admin_user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    if not admin_user.get("is_platform_admin"):
        membership = db.get_company_user(conn, admin_user["id"], company_id)
        if not membership or membership["role"] != "company_admin":
            raise HTTPException(status_code=403, detail="Company admin access required")
    new_role = body.get("role", "").strip()
    if new_role not in db.COMPANY_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role. Must be one of: {sorted(db.COMPANY_ROLES)}",
        )
    target = db.get_company_user(conn, user_id, company_id)
    if not target:
        raise HTTPException(status_code=404, detail="Member not found in this company")
    conn.execute(
        "UPDATE company_users SET role = ? WHERE user_id = ? AND company_id = ?",
        (new_role, user_id, company_id),
    )
    log.info(
        "Member role updated: user=%s company=%s role=%s by=%s",
        user_id,
        company_id,
        new_role,
        admin_user["id"],
    )
    return SuccessResponse()


@app.delete("/companies/{company_id}/members/{user_id}")
def remove_company_member(
    company_id: str,
    user_id: str,
    admin_user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    if not admin_user.get("is_platform_admin"):
        membership = db.get_company_user(conn, admin_user["id"], company_id)
        if not membership or membership["role"] != "company_admin":
            raise HTTPException(status_code=403, detail="Company admin access required")
    if user_id == admin_user["id"]:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")
    target = db.get_company_user(conn, user_id, company_id)
    if not target:
        raise HTTPException(status_code=404, detail="Member not found in this company")
    conn.execute(
        "UPDATE company_users SET is_active = 0 WHERE user_id = ? AND company_id = ?",
        (user_id, company_id),
    )
    log.info(
        "Member removed: user=%s company=%s by=%s",
        user_id,
        company_id,
        admin_user["id"],
    )
    return SuccessResponse()


# ── Projects (IR-1) ──────────────────────────────────────────────────────


@app.post("/companies/{company_id}/projects")
def create_project(
    company_id: str,
    body: ProjectCreateRequest,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Create a new project. Requires company membership (any role)."""
    # Verify company membership (or platform admin).
    if not user.get("is_platform_admin"):
        membership = db.get_company_user(conn, user["id"], company_id)
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this company")
        # Only company_admin and pm can create projects.
        if membership["role"] not in ("company_admin", "pm"):
            raise HTTPException(
                status_code=403,
                detail="Project creation requires company_admin or pm role",
            )

    # Verify company exists.
    company = db.get_company(conn, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # Create the project.
    try:
        project_data = body.model_dump(exclude_none=True)
        project_id = db.create_project(
            conn,
            company_id=company_id,
            created_by_user_id=user["id"],
            **project_data,
        )
    except ValueError as e:
        if "already exists" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))

    log.info(
        "Project created: id=%s number=%s company=%s by=%s",
        project_id,
        body.project_number,
        company_id,
        user["id"],
    )
    return {"id": project_id}


@app.get("/companies/{company_id}/projects")
def list_company_projects(
    company_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """List all projects for a company. Requires company membership."""
    # Verify company membership (or platform admin).
    if not user.get("is_platform_admin"):
        membership = db.get_company_user(conn, user["id"], company_id)
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this company")

    # Verify company exists.
    company = db.get_company(conn, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    projects = db.get_company_projects(conn, company_id)
    return ProjectListResponse(
        projects=[
            ProjectInfo(
                id=p["id"],
                company_id=p["company_id"],
                project_number=p["project_number"],
                project_name=p["project_name"],
                site_address=p["site_address"],
                timezone=p["timezone"],
                rain_station_code=p["rain_station_code"],
                status=p["status"],
                auto_weekly_enabled=bool(p["auto_weekly_enabled"]),
                last_successful_run_at=p["last_successful_run_at"],
                last_run_status=p["last_run_status"],
                created_at=p["created_at"],
            )
            for p in projects
        ]
    )


@app.get("/companies/{company_id}/projects/{project_id}")
def get_project_detail(
    company_id: str,
    project_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Get project detail. Requires company membership."""
    # Verify company membership (or platform admin).
    if not user.get("is_platform_admin"):
        membership = db.get_company_user(conn, user["id"], company_id)
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this company")

    # Get project with tenant isolation.
    project = db.get_project_for_company(conn, project_id, company_id)
    if not project:
        # Return 404 whether project doesn't exist or belongs to another company (tenant isolation).
        raise HTTPException(status_code=404, detail="Project not found")

    return ProjectDetailResponse(**project)


@app.patch("/companies/{company_id}/projects/{project_id}")
def update_project_settings(
    company_id: str,
    project_id: str,
    body: ProjectUpdateRequest,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Update project settings. Requires company_admin or pm role."""
    # Verify company membership (or platform admin).
    if not user.get("is_platform_admin"):
        membership = db.get_company_user(conn, user["id"], company_id)
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this company")
        # Only company_admin and pm can update projects.
        if membership["role"] not in ("company_admin", "pm"):
            raise HTTPException(
                status_code=403,
                detail="Project update requires company_admin or pm role",
            )

    # Verify project exists and belongs to company (tenant isolation).
    project = db.get_project_for_company(conn, project_id, company_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Update project with provided fields.
    update_data = body.model_dump(exclude_none=True)
    if update_data:
        db.update_project(conn, project_id, **update_data)
        log.info(
            "Project updated: id=%s fields=%s by=%s",
            project_id,
            list(update_data.keys()),
            user["id"],
        )

    return SuccessResponse()


# ── Company Dashboard + Run Log + PM Run Trigger (IR-5) ──────────────────


@app.get("/companies/{company_id}/dashboard", response_model=CompanyDashboardResponse)
def get_company_dashboard(
    company_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Company health dashboard (project counts + recent failures)."""
    if not user.get("is_platform_admin"):
        membership = db.get_company_user(conn, user["id"], company_id)
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this company")

    data = db.get_company_dashboard(conn, company_id)
    return CompanyDashboardResponse(
        total_projects=data["total_projects"],
        active=data["active"],
        failing=data["failing"],
        paused=data["paused"],
        setup_incomplete=data["setup_incomplete"],
        recent_failures=[ProjectFailureSummary(**f) for f in data["recent_failures"]],
    )


@app.get(
    "/companies/{company_id}/projects/{project_id}/run-log",
    response_model=RunLogResponse,
)
def get_project_run_log(
    company_id: str,
    project_id: str,
    limit: int = Query(default=30, le=100),
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Return run log entries for a project (newest first)."""
    if not user.get("is_platform_admin"):
        membership = db.get_company_user(conn, user["id"], company_id)
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this company")

    project = db.get_project_for_company(conn, project_id, company_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    entries = db.get_project_run_log(conn, project_id, limit=limit)
    return RunLogResponse(entries=[RunLogEntry(**e) for e in entries])


@app.post(
    "/companies/{company_id}/run-due-reports",
    response_model=RunDueReportsResponse,
)
def run_company_reports(
    company_id: str,
    body: RunDueReportsRequest,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Trigger the reconciliation scheduler scoped to this company (pm or company_admin)."""
    import time

    from web.scheduler.run_due_reports import run_due_reports

    if not user.get("is_platform_admin"):
        membership = db.get_company_user(conn, user["id"], company_id)
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this company")
        if membership["role"] not in ("company_admin", "pm"):
            raise HTTPException(
                status_code=403,
                detail="Requires company_admin or pm role",
            )

    start_ms = time.monotonic()
    result = run_due_reports(
        conn, dry_run=False, force=body.force, company_id=company_id
    )
    duration_ms = int((time.monotonic() - start_ms) * 1000)

    log.info(
        "run-due-reports triggered by company member: company=%s projects=%d filed=%d failures=%d skipped=%d",
        company_id,
        result["projects_processed"],
        result["reports_filed"],
        result["failures"],
        result["skipped"],
    )

    return RunDueReportsResponse(
        projects_processed=result["projects_processed"],
        reports_filed=result["reports_filed"],
        failures=result["failures"],
        skipped=result["skipped"],
        duration_ms=duration_ms,
    )


# ── Project Template Versions (IR-2) ─────────────────────────────────────


@app.post("/companies/{company_id}/projects/{project_id}/template", status_code=201)
def save_template_version(
    company_id: str,
    project_id: str,
    body: TemplateSaveRequest,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Save a new template version. Auto or manual promote based on project setting."""
    # Verify company membership (or platform admin).
    if not user.get("is_platform_admin"):
        membership = db.get_company_user(conn, user["id"], company_id)
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this company")
        # Only company_admin and pm can save templates.
        if membership["role"] not in ("company_admin", "pm"):
            raise HTTPException(
                status_code=403,
                detail="Template save requires company_admin or pm role",
            )

    # Verify project exists and belongs to company (tenant isolation).
    project = db.get_project_for_company(conn, project_id, company_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Create template version.
    template_data = body.template_data.model_dump()
    version_id = db.create_template_version(
        conn,
        project_id=project_id,
        created_by_user_id=user["id"],
        template_data=template_data,
    )

    log.info(
        "Template version saved: version_id=%s project_id=%s by=%s",
        version_id,
        project_id,
        user["id"],
    )
    return {"id": version_id}


@app.get("/companies/{company_id}/projects/{project_id}/template")
def get_template_versions(
    company_id: str,
    project_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Get all template versions for a project. Requires company membership."""
    # Verify company membership (or platform admin).
    if not user.get("is_platform_admin"):
        membership = db.get_company_user(conn, user["id"], company_id)
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this company")

    # Verify project exists and belongs to company (tenant isolation).
    project = db.get_project_for_company(conn, project_id, company_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get all versions.
    versions = db.get_template_versions(conn, project_id)
    version_infos = [
        TemplateVersionInfo(
            id=v["id"],
            project_id=v["project_id"],
            version_number=v["version_number"],
            status=v["status"],
            created_at=v["created_at"],
            created_by_user_id=v["created_by_user_id"],
            promoted_at=v["promoted_at"],
            promoted_by_user_id=v["promoted_by_user_id"],
            superseded_at=v["superseded_at"],
        )
        for v in versions
    ]

    return TemplateVersionListResponse(
        versions=version_infos,
        active_version_id=project["active_template_version_id"],
    )


@app.get("/companies/{company_id}/projects/{project_id}/template/{version_id}")
def get_template_version_detail(
    company_id: str,
    project_id: str,
    version_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Get specific template version detail including template_data."""
    # Verify company membership (or platform admin).
    if not user.get("is_platform_admin"):
        membership = db.get_company_user(conn, user["id"], company_id)
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this company")

    # Verify project exists and belongs to company (tenant isolation).
    project = db.get_project_for_company(conn, project_id, company_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get version.
    version = db.get_template_version(conn, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Template version not found")

    # Verify version belongs to this project (tenant isolation).
    if version["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="Template version not found")

    return TemplateVersionDetail(**version)


@app.post("/companies/{company_id}/projects/{project_id}/template/{version_id}/promote")
def promote_template_version_endpoint(
    company_id: str,
    project_id: str,
    version_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Manually promote a draft template version to active."""
    # Verify company membership (or platform admin).
    if not user.get("is_platform_admin"):
        membership = db.get_company_user(conn, user["id"], company_id)
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this company")
        # Only company_admin and pm can promote templates.
        if membership["role"] not in ("company_admin", "pm"):
            raise HTTPException(
                status_code=403,
                detail="Template promote requires company_admin or pm role",
            )

    # Verify project exists and belongs to company (tenant isolation).
    project = db.get_project_for_company(conn, project_id, company_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get version.
    version = db.get_template_version(conn, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Template version not found")

    # Verify version belongs to this project (tenant isolation).
    if version["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="Template version not found")

    # Promote version.
    try:
        db.promote_template_version(conn, version_id, user["id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log.info(
        "Template version promoted: version_id=%s project_id=%s by=%s",
        version_id,
        project_id,
        user["id"],
    )
    return SuccessResponse()


@app.post(
    "/companies/{company_id}/projects/{project_id}/template/{version_id}/revert",
    status_code=201,
)
def revert_template_version(
    company_id: str,
    project_id: str,
    version_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Revert to a previous template version by creating a new version with old data."""
    # Verify company membership (or platform admin).
    if not user.get("is_platform_admin"):
        membership = db.get_company_user(conn, user["id"], company_id)
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this company")
        # Only company_admin and pm can revert templates.
        if membership["role"] not in ("company_admin", "pm"):
            raise HTTPException(
                status_code=403,
                detail="Template revert requires company_admin or pm role",
            )

    # Verify project exists and belongs to company (tenant isolation).
    project = db.get_project_for_company(conn, project_id, company_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get old version.
    old_version = db.get_template_version(conn, version_id)
    if not old_version:
        raise HTTPException(status_code=404, detail="Template version not found")

    # Verify version belongs to this project (tenant isolation).
    if old_version["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="Template version not found")

    # Create new version with old template_data.
    new_version_id = db.create_template_version(
        conn,
        project_id=project_id,
        created_by_user_id=user["id"],
        template_data=old_version["template_data"],
    )

    log.info(
        "Template version reverted: new_version=%s old_version=%s project_id=%s by=%s",
        new_version_id,
        version_id,
        project_id,
        user["id"],
    )
    return {"id": new_version_id}


@app.get("/companies/{company_id}/projects/{project_id}/template/preview")
def preview_template(
    company_id: str,
    project_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Generate a watermarked preview PDF from the active template version."""
    from datetime import date, timedelta
    from pathlib import Path
    import tempfile
    import shutil

    # Verify company membership (or platform admin).
    if not user.get("is_platform_admin"):
        membership = db.get_company_user(conn, user["id"], company_id)
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this company")

    # Verify project exists and belongs to company (tenant isolation).
    project = db.get_project_for_company(conn, project_id, company_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get active template version.
    active_version = db.get_active_template_version(conn, project_id)
    if not active_version:
        raise HTTPException(
            status_code=400,
            detail="No active template version. Please save a template first.",
        )

    # Import SWPPP generation functions
    try:
        from app.core.config_manager import (
            build_project_info,
            build_run_options,
            load_mapping,
        )
        from app.core.dates import weekly_dates
        from app.core.fill import generate_batch
        from web.swppp_api.main import add_preview_watermark, TEMPLATE_PDF, MAPPING_YAML
    except ImportError as exc:
        log.error("Failed to import SWPPP generation modules: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Preview generation unavailable",
        )

    # Convert template_data to project_fields format
    template_data = active_version["template_data"]
    project_fields = {}

    # Map named fields from TemplateVersionData
    field_names = [
        "job_piece",
        "project_number",
        "contract_id",
        "location_description_1",
        "location_description_2",
        "re_odot_contact_1",
        "re_odot_contact_2",
        "inspection_type",
        "inspected_by",
        "reviewed_by",
    ]
    for field_name in field_names:
        value = template_data.get(field_name)
        if value is not None:
            project_fields[field_name] = str(value)

    # Add extra_fields if present
    extra_fields = template_data.get("extra_fields", {})
    if extra_fields:
        project_fields.update(extra_fields)

    # Get checkboxes if present
    checkbox_states = template_data.get("checkboxes", {})

    # Calculate next Monday (start of next inspection week)
    today = date.today()
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7  # If today is Monday, get next Monday
    next_monday = today + timedelta(days=days_until_monday)

    # Generate preview for next week only
    tmpdir = tempfile.mkdtemp(prefix="swppp_preview_")

    try:
        mapping = load_mapping(MAPPING_YAML)
        project_obj = build_project_info(project_fields)
        options = build_run_options(
            output_dir=tmpdir,
            start_date=next_monday.isoformat(),
            end_date=next_monday.isoformat(),
            make_zip=False,
        )

        dates = [next_monday]

        created = generate_batch(
            template_path=str(TEMPLATE_PDF),
            project=project_obj,
            options=options,
            dates=dates,
            mapping=mapping,
            checkbox_states=checkbox_states or None,
            notes_texts=None,
        )

        if not created:
            raise HTTPException(
                status_code=500,
                detail="PDF generation produced no output",
            )

        # Read the generated PDF
        pdf_path = Path(created[0])
        if not pdf_path.exists():
            raise HTTPException(
                status_code=500,
                detail="Generated PDF not found",
            )

        pdf_bytes = pdf_path.read_bytes()

        # Apply watermark
        watermarked_pdf = add_preview_watermark(pdf_bytes)

        # Clean up temp directory
        shutil.rmtree(tmpdir, ignore_errors=True)

        # Return watermarked PDF
        return Response(
            content=watermarked_pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="preview_{next_monday.isoformat()}.pdf"'
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        log.error("Preview generation failed: %s", exc, exc_info=True)
        # Clean up on error
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise HTTPException(
            status_code=500,
            detail=f"Preview generation failed: {str(exc)}",
        )


# ── Public Mailbox (IR-3) ─────────────────────────────────────────────
# All endpoints are fully public with no authentication required.


@app.get("/mailbox/{project_number}")
def get_mailbox_for_project(
    project_number: str,
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Get mailbox entries for a project (public, no auth)."""
    # Find project by project_number (globally unique).
    project = conn.execute(
        "SELECT id, project_number, project_name, site_address, company_id,"
        " status, archived_at, archive_zip_path, not_document_path"
        " FROM projects WHERE project_number = ?",
        (project_number,),
    ).fetchone()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get mailbox entries.
    entries = db.get_mailbox_entries(conn, project["id"], sort_order="desc")

    # Convert to public model (hide internal fields).
    public_entries = [
        MailboxEntryPublic(
            id=entry["id"],
            report_date=entry["report_date"],
            report_type=entry["report_type"],
            generation_mode=entry["generation_mode"],
            file_size_bytes=entry["file_size_bytes"],
            created_at=entry["created_at"],
        )
        for entry in entries
    ]

    # Get entry count.
    entry_count = db.get_mailbox_entry_count(conn, project["id"])

    is_archived = project["status"] == "archived"
    return MailboxProjectView(
        project_number=project["project_number"],
        project_name=project["project_name"],
        site_address=project["site_address"],
        entry_count=entry_count,
        entries=public_entries,
        is_archived=is_archived,
        archived_at=project["archived_at"] if is_archived else None,
        archive_zip_ready=bool(project["archive_zip_path"]) if is_archived else False,
        not_on_file=bool(project["not_document_path"]) if is_archived else False,
    )


@app.get("/mailbox/{project_number}/download/{entry_id}")
def download_mailbox_entry(
    project_number: str,
    entry_id: str,
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Download a single mailbox entry PDF (public, no auth)."""
    # Verify project exists.
    project = conn.execute(
        "SELECT id FROM projects WHERE project_number = ?",
        (project_number,),
    ).fetchone()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get mailbox entry.
    entry = db.get_mailbox_entry(conn, entry_id)

    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    # Verify entry belongs to project (tenant isolation).
    if entry["project_id"] != project["id"]:
        raise HTTPException(status_code=404, detail="Entry not found")

    # Resolve file path and serve file.
    file_path = _resolve_mailbox_file_path(entry["file_path"])

    if not file_path.exists():
        log.error("Mailbox file not found: path=%s entry_id=%s", file_path, entry_id)
        raise HTTPException(status_code=404, detail="File not found")

    # Determine filename from report_date.
    filename = f"swppp_{entry['report_date']}.pdf"

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=filename,
    )


@app.post("/mailbox/{project_number}/download/batch")
async def download_batch_mailbox_entries(
    project_number: str,
    request: Request,
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Download multiple entries as a ZIP file (public, no auth).

    Request body: {"entry_ids": ["id1", "id2", ...]}
    Max 50 entries per batch.
    """
    import json

    # Parse request body.
    try:
        body_bytes = await request.body()
        body = json.loads(body_bytes)
        entry_ids = body.get("entry_ids", [])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")

    if not entry_ids:
        raise HTTPException(status_code=400, detail="No entry_ids provided")

    if len(entry_ids) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 entries per batch")

    # Verify project exists.
    project = conn.execute(
        "SELECT id FROM projects WHERE project_number = ?",
        (project_number,),
    ).fetchone()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Fetch entries and verify they all belong to this project.
    entries = []
    for entry_id in entry_ids:
        entry = db.get_mailbox_entry(conn, entry_id)
        if not entry:
            raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
        if entry["project_id"] != project["id"]:
            raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
        entries.append(entry)

    # Generate ZIP in memory.
    zip_bytes = _generate_batch_zip(entries, project_number)

    # Return ZIP file.
    filename = f"swppp_{project_number}_batch.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/mailbox/{project_number}/download/all")
def download_all_mailbox_entries(
    project_number: str,
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Download all entries as a ZIP file (public, no auth)."""
    # Verify project exists.
    project = conn.execute(
        "SELECT id FROM projects WHERE project_number = ?",
        (project_number,),
    ).fetchone()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get all mailbox entries.
    entries = db.get_mailbox_entries(conn, project["id"], sort_order="desc")

    if not entries:
        raise HTTPException(status_code=404, detail="No entries found")

    # Generate ZIP in memory.
    zip_bytes = _generate_batch_zip(entries, project_number)

    # Return ZIP file.
    filename = f"swppp_{project_number}_all.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/not")
def serve_not_file(conn: sqlite3.Connection = Depends(db.get_db)):
    """Serve the NOT file (public, no auth).

    Looks up project by cookie 'mailbox_project_number' or returns 404.
    """
    # This endpoint is optional and may not be implemented in initial version.
    # For now, return 501 Not Implemented.
    raise HTTPException(status_code=501, detail="NOT endpoint not yet implemented")


@app.get("/mailbox", response_class=HTMLResponse)
def serve_mailbox_html():
    """Serve the public mailbox HTML frontend (no auth)."""
    html_path = (
        Path(__file__).resolve().parent.parent / "frontend" / "mailbox" / "index.html"
    )

    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Mailbox HTML not found")

    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


# ── Platform Admin: Company List ─────────────────────────────────────────


@app.get("/admin/companies")
def list_companies(
    _admin: dict[str, Any] = Depends(require_platform_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    companies = db.get_all_companies(conn)
    return CompanyListResponse(
        companies=[
            CompanyInfo(
                id=c["id"],
                legal_name=c["legal_name"],
                display_name=c["display_name"],
                slug=c["slug"],
                primary_timezone=c["primary_timezone"],
                is_active=bool(c["is_active"]),
                created_at=c["created_at"],
            )
            for c in companies
        ]
    )


# ── Platform Admin: Health Dashboard ─────────────────────────────────────


@app.get("/admin/platform-health", response_model=PlatformDashboardResponse)
def get_platform_health(
    _admin: dict[str, Any] = Depends(require_platform_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Return cross-company health summary (platform admin only)."""
    data = db.get_platform_dashboard(conn)

    problem_projects = [
        ProblemProjectRow(
            company_name=p["company_name"],
            project_id=p["project_id"],
            project_number=p["project_number"],
            project_name=p["project_name"],
            health_flag=p["health_flag"],
            status_reason=(
                "Failing (auto-weekly)"
                if p["health_flag"] == "red"
                else (
                    "Setup incomplete"
                    if p["status"] == "setup_incomplete"
                    else "Stale (>8 days)"
                )
            ),
            last_successful_run_at=p["last_successful_run_at"],
            failure_count_7d=p["failure_count_7d"],
        )
        for p in data["problem_projects"]
    ]

    company_rollup = [
        CompanyHealthRow(
            id=c["id"],
            display_name=c["display_name"],
            total_projects=c["total_projects"] or 0,
            active=c["active"] or 0,
            failing=c["failing"] or 0,
            paused=c["paused"] or 0,
            setup_incomplete=c["setup_incomplete"] or 0,
            last_activity=c["last_activity"],
            admin_name=c["admin_name"],
        )
        for c in data["company_rollup"]
    ]

    return PlatformDashboardResponse(
        total_companies=data["total_companies"],
        total_active_projects=data["total_active_projects"],
        reports_filed_7d=data["reports_filed_7d"],
        reports_filed_30d=data["reports_filed_30d"],
        last_run_at=data["last_run_at"],
        problem_projects=problem_projects,
        company_rollup=company_rollup,
    )


# ── Platform Admin: Scheduler ─────────────────────────────────────────────


@app.post("/admin/run-due-reports", response_model=RunDueReportsResponse)
def run_due_reports_endpoint(
    body: RunDueReportsRequest,
    _admin: dict[str, Any] = Depends(require_platform_admin),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Trigger the reconciliation scheduler immediately (platform admin only)."""
    import time

    from web.scheduler.run_due_reports import run_due_reports

    start_ms = time.monotonic()
    result = run_due_reports(conn, dry_run=False, force=body.force)
    duration_ms = int((time.monotonic() - start_ms) * 1000)

    log.info(
        "run-due-reports triggered by admin: projects=%d filed=%d failures=%d skipped=%d duration_ms=%d",
        result["projects_processed"],
        result["reports_filed"],
        result["failures"],
        result["skipped"],
        duration_ms,
    )

    return RunDueReportsResponse(
        projects_processed=result["projects_processed"],
        reports_filed=result["reports_filed"],
        failures=result["failures"],
        skipped=result["skipped"],
        duration_ms=duration_ms,
    )


# ── Archive Flow (IR-7) ──────────────────────────────────────────────────


def _generate_archive_zip(project_id: str) -> None:
    """Background task: build a ZIP of all project data and mailbox PDFs.

    Opens its own DB connection because the HTTP response has already been
    sent by the time this runs.
    """
    conn = sqlite3.connect(db.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        project = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not project:
            log.error("_generate_archive_zip: project not found: %s", project_id)
            return

        project_dict = dict(project)
        entries = db.get_mailbox_entries(conn, project_id, sort_order="asc")

        versions = conn.execute(
            "SELECT * FROM project_template_versions WHERE project_id = ?",
            (project_id,),
        ).fetchall()

        data_dir = Path(os.environ.get("TOOLS_DATA_DIR", "web/data"))
        archive_dir = data_dir / "archives" / project_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        zip_path = archive_dir / f"archive_{project_id}.zip"

        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            # Serialise project metadata + report index + template version list.
            not_path_raw = project_dict.get("not_document_path")
            manifest = {
                "schema_version": "1.0",
                "project": {
                    k: project_dict[k] for k in project_dict if k != "not_document_path"
                },
                "not_on_file": bool(not_path_raw),
                "not_document_filename": (
                    Path(not_path_raw).name if not_path_raw else None
                ),
                "reports": [
                    {
                        "id": e["id"],
                        "report_date": e["report_date"],
                        "report_type": e["report_type"],
                        "generation_mode": e["generation_mode"],
                        "file_size_bytes": e["file_size_bytes"],
                        "created_at": e["created_at"],
                    }
                    for e in entries
                ],
                "template_versions": [dict(v) for v in versions],
            }
            zf.writestr(
                "project-data.json", json.dumps(manifest, indent=2, default=str)
            )

            # Add PDF reports.
            for entry in entries:
                try:
                    pdf_path = _resolve_mailbox_file_path(entry["file_path"])
                    zf.write(pdf_path, f"reports/{Path(entry['file_path']).name}")
                except (HTTPException, FileNotFoundError) as exc:
                    log.warning(
                        "Archive ZIP: skipping missing report: entry=%s error=%s",
                        entry["id"],
                        exc,
                    )

            # Add NOT document if present.
            if not_path_raw:
                not_file = Path(not_path_raw)
                if not_file.exists():
                    zf.write(not_file, f"not/{not_file.name}")
                else:
                    log.warning(
                        "Archive ZIP: NOT file not found on disk: %s", not_path_raw
                    )

        zip_path.write_bytes(buf.getvalue())
        db.set_archive_zip_path(conn, project_id, str(zip_path))
        conn.commit()
        log.info(
            "Archive ZIP created: project_id=%s path=%s size=%d",
            project_id,
            zip_path,
            zip_path.stat().st_size,
        )
    except Exception as exc:
        log.error(
            "_generate_archive_zip failed: project_id=%s error=%s", project_id, exc
        )
        raise
    finally:
        conn.close()


def _require_project_member(
    company_id: str,
    project_id: str,
    user: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    allowed_roles: tuple[str, ...] = ("company_admin", "pm"),
) -> dict[str, Any]:
    """Verify project belongs to company and user has required role.

    Returns the project dict on success.  Raises HTTPException otherwise.
    """
    if not user.get("is_platform_admin"):
        membership = db.get_company_user(conn, user["id"], company_id)
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this company")
        if membership["role"] not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient role")

    project = db.get_project_for_company(conn, project_id, company_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.post(
    "/companies/{company_id}/projects/{project_id}/archive",
    response_model=ProjectArchiveResponse,
    status_code=202,
)
async def archive_project(
    company_id: str,
    project_id: str,
    background_tasks: BackgroundTasks,
    archive_without_not: bool = Form(False),
    not_file: UploadFile | None = File(None),
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Archive a project. pm or company_admin only.

    A Notice of Termination (NOT) file is required unless archive_without_not=True.
    """
    project = _require_project_member(company_id, project_id, user, conn)

    if project["status"] == "archived":
        raise HTTPException(status_code=409, detail="Project is already archived")

    if not archive_without_not and not_file is None:
        raise HTTPException(
            status_code=400,
            detail="A Notice of Termination file is required. Set archive_without_not=true to skip.",
        )

    not_document_path: str | None = None
    if not_file is not None:
        data_dir = Path(os.environ.get("TOOLS_DATA_DIR", "web/data"))
        not_dir = data_dir / "not" / project_id
        not_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(not_file.filename or "not_document").name
        dest = not_dir / safe_name
        content = await not_file.read()
        dest.write_bytes(content)
        not_document_path = str(dest)

    db.archive_project(conn, project_id, user["id"], not_document_path)
    conn.commit()

    # Fetch the just-written timestamp so we can return it.
    row = conn.execute(
        "SELECT archived_at FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    archived_at = row["archived_at"]

    background_tasks.add_task(_generate_archive_zip, project_id)
    log.info("Archive initiated: project_id=%s by_user=%s", project_id, user["id"])

    return ProjectArchiveResponse(
        project_id=project_id,
        archived_at=archived_at,
        archive_zip_ready=False,
    )


@app.get(
    "/companies/{company_id}/projects/{project_id}/archive/status",
    response_model=ProjectArchiveStatusResponse,
)
def get_archive_status(
    company_id: str,
    project_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Poll whether the archive ZIP is ready. Any company member."""
    _require_project_member(
        company_id,
        project_id,
        user,
        conn,
        allowed_roles=("company_admin", "pm", "viewer"),
    )
    row = conn.execute(
        "SELECT archive_zip_path FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    zip_path = row["archive_zip_path"]
    return ProjectArchiveStatusResponse(
        archive_zip_ready=bool(zip_path),
        archive_zip_path=zip_path,
    )


@app.post(
    "/companies/{company_id}/projects/{project_id}/unarchive",
)
def unarchive_project(
    company_id: str,
    project_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Restore a project to active status. company_admin only."""
    project = _require_project_member(
        company_id,
        project_id,
        user,
        conn,
        allowed_roles=("company_admin",),
    )

    if project["status"] != "archived":
        raise HTTPException(status_code=400, detail="Project is not archived")

    db.unarchive_project(conn, project_id)
    conn.commit()
    log.info("Project unarchived: project_id=%s by_user=%s", project_id, user["id"])
    return {"project_id": project_id, "status": "active"}


@app.post(
    "/companies/{company_id}/projects/{project_id}/not",
    response_model=NotUploadResponse,
)
async def upload_not_document(
    company_id: str,
    project_id: str,
    background_tasks: BackgroundTasks,
    not_file: UploadFile = File(...),
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Upload a Notice of Termination document for an archived project. pm or company_admin."""
    project = _require_project_member(company_id, project_id, user, conn)

    if project["status"] != "archived":
        raise HTTPException(
            status_code=400, detail="Project must be archived to upload a NOT"
        )

    data_dir = Path(os.environ.get("TOOLS_DATA_DIR", "web/data"))
    not_dir = data_dir / "not" / project_id
    not_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(not_file.filename or "not_document").name
    dest = not_dir / safe_name
    content = await not_file.read()
    dest.write_bytes(content)

    db.add_not_document(conn, project_id, user["id"], str(dest))
    conn.commit()

    row = conn.execute(
        "SELECT not_uploaded_at FROM projects WHERE id = ?", (project_id,)
    ).fetchone()

    background_tasks.add_task(_generate_archive_zip, project_id)
    log.info(
        "NOT document uploaded: project_id=%s path=%s by_user=%s",
        project_id,
        str(dest),
        user["id"],
    )

    return NotUploadResponse(
        project_id=project_id,
        not_document_path=str(dest),
        not_uploaded_at=row["not_uploaded_at"],
    )


@app.get("/mailbox/{project_number}/archive/download")
def download_archive_zip(
    project_number: str,
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Download the complete project archive ZIP (public, no auth).

    Returns 202 + JSON body while the ZIP is still being prepared.
    """
    row = conn.execute(
        "SELECT id, status, archive_zip_path FROM projects WHERE project_number = ?",
        (project_number,),
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Project not found")

    if row["status"] != "archived":
        raise HTTPException(status_code=404, detail="Project is not archived")

    zip_path_str = row["archive_zip_path"]
    if not zip_path_str:
        return Response(
            content=json.dumps(
                {"detail": "Archive is being prepared. Try again shortly."}
            ),
            status_code=202,
            media_type="application/json",
        )

    zip_path = Path(zip_path_str)
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="Archive file not found on disk")

    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename=f"archive_{project_number}.zip",
    )


# ── Dev-mode: mount SWPPP sub-app ────────────────────────────────────────

if DEV_MODE:
    from web.swppp_api.main import app as _swppp_app

    # Mount SWPPP sub-app last so auth routes take priority.
    # The sub-app keeps its own middleware stack and its routes already
    # include the /swppp/ prefix, so no path stripping is needed.
    app.mount("", _swppp_app)
