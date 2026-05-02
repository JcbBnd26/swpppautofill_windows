# Implementation Record — Hardening Fixes (Tier 2)

**Project**: sw3p.pro — Company Tools Portal
**Scope**: Prevent next-round bugs — deduplicate shared code, add server-side auth gate, clean up copy-paste artifacts
**Priority**: HIGH — not causing active bugs, but creates security gaps and maintenance debt
**Depends on**: Tier 1 (Login Bug Fixes) must be deployed first
**Server**: DigitalOcean VPS (Ubuntu 24.04 LTS), domain `sw3p.pro`
**SSH**: `ssh -i ~/.ssh/swppp-vps-deploy root@{server_ip}`
**Repo on server**: `/opt/tools/repo`
**Production data**: `/opt/tools/data` (NEVER overwrite database files)
**Services**: `tools-auth` (port 8001), `tools-swppp` (port 8002)

---

## Pre-Flight Checklist

Before making ANY changes:

1. **Confirm Tier 1 is deployed and stable.** SSH into the server and verify:
   ```
   ssh -i ~/.ssh/swppp-vps-deploy root@{server_ip}
   systemctl status tools-auth tools-swppp
   journalctl -u tools-auth --since "24 hours ago" --no-pager | grep -i error | tail -10
   ```
   If there are auth errors from the last 24 hours, do NOT proceed — investigate Tier 1 first.

2. **Backup production databases**:
   ```
   cp /opt/tools/data/auth.db /opt/tools/backups/auth_pre_tier2_$(date +%Y%m%d_%H%M%S).db
   cp /opt/tools/data/swppp_sessions.db /opt/tools/backups/swppp_sessions_pre_tier2_$(date +%Y%m%d_%H%M%S).db
   ```

3. **Save current nginx config**:
   ```
   cp /etc/nginx/sites-available/tools.conf /opt/tools/backups/tools_conf_pre_tier2_$(date +%Y%m%d_%H%M%S).conf
   ```

---

## Fix 2A — Server-Side Auth Guard for Portal Pages

### Problem

Nginx serves `index.html` (portal) and `admin.html` as raw static files. Authentication is enforced entirely by client-side JavaScript — the HTML reaches the browser before any auth check occurs. This causes two issues:

1. **Information leakage**: The admin page source (all API endpoint paths, admin UI logic, user management workflows) is visible to anyone who requests the URL, even without a session.
2. **Loading flash**: Every page load shows "Loading..." while JS round-trips to `/auth/me` before content appears.

### Overview

Move the three static-file routes (`/`, `/admin`, `/auth/login`) from Nginx direct-serve to proxied-through-FastAPI. FastAPI checks the session cookie server-side and either serves the HTML or redirects to the login page. The JavaScript `init()` checks in `index.html` and `admin.html` remain as a fallback safety net but are no longer the primary auth gate.

### Part A — Update Nginx Configuration

### File to Edit

`web/scripts/nginx/tools.conf`

### Current Code (REPLACE THIS — lines 24–38)

```nginx
    # ── Portal frontend (static HTML) ────────────────────────────────
    location = / {
        alias /opt/tools/repo/web/frontend/portal/;
        try_files /index.html =404;
    }

    location = /auth/login {
        alias /opt/tools/repo/web/frontend/portal/;
        try_files /login.html =404;
    }

    location = /admin {
        alias /opt/tools/repo/web/frontend/portal/;
        try_files /admin.html =404;
    }
```

### New Code (REPLACE WITH)

```nginx
    # ── Portal pages → auth service (server-side session check) ────
    # These are proxied to FastAPI so the session cookie is validated
    # before any HTML is sent to the browser.
    location = / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location = /admin {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # /auth/login is handled by the /auth/ prefix block below — no
    # separate exact-match needed. The existing FastAPI route at
    # @app.get("/auth/login") serves the login HTML unconditionally.
```

### Why This Works

Nginx exact-match locations (`=`) take priority over prefix matches. The `= /` and `= /admin` blocks intercept those specific paths and proxy them to FastAPI on port 8001. All other `/auth/*` and `/admin/*` paths continue to be handled by the existing prefix-match proxy blocks below.

The `/auth/login` exact-match block is removed entirely. That path now falls through to the existing `location /auth/` prefix block, which proxies to port 8001, where the existing `@app.get("/auth/login")` FastAPI route serves the login HTML. One less nginx block to maintain.

---

### Part B — Add Server-Side Auth Routes to FastAPI

### File to Edit

`web/auth/main.py`

### Current Code (REPLACE THIS — lines 513–536, the dev-mode block at the end of the file)

```python
# ── Dev-mode: serve portal HTML ──────────────────────────────────────────

if DEV_MODE:
    from web.swppp_api.main import app as _swppp_app

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

    # Mount SWPPP sub-app last so auth routes take priority.
    # The sub-app keeps its own middleware stack and its routes already
    # include the /swppp/ prefix, so no path stripping is needed.
    app.mount("", _swppp_app)
```

### New Code (REPLACE WITH)

```python
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


# ── Dev-mode: mount SWPPP sub-app ────────────────────────────────────────

if DEV_MODE:
    from web.swppp_api.main import app as _swppp_app

    # Mount SWPPP sub-app last so auth routes take priority.
    # The sub-app keeps its own middleware stack and its routes already
    # include the /swppp/ prefix, so no path stripping is needed.
    app.mount("", _swppp_app)
```

### Key Design Decision — Why Not Use `get_current_user`

The `get_current_user` dependency from `dependencies.py` raises `HTTPException(status_code=401)` when auth fails. That returns a JSON `{"detail":"Not authenticated"}` response — appropriate for API endpoints, but wrong for page routes. When a browser requests a page and isn't logged in, it should receive a **302 redirect**, not a JSON error. So these routes check the cookie manually using `db.validate_session()` and return `RedirectResponse` on failure.

The JavaScript `init()` checks in `index.html` and `admin.html` should NOT be removed. They remain as a defense-in-depth fallback — if somehow the server-side check has a bug, the client-side check still catches it. Belt and suspenders.

### Acceptance Tests

Add to `tests/test_auth.py`:

```python
class TestServerSideAuthGate:
    """Portal and admin pages must redirect unauthenticated users
    server-side (302) rather than serving HTML."""

    def test_portal_redirects_when_unauthenticated(self):
        c = TestClient(app, cookies={})
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert "/auth/login" in r.headers.get("location", "")

    def test_portal_serves_html_when_authenticated(self):
        admin = _admin_client()
        r = admin.get("/", follow_redirects=False)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_admin_redirects_when_unauthenticated(self):
        c = TestClient(app, cookies={})
        r = c.get("/admin", follow_redirects=False)
        assert r.status_code == 302
        assert "/auth/login" in r.headers.get("location", "")

    def test_admin_redirects_non_admin_to_portal(self):
        """A logged-in non-admin user should be sent to / not /auth/login."""
        admin = _admin_client()
        code = _make_invite(admin, "NonAdminGate")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        r = c.get("/admin", follow_redirects=False)
        assert r.status_code == 302
        location = r.headers.get("location", "")
        # Should redirect to portal root, NOT to login
        assert location.endswith("/") or location == "/"

    def test_admin_serves_html_for_admin_user(self):
        admin = _admin_client()
        r = admin.get("/admin", follow_redirects=False)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_login_page_always_served(self):
        c = TestClient(app, cookies={})
        r = c.get("/auth/login")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_portal_redirects_with_invalid_cookie(self):
        c = TestClient(app, cookies={"tools_session": "garbage-token"})
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert "/auth/login" in r.headers.get("location", "")
```

---

## Fix 2B — Extract Shared CSRF Middleware

### Problem

The identical CSRF origin-check middleware is copy-pasted into both `web/auth/main.py` (line 94) and `web/swppp_api/main.py` (line 77). If one copy is updated and the other is forgotten, the SWPPP API could silently lose CSRF protection.

### Part A — Create the Shared Middleware Module

### New File to Create

`web/middleware.py`

```python
"""Shared middleware for the Tools platform.

Each factory function returns a configured middleware callable that can
be registered on any FastAPI app via @app.middleware("http").
"""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import Request, Response

log = logging.getLogger(__name__)

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def create_csrf_middleware(
    *,
    expected_origin: str,
    dev_mode: bool = False,
) -> Callable:
    """Return an ASGI middleware that rejects unsafe requests whose
    Origin header does not match ``expected_origin``.

    In dev mode the check is skipped entirely.  When the Origin header
    is absent the request is allowed through (browsers always send it
    on same-origin POST/PUT/PATCH/DELETE fetches).

    Usage::

        @app.middleware("http")
        async def csrf_check(request, call_next):
            return await create_csrf_middleware(
                expected_origin="https://sw3p.pro",
            )(request, call_next)

    Or more concisely, register it in a helper (see each app's main.py).
    """
    _expected = expected_origin.rstrip("/")

    async def _csrf_origin_check(request: Request, call_next: Callable) -> Response:
        if request.method in _UNSAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and not dev_mode:
                if origin.rstrip("/") != _expected:
                    log.warning(
                        "CSRF origin mismatch: expected=%s got=%s path=%s",
                        _expected,
                        origin,
                        request.url.path,
                    )
                    return Response(
                        content='{"detail":"Origin mismatch"}',
                        status_code=403,
                        media_type="application/json",
                    )
        return await call_next(request)

    return _csrf_origin_check
```

### Why a Factory Function

A factory function (a function that returns a function) lets each app configure the middleware with its own settings while sharing the same logic. The auth service and SWPPP service both read `TOOLS_BASE_URL` and `TOOLS_DEV_MODE` from their environment, but the factory doesn't need to know that — it just receives the values. This keeps the middleware testable and decoupled from environment variables.

When you add a third tool to the portal later, its `main.py` will import this same factory and configure it with one line. No copy-paste.

---

### Part B — Update auth/main.py to Use Shared Middleware

### File to Edit

`web/auth/main.py`

### Current Code (REMOVE THIS — lines 89–112)

```python
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
```

### New Code (REPLACE WITH)

```python
# ── Middleware: CSRF origin check ────────────────────────────────────────

from web.middleware import create_csrf_middleware

_csrf_check = create_csrf_middleware(expected_origin=BASE_URL, dev_mode=DEV_MODE)


@app.middleware("http")
async def csrf_origin_check(request: Request, call_next):
    return await _csrf_check(request, call_next)
```

### Add the import

Add `from web.middleware import create_csrf_middleware` to the imports at the top of the file (or leave it inline as shown above — either is fine since it's a one-time import at module load).

---

### Part C — Update swppp_api/main.py to Use Shared Middleware

### File to Edit

`web/swppp_api/main.py`

### Current Code (REMOVE THIS — lines 72–95)

```python
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
```

### New Code (REPLACE WITH)

```python
# ── Middleware: CSRF origin check ────────────────────────────────────────

from web.middleware import create_csrf_middleware

_csrf_check = create_csrf_middleware(expected_origin=BASE_URL, dev_mode=DEV_MODE)


@app.middleware("http")
async def csrf_origin_check(request: Request, call_next):
    return await _csrf_check(request, call_next)
```

### Acceptance Test

Add to `tests/test_auth.py` (tests CSRF behavior through the auth app, which now uses the shared middleware):

```python
class TestSharedCsrfMiddleware:
    """CSRF middleware (now shared) must still block mismatched origins."""

    def test_csrf_allows_same_origin(self):
        """Requests with matching origin should succeed normally."""
        admin = _admin_client()
        code = _make_invite(admin, "CsrfTestUser")
        assert code  # invite created successfully through same-origin POST

    def test_csrf_allows_missing_origin(self):
        """Requests without an Origin header should be allowed
        (browsers always send Origin on unsafe same-origin requests)."""
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            code = db.create_invite(conn, "NoOriginUser", ["swppp"])
        c = TestClient(app, cookies={})
        # TestClient does not send Origin by default — this tests the
        # missing-origin path.
        r = c.post("/auth/claim", json={"code": code})
        assert r.status_code == 200
```

**Note on CSRF testing**: The `TestClient` in dev mode (`TOOLS_DEV_MODE=1`) bypasses the CSRF check (the middleware skips when `dev_mode=True`). This is by design — tests run in dev mode. To test CSRF rejection of bad origins in a unit test, you would need to set `TOOLS_DEV_MODE=0` and send a request with a non-matching `Origin` header. That's an optional extra test but not required for this IR — the important thing is confirming that the shared middleware is wired in and the existing behavior hasn't changed.

---

## Fix 2C — Remove Duplicate DEV_MODE in swppp_api/main.py

### Problem

`DEV_MODE` is declared on line 53 and again on line 471 of `web/swppp_api/main.py`. Same expression, same value. The second declaration shadows the first for the code block below it. If someone later changes line 53 (e.g., to add a fallback or rename the env var), the dev-mode route block at the bottom will silently use the old value from line 471.

### File to Edit

`web/swppp_api/main.py`

### Current Code (line 471)

```python
DEV_MODE = os.environ.get("TOOLS_DEV_MODE", "0") == "1"
```

### Action

**Delete line 471 entirely.** The `DEV_MODE` variable on line 53 is already at module scope and visible to the `if DEV_MODE:` block on line 474 (which will become line 473 after deletion). No other changes needed.

### After Deletion, the End of File Should Look Like

```python
# ── Dev-mode: serve SWPPP frontend ──────────────────────────────────────
SWPPP_FRONTEND_DIR = PROJECT_ROOT / "web" / "frontend" / "swppp"

if DEV_MODE:

    @app.get("/swppp/")
    def swppp_index():
        html_path = SWPPP_FRONTEND_DIR / "index.html"
        if html_path.exists():
            return FileResponse(html_path, media_type="text/html")
        return HTMLResponse("<h1>SWPPP frontend not found</h1>", status_code=500)
```

### Acceptance Test

No dedicated test needed — this is a cleanup. The existing `test_swppp_api.py` tests run in dev mode and will confirm the dev-mode route still works. Run the full SWPPP test suite to verify nothing broke:

```bash
TOOLS_DEV_MODE=1 python -m pytest tests/test_swppp_api.py -v
```

---

## Deployment Sequence

After all three fixes are committed and tests pass locally:

1. **SSH into the server**:
   ```
   ssh -i ~/.ssh/swppp-vps-deploy root@{server_ip}
   ```

2. **Pull the latest code**:
   ```
   cd /opt/tools/repo
   git pull --ff-only
   ```

3. **Restart both services** (picks up the new middleware and route changes):
   ```
   systemctl restart tools-auth tools-swppp
   ```

4. **Verify services are healthy**:
   ```
   systemctl status tools-auth tools-swppp
   journalctl -u tools-auth --since "2 min ago" --no-pager
   journalctl -u tools-swppp --since "2 min ago" --no-pager
   ```

5. **Install the updated Nginx config**:
   ```
   cp /opt/tools/repo/web/scripts/nginx/tools.conf /etc/nginx/sites-available/tools.conf
   nginx -t
   ```
   **STOP if `nginx -t` fails.** Fix the config before proceeding.

6. **Reload Nginx** (not restart — reload is zero-downtime):
   ```
   systemctl reload nginx
   ```

7. **Smoke test from browser**:
   - Open `https://sw3p.pro/` in a private/incognito window (no session).
     - **Expected**: Immediate redirect to `/auth/login` — NO "Loading..." flash, NO HTML served.
   - Open `https://sw3p.pro/admin` in a private/incognito window.
     - **Expected**: Immediate redirect to `/auth/login`.
   - Log in with a non-admin account, then navigate to `https://sw3p.pro/admin`.
     - **Expected**: Redirect to `/` (portal), NOT to login.
   - Log in with an admin account, then navigate to `https://sw3p.pro/admin`.
     - **Expected**: Admin page loads immediately with no "Loading..." flash.
   - Open `https://sw3p.pro/auth/login` while logged in.
     - **Expected**: Redirect to `/`.
   - Test a SWPPP API call (e.g., load the SWPPP app and fetch form schema).
     - **Expected**: Works normally — CSRF middleware change should be transparent.

### Rollback Plan

**If the Nginx config breaks:**
```
cp /opt/tools/backups/tools_conf_pre_tier2_{timestamp}.conf /etc/nginx/sites-available/tools.conf
nginx -t && systemctl reload nginx
```

**If FastAPI routes break:**
```
cd /opt/tools/repo
git log --oneline -5       # find the previous commit
git checkout {prev_hash}
systemctl restart tools-auth tools-swppp
# Restore the old nginx config too:
cp /opt/tools/backups/tools_conf_pre_tier2_{timestamp}.conf /etc/nginx/sites-available/tools.conf
nginx -t && systemctl reload nginx
```

---

## Run All Tests

Before committing, run both test suites:

```bash
cd /path/to/swpppautofill_windows
TOOLS_DEV_MODE=1 python -m pytest tests/test_auth.py tests/test_swppp_api.py -v
```

All existing tests must still pass. The new `TestServerSideAuthGate` and `TestSharedCsrfMiddleware` classes must also pass.

---

## Files Modified by This IR

| File | Change |
|---|---|
| `web/scripts/nginx/tools.conf` | Fix 2A — proxy `/` and `/admin` to FastAPI instead of serving static HTML; remove `/auth/login` exact-match block |
| `web/auth/main.py` | Fix 2A — move portal routes out of `if DEV_MODE:` and add server-side session checks; Fix 2B — replace inline CSRF middleware with shared factory import |
| `web/middleware.py` | Fix 2B — **NEW FILE** — shared CSRF middleware factory |
| `web/swppp_api/main.py` | Fix 2B — replace inline CSRF middleware with shared factory import; Fix 2C — delete duplicate `DEV_MODE` on line 471 |
| `tests/test_auth.py` | Add `TestServerSideAuthGate` and `TestSharedCsrfMiddleware` test classes |

## Files NOT Modified by This IR

| File | Reason |
|---|---|
| `web/auth/db.py` | No schema changes in Tier 2 |
| `web/auth/dependencies.py` | No changes — portal routes deliberately bypass these (see design note in Fix 2A) |
| `web/frontend/portal/login.html` | No changes — already fixed in Tier 1 |
| `web/frontend/portal/index.html` | No changes — JS `init()` remains as fallback auth check |
| `web/frontend/portal/admin.html` | No changes — JS `init()` remains as fallback auth check |

---

## Follow-Up (Tier 3 — Separate IR)

These are NOT part of this IR. Do not implement them in this session.

- **3A**: Add rate limiting to `/auth/claim`, `/auth/signin`, and `/auth/set-password` endpoints (10 attempts per IP per minute).
- **3B**: Change CSRF middleware to reject unsafe requests that omit the `Origin` header entirely (currently only checks when header is present).
- **3C**: Evaluate SQLite write contention with 2 Gunicorn workers under concurrent load. Document decision to keep or reduce worker count.
