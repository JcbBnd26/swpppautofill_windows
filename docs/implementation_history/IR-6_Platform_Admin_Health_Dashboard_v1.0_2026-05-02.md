# IR-6 — Platform Admin Health Dashboard
**Date:** 2026-05-02
**Baseline:** IR-5 complete (431 passed, 2 skipped)
**Result:** 446 passed, 2 skipped (+15 new tests)

---

## Scope

Read-only cross-company health dashboard for platform admins. Shows system-wide problem projects (red/yellow), a per-company rollup table, and key report volume metrics. Accessible only to users with `is_platform_admin=1`.

---

## Files Changed

### Backend (3 files)

| File | Change |
|---|---|
| `web/auth/db.py` | `get_platform_dashboard(conn)` function appended after `get_company_dashboard()` |
| `web/auth/models.py` | 3 new Pydantic models appended after `RunLogResponse` |
| `web/auth/main.py` | `GET /admin/platform-health` endpoint + 3 model imports added |

### Frontend (2 files)

| File | Change |
|---|---|
| `web/frontend/portal/admin-health.html` | **New file** — platform admin health dashboard page |
| `web/frontend/portal/admin.html` | "Health Dashboard →" nav link added to header |

### Tests (1 file)

| File | Change |
|---|---|
| `tests/test_platform_admin_dashboard.py` | **New file** — 15 tests for the new endpoint |

---

## New API Endpoint

### `GET /admin/platform-health` → `PlatformDashboardResponse`

Requires `is_platform_admin=1`. Returns three data sets in a single response:

**Scalar metrics:**
- `total_companies` — active companies (non-archived)
- `total_active_projects` — non-archived projects across all companies
- `reports_filed_7d` / `reports_filed_30d` — SUM of `reports_filed` from `project_run_log` within the respective window
- `last_run_at` — MAX `created_at` from `project_run_log` (most recent cron activity, any project)

**`problem_projects`** — all yellow/red projects across all companies:
- Red: `auto_weekly_enabled=1` AND `last_run_status IN ('failed', 'partial_failure')`
- Yellow (setup): `status = 'setup_incomplete'`
- Yellow (stale): `auto_weekly_enabled=1` AND active AND (`last_successful_run_at IS NULL` OR julianday gap > 8 days)
- Ordered red-first, then alphabetical by project name
- `failure_count_7d` — correlated subquery counting failed runs in the last 7 days

**`company_rollup`** — one row per company:
- `total_projects`, `active`, `failing`, `paused`, `setup_incomplete` counts
- `last_activity` — most recent `created_at` from `project_run_log` for that company
- `admin_name` — display name of the first `company_admin` user for that company

**`status_reason`** string (computed in endpoint layer, not DB):
- `"Failing (auto-weekly)"` when `health_flag == 'red'`
- `"Setup incomplete"` when `status == 'setup_incomplete'`
- `"Stale (>8 days)"` otherwise

---

## New Pydantic Models

```python
class ProblemProjectRow(BaseModel):
    company_name: str
    project_id: str
    project_number: str
    project_name: str
    health_flag: str          # 'red' | 'yellow'
    status_reason: str
    last_successful_run_at: str | None = None
    failure_count_7d: int

class CompanyHealthRow(BaseModel):
    id: str
    display_name: str
    total_projects: int
    active: int
    failing: int
    paused: int
    setup_incomplete: int
    last_activity: str | None = None
    admin_name: str | None = None

class PlatformDashboardResponse(BaseModel):
    total_companies: int
    total_active_projects: int
    reports_filed_7d: int
    reports_filed_30d: int
    last_run_at: str | None = None
    problem_projects: list[ProblemProjectRow]
    company_rollup: list[CompanyHealthRow]
```

---

## Frontend: Health Dashboard Page (`admin-health.html`)

Alpine.js component `adminHealthApp()`:

- On `init()`: `GET /auth/me` → check `is_platform_admin` (redirect to `/portal/admin.html` if not) → `GET /auth/admin/platform-health` → set `dashboard`
- 4 stat cards: Companies | Active Projects | Reports (7d) | Reports (30d); `last_run_at` shown as footer note on the last card
- **Problem Projects table:** empty state "All projects healthy"; columns — Company | Project # | Name | Health badge (red/yellow pill) | Reason | Last Success (`timeAgo`) | Failures (7d); red rows get `border-l-4 border-l-red-400`
- **Company Rollup table:** columns — Company | Projects | Active | Failing | Paused | Incomplete | Last Activity | Admin; rows with `failing > 0` get `bg-red-50` highlight
- `timeAgo(iso)` helper: seconds/minutes/hours/days ago relative string
- `logout()`: `POST /auth/logout` → redirect to `/auth/login`

Nav link added to `admin.html` header: `"Health Dashboard →"` linking to `/portal/admin-health.html`.

---

## Key Design Decisions

1. **Read-only.** No action buttons, no mutations. Platform admin observes; company admins act. Actions are deferred to IR-7+.

2. **`status_reason` computed in the endpoint layer**, not in SQL. The DB query returns `health_flag` + `status` and the endpoint maps those to the human-readable reason string.

3. **`is_platform_admin` is distinct from `is_admin`.** `create_user(is_admin=True)` mirrors the flag (`is_platform_admin = is_admin`), so test helpers must explicitly `UPDATE users SET is_platform_admin=0` when creating a regular admin who should not have platform access.

4. **Three SQL queries inside `get_platform_dashboard()`** — no new tables required. All data derives from `projects`, `companies`, `project_run_log`, `company_users`, and `users`.

---

## Test Coverage (`tests/test_platform_admin_dashboard.py`)

15 new tests in one test class.

### `TestGetPlatformHealth` (15 tests)
- `test_platform_admin_gets_200`
- `test_non_platform_admin_gets_403`
- `test_unauthenticated_gets_401_or_403`
- `test_response_shape`
- `test_total_companies_count`
- `test_total_active_projects_count`
- `test_problem_projects_includes_failing`
- `test_problem_projects_excludes_healthy`
- `test_problem_projects_cross_company`
- `test_problem_projects_excludes_archived`
- `test_problem_projects_setup_incomplete_is_yellow`
- `test_company_rollup_all_companies_present`
- `test_company_rollup_counts_correct`
- `test_reports_filed_7d_counts_correctly`
- `test_last_run_at_populated`

### Test Helper Notes

- `_make_conn()` — in-memory SQLite with `SCHEMA_SQL` loop (never call `db.init_db(conn)` — it opens its own file-based connection)
- `_seed_company()` — captures `create_company()` return value directly (it returns the `company_id` string)
- `_create_regular_admin_session()` — explicitly sets `is_platform_admin=0` after `create_user(is_admin=True)` to counteract the flag mirroring

---

## IR Sequence Status

| IR | Description | Status |
|---|---|---|
| IR-1 | Core web stack, auth, company/project CRUD | ✅ deployed |
| IR-2 | Template versioning | ✅ complete (341 tests) |
| IR-3 | Public mailbox | ✅ complete (364 tests) |
| IR-4 | Scheduler engine + run log + admin endpoint | ✅ complete (409 tests) |
| IR-5 | PM Dashboard + Health View | ✅ complete (431 tests) |
| **IR-6** | **Platform Admin Health Dashboard** | **✅ complete (446 tests)** |
| IR-7 | Archive flow (ZIP + DigitalOcean Spaces) | planned |
| IR-8 | Mobile responsiveness pass | planned |
