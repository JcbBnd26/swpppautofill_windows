# Implementation Record — Hardening Fixes (Tier 3)

**Project**: sw3p.pro — Company Tools Portal
**Scope**: Close security and data-integrity gaps surfaced during second-pass review; clean up one shipped test-file artifact
**Priority**: HIGH for 3A and 3B (real security/integrity gaps); MEDIUM for 3C; LOW for 3D (hygiene)
**Depends on**: Tier 1 (Login Bug Fixes) and Tier 2 (Hardening) must be deployed and stable
**Server**: DigitalOcean VPS (Ubuntu 24.04 LTS), domain `sw3p.pro`
**SSH**: `ssh -i ~/.ssh/swppp-vps-deploy root@{server_ip}`
**Repo on server**: `/opt/tools/repo`
**Production data**: `/opt/tools/data` (NEVER overwrite database files)
**Services**: `tools-auth` (port 8001), `tools-swppp` (port 8002)

> **Source**: Second-pass review of the codebase dated 2026-04-17 after Tier 1/Tier 2 shipped. This IR addresses issues that did NOT appear in the prior Tier 3 plan (rate limiting, Origin-absent CSRF, SQLite contention). Those remain open for a future IR.

---

## Pre-Flight Checklist

Before making ANY changes:

1. **Confirm Tier 1 and Tier 2 are deployed and stable.** SSH into the server and verify:
   ```
   ssh -i ~/.ssh/swppp-vps-deploy root@{server_ip}
   systemctl status tools-auth tools-swppp
   journalctl -u tools-auth --since "24 hours ago" --no-pager | grep -i error | tail -10
   ```
   If there are auth errors from the last 24 hours, do NOT proceed — investigate first.

2. **Backup production databases**:
   ```
   cp /opt/tools/data/auth.db /opt/tools/backups/auth_pre_tier3_$(date +%Y%m%d_%H%M%S).db
   cp /opt/tools/data/swppp_sessions.db /opt/tools/backups/swppp_sessions_pre_tier3_$(date +%Y%m%d_%H%M%S).db
   ```

3. **Inspect the users table for display-name collisions BEFORE Fix 3B is applied.** This is read-only diagnostic:
   ```
   sqlite3 /opt/tools/data/auth.db \
     "SELECT LOWER(display_name) AS n, COUNT(*) AS c FROM users GROUP BY n HAVING c > 1;"
   ```
   Record the output. If any rows are returned, Fix 3B's migration will fail until the duplicates are resolved manually. See Fix 3B — Part C for resolution steps.

4. **Baseline test count** (on local dev machine):
   ```
   cd C:\Projects\swpppautofill_windows
   .venv\Scripts\Activate.ps1; $env:TOOLS_DEV_MODE="1"; pytest -q
   ```
   Record: expected is `172 passed`.

---

## Fix 3A — Require Current Password on `/auth/set-password`

### Problem

`POST /auth/set-password` (at `web/auth/main.py:186-194`) accepts a new password and updates `users.password_hash` with only the session cookie as authorization. There is no verification that the caller knows the *current* password. This means:

1. **Session theft = full account takeover.** If an attacker acquires a valid session cookie (XSS, shared device, malware, session fixation in a future bug), they can reset the password and lock the real user out. Without the current-password check, cookie theft is one step from total control.
2. **The threat model matters.** The user base is field inspectors who may leave tools open on shared laptops in vehicles. Cookie theft is not hypothetical for this deployment.

The fix is a standard auth pattern: when a user already has a password set, changing it requires proving knowledge of the old one. When no password exists yet (first-time setup after invite claim), no check is needed.

### Files to Edit

| File | Change |
|---|---|
| `web/auth/models.py` | Add optional `current_password` to `SetPasswordRequest` |
| `web/auth/db.py` | Add `user_has_password(conn, user_id)` helper and `verify_user_password(conn, user_id, password)` helper |
| `web/auth/main.py` | Enforce the check in the endpoint |
| `tests/test_auth.py` | Add a test class `TestSetPasswordCurrentCheck` |
| `web/frontend/portal/index.html` | Add a "Current password" field to the Set Password form, only visible when the user already has a password set |

### Part A — Update the Pydantic Model

**File**: `web/auth/models.py`

**Current Code** (line 34-35):

```python
class SetPasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=200)
```

**New Code**:

```python
class SetPasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=200)
    current_password: str | None = Field(default=None, max_length=200)
```

### Part B — Add DB Helpers

**File**: `web/auth/db.py`

Add the following two functions immediately after `authenticate_user` (after line 369). Do NOT modify `authenticate_user` itself:

```python
def user_has_password(conn: sqlite3.Connection, user_id: str) -> bool:
    """Return True if *user_id* has a password_hash set (i.e. not NULL)."""
    row = conn.execute(
        "SELECT password_hash FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if not row:
        return False
    return bool(row["password_hash"])


def verify_user_password(
    conn: sqlite3.Connection, user_id: str, password: str
) -> bool:
    """Verify *password* against the stored hash for *user_id*.

    Returns False if the user does not exist, has no password set,
    or the password does not match.
    """
    row = conn.execute(
        "SELECT password_hash FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if not row or not row["password_hash"]:
        return False
    return _verify_password(password, row["password_hash"])
```

### Part C — Enforce the Check in the Endpoint

**File**: `web/auth/main.py`

**Current Code** (lines 186-194):

```python
@app.post("/auth/set-password")
def set_password(
    body: SetPasswordRequest,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    db.set_user_password(conn, user["id"], body.password)
    log.info("Password set: user_id=%s", user["id"])
    return SuccessResponse()
```

**New Code**:

```python
@app.post("/auth/set-password")
def set_password(
    body: SetPasswordRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    # If a password already exists, the caller must prove they know it.
    # If no password exists (first-time setup after invite claim), allow
    # the set without a check — the invite-claim session is already proof
    # of identity.
    if db.user_has_password(conn, user["id"]):
        if not body.current_password:
            log.warning(
                "Set-password without current_password: user_id=%s ip=%s",
                user["id"],
                request.client.host if request.client else "unknown",
            )
            raise HTTPException(
                status_code=400, detail="Current password is required"
            )
        if not db.verify_user_password(conn, user["id"], body.current_password):
            log.warning(
                "Set-password with wrong current_password: user_id=%s ip=%s",
                user["id"],
                request.client.host if request.client else "unknown",
            )
            raise HTTPException(
                status_code=401, detail="Current password is incorrect"
            )

    db.set_user_password(conn, user["id"], body.password)
    log.info("Password changed: user_id=%s", user["id"])
    return SuccessResponse()
```

### Part D — Update the Portal UI

**File**: `web/frontend/portal/index.html`

The existing "Set Password" form in `index.html` (around lines 54-74) submits only a new password. It needs:

1. A new field `currentPassword` in the Alpine.js data object (alongside `newPassword`).
2. A boolean `hasPassword` exposed from `/auth/me` — OR a separate lightweight fetch. To avoid changing the `MeResponse` shape, add a conditional password input that the user fills in only if their previous set attempt returned a 400/401 error. That is: the UI attempts the call without `current_password` first; if the server responds with `Current password is required`, the UI reveals a current-password field and retries.

This is a deliberately minimal UX change — the alternative (adding `has_password` to `/auth/me`) pollutes the response shape used elsewhere. Follow this rule: keep state on the server, not in the `/auth/me` payload.

**Change the data object** (around line 78):

```javascript
function portalApp() {
    return {
        loading: true,
        user: { display_name: '', is_admin: false, apps: [] },
        showSettings: false,
        newPassword: '',
        currentPassword: '',
        needsCurrent: false,
        pwLoading: false,
        pwError: '',
        pwSuccess: false,
        // ...existing methods...
    };
}
```

**Replace the `setPassword` method** with:

```javascript
async setPassword() {
    this.pwError = '';
    this.pwSuccess = false;
    this.pwLoading = true;
    const body = { password: this.newPassword };
    if (this.needsCurrent) body.current_password = this.currentPassword;
    try {
        const res = await fetch('/auth/set-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(body),
        });
        if (res.ok) {
            this.pwSuccess = true;
            this.newPassword = '';
            this.currentPassword = '';
            this.needsCurrent = false;
            return;
        }
        const data = await res.json();
        if (res.status === 400 && (data.detail || '').includes('Current password')) {
            // Server told us a current password is required; reveal the field.
            this.needsCurrent = true;
            this.pwError = 'Enter your current password to change it.';
        } else if (res.status === 401) {
            this.pwError = 'Current password is incorrect.';
        } else {
            this.pwError = data.detail || 'Failed to set password';
        }
    } catch {
        this.pwError = 'Network error — try again';
    } finally {
        this.pwLoading = false;
    }
},
```

**Add the current-password input** inside the Settings panel, above the existing new-password input (around line 61):

```html
<input type="password" x-show="needsCurrent" x-model="currentPassword"
    placeholder="Current password" autocomplete="current-password"
    class="w-full border border-gray-300 rounded-lg px-4 py-2.5 text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
    :disabled="pwLoading" />
```

### Why This Works

1. **Server-side enforcement is the only real control.** The UI change is a convenience — the security depends on the FastAPI endpoint refusing the request. A malicious client that bypasses the UI still hits the check.
2. **Opt-in current-password via retry** avoids adding a new field to `/auth/me` that would need to stay in sync forever. The server owns the "does this user have a password" state; the client learns the answer only when it tries to change the password. This keeps `MeResponse` minimal and avoids a second round-trip for users who never visit the Settings panel.
3. **Distinct HTTP status codes** (`400` vs `401`) let the client differentiate "you forgot to send the field" from "you sent the wrong value." Without the distinction, a retry-based UI would conflate missing input with a typo.
4. **Both failure paths are logged with IP and user_id.** If an attacker hammers the endpoint trying to guess a current password, the access log will show the pattern. This is not rate-limiting — that's a separate Tier 3 item — but it makes the attack observable.
5. **First-time setup is intentionally unchecked.** An invite-claim session is the only identity proof available before a password exists. Requiring "current password" on first-time setup would be impossible to satisfy.

### Tradeoff

- **A user who genuinely forgets their current password is locked out of the password form.** Admin must kill their session and issue a fresh invite. This is the correct behavior — the alternative is no protection.

### Consequences

- One existing test (`TestPasswordAuth::test_change_password` in `test_auth.py:534`) will break because it sets a password, then sets another without providing `current_password`. Update that test to include `current_password` in the second call, as part of this fix.

### Acceptance Tests

Add a new class `TestSetPasswordCurrentCheck` to `tests/test_auth.py`:

```python
class TestSetPasswordCurrentCheck:
    def test_first_time_set_does_not_require_current(self):
        """A user who has never set a password can set one without proof."""
        admin = _admin_client()
        code = _make_invite(admin, "FirstTimerSetPw")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        r = c.post("/auth/set-password", json={"password": "FirstPass123"})
        assert r.status_code == 200

    def test_change_requires_current(self):
        """Once a password is set, changing it without current_password fails."""
        admin = _admin_client()
        code = _make_invite(admin, "ChangeRequiresCurrent")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "FirstPass123"})
        r = c.post("/auth/set-password", json={"password": "NewPass456"})
        assert r.status_code == 400
        assert "Current password" in r.json()["detail"]

    def test_change_with_wrong_current_rejected(self):
        admin = _admin_client()
        code = _make_invite(admin, "WrongCurrentPw")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "FirstPass123"})
        r = c.post(
            "/auth/set-password",
            json={"password": "NewPass456", "current_password": "WrongOne!"},
        )
        assert r.status_code == 401

    def test_change_with_correct_current_succeeds(self):
        admin = _admin_client()
        code = _make_invite(admin, "CorrectCurrentPw")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "FirstPass123"})
        r = c.post(
            "/auth/set-password",
            json={"password": "NewPass456", "current_password": "FirstPass123"},
        )
        assert r.status_code == 200

    def test_old_password_no_longer_works_after_change(self):
        """After a successful change, the old password must fail at /auth/signin."""
        admin = _admin_client()
        code = _make_invite(admin, "OldPwDies")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        c.post("/auth/set-password", json={"password": "FirstPass123"})
        c.post(
            "/auth/set-password",
            json={"password": "NewPass456", "current_password": "FirstPass123"},
        )
        c2 = TestClient(app, cookies={})
        r = c2.post(
            "/auth/signin",
            json={"display_name": "OldPwDies", "password": "FirstPass123"},
        )
        assert r.status_code == 401
```

Also update `TestPasswordAuth::test_change_password` to include `current_password` in its second call.

---

## Fix 3B — Enforce Unique Display Names (Case-Insensitive)

### Problem

The `users.display_name` column has no uniqueness constraint (`web/auth/db.py:38-46`). Login matches names case-insensitively (`authenticate_user` at line 357, `WHERE display_name = ? COLLATE NOCASE`). The combination allows:

1. **Duplicate account creation.** An admin generating two invites for "Mike R." creates two separate user rows. Both can claim, both coexist.
2. **Password-login ambiguity.** `authenticate_user` iterates through every matching user and returns the first one whose password matches. If two users named "Mike R." both set passwords, whoever signs in gets whichever row happens to come first — which depends on insertion order and is not guaranteed stable across SQLite versions.
3. **Audit log confusion.** `log.info("Password login: user_id=%s name=%s", ...)` shows the name but the two Mikes are distinguishable only by UUID. If one turns out to be an attacker, you cannot answer "which Mike logged in when" without manually correlating IDs.

The fix is to enforce uniqueness at the schema level, with a migration that handles the case where duplicates already exist.

### Files to Edit

| File | Change |
|---|---|
| `web/auth/db.py` | Add `_users_display_name_unique_index` migration; reject duplicate names in `create_user` |
| `web/auth/main.py` | Reject duplicate names in `create_invite` (prevents the duplicate from ever being generated) |
| `tests/test_auth.py` | Add `TestDisplayNameUniqueness` |

### Part A — The Migration (Production-Safe)

**File**: `web/auth/db.py`

Inside `_run_migrations` (after the existing `password_hash` migration, around line 140), append:

```python
# Migration 2: Enforce case-insensitive uniqueness on users.display_name.
# Uses a unique index rather than a column constraint because (a) SQLite
# does not support ALTER TABLE ADD CONSTRAINT, and (b) an index-based
# constraint can be dropped and recreated without rebuilding the table.
if not _index_exists(conn, "ux_users_display_name_nocase"):
    # Before creating the unique index, check for existing duplicates.
    # If duplicates exist, fail loudly — we refuse to guess which row to keep.
    dupes = conn.execute(
        "SELECT LOWER(display_name) AS n, COUNT(*) AS c FROM users "
        "GROUP BY n HAVING c > 1"
    ).fetchall()
    if dupes:
        names = ", ".join(f"{d['n']} (x{d['c']})" for d in dupes)
        raise RuntimeError(
            f"Cannot enforce unique display_name: duplicates exist: {names}. "
            f"Resolve manually before redeploying."
        )
    conn.execute(
        "CREATE UNIQUE INDEX ux_users_display_name_nocase "
        "ON users(display_name COLLATE NOCASE)"
    )
    log.info("Migration: created unique index on users(display_name COLLATE NOCASE)")
```

Add the helper `_index_exists` alongside `_column_exists`:

```python
def _index_exists(conn: sqlite3.Connection, index_name: str) -> bool:
    """Check if an index exists by name."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    ).fetchone()
    return row is not None
```

### Part B — Reject Duplicates in create_invite

**File**: `web/auth/main.py`

Inside `create_invite` (around line 328), after the existing validations and before calling `db.create_invite`, add a uniqueness check against existing *users*:

```python
    # Reject an invite whose display_name would duplicate an existing user.
    # Case-insensitive match — same rule as the DB's unique index.
    existing = conn.execute(
        "SELECT 1 FROM users WHERE display_name = ? COLLATE NOCASE LIMIT 1",
        (body.display_name.strip(),),
    ).fetchone()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"A user named '{body.display_name.strip()}' already exists",
        )
```

Also reject an invite whose name duplicates a *pending* invite (another admin might have already created one for the same person):

```python
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
```

### Part C — Handling Existing Duplicates (if any)

If preflight check #3 returned duplicates, they must be resolved *before* the migration will succeed. The agent MUST NOT resolve duplicates autonomously — this requires human judgment. Halt and report:

- List the duplicate rows with `sqlite3 /opt/tools/data/auth.db "SELECT id, display_name, created_at, is_admin FROM users WHERE LOWER(display_name) IN (SELECT LOWER(display_name) FROM users GROUP BY LOWER(display_name) HAVING COUNT(*) > 1);"`
- Ask the user to decide: keep which row, rename which row, or deactivate one.
- Only proceed with the deploy after the user has confirmed the resolution.

### Why This Works

1. **The index is the enforcement, not the application code.** App-level checks can be bypassed by bugs, race conditions, or direct DB access. A `UNIQUE INDEX ... COLLATE NOCASE` is the only way SQLite can guarantee uniqueness at the storage layer.
2. **The `COLLATE NOCASE` on the index matches the collation already used by `authenticate_user`.** This is critical — if the index used default (binary) collation but login used NOCASE, you could have "mike" and "MIKE" as unique rows yet both matching the same login attempt. The two must agree.
3. **Rejecting duplicates in `create_invite` prevents the problem at the UX layer.** An admin creating a duplicate invite gets an immediate 400 with a clear message instead of waiting until the invite is claimed and the DB insert fails with an opaque constraint error.
4. **Rejecting pending invites protects against two admins racing to onboard the same person.** Without this, both invites succeed, then whichever is claimed first wins and the other becomes a latent broken invite.
5. **Refusing to run the migration when duplicates exist is the safer default.** The alternative (auto-renaming or auto-merging) would silently mutate user identity. Forcing a human decision preserves the audit trail.

### Tradeoff

- **Admins lose the ability to have two people named "Mike R." as distinct users.** This is the intended outcome — for this tool's scale (a single company), name uniqueness is a reasonable constraint. If it becomes a problem, the mitigation is "Mike R.", "Mike R. (PM)", "Mike R. 2", etc. — the same thing any address book requires.

### Consequences

- On first deploy to production, the migration will raise `RuntimeError` if duplicates exist, which will crash the service on startup. This is the desired loud failure — it cannot be committed to without the operator acknowledging the duplicates. The preflight in step 3 of this IR is meant to surface this before it happens at deploy time.

### Acceptance Tests

Add a new class `TestDisplayNameUniqueness` to `tests/test_auth.py`:

```python
class TestDisplayNameUniqueness:
    def test_index_exists(self):
        with db.connect() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='ux_users_display_name_nocase'"
            ).fetchone()
            assert row is not None

    def test_create_invite_rejects_duplicate_user_name(self):
        admin = _admin_client()
        _make_invite(admin, "UniqueName")
        # Claim the first invite so a user row exists
        code = _make_invite(admin, "UniqueName2")  # different name: should work
        assert code
        # Now try to create an invite with a duplicate of an EXISTING user
        # First, claim UniqueName to make it an actual user
        code1 = _make_invite(admin, "ActualUser1")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code1})
        # Now attempting to invite another "ActualUser1" should fail
        r = admin.post(
            "/admin/invites",
            json={"display_name": "ActualUser1", "app_permissions": ["swppp"]},
        )
        assert r.status_code == 400
        assert "already exists" in r.json()["detail"].lower()

    def test_create_invite_rejects_case_insensitive_duplicate(self):
        admin = _admin_client()
        code = _make_invite(admin, "CaseSensitive1")
        c = TestClient(app, cookies={})
        c.post("/auth/claim", json={"code": code})
        # Different case, same name
        r = admin.post(
            "/admin/invites",
            json={"display_name": "CASESENSITIVE1", "app_permissions": ["swppp"]},
        )
        assert r.status_code == 400

    def test_create_invite_rejects_duplicate_pending_invite(self):
        admin = _admin_client()
        _make_invite(admin, "PendingDupe")
        r = admin.post(
            "/admin/invites",
            json={"display_name": "PendingDupe", "app_permissions": ["swppp"]},
        )
        assert r.status_code == 400
        assert "pending" in r.json()["detail"].lower()

    def test_db_index_rejects_direct_duplicate_insert(self):
        """Even direct DB inserts must be rejected by the unique index."""
        import sqlite3 as sq
        with db.connect() as conn:
            db.create_user(conn, "DirectDupe")
            with pytest.raises(sq.IntegrityError):
                db.create_user(conn, "DirectDupe")
```

---

## Fix 3C — Validate Session Name Characters

### Problem

`_validate_session_name` at `web/swppp_api/main.py:94-99` checks only that `name` is at most 200 characters long. The name is then:

1. **Passed to `NamedTemporaryFile(prefix=f"session_{name}_")`** at line 347. A name containing characters illegal in filenames on the target OS (on Windows: `<>:"|?*`, or any control character; on Linux: NUL) will raise `OSError`, which is NOT caught and returns a 500.
2. **Passed to `FileResponse(filename=f"{name}.json")`** at line 334. This becomes the `Content-Disposition: attachment; filename=...` header. Starlette performs some sanitization, but relying on framework-level header sanitization for arbitrary user input is not a defensible posture — it's implicit trust of a downstream library.
3. **Used directly as a SQLite primary-key component.** Any string is valid here since SQLite uses parameterized queries, but newlines, tabs, and unicode control characters make these session names un-referenceable in error messages, logs, and UI.

This is not a high-severity vulnerability — the attacker must be authenticated and can only corrupt their own session data — but it's shipped input validation you control by name only. Explicit is better than implicit.

### File to Edit

`web/swppp_api/main.py`

### Current Code (lines 94-99)

```python
def _validate_session_name(name: str) -> None:
    if len(name) > MAX_SESSION_NAME:
        raise HTTPException(
            status_code=400,
            detail=f"Session name too long (max {MAX_SESSION_NAME} chars)",
        )
```

### New Code

```python
_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9 _\-.]{1,200}$")


def _validate_session_name(name: str) -> None:
    if not name:
        raise HTTPException(status_code=400, detail="Session name is required")
    if len(name) > MAX_SESSION_NAME:
        raise HTTPException(
            status_code=400,
            detail=f"Session name too long (max {MAX_SESSION_NAME} chars)",
        )
    if not _SESSION_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=(
                "Session name may contain only letters, numbers, spaces, "
                "underscores, hyphens, and periods"
            ),
        )
```

Add `import re` to the import block at the top of the file if not already present.

### Why This Works

1. **Whitelist, not blacklist.** Listing allowed characters is safer than listing forbidden ones because the set of allowed characters is small and finite, while the set of problematic characters varies by OS, encoding, and consumer (filesystem, HTTP header, SQL LIKE clause, JSON key). The whitelist covers every sensible session name while excluding every known-problematic character.
2. **Validation happens in one place.** Every endpoint that takes `name` already calls `_validate_session_name`; this keeps that contract. Do not duplicate the regex into individual handlers.
3. **Empty names are now rejected.** Previously, an empty path parameter (matched by FastAPI from e.g. `/swppp/api/sessions//export`) would silently proceed. FastAPI actually returns 404 for empty path params in practice, but the explicit check documents the intent.
4. **Periods are allowed to support names like `project_2026-04.session`.** The closing `.json` suffix is appended by the export endpoint, not supplied by the user, so a user-supplied period does not create double-extension confusion.

### Tradeoff

- **Users can no longer use emoji, apostrophes, or commas in session names.** For a compliance tool used by construction inspectors, this is an acceptable constraint. If a user complains, expand the regex — but do so knowing which downstream consumer you are loosening for.

### Consequences

- Any existing session in production with a name outside the whitelist will become inaccessible via these endpoints. The preflight should include:
  ```
  sqlite3 /opt/tools/data/swppp_sessions.db \
    "SELECT user_id, name FROM saved_sessions WHERE name GLOB '*[^A-Za-z0-9 _.-]*';"
  ```
  If rows are returned, decide with the user whether to rename them or accept the inaccessibility.

### Acceptance Tests

Add to the existing `test_swppp_api.py` (or create a new class if appropriate):

```python
class TestSessionNameValidation:
    def test_reject_empty_name(self, auth_client):
        # Note: FastAPI may reject empty path segments before reaching the handler;
        # this is belt-and-suspenders.
        r = auth_client.get("/swppp/api/sessions/%20/export")
        # Either 400 from our validator or 404 from FastAPI is acceptable
        assert r.status_code in (400, 404)

    def test_reject_illegal_characters(self, auth_client):
        # First save a valid session so the endpoint has data to work with
        auth_client.put(
            "/swppp/api/sessions/valid_name",
            json={"foo": "bar"},
        )
        # Then try to reference an invalid name
        r = auth_client.get("/swppp/api/sessions/has%2Fslash/export")
        assert r.status_code in (400, 404)
        r = auth_client.put(
            "/swppp/api/sessions/has%3Csomething%3E",
            json={"foo": "bar"},
        )
        assert r.status_code == 400

    def test_accept_typical_names(self, auth_client):
        for name in ["project_2026-04", "Site.A.1", "My Session 7"]:
            r = auth_client.put(
                f"/swppp/api/sessions/{name}",
                json={"foo": "bar"},
            )
            assert r.status_code == 200, f"rejected valid name: {name}"
```

> **Note**: `auth_client` is a fixture already present in `test_swppp_api.py`. If it does not exist, model after the `_admin_client()` pattern in `test_auth.py`.

---

## Fix 3D — Move Misplaced Test Methods

### Problem

Two test methods exist inside `class TestSharedCsrfMiddleware` in `tests/test_auth.py` that have nothing to do with CSRF:

| Method | Current line | Tests |
|---|---|---|
| `test_promote_to_admin` | 813 | Admin-granted `is_admin` flag flip |
| `test_deactivate_already_deactivated` | 826 | Admin-deactivated user handling |

Both pass, which is why they slipped through the Tier 2 review. The structural cost: if someone reads `TestSharedCsrfMiddleware` to audit CSRF coverage, two of seven methods are misleading. This is the Tier 2 edit artifact your post-mortem principles say to surface openly.

### File to Edit

`tests/test_auth.py`

### Action

1. **Cut** the two method definitions (lines 813-836 as they currently stand) from inside `class TestSharedCsrfMiddleware`.
2. **Paste** them into `class TestAdminUsersExtended` (at `test_auth.py:636`). This class already has admin-user-related extended tests; these two fit there.
3. **Do not modify the test bodies.** They test real behavior and pass as-is. Only the class membership is wrong.

### Why This Works

1. **Tests document intent.** A test's class is part of its documentation. A CSRF test inside a CSRF test class, read six months later, means "this is what CSRF is supposed to do." An admin test inside the same class is a lie by filing error.
2. **pytest does not care about class membership for correctness.** The test count stays at 172, the pass count stays at 172. This is purely a hygiene fix with zero runtime behavior change.
3. **Doing it now is cheap.** This edit scrambled class boundaries once already during the Tier 2 insertion; fixing it now reduces the surface for the next scrambling.

### Tradeoff

None. This is pure cleanup.

### Consequences

None. The test suite is unchanged in count and coverage.

### Acceptance Test

Run the full suite and confirm:

```
.venv\Scripts\Activate.ps1; $env:TOOLS_DEV_MODE="1"; pytest -q
```

Expected: `172 passed`.

Then, specifically verify membership:

```
pytest tests/test_auth.py::TestSharedCsrfMiddleware --collect-only -q
pytest tests/test_auth.py::TestAdminUsersExtended --collect-only -q
```

`TestSharedCsrfMiddleware` should show exactly 5 methods (all starting with `test_csrf_`).
`TestAdminUsersExtended` should show its original 2 methods plus the 2 moved-in methods (4 total).

---

## Deployment Sequence

After all four fixes are committed and tests pass locally:

1. **Run preflight #3 (duplicate-name check).** If duplicates exist, STOP and resolve with the user before continuing.

2. **SSH into the server**:
   ```
   ssh -i ~/.ssh/swppp-vps-deploy root@{server_ip}
   ```

3. **Backup databases** (redundant with preflight, but do it again right before deploy):
   ```
   cp /opt/tools/data/auth.db /opt/tools/backups/auth_pre_tier3_$(date +%Y%m%d_%H%M%S).db
   cp /opt/tools/data/swppp_sessions.db /opt/tools/backups/swppp_sessions_pre_tier3_$(date +%Y%m%d_%H%M%S).db
   ```

4. **Pull the latest code**:
   ```
   cd /opt/tools/repo
   git status --short                # must be clean
   git pull --ff-only
   ```

5. **Restart both services** (this triggers `init_db()` → runs the new migration):
   ```
   systemctl restart tools-auth tools-swppp
   ```

6. **Verify services are healthy**:
   ```
   systemctl status tools-auth tools-swppp
   journalctl -u tools-auth --since "2 min ago" --no-pager
   ```
   Look for: `Migration: created unique index on users(display_name COLLATE NOCASE)` (only if this is a first-time index creation; on re-deploy it will be absent — that's expected).

7. **Verify the unique index is in place**:
   ```
   sqlite3 /opt/tools/data/auth.db \
     "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND tbl_name='users';"
   ```
   Confirm `ux_users_display_name_nocase` appears.

8. **Smoke test from browser**:
   - Log in as an existing user. Visit `/` → Settings → attempt to change password without current password → expect error "Current password is required."
   - Retry with current password → expect success.
   - Log out and log back in with the new password. Old password should fail.
   - As admin, attempt to generate an invite with the same display name as an existing user → expect error "A user named '...' already exists."
   - Save a SWPPP session with a name containing a slash or angle bracket → expect 400.

### Rollback Plan

**Tier 3 introduces a destructive-ish migration** (the unique index). Rolling back requires dropping the index before reverting code, otherwise the downgraded code will work fine but the index will remain. The migration is additive (an index, not a column drop), so the old code runs fine with or without the index — this simplifies rollback.

**If the deploy fails:**

1. Revert the code:
   ```
   cd /opt/tools/repo
   git log --oneline -5       # find the previous commit
   git checkout {prev_hash}
   systemctl restart tools-auth tools-swppp
   ```
2. The unique index can stay in place — it will not affect the prior code. If you specifically need to remove it:
   ```
   sqlite3 /opt/tools/data/auth.db "DROP INDEX IF EXISTS ux_users_display_name_nocase;"
   ```
3. If a duplicate-name situation emerged between deploy and rollback and caused a write to fail, restore the database backup:
   ```
   systemctl stop tools-auth tools-swppp
   cp /opt/tools/backups/auth_pre_tier3_{timestamp}.db /opt/tools/data/auth.db
   systemctl start tools-auth tools-swppp
   ```

---

## Run All Tests

Before committing, run the full suite:

```powershell
cd C:\Projects\swpppautofill_windows
.venv\Scripts\Activate.ps1; $env:TOOLS_DEV_MODE="1"; pytest -q
```

All existing tests must still pass. New test classes must also pass.

Expected final count: `172 + N passed`, where N is the number of tests added by this IR (approximately 13: 5 in `TestSetPasswordCurrentCheck`, 5 in `TestDisplayNameUniqueness`, 3 in `TestSessionNameValidation`).

If the count is not `~185 passed`, investigate before proceeding.

Paste the full `pytest -q` output into the review message. Do not paraphrase "all pass" — paste the tail.

---

## Files Modified by This IR

| File | Change | Fix |
|---|---|---|
| `web/auth/models.py` | Add `current_password` field to `SetPasswordRequest` | 3A |
| `web/auth/db.py` | Add `user_has_password`, `verify_user_password`, `_index_exists`, unique-index migration | 3A, 3B |
| `web/auth/main.py` | Enforce current-password check; reject duplicate-name invites | 3A, 3B |
| `web/frontend/portal/index.html` | UI for current-password retry flow | 3A |
| `web/swppp_api/main.py` | Character whitelist in `_validate_session_name` | 3C |
| `tests/test_auth.py` | `TestSetPasswordCurrentCheck`, `TestDisplayNameUniqueness`; move misplaced methods | 3A, 3B, 3D |
| `tests/test_swppp_api.py` | `TestSessionNameValidation` | 3C |

## Files NOT Modified by This IR

| File | Reason |
|---|---|
| `web/middleware.py` | No changes — CSRF middleware untouched |
| `web/auth/dependencies.py` | No changes — dependency factories untouched |
| `web/scripts/*` | No changes — deploy and nginx configs untouched |
| `app/core/*` | No changes — desktop/core logic untouched |

---

## Required Reporting

After each fix is implemented, report to the user the following, exactly:

1. The fix number (3A / 3B / 3C / 3D).
2. Every file modified, with line-count delta.
3. Full `pytest -q` tail (last 20 lines).
4. For fixes with new tests, the name of each new test class and its test count.
5. Any deviation from this IR and why.

Do not summarize as "done" — show evidence. If a step fails, stop and report the failure.

---

## Follow-Up (Tier 4 — Separate IR)

Deferred because they require architectural discussion before implementation:

- **4A — Mesonet unit contract.** The `RainDay.rainfall_inches` attribute is populated from two code paths (`fetch_rainfall` converts mm→inches, `parse_rainfall_csv` assumes the input is already in inches). There is no runtime check that these agree. A single-constructor pattern with explicit unit handling would close the gap, but the design choice affects both paths non-trivially.
- **4B — Partial-success reporting in `/swppp/api/generate`.** `generate_batch` silently skips PDFs that fail to write (`fill.py:239-241`), and the API returns 200 with a ZIP containing however many succeeded. The user has no indication that their batch is incomplete. The fix requires deciding: return a partial-success status + a manifest of skipped dates, or fail the entire batch on any single failure. Both have costs.

Also carried forward from the original Tier 3 plan (not addressed here):

- **Rate limiting on auth endpoints** (originally Tier 3A).
- **CSRF rejection when Origin is absent** (originally Tier 3B).
- **SQLite write-contention evaluation** (originally Tier 3C).

---

*End of IR. Implement in order 3D → 3C → 3A → 3B (simplest to most impactful). Do not skip the preflight duplicate-name check before 3B.*
