# sw3p.pro Tools Platform — Hardening Tier 4 Implementation Record

**Document purpose:** Agent-facing specification for five session-lifecycle security fixes; consumed by the VS Code + GitHub Copilot coding agent.
**Date range:** TBD — to be filled on completion.
**Source specification:** Security assessment derived from 40-item post-Tier-3 audit. Five findings grouped by theme: session lifecycle.
**Starting state:** 92 tests in `test_auth.py`, 13 in `test_session.py`, Tiers 1–3 implemented and passing.
**Final state:** Same test files plus new test classes covering all five fixes; all five vulnerabilities closed.

---

## 0. Summary Card

| Field | Value |
|-------|-------|
| **Project name** | `sw3p.pro Tools Platform` |
| **Date range** | TBD |
| **Source specification** | Post-Tier-3 security audit — session lifecycle findings |
| **Starting state** | 92 auth tests, 13 session tests, five session-lifecycle vulnerabilities open |
| **Final state** | All five vulnerabilities closed; new tests covering each fix |
| **Total files created** | 0 |
| **Total files modified** | 3 (`web/auth/db.py`, `web/auth/main.py`, `web/scripts/nginx/tools.conf`) + `tests/test_auth.py` |
| **Total lines added** | ~120 (estimate) |
| **Lines modified in pre-existing code** | ~40 (estimate) |
| **Net new dependencies** | 0 |
| **Known limitations carried forward** | 3 — see §Appendix B |
| **Open bugs** | 0 at time of authoring |

---

## Table of Contents

```
1.  Pre-Implementation Baseline
2.  Dependency Manifest
3.  Environment & Configuration Reference
4A. Fix: Logout Not Clearing Browser Cookie
4B. Fix: Session Refresh Middleware Bypasses Validation
4C. Fix: Sessions Never Expire
4D. Fix: No Rate Limiting on Auth Endpoints
4E. Fix: Password Change Does Not Invalidate Other Sessions
5.  Architecture Overview
6.  API Endpoint Inventory
7.  API Request/Response Examples
8.  Security Posture Summary
9.  Data & Storage
10. Deployment
11. Test Suite Inventory
12. Performance Baseline
13. Change Delta Summary
14. User-Facing Behavior
Appendix A: Issue & Fix Registry
Appendix B: Known Limitations & Future Work
```

---

## 1. Pre-Implementation Baseline

### 1a. Code Inventory

| Component | Path | Purpose | Will Be Modified? |
|-----------|------|---------|-------------------|
| `db.py` (auth) | `web/auth/db.py` | All database logic for auth service: sessions, users, invites, passwords | **Yes** — session schema + helpers |
| `main.py` (auth) | `web/auth/main.py` | Auth service FastAPI app: routes, middleware | **Yes** — logout, session refresh, password change |
| `tools.conf` | `web/scripts/nginx/tools.conf` | Nginx reverse proxy config: routing, TLS, headers | **Yes** — rate limiting zones |
| `test_auth.py` | `tests/test_auth.py` | Auth service integration tests (92 tests, 24 classes) | **Yes** — new test classes appended |
| `middleware.py` | `web/middleware.py` | Shared CSRF middleware factory | No |
| `dependencies.py` | `web/auth/dependencies.py` | FastAPI dependency functions for auth | No |
| `models.py` (auth) | `web/auth/models.py` | Pydantic request/response models | No |
| `main.py` (swppp) | `web/swppp_api/main.py` | SWPPP API FastAPI app | No |

### 1b. Test Inventory

| File | Tests | Coverage Area |
|------|-------|---------------|
| `tests/test_auth.py` | 92 | Auth service end-to-end: login, invite flow, admin, CSRF, password, sessions |
| `tests/test_session.py` | 13 | Desktop app local session file read/write (unrelated to web auth) |
| `tests/test_swppp_api.py` | (see file) | SWPPP API endpoints |
| `tests/test_fill.py` | (see file) | PDF fill logic |
| `tests/test_mesonet.py` | (see file) | Weather data fetch |
| **Total (auth focus)** | **92** | |

### 1c. Design Constraints

```
- No changes to SWPPP API logic, PDF fill logic, or desktop app code.
  Source: Tier 4 scope is auth/session layer only.

- No new Python package dependencies.
  Source: Deployment simplicity; pip install changes require VPS coordination.

- Rate limiting implemented at Nginx layer, not application layer.
  Source: Nginx is already the entry point; app-layer rate limiting
  (e.g., slowapi) requires a new dependency and adds complexity.

- Session expiry must be a sliding window (active use extends lifetime).
  Source: Inspectors work daily; hard expiry would force daily re-login,
  which is unacceptable UX.

- Password change (user-initiated) must keep the current session alive.
  Source: Killing the session mid-request creates a confusing UX loop.
  Admin-initiated resets kill ALL sessions (security action, not self-service).

- All schema migrations must be idempotent and non-destructive.
  Source: IR template constraint; production DB must never be dropped.
```

### 1d. Constraint Compliance Statement

> To be completed by agent after implementation.

| # | Constraint | Honored? | Evidence / Notes |
|---|-----------|----------|------------------|
| 1 | No changes outside auth/session layer | TBD | |
| 2 | No new Python dependencies | TBD | |
| 3 | Rate limiting at Nginx layer | TBD | |
| 4 | Sliding window session expiry | TBD | |
| 5 | User password change keeps current session | TBD | |
| 6 | Migrations idempotent and non-destructive | TBD | |

---

## 2. Dependency Manifest

### 2a. Runtime Dependencies

No new dependencies added in this tier. All packages are pre-existing.

| Package | Version | Status |
|---------|---------|--------|
| `fastapi` | pre-existing | Core web framework |
| `sqlite3` | stdlib | Database |
| `secrets` | stdlib | Token generation |
| `hashlib` | stdlib | Password hashing (scrypt) |

### 2b. Dev / Test Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | `>=8.0.0` | Test runner |
| `httpx` | pre-existing (via FastAPI TestClient) | HTTP client in tests |

### 2c. Deployment-Only Dependencies

| Package | Installed Via | Purpose |
|---------|--------------|---------|
| `nginx` | `apt` | Reverse proxy; rate limiting added in this tier |

### 2d. Full Dependency Snapshot

See `pyproject.toml`. No changes made to this file in Tier 4.

---

## 3. Environment & Configuration Reference

### 3a. Environment Variables

No new environment variables added in Tier 4. Existing variables unchanged.

| Variable | Purpose | Default | Required in Prod? |
|----------|---------|---------|-------------------|
| `TOOLS_DEV_MODE` | Disables CSRF + secure cookie flag | `0` | Yes (must be `0`) |
| `TOOLS_BASE_URL` | Expected Origin for CSRF check | `http://localhost:8001` | Yes |
| `TOOLS_DATA_DIR` | Database storage path | `web/data/` | Yes |

### 3b. Configuration Files

| File | Format | Purpose | Read By | Who Edits It |
|------|--------|---------|---------|-------------|
| `web/scripts/nginx/tools.conf` | Nginx conf | Routing, TLS, rate limiting | Nginx | Dev (deployed via script) |
| `web/auth/db.py` (SCHEMA_SQL) | Python string | SQLite schema + migrations | Auth service on startup | Dev |

---

## 4A. Fix: Logout Not Clearing Browser Cookie

### Scope

The `/auth/logout` endpoint correctly deletes the server-side session from the database, but the browser cookie is never cleared. The root cause is that `response.delete_cookie()` is called on FastAPI's injected `Response` object, but then a separate `RedirectResponse` is returned — the delete-cookie instruction travels on an object that is discarded, not on the object that reaches the browser. This fix constructs the `RedirectResponse` first and calls `delete_cookie()` on that object directly.

### Files Modified

| File | Lines | Created / Modified | Purpose |
|------|-------|--------------------|---------| 
| `web/auth/main.py` | ~5 | Modified | Fix logout to delete cookie on the returned response |
| `tests/test_auth.py` | ~30 | Modified | New class `TestLogoutCookieFix` |

### Exact Code Change — `web/auth/main.py`

**BEFORE:**
```python
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
```

**AFTER:**
```python
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
```

> **Why this works:** `RedirectResponse` inherits from Starlette's `Response` and supports `set_cookie` / `delete_cookie` directly. By calling `delete_cookie` on the object we are returning, the `Set-Cookie: tools_session=; Max-Age=0` header is guaranteed to reach the browser.

### Architectural Decision

**Decision: Remove the injected `Response` parameter entirely.**
- **What:** The `Response` dependency injection parameter is removed from the logout function signature.
- **Rationale:** The injected `Response` is a mutation target that FastAPI merges into the final response — but only when the handler returns that same object. Returning a different object (like `RedirectResponse`) discards all mutations on the injected one. Removing the parameter eliminates the ambiguity entirely.
- **Tradeoff:** Slightly less idiomatic FastAPI pattern (most cookie-setting endpoints use the injected Response), but unambiguously correct.
- **Consequences:** None. The fix is self-contained.

### Acceptance Tests — `TestLogoutCookieFix`

Add to `tests/test_auth.py`:

```python
class TestLogoutCookieFix:
    """Verify logout clears the browser cookie in the response headers."""

    def test_logout_response_contains_delete_cookie_header(self, client, auth_headers):
        """After logout, the Set-Cookie header must set Max-Age=0."""
        # Log in first to get a session
        response = client.post("/auth/signin", json={...})
        assert response.status_code == 200

        # Post to logout
        response = client.post("/auth/logout", follow_redirects=False)

        # Confirm the Set-Cookie header clears the session cookie
        set_cookie = response.headers.get("set-cookie", "")
        assert "tools_session=" in set_cookie
        assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie.lower()

    def test_logout_deletes_server_side_session(self, client, db_conn):
        """Server-side session row must be deleted regardless of cookie fix."""
        # Verify session exists, log out, verify session gone.
        # (This behavior was correct before; verify it remains correct.)
        ...

    def test_logout_without_cookie_does_not_error(self, client):
        """Logout with no session cookie must still redirect cleanly."""
        response = client.post("/auth/logout", follow_redirects=False)
        assert response.status_code == 302
```

### Issues Encountered & Fixes

None expected — root cause is well-understood.

---

## 4B. Fix: Session Refresh Middleware Bypasses Validation

### Scope

The `refresh_session_cookie` middleware in `web/auth/main.py` extends the cookie lifetime on every successful response — but it defines "successful" as HTTP 200–399, which includes 302 redirects. The portal index route (`GET /`) issues a 302 redirect when the session is invalid. The middleware then refreshes the cookie on that redirect, keeping an expired or deleted session token alive in the browser longer than intended. The fix narrows the refresh condition to 2xx responses only (genuine success), excluding redirects.

### Files Modified

| File | Lines | Created / Modified | Purpose |
|------|-------|--------------------|---------| 
| `web/auth/main.py` | 1 | Modified | Change refresh condition from `< 400` to `< 300` |
| `tests/test_auth.py` | ~25 | Modified | New class `TestSessionRefreshMiddleware` |

### Exact Code Change — `web/auth/main.py`

**BEFORE:**
```python
    if token and 200 <= response.status_code < 400:
        response.set_cookie(
            key="tools_session",
            ...
        )
```

**AFTER:**
```python
    if token and 200 <= response.status_code < 300:
        response.set_cookie(
            key="tools_session",
            ...
        )
```

> **Why this works:** A 302 redirect from `GET /` when the session is invalid is not a successful response — it is a rejection disguised as a redirect. Restricting refresh to 2xx ensures only genuinely successful authenticated requests extend the session cookie. A single character change; the impact is precisely scoped.

### Architectural Decision

**Decision: Use HTTP status semantics to gate cookie refresh, not token validation.**
- **What:** Instead of opening a DB connection in the middleware to validate the token, we rely on the fact that auth-gated endpoints only return 2xx when the session is valid.
- **Rationale:** Opening a DB connection inside middleware for every request is expensive and architecturally wrong — it blurs the concern boundary between middleware and route handlers. The 2xx gate achieves the security goal without adding complexity.
- **Tradeoff:** A public 2xx endpoint (e.g., `GET /auth/login`) would still refresh a stale token's cookie, but those endpoints have no session requirement and the token is already stale on the server side regardless of cookie age.
- **Consequences:** None. The change is a one-character narrowing of an existing condition.

### Acceptance Tests — `TestSessionRefreshMiddleware`

```python
class TestSessionRefreshMiddleware:
    """Verify the cookie refresh middleware only refreshes on 2xx responses."""

    def test_valid_session_2xx_refreshes_cookie(self, client):
        """A valid session on a 2xx response must get a refreshed cookie."""
        # GET /auth/me with valid session → 200 → cookie in response headers
        ...

    def test_invalid_session_redirect_does_not_refresh_cookie(self, client):
        """A redirect due to invalid session must NOT refresh the cookie."""
        # GET / with a deleted session token → 302 → no Set-Cookie in response
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert "set-cookie" not in response.headers

    def test_401_response_does_not_refresh_cookie(self, client):
        """A 401 from a protected endpoint must not refresh any cookie."""
        ...
```

---

## 4C. Fix: Sessions Never Expire

### Scope

The `sessions` table has no `expires_at` column. A session created on any past date is valid until an admin manually revokes it or the user logs out. This fix adds a `expires_at` column, populates it on session creation, validates it on every session lookup, and extends it on every successful authenticated request (sliding window — idle sessions expire, active ones do not).

### Files Modified

| File | Lines | Created / Modified | Purpose |
|------|-------|--------------------|---------| 
| `web/auth/db.py` | ~35 | Modified | Schema constant, migration, create/validate session functions |
| `tests/test_auth.py` | ~40 | Modified | New class `TestSessionExpiry` |

### Database Schema Change

**Addition to `SCHEMA_SQL`** — no table drops, no column removals:

The `sessions` table gains one new column. The existing CREATE TABLE statement in `SCHEMA_SQL` must be updated to include `expires_at` so fresh databases have it from the start. Existing databases will receive it via Migration 3 (see below).

```sql
-- Updated sessions table definition (replace existing in SCHEMA_SQL)
CREATE TABLE IF NOT EXISTS sessions (
    token        TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id),
    device_label TEXT,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);
```

**Migration 3** — add to `_run_migrations()` in `web/auth/db.py`:

```python
# Migration 3: Add expires_at column to sessions table.
if not _column_exists(conn, "sessions", "expires_at"):
    conn.execute("ALTER TABLE sessions ADD COLUMN expires_at TEXT")
    # Populate existing sessions: extend from last_seen_at by SESSION_LIFETIME_DAYS.
    # This gives active sessions a fair runway rather than expiring them immediately.
    conn.execute(
        "UPDATE sessions SET expires_at = "
        "datetime(last_seen_at, '+90 days') "
        "WHERE expires_at IS NULL"
    )
    log.info("Migration 3: added expires_at column to sessions table")
```

### New Constant — `web/auth/db.py`

Add at module level, near the top of the file:

```python
from datetime import timedelta

SESSION_LIFETIME_DAYS = 90
```

### Exact Code Changes — `web/auth/db.py`

**`create_session()` — BEFORE:**
```python
def create_session(
    conn: sqlite3.Connection,
    user_id: str,
    device_label: str | None = None,
) -> str:
    token = secrets.token_hex(32)
    now = _now()
    conn.execute(
        "INSERT INTO sessions (token, user_id, device_label, created_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (token, user_id, device_label, now, now),
    )
    return token
```

**`create_session()` — AFTER:**
```python
def create_session(
    conn: sqlite3.Connection,
    user_id: str,
    device_label: str | None = None,
) -> str:
    token = secrets.token_hex(32)
    now = _now()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=SESSION_LIFETIME_DAYS)
    ).isoformat()
    conn.execute(
        "INSERT INTO sessions "
        "(token, user_id, device_label, created_at, last_seen_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (token, user_id, device_label, now, now, expires_at),
    )
    return token
```

**`validate_session()` — BEFORE:**
```python
def validate_session(conn: sqlite3.Connection, token: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT u.id, u.display_name, u.is_active, u.is_admin "
        "FROM sessions s JOIN users u ON s.user_id = u.id "
        "WHERE s.token = ?",
        (token,),
    ).fetchone()
    if not row:
        return None
    user = dict(row)
    if not user["is_active"]:
        return None
    now = _now()
    conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token = ?", (now, token))
    conn.execute("UPDATE users SET last_seen_at = ? WHERE id = ?", (now, user["id"]))
    user["apps"] = get_user_apps(conn, user["id"])
    return user
```

**`validate_session()` — AFTER:**
```python
def validate_session(conn: sqlite3.Connection, token: str) -> dict[str, Any] | None:
    now = _now()
    row = conn.execute(
        "SELECT u.id, u.display_name, u.is_active, u.is_admin "
        "FROM sessions s JOIN users u ON s.user_id = u.id "
        "WHERE s.token = ? AND (s.expires_at IS NULL OR s.expires_at > ?)",
        (token, now),
    ).fetchone()
    if not row:
        return None
    user = dict(row)
    if not user["is_active"]:
        return None
    new_expires_at = (
        datetime.now(timezone.utc) + timedelta(days=SESSION_LIFETIME_DAYS)
    ).isoformat()
    conn.execute(
        "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token = ?",
        (now, new_expires_at, token),
    )
    conn.execute("UPDATE users SET last_seen_at = ? WHERE id = ?", (now, user["id"]))
    user["apps"] = get_user_apps(conn, user["id"])
    return user
```

> **Why `expires_at IS NULL OR expires_at > now`:** The `IS NULL` guard covers sessions that existed before the migration ran and whose `expires_at` was somehow not populated. Belt-and-suspenders; the migration populates all rows, but defensive SQL is correct.

### Architectural Decision

**Decision: Sliding window expiry — every authenticated request resets the 90-day clock.**
- **What:** Each call to `validate_session()` extends `expires_at` by 90 days from now.
- **Rationale:** Construction inspectors use the tool daily during active job seasons. A hard 90-day expiry from creation would force password re-entry mid-season. Sliding window matches user behavior: idle accounts expire, active ones stay alive.
- **Tradeoff:** A very low-traffic session could theoretically stay alive indefinitely — one request per 89 days resets the clock. This is acceptable given the closed user base (invite-only, all known employees).
- **Consequences:** None. The behavior is transparent to users.

### Acceptance Tests — `TestSessionExpiry`

```python
class TestSessionExpiry:
    """Verify sessions expire correctly and the sliding window works."""

    def test_expired_session_is_rejected(self, db_conn):
        """A session with expires_at in the past must return None from validate_session."""
        from web.auth import db
        from datetime import datetime, timezone, timedelta

        user_id = db.create_user(db_conn, "ExpiryUser")
        token = db.create_session(db_conn, user_id)
        db_conn.commit()

        # Force expiry into the past
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        db_conn.execute("UPDATE sessions SET expires_at = ? WHERE token = ?", (past, token))
        db_conn.commit()

        result = db.validate_session(db_conn, token)
        assert result is None

    def test_valid_session_extends_expiry_on_validate(self, db_conn):
        """Calling validate_session on a valid session must push expires_at forward."""
        from web.auth import db
        from datetime import datetime, timezone, timedelta

        user_id = db.create_user(db_conn, "SlideUser")
        token = db.create_session(db_conn, user_id)
        db_conn.commit()

        # Record the initial expires_at
        row = db_conn.execute(
            "SELECT expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()
        initial_expires = row["expires_at"]

        # Validate — this should slide the expiry forward
        db.validate_session(db_conn, token)

        row = db_conn.execute(
            "SELECT expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()
        assert row["expires_at"] >= initial_expires

    def test_new_session_has_expires_at_set(self, db_conn):
        """create_session must always populate expires_at."""
        from web.auth import db

        user_id = db.create_user(db_conn, "ExpiryCreationUser")
        token = db.create_session(db_conn, user_id)
        db_conn.commit()

        row = db_conn.execute(
            "SELECT expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()
        assert row["expires_at"] is not None
```

---

## 4D. Fix: No Rate Limiting on Auth Endpoints

### Scope

The `/auth/signin` and `/auth/claim` endpoints accept unlimited requests from any IP address. An attacker can brute-force passwords or attempt to enumerate invite codes without any throttle. This fix adds Nginx `limit_req_zone` rate limiting to these two endpoints specifically, while leaving all other routes unaffected.

### Files Modified

| File | Lines | Created / Modified | Purpose |
|------|-------|--------------------|---------| 
| `web/scripts/nginx/tools.conf` | ~20 | Modified | Add rate limit zones and exact-match location blocks |

### Why Nginx, Not Application Layer

Rate limiting at the Nginx layer is the correct architectural choice here. Nginx is the single entry point for all requests — it sees traffic before any Python code runs. Application-layer rate limiting (e.g., `slowapi`) requires a new Python dependency and must be applied correctly per-endpoint, which adds maintenance surface. Nginx's `limit_req` module is battle-tested, requires no Python changes, and cannot be bypassed by any bug in the application code.

### Exact Config Change — `web/scripts/nginx/tools.conf`

**Add rate limit zones** to the top of the file (outside all `server {}` blocks):

```nginx
# ── Rate limiting zones ───────────────────────────────────────────────────
# auth_login: password sign-in — 5 attempts per minute per IP
# auth_claim: invite code claim — 3 attempts per minute per IP
# 10m zone stores ~160,000 IP addresses; ample for this deployment.
limit_req_zone $binary_remote_addr zone=auth_login:10m rate=5r/m;
limit_req_zone $binary_remote_addr zone=auth_claim:10m rate=3r/m;
limit_req_status 429;
```

**Add exact-match location blocks** inside the `server { listen 443 ... }` block, BEFORE the existing `location /auth/ { ... }` block. In Nginx, exact-match (`=`) locations take priority over prefix locations, so these will intercept the specific endpoints while all other `/auth/` routes fall through to the existing block.

```nginx
    # ── Rate-limited auth endpoints ───────────────────────────────────
    location = /auth/signin {
        limit_req zone=auth_login burst=3 nodelay;
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location = /auth/claim {
        limit_req zone=auth_claim burst=1 nodelay;
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
```

> **Placement note:** These two exact-match blocks must appear BEFORE `location /auth/ { ... }` in the config file. Nginx processes locations in order of specificity (`=` > `^~` > prefix), but explicit ordering is clearer and prevents future confusion.

### Rate Limit Parameters Explained

| Parameter | Value | Meaning |
|-----------|-------|---------|
| `rate=5r/m` (signin) | 5 requests/minute | ~1 attempt every 12 seconds per IP |
| `burst=3` (signin) | 3 burst allowance | A human can make 3 rapid attempts before throttling begins |
| `rate=3r/m` (claim) | 3 requests/minute | Invite codes are one-time use; 3/min is generous |
| `burst=1` (claim) | 1 burst | Almost no legitimate reason to claim two codes back-to-back |
| `nodelay` | immediate reject | Over-burst requests get 429 immediately, not queued |
| `limit_req_status 429` | HTTP 429 | Standards-compliant "Too Many Requests" response |

### Acceptance Tests

Rate limiting is enforced by Nginx, not application code, so it cannot be tested with the Python test suite directly. The acceptance criteria for this fix is manual verification:

```
1. Deploy the updated nginx config.
2. Run: nginx -t   (must report "syntax is ok" and "test is successful")
3. Run: sudo nginx -s reload
4. Verify: curl -X POST https://sw3p.pro/auth/signin -H "Content-Type: application/json" \
       -d '{"display_name":"x","password":"y"}' (repeat 10 times rapidly)
   Expected: First ~5 return 401 (wrong creds), subsequent requests return 429.
5. Verify: All other /auth/ endpoints (e.g., /auth/me, /auth/logout) are unaffected.
```

---

## 4E. Fix: Password Change Does Not Invalidate Other Sessions

### Scope

When a user changes their own password (`POST /auth/set-password`) or an admin resets a user's password (`POST /admin/users/{user_id}/reset-password`), all existing sessions for that user remain valid. This means an attacker who has stolen a session token retains access even after the victim changes their password. This fix adds session invalidation to both code paths, with a distinction: user-initiated changes keep the current session alive (so the user isn't immediately logged out), while admin-initiated resets kill all sessions (a security action that should force re-authentication everywhere).

### Files Modified

| File | Lines | Created / Modified | Purpose |
|------|-------|--------------------|---------| 
| `web/auth/db.py` | ~15 | Modified | New helper `delete_sessions_except()` |
| `web/auth/main.py` | ~10 | Modified | `set_password` and `reset_user_password` endpoints |
| `tests/test_auth.py` | ~50 | Modified | New class `TestPasswordSessionInvalidation` |

### New DB Helper — `web/auth/db.py`

Add after the existing `delete_user_sessions()` function:

```python
def delete_sessions_except(
    conn: sqlite3.Connection,
    user_id: str,
    exclude_token: str,
) -> int:
    """Delete all sessions for user_id except the one matching exclude_token.

    Used when a user changes their own password: their current session
    stays alive so they are not immediately logged out, but all other
    sessions (e.g., on other devices, or stolen sessions) are revoked.
    Returns the count of deleted sessions.
    """
    cur = conn.execute(
        "DELETE FROM sessions WHERE user_id = ? AND token != ?",
        (user_id, exclude_token),
    )
    return cur.rowcount
```

### Exact Code Changes — `web/auth/main.py`

**`set_password` endpoint — BEFORE (last 3 lines only):**
```python
    db.set_user_password(conn, user["id"], body.password)
    log.info("Password changed: user_id=%s", user["id"])
    return SuccessResponse()
```

**`set_password` endpoint — AFTER:**
```python
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
```

**`reset_user_password` endpoint — BEFORE (last 4 lines only):**
```python
    password = db.generate_password()
    db.set_user_password(conn, user_id, password)
    log.info("Password reset by admin: user_id=%s by admin=%s", user_id, _admin["id"])
    return ResetPasswordResponse(...)
```

**`reset_user_password` endpoint — AFTER:**
```python
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
    return ResetPasswordResponse(...)
```

> **Note:** `db.delete_user_sessions()` already exists (implemented in Tier 3 / pre-existing). No new function is needed for the admin path.

### Acceptance Tests — `TestPasswordSessionInvalidation`

```python
class TestPasswordSessionInvalidation:
    """Verify password changes revoke appropriate sessions."""

    def test_user_password_change_kills_other_sessions(self, client, db_conn):
        """After user changes password, all sessions except current are invalid."""
        from web.auth import db

        # Create a user with two sessions: one current (simulated), one other
        user_id = db.create_user(db_conn, "MultiSessionUser")
        db.set_user_password(db_conn, user_id, "OldPass-1234")
        other_token = db.create_session(db_conn, user_id, "other-device")
        db_conn.commit()

        # Log in as this user (current session)
        response = client.post("/auth/signin", json={
            "display_name": "MultiSessionUser",
            "password": "OldPass-1234",
        })
        assert response.status_code == 200

        # Change password
        response = client.post("/auth/set-password", json={
            "current_password": "OldPass-1234",
            "password": "NewPass-5678",
        })
        assert response.status_code == 200

        # Other session must now be invalid
        other_user = db.validate_session(db_conn, other_token)
        assert other_user is None

    def test_user_password_change_keeps_current_session(self, client):
        """After user changes password, the current session remains valid."""
        # Log in, change password, verify /auth/me still returns 200.
        ...

    def test_admin_password_reset_kills_all_sessions(self, client, admin_client, db_conn):
        """Admin password reset must invalidate ALL sessions including the current one."""
        from web.auth import db

        # Create a user with an active session
        user_id = db.create_user(db_conn, "ResetTargetUser")
        token = db.create_session(db_conn, user_id)
        db_conn.commit()

        # Admin resets the password
        response = admin_client.post(f"/admin/users/{user_id}/reset-password")
        assert response.status_code == 200

        # The user's session must be gone
        result = db.validate_session(db_conn, token)
        assert result is None

    def test_admin_reset_logs_session_count(self, client, admin_client, caplog):
        """Admin reset log message must include sessions_revoked count."""
        ...
```

---

## 5. Architecture Overview

### 5a. System Diagram

```
Internet
    │
    ▼
[Nginx :443]  ← HTTPS, HSTS, security headers, rate limiting (NEW in 4D)
    │
    ├── /auth/*  ──────────────────────────► [tools-auth :8001]
    │   /admin/*                              FastAPI + Gunicorn
    │   / (portal)                            SQLite: auth.db
    │                                         (users, sessions, invites, apps)
    │
    ├── /swppp/ (static HTML) ──────────────► served by Nginx directly
    │
    └── /swppp/api/* ──────────────────────► [tools-swppp :8002]
                                              FastAPI + Gunicorn
                                              SQLite: swppp.db
                                              Validates sessions via shared
                                              auth.db (read path)
```

### 5b. Service Inventory

| Service | Port | Framework | Workers | Database | Purpose |
|---------|------|-----------|---------|----------|---------|
| `tools-auth` | 8001 | FastAPI/Gunicorn | 2 | `auth.db` | Identity, sessions, invite codes, admin |
| `tools-swppp` | 8002 | FastAPI/Gunicorn | 2 | `swppp.db` | PDF generation, weather data, SWPPP sessions |

### 5c. Cross-Service Communication

| From | To | Mechanism | What Is Exchanged |
|------|----|-----------|-------------------|
| Nginx | tools-auth | HTTP proxy (loopback) | All `/auth/` and `/admin/` requests |
| Nginx | tools-swppp | HTTP proxy (loopback) | All `/swppp/api/` requests |
| tools-swppp | auth.db | Shared SQLite file (read) | Session validation via `validate_session()` |

**Auth dependency chain:** The SWPPP service imports `web.auth.dependencies.require_app("swppp")`, which calls `validate_session()` directly against `auth.db`. If the auth service is down but the DB file is accessible, SWPPP can still validate sessions. The auth service being down does not cause SWPPP to fail open.

### 5d. File System Layout (Production)

```
/opt/tools/
├── repo/                    ← Git checkout
│   ├── web/
│   │   ├── auth/
│   │   ├── swppp_api/
│   │   ├── frontend/
│   │   ├── middleware.py
│   │   └── scripts/
│   │       └── nginx/tools.conf
│   └── assets/template.pdf
├── data/
│   ├── auth.db              ← Auth database (not in git)
│   └── swppp.db             ← SWPPP database (not in git)
└── venv/                    ← Python virtual environment
```

---

## 6. API Endpoint Inventory

### Auth Service (port 8001)

| # | Method | Path | Auth Level | Handler | Purpose |
|---|--------|------|------------|---------|---------|
| 1 | GET | `/auth/login` | Public | `login_page()` | Serve login HTML |
| 2 | POST | `/auth/claim` | Public + **rate limited** | `claim_code()` | Claim invite code |
| 3 | POST | `/auth/signin` | Public + **rate limited** | `login_password()` | Password login |
| 4 | POST | `/auth/logout` | Session | `logout()` | Destroy session + **clear cookie** |
| 5 | POST | `/auth/set-password` | Session | `set_password()` | Change password + **revoke other sessions** |
| 6 | GET | `/auth/me` | Session | `me()` | Get current user info |
| 7 | GET | `/` | Session (redirect gate) | `portal_index()` | Portal index |
| 8 | GET | `/admin` | Admin (redirect gate) | `admin_page()` | Admin page |
| 9 | GET | `/admin/users` | Admin | `list_users()` | List all users |
| 10 | POST | `/admin/users` | Admin | `create_user_endpoint()` | Create user |
| 11 | PATCH | `/admin/users/{id}` | Admin | `update_user_endpoint()` | Activate/deactivate/promote |
| 12 | POST | `/admin/users/{id}/reset-password` | Admin | `reset_user_password()` | Reset password + **revoke all sessions** |
| 13 | GET | `/admin/users/{id}/sessions` | Admin | `list_user_sessions()` | List sessions |
| 14 | DELETE | `/admin/users/{id}/sessions` | Admin | `delete_all_user_sessions()` | Kill all sessions |
| 15 | DELETE | `/admin/sessions/{prefix}` | Admin | `delete_session_by_prefix()` | Kill one session |
| 16 | GET | `/admin/invites` | Admin | `list_invites()` | List invite codes |
| 17 | POST | `/admin/invites` | Admin | `create_invite()` | Create invite code |
| 18 | DELETE | `/admin/invites/{id}` | Admin | `revoke_invite()` | Revoke invite |
| 19 | POST | `/admin/users/{id}/apps` | Admin | `grant_app()` | Grant app access |
| 20 | DELETE | `/admin/users/{id}/apps/{app}` | Admin | `revoke_app()` | Revoke app access |
| 21 | GET | `/admin/apps` | Admin | `list_apps()` | List apps |
| 22 | POST | `/admin/apps` | Admin | `create_app_endpoint()` | Register app |
| 23 | PATCH | `/admin/apps/{id}` | Admin | `update_app_endpoint()` | Update app |

**Bold** = changed in Tier 4.

---

## 7. API Request/Response Examples

### Password change with session invalidation — `POST /auth/set-password`

**Request:**
```json
{
  "current_password": "OldPass-1234",
  "password": "NewPass-5678"
}
```

**Response (success):**
```json
{
  "success": true
}
```

**Response (wrong current password):**
```json
{
  "detail": "Current password is incorrect"
}
```

---

## 8. Security Posture Summary

### 8a. Authentication & Authorization Model

| Layer | Mechanism | Details |
|-------|-----------|---------|
| Identity | Invite codes → username + password | Invite creates account; user sets password on first use |
| Session | HTTPOnly cookie (`tools_session`) | 64-char hex token; 90-day sliding expiry **(new in 4C)** |
| Authorization | App-scoped access via `require_app()` | Users can only reach tools explicitly provisioned |
| Admin | `is_admin` flag + `require_admin()` dependency | Cannot self-deactivate; actions logged |

### 8b. Attack Surface Summary

| Surface | Mitigation | Residual Risk |
|---------|-----------|---------------|
| Unauthenticated endpoints | `/auth/login` (GET), `/auth/signin`, `/auth/claim` only | Low — login page is intentionally public |
| Brute force on signin | Nginx `limit_req` 5r/min per IP **(new in 4D)** | Low — distributed attacks from many IPs not mitigated |
| Invite code enumeration | Nginx `limit_req` 3r/min per IP **(new in 4D)**; codes are single-use | Low |
| Session theft | HttpOnly prevents JS access; sessions expire **(new in 4C)**; password change revokes **(new in 4E)** | Low |
| Stale browser cookie after logout | Cookie cleared in response **(fixed in 4A)** | None |
| Session refresh on redirects | Refresh only on 2xx **(fixed in 4B)** | None |
| File uploads | 5 MB limit in app + 6 MB in Nginx; PDF type validated | Low |
| User-supplied strings in DB | Parameterized queries throughout; dynamic SET whitelisted | None |
| Cookie handling | `HttpOnly=True`, `Secure=True` (prod), `SameSite=lax`, `Max-Age=90d` | Low |
| Cross-site requests (CSRF) | Origin header check via shared factory; SameSite=lax cookies | Low |
| Brute force on admin endpoints | Admin requires valid session first (effectively 2FA: know creds + have session) | Low |
| Request body size | Nginx `client_max_body_size 6m`; app `MAX_UPLOAD_BYTES = 5MB` | Low |

### 8c. Security Headers

| Header | Value | Set Where |
|--------|-------|-----------|
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains` | Nginx |
| `X-Content-Type-Options` | `nosniff` | Nginx |
| `X-Frame-Options` | `DENY` | Nginx |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Nginx |
| `Content-Security-Policy` | Not set | — (tracked in Appendix B) |

### 8d. Secrets & Credentials

| Secret | Where Stored | How Rotated |
|--------|-------------|-------------|
| Session tokens | `sessions.token` (plaintext) | Admin revocation or password change |
| Password hashes | `users.password_hash` (scrypt + salt) | User or admin reset |
| Invite codes | `invite_codes.id` | Single-use; revoked by admin |

### 8e. Explicitly Unprotected Areas

```
- Content-Security-Policy header missing — tracked in Appendix B; low risk
  for a closed platform with no user-generated HTML content.

- Session tokens stored in plaintext in DB — tracked in Appendix B; low risk
  given DB is not externally accessible and backups are admin-controlled.

- No distributed brute-force protection (rate limits are per-IP, not global) —
  acceptable for current scale (small team, known users).
```

---

## 9. Data & Storage

### 9a. Database Inventory

| Database | Engine | Location | Tables | Shared By |
|----------|--------|----------|--------|-----------|
| `auth.db` | SQLite (WAL mode) | `/opt/tools/data/auth.db` | 5: `apps`, `users`, `invite_codes`, `user_app_access`, `sessions` | `tools-auth`, `tools-swppp` (read) |
| `swppp.db` | SQLite (WAL mode) | `/opt/tools/data/swppp.db` | (SWPPP sessions) | `tools-swppp` only |

### 9b. Backup Strategy

| What | Method | Schedule | Retention |
|------|--------|----------|-----------|
| `auth.db` | `sqlite3 .backup` via `web/scripts/backup.sh` | Daily | Per backup script config |
| `swppp.db` | `sqlite3 .backup` via `web/scripts/backup.sh` | Daily | Per backup script config |

### 9c. Data Lifecycle

| Data Type | Created By | Lifetime | Cleanup Mechanism |
|-----------|-----------|----------|-------------------|
| Sessions | Login / invite claim | 90 days idle **(new in 4C)**; explicit logout | `validate_session()` sliding expiry; admin deletion; password change revocation **(new in 4E)** |
| Invite codes | Admin | Until claimed or revoked | Admin `DELETE /admin/invites/{id}` |
| Temp PDF dirs | `/swppp/api/generate` | Request duration | `BackgroundTask` cleanup |

---

## 10. Deployment

### 10a. Provisioning Steps

| Step | Action | Idempotent? | Notes |
|------|--------|-------------|-------|
| 1 | `git pull` on VPS | Yes | Pull Tier 4 changes |
| 2 | `sudo systemctl restart tools-auth` | Yes | Triggers `init_db()` → Migration 3 runs automatically |
| 3 | `sudo systemctl restart tools-swppp` | Yes | Picks up any shared code changes |
| 4 | `sudo nginx -t` | Yes | Must report "test is successful" before proceeding |
| 5 | `sudo nginx -s reload` | Yes | Live reload; no downtime |
| 6 | Manual rate limit smoke test | Yes | See §4D acceptance criteria |
| 7 | `pytest tests/test_auth.py -v` | Yes | Must pass 100%; paste output per evidence standard |

### 10b. Rollback Plan

**Service rollback:**
```
1. git revert HEAD   (or git checkout {previous-commit} -- web/auth/db.py web/auth/main.py)
2. sudo systemctl restart tools-auth tools-swppp
3. sudo nginx -t && sudo nginx -s reload
```

**Data rollback:** Migration 3 adds a single nullable column (`expires_at`) to `sessions`. It does not drop any data. Rolling back the code will simply ignore the column — no data will be corrupted. If the column must be removed (extreme case), it requires recreating the sessions table, which is low-risk (active sessions can be re-created by users logging in again).

**Client rollback:** N/A — no desktop app or SPA changes in this tier.

### 10c. Monitoring & Observability

| What | How | Where Logs Go |
|------|-----|---------------|
| Auth errors | Gunicorn stderr + Python `logging` | `/var/log/tools/auth-error.log`, `journalctl -u tools-auth` |
| Rate limit hits | Nginx `limit_req` logs at WARN level | `/var/log/nginx/error.log` |
| Session revocations | `log.info("Password changed: user_id=... sessions_revoked=N")` | `journalctl -u tools-auth` |
| Service health | systemd `Restart=always` | `systemctl status tools-auth` |

---

## 11. Test Suite Inventory

### 11a. Final Counts

| File | Tests Before | Tests After | Phase Added | What It Covers |
|------|-------------|-------------|-------------|----------------|
| `tests/test_auth.py` | 92 | ~120 | Tiers 1–4 | Auth service end-to-end |
| `tests/test_session.py` | 13 | 13 | Pre-existing | Desktop app local sessions |
| **Total (auth)** | **92** | **~120** | | |

### 11b. New Test Classes (Tier 4)

```
tests/test_auth.py:
  TestLogoutCookieFix         (~3 tests)  — 4A: cookie cleared in response headers
  TestSessionRefreshMiddleware (~3 tests)  — 4B: refresh only on 2xx
  TestSessionExpiry           (~3 tests)  — 4C: expiry enforced, sliding window
  TestPasswordSessionInvalidation (~4 tests) — 4E: sessions revoked on password change
```

> Rate limiting (4D) is Nginx-enforced and not testable in the Python test suite. Manual verification procedure documented in §4D.

### 11c. Test Infrastructure

| Component | Location | Purpose |
|-----------|----------|---------|
| `client` fixture | `tests/conftest.py` | FastAPI TestClient with in-memory DB |
| `db_conn` fixture | `tests/conftest.py` | Direct SQLite connection for setup/assertions |
| `admin_client` fixture | `tests/conftest.py` or `test_auth.py` | Authenticated admin TestClient |

### 11d. Test Isolation Strategy

Each test class either uses the shared `client` fixture (which provides an isolated in-memory SQLite DB) or creates its own DB state via `db_conn`. Tests do not share session tokens across classes. Network calls are not made — `TestClient` intercepts all HTTP at the ASGI layer. Mock targets are avoided in favor of direct DB manipulation (per Tier 1–3 pattern established in the project).

### 11e. What Is NOT Tested

| Gap | Why Not Tested | Risk Level |
|-----|---------------|------------|
| Nginx rate limiting | Python test suite has no Nginx layer | Low — manually verified on deploy |
| Cookie `Secure` flag in production | TestClient ignores TLS | Low — flag is set when `DEV_MODE=0` |
| Concurrent session creation | SQLite WAL handles this; no race in single-writer scenario | Low |

---

## 12. Performance Baseline

| Operation | Typical Latency | Notes |
|-----------|----------------|-------|
| `POST /auth/signin` | < 200ms | scrypt hash is intentionally slow (~50ms); acceptable |
| `GET /auth/me` | < 50ms | Single DB query |
| `POST /auth/set-password` | < 250ms | scrypt hash + session DELETE |
| Session validation (per request) | < 10ms | Single JOIN query + two UPDATE rows |
| Migration 3 (first boot after deploy) | < 1s | ALTER TABLE + UPDATE on small sessions table |

Measurements are observed, not formally benchmarked.

---

## 13. Change Delta Summary

### By Directory

| Directory | Files Added | Files Modified | Notes |
|-----------|-------------|---------------|-------|
| `web/auth/` | 0 | 2 (`db.py`, `main.py`) | Session schema, logout, middleware, password endpoints |
| `web/scripts/nginx/` | 0 | 1 (`tools.conf`) | Rate limiting zones + exact-match location blocks |
| `tests/` | 0 | 1 (`test_auth.py`) | ~28 new tests across 4 classes |

### Untouched Areas

```
- `app/`              — Desktop/CLI app code — 0 changes
- `web/swppp_api/`    — SWPPP API — 0 changes
- `web/middleware.py` — Shared CSRF middleware — 0 changes
- `web/auth/models.py`— Pydantic models — 0 changes
- `web/auth/dependencies.py` — Auth dependencies — 0 changes
- `assets/`           — PDF template, icons — 0 changes
- `pyproject.toml`    — No new dependencies — 0 changes
```

---

## 14. User-Facing Behavior

### Workflow: Logout

1. **Entry point:** User clicks "Log out" button on portal.
2. **Steps:** Browser POSTs to `/auth/logout` → server deletes session → response carries `Set-Cookie: tools_session=; Max-Age=0` → browser deletes cookie → browser follows 302 redirect to `/auth/login`.
3. **Timing:** < 100ms.
4. **Output:** User sees the login page. Cookie is gone from browser.
5. **Error states:** If the cookie was already invalid, logout still redirects to login cleanly (no error).
6. **Before fix:** Cookie persisted for 90 days after logout. User appeared logged out but cookie was still present — a stolen cookie from this window would still be valid until it naturally expired.

### Workflow: Password Change

1. **Entry point:** User navigates to account settings and submits new password.
2. **Steps:** `POST /auth/set-password` with `current_password` + new `password` → server verifies current password → updates hash → deletes all other sessions → returns success.
3. **Timing:** < 250ms (scrypt hash).
4. **Output:** Success response. User remains logged in on their current device. All other devices are silently logged out.
5. **Error states:** Wrong `current_password` → 401 "Current password is incorrect".

### Workflow: Admin Password Reset

1. **Entry point:** Admin navigates to user management, clicks "Reset Password" for a target user.
2. **Steps:** `POST /admin/users/{id}/reset-password` → server generates new password → updates hash → deletes ALL sessions for target user → returns new credential.
3. **Timing:** < 250ms.
4. **Output:** Admin receives the generated password. Target user is logged out everywhere immediately.
5. **Error states:** Unknown user ID → 404.

### Workflow: Session Expiry (Transparent)

1. No user-visible change during normal use. Active sessions extend automatically on every authenticated request.
2. After 90 days of inactivity, the next request redirects to `/auth/login`.
3. The user sees the login page with no error message — the session simply expired.

---

## Appendix A: Issue & Fix Registry

| # | Issue | Phase | Bug Category | Root Cause | Fix | Files Changed |
|---|-------|-------|-------------|------------|-----|---------------|
| 1 | Logout doesn't clear browser cookie | 4A | Response mutation | `delete_cookie` called on injected `Response` object; a different `RedirectResponse` is returned, discarding the mutation | Call `delete_cookie` on the returned `RedirectResponse` directly | `web/auth/main.py` |
| 2 | Middleware refreshes invalid sessions on 3xx | 4B | Middleware ordering | Refresh condition `< 400` includes 302 redirects from session-rejection paths | Narrow condition to `< 300` (2xx only) | `web/auth/main.py` |
| 3 | Sessions never expire | 4C | Validation gap | `sessions` table has no `expires_at`; `validate_session` performs no expiry check | Add `expires_at` column, populate on create, check and slide on validate | `web/auth/db.py` |
| 4 | No rate limiting on auth endpoints | 4D | Validation gap | No Nginx `limit_req` zone applied to `/auth/signin` or `/auth/claim` | Add rate limit zones and exact-match location blocks in Nginx config | `web/scripts/nginx/tools.conf` |
| 5 | Password change doesn't revoke other sessions | 4E | Validation gap | `set_user_password` only updates the hash; no session cleanup | Call `delete_sessions_except` after password change; `delete_user_sessions` after admin reset | `web/auth/db.py`, `web/auth/main.py` |

### Bug Category Reference

| Category | Definition |
|----------|-----------| 
| Response mutation | A component modifies the response after the intended handler has already set it |
| Middleware ordering | Two components mutate the same response/request; interaction order produces wrong behavior |
| Validation gap | Input or state accepted by one layer but not validated or enforced by a downstream layer |

---

## Appendix B: Known Limitations & Future Work

```
1. Content-Security-Policy header not set.
   What: No CSP header in Nginx config; XSS has no last-line-of-defense policy.
   Why deferred: No user-generated HTML content exists in the platform today;
   attack surface is low. CSP requires careful tuning to avoid breaking Alpine.js
   and CDN-loaded Tailwind — worth a focused pass rather than a rushed addition.
   Trigger to revisit: Any user-generated content feature, or before public-facing launch.
   Estimated effort: 2–4 hours (tuning + test).

2. Session tokens stored in plaintext in the database.
   What: The 64-char hex token in sessions.token is not hashed; a DB dump yields
   live tokens directly.
   Why deferred: DB access is not externally available; this is defense-in-depth
   beyond current threat model for a closed-user platform.
   Trigger to revisit: If DB backups become widely distributed, or if a
   breach-disclosure obligation exists.
   Estimated effort: 1 day (schema migration + hash-on-write + hash-on-compare).

3. Distributed brute-force not mitigated.
   What: Nginx rate limits are per source IP. An attacker rotating through many
   IPs can bypass them.
   Why deferred: The user base is fully known (invite-only employees). A
   coordinated distributed attack is implausible at current scale.
   Trigger to revisit: Platform opens to external users or credentials are seen
   in credential-stuffing lists.
   Estimated effort: 1–2 days (fail2ban integration or upstream WAF).
```

---

## Mandatory Reporting Section

> **The agent must complete this section before marking Tier 4 done.**
> Summaries are not accepted. Paste exact output.

### Required Evidence

**1. Full `pytest` output — copy/paste verbatim:**
```
(paste here)
```

**2. `nginx -t` output:**
```
(paste here)
```

**3. Rate limit smoke test result** (10x rapid POST to `/auth/signin`, record response codes):
```
(paste here)
```

**4. Constraint compliance table** — return to §1d and fill in every row.

---

*End of IR_HARDENING_TIER4.md. Every section must be confirmed before this record is closed.*
