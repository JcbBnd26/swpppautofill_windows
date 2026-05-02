# IR-5 — PM Dashboard + Health View
**Date:** 2026-05-02
**Baseline:** IR-4 complete (409 passed, 2 skipped)
**Result:** 431 passed, 2 skipped (+22 new tests)

---

## Scope

Company-scoped PM dashboard with project health indicators, per-project run history, and a "Run Reports Now" trigger scoped to the PM's own company.

---

## Files Changed

### Backend (4 files)

| File | Change |
|---|---|
| `web/auth/models.py` | 4 new Pydantic models appended after `RunDueReportsResponse` |
| `web/auth/db.py` | `get_company_dashboard()` function appended |
| `web/auth/main.py` | 3 new API endpoints + `Query` import added |
| `web/scheduler/run_due_reports.py` | `company_id: str | None = None` filter param added |

### Frontend (3 files)

| File | Change |
|---|---|
| `web/frontend/portal/projects.html` | Health dot column, `calculateNextDue()` fix, Dashboard header link |
| `web/frontend/portal/project-detail.html` | Settings tab, Mailbox tab, Run History section filled in |
| `web/frontend/portal/dashboard.html` | **New file** — company PM dashboard page |

---

## New API Endpoints

All three require an authenticated session. Authorization rules are described below.

### `GET /companies/{company_id}/dashboard` → `CompanyDashboardResponse`
- Returns project counts bucketed as: `total_projects`, `active`, `failing`, `paused`, `setup_incomplete`
- `failing` = `auto_weekly_enabled=1` AND `last_run_status IN ('failed', 'partial_failure')`
- `recent_failures`: last 5 failed run_log rows JOINed to projects for project_number + name
- `archived` projects excluded from all counts
- Authorization: company membership OR platform_admin bypass

### `GET /companies/{company_id}/projects/{project_id}/run-log?limit=30` → `RunLogResponse`
- Entries ordered newest-first; `limit` max 100 (returns 422 if exceeded)
- Tenant-isolated via `get_project_for_company` before log fetch
- Authorization: company membership required

### `POST /companies/{company_id}/run-due-reports` → `RunDueReportsResponse`
- Triggers `run_due_reports(conn, dry_run=False, force=body.force, company_id=company_id)`
- Only processes projects belonging to the specified company (filter applied in scheduler)
- Authorization: `pm` or `company_admin` role required (outsiders get 403)

---

## New Pydantic Models

```python
class ProjectFailureSummary(BaseModel):
    project_id: str
    project_number: str
    project_name: str
    run_date: str
    error_message: str | None = None

class CompanyDashboardResponse(BaseModel):
    total_projects: int
    active: int
    failing: int
    paused: int
    setup_incomplete: int
    recent_failures: list[ProjectFailureSummary]

class RunLogEntry(BaseModel):
    id: str
    run_date: str
    status: str
    error_type: str | None
    error_message: str | None
    reports_filed: int
    duration_ms: int | None
    created_at: str

class RunLogResponse(BaseModel):
    entries: list[RunLogEntry]
```

---

## Scheduler Change

`run_due_reports()` signature extended:

```python
def run_due_reports(conn, *, dry_run=False, force=False, company_id=None) -> dict[str, int]:
```

After `get_projects_due_for_run(conn)`, if `company_id` is not None:

```python
projects = [p for p in projects if p.get("company_id") == company_id]
```

`get_projects_due_for_run` uses `SELECT *` so `company_id` is present in every project row.

The existing `POST /admin/run-due-reports` (platform admin, no company filter) is unchanged.

---

## Frontend: Health Dot Logic

In `projects.html`, each project row has a colored status dot:

| Color | Condition |
|---|---|
| Gray | `auto_weekly_enabled=false` or status null/missing |
| Green | status ok AND next-due ≤ 8 days away |
| Yellow | status ok AND next-due > 8 days away, OR status `skipped` |
| Red | status `failed` or `partial_failure` |

The 8-day staleness threshold was chosen to flag projects that are active but haven't filed a report recently without being outright failing.

`calculateNextDue()` uses `project_start_date` + `schedule_day_of_week` (0=Mon…6=Sun, converted to JS 0=Sun convention via `(dow+1)%7`) and returns the next occurrence date string, `"Ended"`, or `"—"`.

---

## Frontend: Dashboard Page (`dashboard.html`)

Alpine.js component `dashboardApp()`:
- On init: `GET /auth/me` → `GET /auth/companies/{companyId}/dashboard`
- 4 stat cards: Total Projects, Active, Failing, Paused+Incomplete
- "Run Reports Now" button → `POST /auth/companies/{companyId}/run-due-reports` with spinner
- After run: shows result summary (`projects_processed`, `reports_filed`, `failures`, `skipped`)
- Dashboard counts refresh automatically after a run completes
- Recent failures table: Project # | Project Name | Date | Error → links to `project-detail.html?id={project_id}`

---

## Key Design Decisions

1. **Company-scoped trigger (not platform-wide):** PMs can run reports for their own company only. The platform-admin global trigger from IR-4 is preserved at `POST /admin/run-due-reports`.

2. **"Failing" definition:** `auto_weekly_enabled=1 AND last_run_status IN ('failed', 'partial_failure')`. A project with auto disabled showing failures is not counted as failing — it's inactive by intent.

3. **Mailbox tab uses existing public endpoint:** `GET /mailbox/{project_number}` — no new API needed.

4. **8-day staleness for health dot:** Chosen to catch projects that haven't reported in over a week without requiring a dedicated staleness column.

---

## Test Coverage (`tests/test_pm_dashboard.py`)

22 new tests across 3 test classes.

### `TestGetCompanyDashboard` (9 tests)
- `test_member_gets_200`
- `test_non_member_gets_403`
- `test_unauthenticated_gets_401_or_403`
- `test_platform_admin_bypasses_membership`
- `test_response_shape`
- `test_counts_active_correctly`
- `test_failing_definition`
- `test_recent_failures_populated`
- `test_archived_excluded_from_totals`

### `TestGetProjectRunLog` (9 tests)
- `test_member_gets_200`
- `test_non_member_gets_403`
- `test_wrong_company_gets_404`
- `test_bad_project_id_gets_404`
- `test_empty_log_returns_empty_list`
- `test_entries_returned_newest_first`
- `test_limit_param_respected`
- `test_limit_capped_at_100`
- `test_error_fields_surfaced`

### `TestRunCompanyReports` (4 tests)
- `test_pm_can_trigger`
- `test_company_admin_can_trigger`
- `test_non_member_gets_403`
- `test_company_id_passed_to_engine`

---

## IR Sequence Status

| IR | Description | Status |
|---|---|---|
| IR-1 | Core web stack, auth, company/project CRUD | ✅ deployed |
| IR-2 | Template versioning | ✅ complete (341 tests) |
| IR-3 | Public mailbox | ✅ complete (364 tests) |
| IR-4 | Scheduler engine + run log + admin endpoint | ✅ complete (409 tests) |
| **IR-5** | **PM Dashboard + Health View** | **✅ complete (431 tests)** |
| IR-6 | Platform admin health dashboard (cross-company view) | planned |
| IR-7 | Archive flow (ZIP + DigitalOcean Spaces) | planned |
| IR-8 | Mobile responsiveness pass | planned |
