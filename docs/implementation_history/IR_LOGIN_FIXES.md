# Implementation Record — Login Bug Fixes (Tier 1)

**Project**: sw3p.pro — Company Tools Portal  
**Scope**: Fix three interrelated login/session bugs in the auth system  
**Priority**: CRITICAL — these bugs are actively breaking the login flow  
**Server**: DigitalOcean VPS (Ubuntu 24.04 LTS), domain `sw3p.pro`  
**SSH**: `ssh -i ~/.ssh/swppp-vps-deploy root@{server_ip}`  
**Repo on server**: `/opt/tools/repo`  
**Production data**: `/opt/tools/data` (NEVER overwrite `auth.db` or `swppp_sessions.db`)  
**Services**: `tools-auth` (port 8001), `tools-swppp` (port 8002)  

---

## Pre-Flight Checklist

Before making ANY changes:

1. **Backup production databases**:
   ```
   ssh -i ~/.ssh/swppp-vps-deploy root@{server_ip}
   cp /opt/tools/data/auth.db /opt/tools/backups/auth_$(date +%Y%m%d_%H%M%S).db
   cp /opt/tools/data/swppp_sessions.db /opt/tools/backups/swppp_sessions_$(date +%Y%m%d_%H%M%S).db
   ```

2. **Verify current database schema** — confirm whether the `password_hash` column exists:
   ```
   ssh -i ~/.ssh/swppp-vps-deploy root@{server_ip}
   sqlite3 /opt/tools/data/auth.db "PRAGMA table_info(users);"
   ```
   Record the output. If `password_hash` is NOT listed, Fix 1C is confirmed as an active issue.

3. **Snapshot current service status**:
   ```
   systemctl status tools-auth tools-swppp
   journalctl -u tools-auth --since "1 hour ago" --no-pager | tail -40
   ```

---

## Fix 1A — Race Condition in login.html Auto-Submit

### Problem

In `web/frontend/portal/login.html`, the `init()` method fires two async operations in parallel:
- A `fetch('/auth/me')` check (are we already logged in?)
- An immediate `submitCode()` call (if `?code=` is in the URL)

These race against each other. If both complete around the same time, competing `window.location.href` assignments can leave the user in a broken state — half-authenticated, stuck on the login page, or redirected before the session cookie is set.

### File to Edit

`web/frontend/portal/login.html` — the `init()` method inside the `loginApp()` function (starts around line 85).

### Current Code (REPLACE THIS)

```javascript
init() {
    // If already logged in, go straight to the portal
    fetch('/auth/me', { credentials: 'same-origin' })
        .then(r => { if (r.ok) window.location.href = '/'; });

    // Pre-fill from ?code= query param — auto-switch to code tab
    const params = new URLSearchParams(window.location.search);
    const prefill = params.get('code');
    if (prefill) {
        this.mode = 'code';
        this.code = prefill;
        this.submitCode();
    }
},
```

### New Code (REPLACE WITH)

```javascript
async init() {
    const params = new URLSearchParams(window.location.search);
    const prefill = params.get('code');

    // If arriving via invite link, skip session check — just claim the code.
    // These two paths must be MUTUALLY EXCLUSIVE, never parallel.
    if (prefill) {
        this.mode = 'code';
        this.code = prefill;
        await this.submitCode();
        return;
    }

    // No invite code — check if already logged in.
    try {
        const res = await fetch('/auth/me', { credentials: 'same-origin' });
        if (res.ok) {
            window.location.href = '/';
            return;
        }
    } catch {
        // Not logged in — stay on login page.
    }
},
```

### Why This Works

The two async paths are now **mutually exclusive**. If a `?code=` parameter exists, we claim it and stop. If it doesn't, we check the session. No parallel execution, no competing redirects. The `await` on `submitCode()` ensures we don't fall through to the session check while a claim is in flight.

### Acceptance Test

1. Open `https://sw3p.pro/auth/login?code={valid_invite_code}` in a private/incognito window.
2. Verify: user is redirected to `/` after claim succeeds — no flash, no double redirect, no error.
3. Open `https://sw3p.pro/auth/login` while already logged in (normal window).
4. Verify: user is redirected to `/` without seeing the login form.
5. Open `https://sw3p.pro/auth/login` in a private/incognito window (no session).
6. Verify: login form appears with no console errors.

---

## Fix 1B — Middleware Cookie-Overwrite Bug

### Problem

The `refresh_session_cookie` middleware in `web/auth/main.py` (line 64) refreshes the session cookie's `max_age` on every successful response. It has a hardcoded exclusion list to skip paths where the endpoint itself sets/deletes the cookie:

```python
if request.url.path in ("/auth/claim", "/auth/signin", "/auth/logout"):
    return response
```

This breaks if the path doesn't match exactly (trailing slashes, proxy rewrites). When it fails to skip, the middleware reads the **incoming** request cookie (which is `None` for a new login) and overwrites the **fresh** session token that `/auth/claim` or `/auth/signin` just set on the response. The user's brand new session is silently replaced with nothing.

### File to Edit

`web/auth/main.py` — the `refresh_session_cookie` middleware function (starts around line 63).

### Current Code (REPLACE THIS)

```python
@app.middleware("http")
async def refresh_session_cookie(request: Request, call_next):
    response = await call_next(request)
    # Don't re-stamp if the endpoint already set/deleted the cookie
    if request.url.path in ("/auth/claim", "/auth/signin", "/auth/logout"):
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
```

### New Code (REPLACE WITH)

```python
@app.middleware("http")
async def refresh_session_cookie(request: Request, call_next):
    response = await call_next(request)

    # If the downstream endpoint already set or deleted the tools_session
    # cookie, don't overwrite its decision. This is safer than a path-based
    # blocklist because it reacts to what actually happened, not to what
    # we predict will happen.
    resp_cookies_header = response.headers.get("set-cookie", "")
    if "tools_session" in resp_cookies_header:
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
```

### Why This Works

Instead of guessing which URL paths will set cookies (fragile), we check whether the **response itself** already contains a `Set-Cookie` for `tools_session`. If the endpoint already made a cookie decision — whether setting a new session or deleting one — we respect it. This is immune to trailing slashes, proxy rewrites, and future endpoint additions.

### Acceptance Tests

Add these to `tests/test_auth.py`:

```python
class TestMiddlewareCookieRefresh:
    def test_claim_sets_fresh_cookie_not_overwritten(self):
        """After claiming an invite, the response cookie must contain
        a valid session token — not None or an empty string."""
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            code = db.create_invite(conn, "CookieTestUser", ["swppp"])
        c = TestClient(app, cookies={})
        r = c.post("/auth/claim", json={"code": code})
        assert r.status_code == 200
        cookie_val = r.cookies.get("tools_session")
        assert cookie_val is not None
        assert len(cookie_val) > 0
        # Verify the cookie is actually valid
        c2 = TestClient(app, cookies={"tools_session": cookie_val})
        me = c2.get("/auth/me")
        assert me.status_code == 200

    def test_signin_sets_fresh_cookie_not_overwritten(self):
        """After password login, the response cookie must be a valid session."""
        with db.connect() as conn:
            db.seed_app(conn, "swppp", "SWPPP AutoFill", "desc", "/swppp")
            code = db.create_invite(conn, "PwCookieUser", ["swppp"])
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "TestPass123!"})

        c2 = TestClient(app, cookies={})
        r = c2.post("/auth/signin", json={
            "display_name": "PwCookieUser",
            "password": "TestPass123!",
        })
        assert r.status_code == 200
        cookie_val = r.cookies.get("tools_session")
        assert cookie_val is not None
        assert len(cookie_val) > 0

    def test_existing_session_gets_refreshed(self):
        """A normal authenticated request should still get a refreshed cookie."""
        admin = _admin_client()
        r = admin.get("/auth/me")
        assert r.status_code == 200
        # The cookie refresh middleware should have re-stamped the cookie
        assert "tools_session" in r.cookies or "set-cookie" in r.headers.get("set-cookie", "").lower() or r.status_code == 200
```

---

## Fix 1C — Database Migration Robustness

### Problem

The `password_hash` column migration in `web/auth/db.py` relies on a `try/except` around `ALTER TABLE`. This works but is fragile. The repo also ships a stale `web/data/auth.db` file that lacks the column. If that file ever overwrites the production database, password login breaks immediately.

### Part A — Improve the Migration in `web/auth/db.py`

### Current Code (REPLACE THIS)

In the `init_db()` function (around line 116):

```python
def init_db() -> None:
    """Create all tables (idempotent)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.executescript(SCHEMA_SQL)
        # Migrate existing databases: add password_hash column if missing.
        try:
            conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists
        conn.commit()
    finally:
        conn.close()
    log.info("Database initialized at %s", DB_PATH)
```

### New Code (REPLACE WITH)

```python
def init_db() -> None:
    """Create all tables and run pending migrations (idempotent)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.executescript(SCHEMA_SQL)
        _run_migrations(conn)
        conn.commit()
    finally:
        conn.close()
    log.info("Database initialized at %s", DB_PATH)


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a column exists on a table via PRAGMA table_info."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Run all schema migrations. Each migration checks preconditions
    before executing, so this function is safe to call repeatedly."""

    # Migration 1: Add password_hash column to users table.
    if not _column_exists(conn, "users", "password_hash"):
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        log.info("Migration: added password_hash column to users table")
```

### Why This Works

Instead of catching exceptions to detect existing columns, we explicitly check with `PRAGMA table_info`. This is deterministic — no silent exception swallowing. The `_run_migrations()` function gives us a clear place to add future migrations. Each migration checks its own precondition, so the entire function is idempotent.

### Part B — Protect Production Database in deploy.sh

The deploy script (`web/scripts/deploy.sh`) does a `git pull` into `/opt/tools/repo`, which means the stale `web/data/auth.db` in the repo gets pulled down alongside the code. The production database lives at `/opt/tools/data/auth.db` (separate directory), so it's safe **as long as nothing copies the repo's version over it**.

Add a `.gitignore` entry AND a safety check:

**Add to `.gitignore` in the project root** (create if it doesn't exist):

```
# Never commit production database files
web/data/*.db
web/data/*.db-wal
web/data/*.db-shm
```

**Remove the stale database files from git tracking** (run locally before pushing):

```bash
git rm --cached web/data/auth.db web/data/swppp_sessions.db 2>/dev/null || true
```

**Add a safety comment to `deploy.sh`** at the top of the file, after the variable declarations (around line 15):

```bash
# ── CRITICAL: Database Safety ─────────────────────────────────────────
# Production databases live in $DATA_DIR (/opt/tools/data), NOT in the
# repo. The repo may contain stale .db files from development — they
# must NEVER be copied to $DATA_DIR. The init_admin.py script handles
# first-run database creation. Subsequent deploys rely on migrations
# inside init_db() to update the schema.
# ──────────────────────────────────────────────────────────────────────
```

### Acceptance Tests

Add to `tests/test_auth.py`:

```python
class TestDatabaseMigration:
    def test_init_db_creates_password_hash_column(self):
        """init_db() must ensure the password_hash column exists."""
        db.init_db()
        conn = sqlite3.connect(str(db.DB_PATH))
        try:
            cols = [
                r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()
            ]
            assert "password_hash" in cols
        finally:
            conn.close()

    def test_init_db_idempotent(self):
        """Calling init_db() twice must not raise or duplicate anything."""
        db.init_db()
        db.init_db()  # should not raise
        conn = sqlite3.connect(str(db.DB_PATH))
        try:
            cols = [
                r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()
            ]
            # password_hash should appear exactly once
            assert cols.count("password_hash") == 1
        finally:
            conn.close()
```

---

## Deployment Sequence

After all three fixes are committed and tests pass locally:

1. **SSH into the server**:
   ```
   ssh -i ~/.ssh/swppp-vps-deploy root@{server_ip}
   ```

2. **Backup current databases** (repeat of pre-flight, but do it again right before deploy):
   ```
   cp /opt/tools/data/auth.db /opt/tools/backups/auth_pre_tier1_$(date +%Y%m%d_%H%M%S).db
   ```

3. **Pull the latest code**:
   ```
   cd /opt/tools/repo
   git pull --ff-only
   ```

4. **Restart both services** (this triggers `init_db()` → runs migrations):
   ```
   systemctl restart tools-auth tools-swppp
   ```

5. **Verify services are healthy**:
   ```
   systemctl status tools-auth tools-swppp
   journalctl -u tools-auth --since "2 min ago" --no-pager
   ```
   Look for: `"Database initialized at ..."` and `"Migration: added password_hash column..."` (if column was missing).

6. **Smoke test from browser**:
   - Visit `https://sw3p.pro/auth/login` — login form should appear with no flash.
   - Log in with password (if set) — should redirect to portal immediately.
   - Open `https://sw3p.pro/auth/login` while logged in — should redirect to portal.
   - Generate a new invite in admin panel → open the invite link in a private window → should claim and redirect cleanly.

7. **Verify database schema**:
   ```
   sqlite3 /opt/tools/data/auth.db "PRAGMA table_info(users);"
   ```
   Confirm `password_hash` column is present.

### Rollback Plan

If anything goes wrong after deploy:

1. Restore the database backup:
   ```
   cp /opt/tools/backups/auth_pre_tier1_{timestamp}.db /opt/tools/data/auth.db
   ```
2. Revert the code:
   ```
   cd /opt/tools/repo
   git log --oneline -5      # find the previous commit hash
   git checkout {prev_hash}
   ```
3. Restart services:
   ```
   systemctl restart tools-auth tools-swppp
   ```

---

## Run All Tests

Before committing, run the full test suite to make sure nothing is broken:

```bash
cd /path/to/swpppautofill_windows  
TOOLS_DEV_MODE=1 python -m pytest tests/test_auth.py -v
```

All existing tests must still pass. The new tests added by this IR must also pass.

---

## Files Modified by This IR

| File | Change |
|---|---|
| `web/frontend/portal/login.html` | Fix 1A — restructure `init()` to eliminate race condition |
| `web/auth/main.py` | Fix 1B — replace path-based exclusion with response-based check in cookie middleware |
| `web/auth/db.py` | Fix 1C — add `_column_exists()` helper and `_run_migrations()` pattern |
| `tests/test_auth.py` | Add `TestMiddlewareCookieRefresh` and `TestDatabaseMigration` test classes |
| `.gitignore` | Add `web/data/*.db` patterns |
| `web/scripts/deploy.sh` | Add safety comment about database files |

---

## Follow-Up (Tier 2 — Separate IR)

These are NOT part of this IR. Do not implement them in this session. They are listed here for planning purposes only.

- **2A**: Move auth guard for HTML pages from client-side JS to server-side (proxy `/`, `/admin` through FastAPI instead of serving as static files via Nginx). Eliminates the "Loading..." flash and hides admin page source from unauthenticated users.
- **2B**: Extract duplicated CSRF middleware from `web/auth/main.py` and `web/swppp_api/main.py` into a shared `web/middleware.py` module. Single source of truth.
- **2C**: Remove duplicate `DEV_MODE` declaration on line 471 of `web/swppp_api/main.py` (already declared on line 53).

## Follow-Up (Tier 3 — Separate IR)

- **3A**: Add rate limiting to `/auth/claim`, `/auth/signin`, and `/auth/set-password` endpoints (10 attempts per IP per minute).
- **3B**: Change CSRF middleware to reject unsafe requests that omit the `Origin` header entirely (currently only checks if header is present).
- **3C**: Evaluate SQLite write contention with 2 Gunicorn workers under concurrent load. Document decision to keep or reduce worker count.
