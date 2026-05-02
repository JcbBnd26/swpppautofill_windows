from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
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
    MeResponse,
    PatchAppRequest,
    PatchUserRequest,
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectInfo,
    ProjectListResponse,
    ProjectUpdateRequest,
    ResetPasswordResponse,
    SessionInfo,
    SessionListResponse,
    SetPasswordRequest,
    SuccessResponse,
    UserInfo,
    UserListResponse,
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


# ── Dev-mode: mount SWPPP sub-app ────────────────────────────────────────

if DEV_MODE:
    from web.swppp_api.main import app as _swppp_app

    # Mount SWPPP sub-app last so auth routes take priority.
    # The sub-app keeps its own middleware stack and its routes already
    # include the /swppp/ prefix, so no path stripping is needed.
    app.mount("", _swppp_app)
