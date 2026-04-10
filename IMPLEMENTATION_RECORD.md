# SWPPP AutoFill — Web Migration Implementation Record

**Document purpose:** Precise phase-by-phase record of every architectural decision, implementation detail, issue encountered, and fix applied during the migration from a Windows desktop app to a multi-tenant web application. Written for consumption by a planning and review agent.

**Date range:** April 2026
**Source specification:** `execution_plan.md` (1,800 lines, 11 sections, 4 phases + appendix)
**Starting state:** 39 passing tests, desktop-only (Tkinter GUI + Typer CLI), `app/core/` business logic layer
**Final state:** 147 passing tests, full web stack (2 FastAPI services, 4 HTML SPAs, deployment automation)

---

## Table of Contents

1. [Pre-Migration Baseline](#1-pre-migration-baseline)
2. [Phase 1: Auth System + Portal](#2-phase-1-auth-system--portal)
3. [Phase 2: SWPPP API Backend](#3-phase-2-swppp-api-backend)
4. [Phase 3: SWPPP Frontend SPA](#4-phase-3-swppp-frontend-spa)
5. [Phase 4: Server Deployment](#5-phase-4-server-deployment)
6. [Phase 5: QA Lockdown](#6-phase-5-qa-lockdown)
7. [Final Codebase Inventory](#7-final-codebase-inventory)
8. [Test Suite Inventory](#8-test-suite-inventory)
9. [Known Limitations & Future Work](#9-known-limitations--future-work)

---

## 1. Pre-Migration Baseline

### Existing Desktop App

| Component | Location | Purpose |
|-----------|----------|---------|
| `app/core/fill.py` | Business logic | `generate_batch()` — writes weekly inspection PDFs from AcroForm template |
| `app/core/model.py` | Data models | Pydantic models: `TemplateMap`, `ProjectInfo`, `RunOptions`, `CheckboxItem` |
| `app/core/dates.py` | Date math | `weekly_dates()` generator — yields inspection date pairs |
| `app/core/config_manager.py` | Config | `load_mapping()` — parses `odot_mapping.yaml` into `TemplateMap` |
| `app/core/pdf_fields.py` | PDF mapping | `populate_checkbox_targets()` — runtime checkbox field detection from template button layout |
| `app/core/mesonet.py` | Rain data | `fetch_rainfall()` (HTTP client + CSV parser), `parse_rainfall_csv()`, `filter_rain_events()`, `RainDay`/`FetchResult` dataclasses |
| `app/core/mesonet_stations.py` | Stations | `STATIONS` dict (~121 entries), `station_display_list()`, `parse_station_code()` |
| `app/core/rain_fill.py` | Rain PDFs | `generate_rain_batch()` — rain event variant of `generate_batch()` |
| `app/core/session.py` | Persistence | File-based JSON session save/load to `~/.swppp_autofill/` |
| `app/core/odot_mapping.yaml` | Config | 8 text fields, 7 checkbox groups, ~38 questions |
| `assets/template.pdf` | Template | Real AcroForm PDF with named text fields and unnamed button fields (`undefined`, `undefined_2`, …) |
| `app/ui_gui/main.py` | GUI | Tkinter desktop interface |
| `app/ui_cli/main.py` | CLI | Typer command-line interface |

### Pre-Migration Test Suite (39 tests)

| File | Tests | Coverage |
|------|-------|---------|
| `test_fill.py` | 3 | `generate_batch()`, ZIP bundling, empty-range handling |
| `test_model.py` | 3 | Pydantic model validation for `ProjectInfo`, `RunOptions` |
| `test_checkbox_mapping.py` | 4 | Checkbox target derivation from template button layout |
| `test_mesonet.py` | 13 | CSV parsing, rainfall calculation, DST offset, missing data handling |
| `test_rain_fill.py` | 7 | Rain PDF generation, date filtering, inspection type labels |
| `test_session.py` | 13 | File-based session save/load round-trip, schema version |
| `test_template_integration.py` | 1 | Real template PDF field fill verification |

### Design Constraint: Core Left Untouched

The execution plan mandated: *"The existing `app/core/` Python package is imported directly by the SWPPP FastAPI service. No modifications to core logic."* This was honored throughout all 5 phases. The web services wrap `app/core/` via lazy imports inside endpoint function bodies.

---

## 2. Phase 1: Auth System + Portal

### Scope

Build the shared authentication infrastructure: SQLite database, FastAPI auth service, invite-code-based user management, cookie-based sessions, admin panel, and portal frontend.

### Files Created

| File | Lines | Purpose |
|------|-------|---------|
| `web/auth/main.py` | 322 | FastAPI app — 17 endpoints + 2 dev-mode HTML routes |
| `web/auth/db.py` | 320 | SQLite operations — 25 functions covering all 5 tables |
| `web/auth/models.py` | 84 | 18 Pydantic request/response models |
| `web/auth/dependencies.py` | 34 | 3 auth dependency functions |
| `web/auth/__init__.py` | 0 | Package marker |
| `web/frontend/portal/login.html` | 78 | Code entry page (Alpine.js + Tailwind) |
| `web/frontend/portal/index.html` | 64 | App launcher portal (Alpine.js + Tailwind) |
| `web/frontend/portal/admin.html` | 297 | Full admin panel (Alpine.js + Tailwind) |
| `web/scripts/init_admin.py` | 42 | Bootstrap script — seeds DB, creates first admin invite |
| `web/__init__.py` | 0 | Package marker |
| `tests/test_auth.py` | (initial) | Auth endpoint test suite |

### Database Schema (auth.db — 5 tables)

```
apps            — id (PK), name, description, route_prefix (UNIQUE), is_active, created_at
users           — id (PK, UUID4), display_name, is_active, is_admin, created_at, last_seen_at
invite_codes    — id (PK, TOOLS-XXXX-XXXX), display_name, status, claimed_by (FK→users),
                  app_permissions (JSON), grant_admin, created_at, claimed_at
user_app_access — user_id + app_id (composite PK), granted_at
sessions        — token (PK, 64 hex chars), user_id (FK→users), device_label, created_at, last_seen_at
```

### Architectural Decisions

**Decision: Cookie-based sessions, no JWT.**
Rationale: Small user base (dozens, not thousands). Server-side session table allows instant revocation by admins. No need for stateless token infrastructure. Cookie flags: `HttpOnly`, `Secure` (disabled in dev), `SameSite=Lax`, `Max-Age=315360000` (10 years — sessions persist until admin revocation).

**Decision: Invite-code-only registration, no passwords.**
Rationale: Closed system for a small team. Admin generates `TOOLS-XXXX-XXXX` codes using a safe alphabet (`ABCDEFGHJKLMNPQRSTUVWXYZ23456789` — excludes O/0/I/1 to prevent confusion). User claims code → user record + session created atomically via `claim_invite_code()`.

**Decision: `TOOLS_DEV_MODE` environment variable controls dev/prod behavior.**
When `TOOLS_DEV_MODE=1`: FastAPI serves HTML files directly (no Nginx needed for local dev), cookies are not marked `Secure` (allows HTTP). When `TOOLS_DEV_MODE=0`: HTML is served by Nginx, cookies require HTTPS.

**Decision: SQLite with WAL journal mode.**
All database connections enable WAL (`PRAGMA journal_mode=WAL`) and foreign keys (`PRAGMA foreign_keys=ON`). WAL allows concurrent reads during writes — sufficient for the expected load.

**Decision: Dependency injection chain for auth.**
Three FastAPI dependencies form a layered chain:
1. `get_current_user(tools_session: str = Cookie(default=None))` — reads cookie, validates session, updates `last_seen_at`, returns user dict. Raises 401 on failure.
2. `require_admin(user = Depends(get_current_user))` — checks `is_admin`. Raises 403.
3. `require_app(app_id)` — factory function returning a dependency that checks `user_app_access`. Raises 403.

**Decision: Admin cannot self-deactivate.**
`update_user_endpoint()` compares `user_id` against the current user's ID. Returns 400 if they match with `is_active=False`. Prevents accidental lockout.

**Decision: Token prefix for session identification.**
Admin panel shows only first 8 characters of session tokens (`token[:8] + "...."`). Full 64-hex tokens never appear in API responses. Admin can kill sessions by prefix; the `delete_session_by_prefix()` function requires exactly 1 match (returns 400 otherwise).

### Auth Endpoint Inventory (17 + 2 dev routes)

| # | Method | Path | Auth Level | Function |
|---|--------|------|------------|----------|
| 1 | GET | `/auth/login` | Public | Serve login HTML |
| 2 | POST | `/auth/claim` | Public | Claim invite → create user + session |
| 3 | POST | `/auth/logout` | Public | Destroy session, clear cookie, 302 redirect |
| 4 | GET | `/auth/me` | Session | Current user info + app list |
| 5 | GET | `/admin/users` | Admin | List all users with apps |
| 6 | PATCH | `/admin/users/{user_id}` | Admin | Toggle is_active / is_admin |
| 7 | GET | `/admin/users/{user_id}/sessions` | Admin | List user's active sessions |
| 8 | DELETE | `/admin/users/{user_id}/sessions` | Admin | Kill all sessions for user |
| 9 | DELETE | `/admin/sessions/{token_prefix}` | Admin | Kill single session by prefix |
| 10 | POST | `/admin/invites` | Admin | Generate new invite code |
| 11 | GET | `/admin/invites` | Admin | List all invites |
| 12 | DELETE | `/admin/invites/{code_id}` | Admin | Revoke pending invite |
| 13 | POST | `/admin/users/{user_id}/apps` | Admin | Grant app access |
| 14 | DELETE | `/admin/users/{user_id}/apps/{app_id}` | Admin | Revoke app access |
| 15 | GET | `/admin/apps` | Admin | List registered apps |
| 16 | POST | `/admin/apps` | Admin | Register new app |
| 17 | PATCH | `/admin/apps/{app_id}` | Admin | Update app metadata/status |
| D1 | GET | `/` | Dev only | Serve portal index.html |
| D2 | GET | `/admin` | Dev only | Serve admin.html |

### Frontend Architecture (Portal)

All three HTML files use the same stack: **Tailwind CSS v3 (CDN)** + **Alpine.js v3 (CDN)**. Zero build step. Each file is a self-contained SPA with an Alpine `x-data` component.

**login.html:** Centered card. Auto-reads `?code=` query param and pre-fills + auto-submits. POST to `/auth/claim`, follow redirect on success.

**index.html:** Calls `GET /auth/me` on load. Renders app cards from `user.apps` array. Shows "Admin" link if `user.is_admin`. Logout calls `POST /auth/logout`.

**admin.html:** Three sections — Generate Invite (name + app checkboxes → code + copyable link), Pending Invites (table with cancel), Users (table with activate/deactivate, manage apps modal, view sessions modal). Uses a generic `api()` wrapper that detects 401/403 and redirects.

### Issues Encountered & Fixes

**Issue 1: `ModuleNotFoundError: No module named 'web'` when running pytest.**
Root cause: Python couldn't resolve the `web` package from the test runner's working directory.
Fix: Added `pythonpath = ["."]` to `pyproject.toml` under `[tool.pytest.ini_options]`. This adds the project root to `sys.path` so `web.auth.main` resolves correctly.

**Issue 2: `sqlite3.OperationalError: no such table: apps` in tests.**
Root cause: Test client creates the FastAPI app, but the lifespan event (which calls `init_db()`) wasn't being triggered by `TestClient` in its default configuration, or tests were running before DB initialization.
Fix: The `_admin_client()` helper explicitly calls `db.init_db()` and `db.seed_app()` before any test operations to ensure tables exist. Each test file creates its own temp directory via `tempfile.mkdtemp()` and sets `TOOLS_DATA_DIR` to it, giving full isolation.

**Issue 3: Invite code claim was case-sensitive.**
Root cause: Codes stored as uppercase, but user might type lowercase.
Fix: `claim_code()` endpoint strips whitespace and uppercases the input: `code = req.code.strip().upper()`.

### Test Coverage After Phase 1

12 test classes, 37 test methods in `test_auth.py` covering: bootstrap (table creation, seeding, code format), claim flow (valid/invalid/already-claimed/admin/case-insensitive), auth guards (401/403), `/auth/me`, logout, admin users (list/deactivate/self-protection), admin invites (create/list/revoke/validation), admin sessions (list/kill-all/kill-by-prefix), admin app access (grant/revoke), admin apps (list/create/validate/update).

---

## 3. Phase 2: SWPPP API Backend

### Scope

Build the SWPPP-specific FastAPI service with 12 endpoints wrapping the existing `app/core/` business logic. Separate SQLite database for session storage.

### Files Created

| File | Lines | Purpose |
|------|-------|---------|
| `web/swppp_api/main.py` | 346 | FastAPI app — 12 endpoints + 1 dev route |
| `web/swppp_api/db.py` | 98 | Session storage — 7 functions, 1 table |
| `web/swppp_api/models.py` | 64 | 14 Pydantic request/response models |
| `web/swppp_api/__init__.py` | 0 | Package marker |
| `tests/test_swppp_api.py` | (initial) | SWPPP API test suite |

### Database Schema (swppp_sessions.db — 1 table)

```
saved_sessions — user_id + name (composite PK), data (JSON TEXT), created_at, updated_at
```

### Architectural Decisions

**Decision: Separate SQLite database from auth.**
Rationale: `auth.db` is shared infrastructure (used by the auth service and potentially future apps). `swppp_sessions.db` is app-specific data. Separate files allow independent backup/restore and avoid lock contention.

**Decision: Lazy imports inside endpoint function bodies.**
All `app.core` imports happen inside the endpoint functions, not at module level:
```python
@app.post("/swppp/api/generate")
def generate_pdf(...):
    from app.core.config_manager import build_project_info, build_run_options, load_mapping
    from app.core.dates import weekly_dates
    from app.core.fill import bundle_outputs_zip, generate_batch
    ...
```
Rationale: The core modules are heavy (they load the PDF template, parse YAML, etc.). Lazy importing keeps app startup fast and avoids importing unused code for simple endpoints like `/stations`. **This decision had consequences in Phase 5** — it made mock patching harder in tests (see Phase 5 issues).

**Decision: Temp directory lifecycle with BackgroundTasks.**
The generate endpoint creates a temp directory via `tempfile.mkdtemp(prefix="swppp_gen_")`, generates all PDFs into it, creates a ZIP, serves it via `FileResponse`, then schedules cleanup via `BackgroundTasks.add_task(_cleanup_dir, tmp_dir)`. The cleanup runs after the response is sent. A server-side cron job also sweeps orphaned temp dirs older than 60 minutes as a safety net.

**Decision: PUT for session save (upsert semantics).**
`PUT /swppp/api/sessions/{name}` uses SQLite `INSERT ... ON CONFLICT(user_id, name) DO UPDATE` for atomic upsert. This means save is always idempotent — the client doesn't need to know if a session exists before saving.

**Decision: Import returns data without requiring save.**
`POST /swppp/api/sessions/import` accepts an optional `save` query parameter. When `save=false` (default), the file is parsed and the session data is returned directly — the frontend loads it into the form without persisting. When `save=true`, it's also written to the database. This allows "load into form" without creating a saved session.

**Decision: Session names derived from filename on import.**
If the uploaded JSON lacks a `session_name` key, the name is derived from the filename: `Path(file.filename or "imported").stem`. This handles the case where users import JSON files exported from the desktop app (which don't contain a `session_name` field).

### SWPPP Endpoint Inventory (12 + 1 dev route)

| # | Method | Path | Function | Purpose |
|---|--------|------|----------|---------|
| 1 | GET | `/swppp/api/form-schema` | `get_form_schema()` | Dynamic form structure from `odot_mapping.yaml` |
| 2 | GET | `/swppp/api/stations` | `get_stations()` | Mesonet station list (sorted by code) |
| 3 | POST | `/swppp/api/rain/fetch` | `rain_fetch()` | Fetch rainfall from Mesonet API |
| 4 | POST | `/swppp/api/rain/parse-csv` | `rain_parse_csv()` | Parse uploaded Mesonet CSV |
| 5 | GET | `/swppp/api/sessions` | `list_sessions()` | List user's saved sessions |
| 6 | GET | `/swppp/api/sessions/{name}` | `get_session()` | Load a saved session |
| 7 | PUT | `/swppp/api/sessions/{name}` | `save_session()` | Save/overwrite a session |
| 8 | DELETE | `/swppp/api/sessions/{name}` | `delete_session()` | Delete a session |
| 9 | GET | `/swppp/api/sessions/{name}/export` | `export_session()` | Download session as JSON file |
| 10 | POST | `/swppp/api/sessions/import` | `import_session()` | Upload + parse session JSON |
| 11 | POST | `/swppp/api/generate` | `generate_pdf()` | Full PDF generation → ZIP download |
| D1 | GET | `/swppp/` | `swppp_index()` | Dev-mode HTML serving |

### Generate Endpoint Pipeline (the most complex endpoint)

```
Request body (GenerateRequest)
  → load_mapping(MAPPING_YAML) → build_project_info() → build_run_options()
  → tempfile.mkdtemp()
  → weekly_dates(start, end) → generate_batch(template, project, options, dates, mapping, checkboxes, notes)
  → if rain_days: convert dicts → RainDay dataclasses → generate_rain_batch(...)
  → bundle_outputs_zip(all_paths, tmp_dir)
  → FileResponse(zip_path, media_type="application/zip")
  → BackgroundTasks: _cleanup_dir(tmp_dir)
```

### Issues Encountered & Fixes

**Issue 3: `weekly_dates()` raises `ValueError` for invalid date ranges.**
The core `weekly_dates()` function raises `ValueError` when the end date is before the first possible weekly inspection date. The execution plan specified returning an empty ZIP for this case, not an error.
Fix: Wrapped `weekly_dates()` call in try/except `ValueError` → returns empty list instead of propagating.

**Issue 4: Route ordering conflict — `/sessions/import` vs `/sessions/{name}`.**
FastAPI matches routes in declaration order. If `GET /sessions/{name}` was declared before `POST /sessions/import`, a POST to `/sessions/import` would match `{name}=import`.
Fix: Declared the `/sessions/import` route BEFORE the `/{name}` routes in the source file. This ensures the literal path takes priority over the path parameter.

### Test Coverage After Phase 2

9 test classes, 20 test methods in initial `test_swppp_api.py`: auth guards (401/403), form schema (field count, group structure, question count), stations (list size, item structure), session CRUD (list/save/get/delete/export/import), generate (valid ZIP, no-dates error, checkbox inclusion).

---

## 4. Phase 3: SWPPP Frontend SPA

### Scope

Build the single-page SWPPP application as one HTML file with Alpine.js and Tailwind CSS. Two-column layout: project fields + generator settings on the left, inspection checklist on the right.

### Files Created

| File | Lines | Purpose |
|------|-------|---------|
| `web/frontend/swppp/index.html` | 729 | Complete SWPPP SPA |

### Layout Architecture

```
┌─────────────────────────────────────────────────────────┐
│  SWPPP AutoFill    [Save][Load][Export][Import][Clear]   │  ← Toolbar
├────────────────────────┬────────────────────────────────┤
│  Project Fields        │  Inspection Checklist          │
│  (8 text inputs from   │  (7 groups, ~38 questions      │
│   /form-schema)        │   YES/NO/N/A toggles, all      │
│                        │   expanded, not collapsible)    │
│  Generator Settings    │                                │
│  (year, months 4×3,    │  Notes textarea per group      │
│   custom date toggle)  │  (if has_notes=true)           │
│                        │                                │
│  Rain Days             │                                │
│  (station dropdown,    │                                │
│   fetch/upload CSV,    │                                │
│   event list)          │                                │
├────────────────────────┴────────────────────────────────┤
│  [Generate PDFs]                                  sticky │  ← Bottom bar
└─────────────────────────────────────────────────────────┘
```

### Architectural Decisions

**Decision: Fully dynamic form rendering from API.**
The form is never hardcoded. On `init()`, the SPA calls `GET /swppp/api/form-schema` and builds all fields and checklist groups from the response. If a question is added to `odot_mapping.yaml`, it appears in the web form automatically without frontend changes.

**Decision: Three-state toggle buttons for checkboxes.**
Each checklist question has YES / NO / (optional) N/A buttons. Implementation: `toggleCheck(groupKey, questionText, value)` — clicking an already-selected button deselects it (returns to unanswered empty string). State stored in `checkboxStates[groupKey][questionText]` = `"YES"` | `"NO"` | `"N/A"` | `""`.

**Decision: Rain data staleness detection.**
After fetching rain data, if the user changes the station selection or the date range, a warning appears: "Rain data may be outdated — refetch to update". Implemented via `_checkRainStale()` which compares current station/dates against the values at fetch time.

**Decision: PDF download via Blob URL.**
The generate endpoint returns raw ZIP bytes. The SPA reads the response as a blob, creates a `URL.createObjectURL()`, programmatically creates an `<a>` element with `download` attribute, clicks it, then revokes the URL. This avoids navigation and works reliably across browsers.

**Decision: Shift+click on Save forces "Save As" dialog.**
Normal Save: if a session is already loaded, saves silently under the same name. Shift+click: always prompts for a new name. Implemented by checking `event.shiftKey` in `saveSession(event)`.

**Decision: Computed date range from generator settings.**
Two computed properties (`computedStart`, `computedEnd`) derive the actual date range from either custom date inputs or selected month checkboxes + year. The generate button is disabled when no valid range exists (`canGenerate` computed property).

### Page Load Sequence

```
1. HTML loads → Tailwind + Alpine initialize
2. init() fires:
   a. GET /auth/me          → if 401, redirect to /auth/login
   b. GET /swppp/api/form-schema  → build fields + checklist
   c. GET /swppp/api/stations     → populate station dropdown
3. All three requests fire in parallel via Promise.all
4. Loading skeleton replaced with form content
5. Form is ready — all fields empty, no session loaded
```

### Key JavaScript Functions

| Function | Purpose |
|----------|---------|
| `init()` | Parallel API calls, form initialization |
| `_initFormState()` | Builds empty state objects from schema |
| `toggleCheck(group, question, value)` | Three-state checkbox logic |
| `fetchRain()` | POST `/rain/fetch`, stores results, sets stale baseline |
| `uploadCsv()` | File picker → POST `/rain/parse-csv` |
| `generate()` | Collects all state → POST `/generate` → blob download |
| `saveSession()` / `confirmSave()` / `_doSave()` | Session persistence flow |
| `showLoadModal()` / `loadSession()` | Session list → load into form |
| `exportSession()` | Download session as JSON via blob |
| `importSession()` / `handleImport()` | File picker → parse → populate form |
| `clearForm()` | Reset all state to defaults |
| `_collectSessionData()` | Serialize form state to session JSON structure |
| `_applySessionData(data)` | Deserialize session JSON into form state |

### Issues Encountered & Fixes

**Issue 5: Date format mismatch between browser date picker and API.**
Browser `<input type="date">` returns `YYYY-MM-DD`. The API expects the same format. No conversion needed — but the display needed formatting as `MM/DD/YYYY` for user readability. Added `_formatDate()` helper for display-only formatting while keeping ISO format for API calls.

**Issue 6: Alpine.js reactivity with dynamically-created nested objects.**
Checkbox states are nested: `checkboxStates.Erosion_Minimization["question text"] = "YES"`. Alpine.js v3 uses proxies and handles nested reactivity, but the initial state must be set up with all keys present for reactivity to work.
Fix: `_initFormState()` pre-populates all checkbox state keys with empty strings during schema load, before any user interaction.

---

## 5. Phase 4: Server Deployment

### Scope

Create all deployment automation: provisioning script, systemd service units, Nginx configuration, backup scripts, and deployment documentation.

### Files Created

| File | Lines | Purpose |
|------|-------|---------|
| `web/scripts/deploy.sh` | 121 | Full VPS provisioning (idempotent, run as root) |
| `web/scripts/backup.sh` | 23 | Daily SQLite backup with 30-day retention |
| `web/scripts/init_admin.py` | 42 | Database bootstrap + first admin invite |
| `web/scripts/systemd/tools-auth.service` | 24 | Auth service unit (port 8001) |
| `web/scripts/systemd/tools-swppp.service` | 24 | SWPPP service unit (port 8002) |
| `web/scripts/nginx/tools.conf` | 69 | Reverse proxy + static file serving |
| `web/scripts/README-deploy.md` | 134 | Step-by-step deployment guide |

### Server Architecture

```
Internet → Nginx (443/SSL) → ┬─ /auth/*, /admin/*    → Gunicorn:8001 (auth service, 2 Uvicorn workers)
                              ├─ /swppp/api/*         → Gunicorn:8002 (SWPPP service, 2 Uvicorn workers)
                              ├─ /swppp/*             → Static files (/opt/tools/frontend/swppp/)
                              ├─ /                    → Static files (/opt/tools/frontend/portal/)
                              └─ /auth/login          → Static file (login.html)
```

### deploy.sh — 13-Step Provisioning

| Step | Action | Idempotent |
|------|--------|-----------|
| 1 | Create `tools` system user (no login shell) | Yes — `id tools` check |
| 2 | Install packages (python3, nginx, certbot, fail2ban, ufw, sqlite3, git) | Yes — apt |
| 3 | Configure UFW firewall (ports 22, 80, 443 only) | Yes — `ufw status` check |
| 4 | Enable fail2ban for SSH + Nginx | Yes — service check |
| 5 | Enable unattended-upgrades | Yes — package install |
| 6 | Clone or pull repo to `/opt/tools/repo` | Yes — clone if missing, pull if exists |
| 7 | Create venv, install deps (fastapi, gunicorn, uvicorn, pypdf, pydantic, etc.) | Yes — venv create if missing |
| 8 | Create data/log/backup directories, chown to `tools` | Yes — mkdir -p |
| 9 | Install systemd units, daemon-reload, enable + restart | Yes — cp + systemctl |
| 10 | Install Nginx config, test + reload | Yes — cp + nginx -t |
| 11 | Bootstrap admin (first run only) | Conditional — skips if `auth.db` exists |
| 12 | Install backup cron (daily at 02:00) | Yes — overwrites cron file |
| 13 | Install temp cleanup cron (hourly) | Yes — overwrites cron file |

### Nginx Configuration Decisions

**Decision: Security headers on all responses.**
```
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
```

**Decision: Extended timeout for SWPPP API.**
`proxy_read_timeout 120s` + `proxy_buffering off` on `/swppp/api/` to handle slow PDF generation (rain data fetch can take 2-5 seconds, PDF generation can take 10-30 seconds for large date ranges).

**Decision: Static HTML served by Nginx, not FastAPI.**
In production, Nginx serves all HTML files directly. FastAPI only handles API routes. This eliminates Python overhead for static content and allows aggressive caching.

### Systemd Service Configuration

Both services share the same pattern:
- `WorkingDirectory=/opt/tools/repo`
- `ExecStart=/opt/tools/venv/bin/gunicorn web.{service}.main:app --worker-class uvicorn.workers.UvicornWorker --workers 2 --bind 127.0.0.1:{port}`
- Environment: `TOOLS_DATA_DIR=/opt/tools/data`, `TOOLS_DEV_MODE=0`, `TOOLS_BASE_URL=https://tools.example.com`
- `Restart=always`, `RestartSec=5`
- Runs as `tools:tools` user (non-root, no login shell)

### Backup Strategy

| What | Method | Schedule |
|------|--------|----------|
| `auth.db` | `sqlite3 .backup` (WAL-safe) | Daily 02:00 UTC |
| `swppp_sessions.db` | `sqlite3 .backup` (WAL-safe) | Daily 02:00 UTC |
| Retention | `find -mtime +30 -delete` | 30 days |
| Orphan temp dirs | `find /tmp -name "swppp_gen_*" -mmin +60 -exec rm -rf` | Hourly |

### Server File Layout

```
/opt/tools/
├── repo/                    # Git clone of the full repository
│   ├── app/core/            # Core business logic (untouched)
│   ├── assets/template.pdf  # PDF template
│   ├── web/                 # Web-specific code
│   └── tests/               # Test suite
├── venv/                    # Python virtual environment
├── data/
│   ├── auth.db              # Auth database
│   └── swppp_sessions.db    # SWPPP session database
├── backups/                 # Daily DB backups (30-day retention)
└── logs/                    # Gunicorn access/error logs
```

---

## 6. Phase 5: QA Lockdown

### Scope

Phase 5 was not in the original execution plan (which defined only 4 phases). It was added as a comprehensive QA pass after all functional phases were complete. Scope: code hardening (input validation, error boundaries, size limits) + ~57 new tests covering edge cases, fuzzing, boundary conditions, and error paths.

### Two Workstreams

**Workstream A — Code Hardening (6 tasks):**
- A0: Global test network blocker
- A1: Pydantic `Field` constraints on SWPPP models
- A2: Pydantic `Field` constraints on Auth models
- A3: Exception wrapping in `rain_fetch()` endpoint
- A4: File upload size guards (5 MB limit)
- A5: Session name length validation
- A6: Exception wrapping in `generate_pdf()` endpoint

**Workstream B — New Tests (11 groups):**
- B1–B4: SWPPP API tests (rain parse, rain fetch, generate, sessions)
- B5–B10: Auth API tests (claim flow, invites, users, sessions, apps, dependencies)
- B11: Dev route tests

### A0: Global Network Safety Net

**File created:** `tests/conftest.py` (16 lines)

```python
@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    def _blocked(*a, **kw):
        raise RuntimeError("Network access is blocked in tests")
    monkeypatch.setattr("requests.Session.send", _blocked)
```

**Decision: Autouse fixture that blocks all HTTP.**
Every test function automatically gets this fixture. If any test accidentally makes a real HTTP request (e.g., to the Mesonet API), it fails immediately with `RuntimeError` instead of making a network call. This prevents: (1) flaky tests dependent on external services, (2) test data leaking into production systems, (3) slow test runs waiting on network timeouts.

The fixture monkeypatches `requests.Session.send` — the lowest-level method in the requests library. All HTTP methods (GET, POST, etc.) go through `send()`, so this catches everything.

### A1: SWPPP Model Constraints

**File modified:** `web/swppp_api/models.py`

| Model | Field | Constraint Added |
|-------|-------|-----------------|
| `RainDayItem` | `date` | `max_length=10` |
| `RainDayItem` | `rainfall_inches` | `ge=0.0` (rejects negative values) |
| `RainFetchRequest` | `station` | `max_length=50` |
| `RainFetchRequest` | `start_date` | `max_length=10` |
| `RainFetchRequest` | `end_date` | `max_length=10` |
| `RainFetchRequest` | `threshold` | `ge=0.0, le=10.0` (Pydantic rejects out-of-range via 422) |
| `GenerateRequest` | `start_date` | `max_length=10` |
| `GenerateRequest` | `end_date` | `max_length=10` |
| `GenerateRequest` | `original_inspection_type` | `max_length=200` |

**Rationale:** These constraints are enforced by Pydantic before the endpoint function executes. Invalid requests get an automatic 422 response with field-level error details. This prevents: oversized strings reaching the database or PDF filler, negative rainfall amounts corrupting data, absurd thresholds causing unexpected behavior.

### A2: Auth Model Constraints

**File modified:** `web/auth/models.py`

| Model | Field | Constraint Added |
|-------|-------|-----------------|
| `ClaimRequest` | `code` | `max_length=50` |
| `InviteCreateRequest` | `display_name` | `max_length=200` |
| `InviteCreateRequest` | `app_permissions` | `max_length=20` (list length limit) |
| `AppCreateRequest` | `id` | `max_length=50` |
| `AppCreateRequest` | `name` | `max_length=200` |
| `AppCreateRequest` | `description` | `max_length=500` |
| `AppCreateRequest` | `route_prefix` | `max_length=100` |

### A3: Exception Wrapping in rain_fetch()

**File modified:** `web/swppp_api/main.py`

The `rain_fetch()` endpoint was hardened with 5 explicit error boundaries:

```
1. parse_station_code() → try/except → 400 "Invalid station code"
2. Empty station code    → if not station_code → 400 "Invalid station code"
3. date.fromisoformat()  → try/except ValueError → 400 "Invalid date format"
4. end < start           → 400 "End date must not precede start date"
5. fetch_rainfall()      → try/except Exception → 502 "Rain data fetch failed"
```

**Decision: 502 for upstream failures, 400 for client errors.**
When the Mesonet API is unreachable or returns garbage, the endpoint returns 502 (Bad Gateway) — indicating the error is from an upstream service, not from the client's request. The frontend can display "Unable to reach Mesonet — try again or use CSV upload."

### A4: File Upload Size Guards

**File modified:** `web/swppp_api/main.py`

**Constant added:** `MAX_UPLOAD_BYTES = 5 * 1024 * 1024` (5 MB)

Applied to three upload endpoints:
- `rain_parse_csv()` — reads file content, checks `len(content) > MAX_UPLOAD_BYTES` → 413
- `import_session()` — same pattern → 413
- Both also check for UTF-8 decode errors → 400

**Decision: 413 status code (Payload Too Large).**
Standard HTTP semantics. The 5 MB limit is generous for CSV rainfall data (typical files are 10-50 KB) and session JSON (typically < 5 KB). This prevents memory exhaustion from malicious uploads.

### A5: Session Name Length Validation

**File modified:** `web/swppp_api/main.py`

**Constant added:** `MAX_SESSION_NAME = 200`

**Helper added:** `_validate_session_name(name: str) → None` — raises `HTTPException(400, "Session name too long")` if exceeded.

Called from: `get_session()`, `save_session()`, `export_session()`, `delete_session()` — all four endpoints that accept a session name path parameter.

**Rationale:** Path parameters aren't validated by Pydantic body models. Without this check, a client could send an arbitrarily long session name that gets stored in SQLite or used in file paths.

### A6: Exception Wrapping in generate_pdf()

**File modified:** `web/swppp_api/main.py`

The generate endpoint was wrapped with error boundaries:

```
1. weekly_dates() ValueError           → returns empty list (not an error)
2. generate_batch() exception          → 500 "PDF generation failed"
3. rain_day date parsing ValueError    → 400 "Invalid rain day date: {value}"
4. generate_rain_batch() exception     → 500 "Rain PDF generation failed"
5. bundle_outputs_zip() exception      → 500 "ZIP bundling failed"
```

### A3 Addendum: CSV Threshold Validation

**File modified:** `web/swppp_api/main.py`

The `rain_parse_csv()` endpoint takes `threshold` as a **query parameter** (not a Pydantic body field). Pydantic body validation doesn't apply to query params. Added explicit validation:

```python
if threshold < 0 or threshold > 10:
    raise HTTPException(status_code=422, detail="Threshold must be between 0 and 10")
```

**This was identified and fixed during test debugging** — see Issue 8 below.

### Phase 5 Test Additions

#### New tests in `test_swppp_api.py` (35 tests across 5 classes)

**`TestRainParseCsv` (4 new tests added to existing class):**

| Test | Validates |
|------|-----------|
| `test_parse_csv_empty_file` | Empty CSV returns 200 or 400 (graceful handling) |
| `test_parse_csv_oversized_file` | 6 MB file → 413 |
| `test_parse_csv_binary_content` | PNG magic bytes → 400 "UTF-8" |
| `test_parse_csv_negative_threshold` | `threshold=-1` → 422 |

**`TestRainFetch` (8 tests, new class):**

| Test | Validates |
|------|-----------|
| `test_rain_fetch_invalid_station` | Empty station → 400 |
| `test_rain_fetch_invalid_date_format` | `2025/01/01` → 400 |
| `test_rain_fetch_end_before_start` | End < start → 400, "precede" in detail |
| `test_rain_fetch_negative_threshold` | `threshold=-1` → 422 (Pydantic) |
| `test_rain_fetch_threshold_too_high` | `threshold=99` → 422 (Pydantic) |
| `test_rain_fetch_valid` | Mocked fetch → 200, correct response shape |
| `test_rain_fetch_network_error` | Mocked exception → 502, "fetch failed" in detail |
| `test_rain_fetch_returns_events` | Mocked data with events → correct counts and values |

**`TestGenerateExtended` (10 tests, new class):**

| Test | Validates |
|------|-----------|
| `test_generate_missing_start_date` | No start_date → 422 |
| `test_generate_missing_end_date` | No end_date → 422 |
| `test_generate_malformed_rain_day_date` | `"not-a-date"` → 400 |
| `test_generate_empty_rain_days_list` | `rain_days=[]` → 200 (valid) |
| `test_generate_with_rain_days` | Valid rain days → 200 ZIP |
| `test_generate_with_all_checkbox_groups` | Every group set to YES → 200 ZIP |
| `test_generate_with_notes` | Notes text → 200 ZIP |
| `test_generate_unknown_checkbox_group_ignored` | Unknown group key → 200 (ignored) |
| `test_generate_very_long_field_values` | 500-char field values → 200 ZIP |
| `test_generate_negative_rain_amount_rejected` | `rainfall_inches=-1` → 422 |

**`TestSessionCRUDExtended` (11 tests, new class):**

| Test | Validates |
|------|-----------|
| `test_save_empty_body` | `{}` body → 200 |
| `test_save_very_long_name` | 300-char name → 400 "too long" |
| `test_get_very_long_name` | 300-char name GET → 400 "too long" |
| `test_save_special_chars_in_name` | URL-encoded spaces/amps/parens → 200 |
| `test_delete_nonexistent_session` | DELETE on missing → 200 (idempotent) |
| `test_export_nonexistent_returns_404` | Export missing → 404 |
| `test_import_missing_session_name` | No `session_name` key → derives from filename |
| `test_import_oversized_file` | >1 MB → 413 |
| `test_import_non_json_content` | Plain text → 400 |
| `test_import_json_array_not_dict` | `[1,2,3]` → 400 "object" |
| `test_import_save_and_verify` | Import with save → round-trip verification |

**`TestDevRoutes` (2 tests, new class):**

| Test | Validates |
|------|-----------|
| `test_swppp_index_serves_html` | `/swppp/` → 200 with `text/html` |
| `test_swppp_index_contains_alpine` | HTML contains "alpine" |

#### New tests in `test_auth.py` (22 tests across 6 classes)

**`TestClaimFlowExtended` (3 tests):**

| Test | Validates |
|------|-----------|
| `test_claim_whitespace_padded_code` | `"  TOOLS-XXXX-XXXX  "` → accepted |
| `test_claim_empty_code` | `""` → 400 |
| `test_claim_very_long_code` | 60+ chars → 422 (Pydantic max_length) |

**`TestAdminInvitesExtended` (4 tests):**

| Test | Validates |
|------|-----------|
| `test_create_invite_empty_apps` | `app_permissions=[]` → 400 |
| `test_revoke_already_revoked` | Double-revoke → 400 "revoked" |
| `test_revoke_nonexistent_code` | Fake code → 404 |
| `test_create_invite_very_long_name` | 250-char name → 422 |

**`TestAdminUsersExtended` (4 tests):**

| Test | Validates |
|------|-----------|
| `test_patch_user_no_fields` | Empty body PATCH → 200 (no-op) |
| `test_patch_nonexistent_user` | Fake UUID → 404 |
| `test_promote_to_admin` | `is_admin=true` → admin flag set |
| `test_deactivate_already_deactivated` | Double-deactivate → 200 (idempotent) |

**`TestAdminSessionsExtended` (2 tests):**

| Test | Validates |
|------|-----------|
| `test_kill_session_nonexistent_prefix` | Fake prefix → 400 |
| `test_kill_session_empty_prefix` | Empty string → 400 |

**`TestAdminAppsExtended` (6 tests):**

| Test | Validates |
|------|-----------|
| `test_create_app_uppercase_id` | `"UPPER"` → 400 (IDs must be lowercase) |
| `test_create_app_duplicate` | Same ID twice → 400 |
| `test_create_app_missing_slash` | `route_prefix="noslash"` → 400 |
| `test_update_app_no_fields` | Empty body → 200 (no-op) |
| `test_update_nonexistent_app` | Fake ID → 404 |
| `test_create_app_long_fields` | 300-char name → 422 (max_length) |

**`TestAuthDependencies` (3 tests):**

| Test | Validates |
|------|-----------|
| `test_empty_cookie` | Empty session cookie → 401 |
| `test_garbage_cookie` | Random string → 401 |
| `test_deleted_session` | Valid cookie after session deletion → 401 |

### Issues Encountered & Fixes During Phase 5

**Issue 7: Mock path `web.swppp_api.main.parse_station_code` fails with `AttributeError`.**

5 out of 8 `TestRainFetch` tests failed immediately:
```
AttributeError: <module 'web.swppp_api.main'> does not have the attribute 'parse_station_code'
```

**Root cause:** The Phase 2 decision to use lazy imports inside endpoint bodies meant that `parse_station_code`, `fetch_rainfall`, and `filter_rain_events` are never module-level attributes of `web.swppp_api.main`. They exist only as local variables within the `rain_fetch()` function. `unittest.mock.patch()` can only patch names that exist as attributes of a module.

**Fix:** Changed all mock targets from the import destination to the source modules:
| Before (broken) | After (working) |
|------------------|-----------------|
| `@patch("web.swppp_api.main.parse_station_code")` | `@patch("app.core.mesonet_stations.parse_station_code")` |
| `@patch("web.swppp_api.main.fetch_rainfall")` | `@patch("app.core.mesonet.fetch_rainfall")` |
| `@patch("web.swppp_api.main.filter_rain_events")` | `@patch("app.core.mesonet.filter_rain_events")` |

This works because patching at the source module affects all callers — when `rain_fetch()` does `from app.core.mesonet import fetch_rainfall`, it gets the mocked version. This is a standard pattern for mocking deferred imports.

**Lesson:** When using lazy imports, always mock at the source module, not the import destination.

**Issue 8: `test_parse_csv_negative_threshold` expected 422 but got 200.**

**Root cause:** The `threshold` parameter on `rain_parse_csv()` is a **query parameter** (`threshold: float = 0.5`), not part of a Pydantic request body. Pydantic `Field` constraints on `RainFetchRequest.threshold` only apply to the `/rain/fetch` endpoint which uses that model. The CSV endpoint receives `threshold` as a raw query param — FastAPI validates the type (float) but not custom constraints.

**Fix:** Added explicit validation at the top of `rain_parse_csv()`:
```python
if threshold < 0 or threshold > 10:
    raise HTTPException(status_code=422, detail="Threshold must be between 0 and 10")
```

**Lesson:** Pydantic `Field` constraints only apply to body models. Query parameters need manual validation unless wrapped in a dedicated Pydantic model with `Query()`.

**Issue 9: Empty station string passes `parse_station_code()` without error.**

**Root cause:** `parse_station_code("")` returns an empty string `""` rather than raising an exception. The `try/except` around it caught nothing, and the empty station was passed to `fetch_rainfall()`.

**Fix:** Added a guard after the try/except:
```python
if not station_code:
    raise HTTPException(status_code=400, detail="Invalid station code")
```

### Phase 5 Regression Validation

After all hardening changes, the original 90 tests (39 desktop + 51 web from Phases 1-3) were re-run to confirm no regressions: **90/90 passed**.

After adding all 57 new tests and fixing the 6 failures: **147/147 passed** (78.48 seconds).

---

## 7. Final Codebase Inventory

### Web Application Files (21 files, 138 KB, 2,883 lines)

| File | Lines | Role |
|------|-------|------|
| `web/auth/main.py` | 322 | Auth FastAPI app (17 endpoints) |
| `web/auth/db.py` | 320 | Auth database layer (25 functions, 5 tables) |
| `web/auth/models.py` | 84 | 18 Pydantic models |
| `web/auth/dependencies.py` | 34 | 3 auth dependency functions |
| `web/swppp_api/main.py` | 346 | SWPPP FastAPI app (12 endpoints) |
| `web/swppp_api/db.py` | 98 | Session storage (7 functions, 1 table) |
| `web/swppp_api/models.py` | 64 | 14 Pydantic models |
| `web/frontend/swppp/index.html` | 729 | SWPPP SPA |
| `web/frontend/portal/admin.html` | 297 | Admin panel SPA |
| `web/frontend/portal/login.html` | 78 | Login page |
| `web/frontend/portal/index.html` | 64 | Portal launcher |
| `web/scripts/deploy.sh` | 121 | VPS provisioning script |
| `web/scripts/README-deploy.md` | 134 | Deployment guide |
| `web/scripts/nginx/tools.conf` | 69 | Nginx reverse proxy config |
| `web/scripts/init_admin.py` | 42 | Admin bootstrap |
| `web/scripts/systemd/tools-auth.service` | 24 | Auth systemd unit |
| `web/scripts/systemd/tools-swppp.service` | 24 | SWPPP systemd unit |
| `web/scripts/backup.sh` | 23 | Daily DB backup |
| `web/auth/__init__.py` | 0 | Package marker |
| `web/swppp_api/__init__.py` | 0 | Package marker |
| `web/__init__.py` | 0 | Package marker |

### Endpoint Summary (29 endpoints + 3 dev routes)

| Service | Auth Level | Count |
|---------|------------|-------|
| Auth — Public | None | 3 (login page, claim, logout) |
| Auth — Session | Valid session | 1 (/auth/me) |
| Auth — Admin | Admin flag | 13 (users, invites, sessions, apps CRUD) |
| SWPPP — App access | swppp permission | 11 (schema, stations, rain, sessions, generate) |
| Dev-only routes | None | 3 (portal index, admin page, SWPPP index) |
| **Total** | | **31** |

### Database Tables (6 total across 2 databases)

**auth.db:** `apps`, `users`, `invite_codes`, `user_app_access`, `sessions`
**swppp_sessions.db:** `saved_sessions`

---

## 8. Test Suite Inventory

### Final Count: 147 tests across 10 files

| File | Tests | Lines | Phase Added |
|------|-------|-------|-------------|
| `test_auth.py` | 49 | 495 | Phase 1 (27) + Phase 5 (+22) |
| `test_swppp_api.py` | 54 | 581 | Phase 2 (19) + Phase 5 (+35) |
| `test_mesonet.py` | 13 | 131 | Pre-migration |
| `test_session.py` | 13 | 106 | Pre-migration |
| `test_rain_fill.py` | 7 | 155 | Pre-migration |
| `test_checkbox_mapping.py` | 4 | 102 | Pre-migration |
| `test_fill.py` | 3 | 76 | Pre-migration |
| `test_model.py` | 3 | 59 | Pre-migration |
| `test_template_integration.py` | 1 | 52 | Pre-migration |
| `conftest.py` | 0 (1 fixture) | 16 | Phase 5 |
| **Total** | **147** | **1,773** | |

### Test Classes (25 total)

**Auth tests (14 classes, 49 tests):**
`TestBootstrap` (3), `TestClaimFlow` (5), `TestAuthGuards` (3), `TestMe` (1), `TestLogout` (1), `TestAdminUsers` (3), `TestAdminInvites` (4), `TestAdminSessions` (2), `TestAdminAppAccess` (1), `TestAdminApps` (4), `TestClaimFlowExtended` (3), `TestAdminInvitesExtended` (4), `TestAdminUsersExtended` (4), `TestAdminSessionsExtended` (2), `TestAdminAppsExtended` (6), `TestAuthDependencies` (3)

**SWPPP API tests (11 classes, 54 tests):**
`TestAuthGuard` (2), `TestFormSchema` (3), `TestStations` (2), `TestSessionCRUD` (8), `TestGenerate` (3), `TestRainParseCsv` (5), `TestRainFetch` (8), `TestGenerateExtended` (10), `TestSessionCRUDExtended` (11), `TestDevRoutes` (2)

### Test Infrastructure

| Component | Location | Purpose |
|-----------|----------|---------|
| `conftest.py` `_block_network` fixture | `tests/conftest.py` | Autouse — blocks all HTTP via `requests.Session.send` monkeypatch |
| `_admin_client()` | `test_auth.py` | Seeds DB, creates admin, returns authenticated TestClient |
| `_make_invite()` | `test_auth.py` | Generates invite via admin API |
| `_authed_client()` | `test_swppp_api.py` | Full auth flow → returns SWPPP-authorized TestClient |
| `_no_access_client()` | `test_swppp_api.py` | Creates user without SWPPP access (for 403 tests) |
| `_MockRainDay` | `test_swppp_api.py` | Dataclass stub for rain day mocking |
| `_MockFetchResult` | `test_swppp_api.py` | Dataclass stub for fetch result mocking |

### Test Isolation Strategy

Each test file creates its own `tempfile.mkdtemp()` and sets `os.environ["TOOLS_DATA_DIR"]` to it at module level. This gives each file a fresh SQLite database. The `_block_network` autouse fixture ensures no test can make real HTTP requests. Mock objects use `@patch` decorators targeting source modules (not import destinations) due to the lazy import pattern.

---

## 9. Known Limitations & Future Work

### Current Limitations

1. **No rate limiting.** Trusted-user-only model. If exposed to untrusted traffic, add Nginx `limit_req_zone` or a FastAPI middleware.
2. **No CSRF protection.** Mitigated by `SameSite=Lax` cookies, but explicit CSRF tokens would be stronger for state-changing requests.
3. **No email/password recovery.** User lockout requires admin intervention (generate new invite code).
4. **Mobile layout not optimized.** Two-column desktop layout. Works on tablet; phone requires scrolling.
5. **Rain fetch is synchronous.** Large date ranges (3+ months) take 2-5 seconds. Acceptable now, but the execution plan appendix documents an async job pattern for future use.
6. **Static file caching headers not set.** Nginx serves HTML/JS without `Cache-Control`. Should add `max-age=86400` for form-schema and stations responses (they don't change at runtime).
7. **No audit log.** Admin actions (deactivate user, revoke invite) are not logged beyond Gunicorn access logs.

### Documented Future Enhancement: Async Rain Fetch

From execution plan appendix — if synchronous rain fetch proves too slow:

```
POST /swppp/api/rain/fetch → returns { job_id, status: "processing" }
GET /swppp/api/rain/status/{job_id} → { status: "processing", progress: 45 }
                                    → { status: "complete", data: {...} }
```

Requires adding a `jobs` table, background task worker, and frontend polling logic. Not implemented because synchronous performance (2-5 seconds) is acceptable for the current user base.

---

## Appendix: Issue & Fix Registry

| # | Issue | Phase | Root Cause | Fix | Files Changed |
|---|-------|-------|-----------|-----|---------------|
| 1 | `ModuleNotFoundError: No module named 'web'` | 1 | pytest couldn't resolve web package | `pythonpath = ["."]` in pyproject.toml | `pyproject.toml` |
| 2 | `sqlite3.OperationalError: no such table` | 1 | DB not initialized before tests | Explicit `init_db()` + `seed_app()` in test helpers | `test_auth.py` |
| 3 | `ValueError` from `weekly_dates()` on edge-case ranges | 2 | Core function raises on invalid ranges | try/except → returns empty list | `web/swppp_api/main.py` |
| 4 | Route conflict: `/sessions/import` vs `/sessions/{name}` | 2 | FastAPI matches in declaration order | Declared literal route before parameterized route | `web/swppp_api/main.py` |
| 5 | Date format mismatch display vs API | 3 | Browser date picker returns YYYY-MM-DD | Added `_formatDate()` for display-only MM/DD/YYYY | `web/frontend/swppp/index.html` |
| 6 | Alpine.js nested reactivity for checkbox states | 3 | Dynamic objects need pre-populated keys | `_initFormState()` pre-fills all keys from schema | `web/frontend/swppp/index.html` |
| 7 | Mock path `web.swppp_api.main.X` fails for lazy imports | 5 | `parse_station_code` etc. not module-level attrs | Changed to source module paths: `app.core.mesonet.X`, `app.core.mesonet_stations.X` | `tests/test_swppp_api.py` |
| 8 | CSV threshold not validated (query param, not body) | 5 | Pydantic `Field` only applies to body models | Manual `if threshold < 0 or threshold > 10` check | `web/swppp_api/main.py` |
| 9 | Empty station string passes `parse_station_code()` | 5 | Function returns `""` instead of raising | Added `if not station_code` guard | `web/swppp_api/main.py` |
