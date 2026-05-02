# IR-2: Template Versioning + Standing Template UI + Project Frontend
**Version:** 1.0  
**Date:** 2026-05-02  
**Status:** Ready for implementation  
**Depends on:** IR-1 complete (316 tests passing)  
**Repo:** https://github.com/JcbBnd26/swpppautofill_windows

---

## Objective

After IR-2, a PM can:
1. Create a project and land on a project detail page
2. Set up a standing template (one-time SWPPP form fill)
3. Save it — creating version 1, automatically promoted to active
4. Update the template later — creating version 2, promoting based on `template_promote_mode`
5. Revert to a previous version (creates a new version as a copy)
6. Preview what next week's auto-weekly report will look like (watermarked PDF)
7. See full template version history

No scheduler yet. No Mailbox yet. Just the template layer and the PM-facing UI.

---

## Pre-Work

Verify before writing any code:
1. IR-1 deployed to `sw3p.pro` and manual smoke test passed
2. Full test suite green: `python -m pytest tests/ -q` (316 tests)

---

## Implementation

### 1. Schema

Add to `web/auth/db.py` `SCHEMA_SQL`:

```sql
CREATE TABLE IF NOT EXISTS project_template_versions (
    id                      TEXT PRIMARY KEY,
    project_id              TEXT NOT NULL REFERENCES projects(id),
    version_number          INTEGER NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'draft',
    template_data           TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    created_by_user_id      TEXT NOT NULL REFERENCES users(id),
    promoted_at             TEXT,
    promoted_by_user_id     TEXT REFERENCES users(id),
    superseded_at           TEXT,
    UNIQUE(project_id, version_number)
);
```

**Valid `status` values:** `draft`, `active`, `superseded`, `archived`

**Version numbering:** auto-incremented per project (Project A: 1,2,3 / Project B: 1,2,3). Combination of `project_id + version_number` is globally unique.

**Revert pattern:** reverting to a previous version creates a new version record copying old `template_data`. History is immutable and append-only — never mutate existing version records.

---

### 2. Database Functions

Add to `web/auth/db.py`:

- `create_template_version(conn, project_id, created_by_user_id, template_data: dict) -> str` — creates new version, auto-assigns next version_number, status=`draft`, returns version id
- `get_template_version(conn, version_id) -> dict | None`
- `get_template_versions(conn, project_id) -> list[dict]` — all versions for a project, ordered by version_number DESC
- `get_active_template_version(conn, project_id) -> dict | None` — the one with status=`active`
- `promote_template_version(conn, version_id, promoted_by_user_id) -> None` — sets version to `active`, supersedes previous active version (sets its `superseded_at`), updates `projects.active_template_version_id`
- `archive_template_versions_for_project(conn, project_id) -> None` — called at project archive time, sets all versions to `archived`

**Auto-promote logic (in `create_template_version`):**  
After creating the version record, check `projects.template_promote_mode`:
- If `auto` — immediately call `promote_template_version()` on the new version
- If `manual` — leave status as `draft`, return

---

### 3. Pydantic Models

Add to `web/auth/models.py`:

```python
class TemplateVersionData(BaseModel):
    # Core project fields (mirrors existing SWPPP form fields)
    job_piece: str | None = Field(default=None, max_length=200)
    project_number: str | None = Field(default=None, max_length=100)
    contract_id: str | None = Field(default=None, max_length=100)
    location_description_1: str | None = Field(default=None, max_length=400)
    location_description_2: str | None = Field(default=None, max_length=400)
    re_odot_contact_1: str | None = Field(default=None, max_length=200)
    re_odot_contact_2: str | None = Field(default=None, max_length=200)
    inspection_type: str | None = Field(default=None, max_length=100)
    inspected_by: str | None = Field(default=None, max_length=200)
    reviewed_by: str | None = Field(default=None, max_length=200)
    # Checkbox group defaults — JSON-serializable dict
    checkboxes: dict = Field(default_factory=dict)
    # Any additional SWPPP fields from odot_mapping.yaml
    extra_fields: dict = Field(default_factory=dict)

class TemplateSaveRequest(BaseModel):
    template_data: TemplateVersionData

class TemplateVersionInfo(BaseModel):
    id: str
    project_id: str
    version_number: int
    status: str
    created_at: str
    created_by_user_id: str
    promoted_at: str | None
    superseded_at: str | None

class TemplateVersionDetail(TemplateVersionInfo):
    template_data: dict

class TemplateVersionListResponse(BaseModel):
    versions: list[TemplateVersionInfo]
    active_version_id: str | None

class TemplatePromoteModeRequest(BaseModel):
    template_promote_mode: str  # 'auto' or 'manual'
```

---

### 4. API Endpoints

Add to `web/auth/main.py`:

```
POST   /companies/{company_id}/projects/{project_id}/template
       — Save new template version (auto or manual promote based on project setting)

GET    /companies/{company_id}/projects/{project_id}/template
       — Get active template version + full version history

GET    /companies/{company_id}/projects/{project_id}/template/{version_id}
       — Get specific version detail including template_data

POST   /companies/{company_id}/projects/{project_id}/template/{version_id}/promote
       — Manually promote a draft version to active (only valid in manual promote mode)

POST   /companies/{company_id}/projects/{project_id}/template/{version_id}/revert
       — Create a new version copying this version's template_data, then promote

PATCH  /companies/{company_id}/projects/{project_id}/settings
       — Update project settings including template_promote_mode

GET    /companies/{company_id}/projects/{project_id}/template/preview
       — Generate a watermarked preview PDF from the active template version
```

**Auth rules (all endpoints):**
- Active session required
- User must be active member of `company_id` (any role)
- Write endpoints (POST, PATCH) restricted to `company_admin` and `pm`
- Platform admin bypasses company membership check

**Preview endpoint behavior:**
- Uses active template version's `template_data`
- Uses next scheduled report date (calculated from project's `schedule_day_of_week`)
- Fetches real Mesonet rain data for most recent completed week
- Generates PDF using existing SWPPP generation pipeline with `preview=True` flag
- Adds "PREVIEW — NOT FILED" watermark diagonally across every page
- Returns PDF as `application/pdf` response — not saved anywhere, not logged as a run
- Updates `projects.last_preview_generated_at`
- Returns HTTP 400 if project has no active template version yet

**Status transitions on first template save:**
When a project's first template version is created and promoted, update `projects.status` from `setup_incomplete` to `active`.

---

### 5. Frontend

#### `web/frontend/portal/project_create.html`
Project creation form. Required fields:
- Project number (inline duplicate check on blur via `GET /companies/{id}/projects?number={n}`)
- Project name
- Site address
- Timezone (dropdown, default America/Chicago)
- Rain station (reuse existing station picker component from SWPPP)

Optional fields (collapsible "Additional Details" section):
- Start date / end date
- RE/ODOT contact 1 + 2
- Contractor name + contract ID
- Notes

On success: redirect to `project_detail.html?id={project_id}`

#### `web/frontend/portal/project_list.html`
PM home screen after login. Table of company projects:

Columns: Project Number | Project Name | Status | Last Report | Next Due | Actions

Status light rules:
- 🟢 Green — `active`, auto-weekly off OR no failures
- 🟡 Yellow — `setup_incomplete` OR `paused`
- 🔴 Red — (reserved for scheduler failures, IR-4)

"New Project" button links to `project_create.html`.

#### `web/frontend/portal/project_detail.html`
Project workspace. Tabbed layout:

**Tab 1: Overview**
- Project info display (number, name, address, contacts)
- "Setup incomplete" banner if `status === 'setup_incomplete'` — links to Template tab
- Status light + last run info (placeholder until IR-4)

**Tab 2: Template**
- If no active version: empty state with "Set up your standing template" prompt
- If active version exists: display current template fields (read-only view) with "Edit Template" button
- Edit mode: full SWPPP form (reuse existing form components) with date picker removed, submit button labeled "Save Template"
- Template promote mode toggle: "Auto-promote" (default) / "Manual promote" — calls PATCH /settings
- "Preview Report" button — calls preview endpoint, opens returned PDF in new browser tab
- Version history table: version number | status | created date | created by | actions (promote if draft, revert)

**Tab 3: Settings**
- All 14 automation knobs (schedule, content, delivery, compliance audit trail)
- Editable via PATCH /settings
- Auto-weekly enabled toggle (prominent, at top)
- Pause until date picker
- Notification email list (add/remove)

**Tab 4: Mailbox** (placeholder — built in IR-3)
- "Mailbox view coming soon" — links to public Mailbox URL when available

---

### 6. Watermark Implementation

The preview watermark is applied after PDF generation, before the response is returned. It must not touch the generation pipeline itself — the pipeline stays clean.

Pattern:
```python
def add_preview_watermark(pdf_bytes: bytes) -> bytes:
    # Use pypdf to overlay "PREVIEW — NOT FILED" diagonally on each page
    # Text: large, semi-transparent, rotated 45 degrees, centered
    # Return watermarked PDF bytes
```

This function lives in `web/swppp_api/` alongside the existing generation code. The preview endpoint calls `generate_pdf()` then `add_preview_watermark()` before returning.

---

### 7. `odot_mapping.yaml` → Template Data

The `TemplateVersionData` model must map cleanly to the fields defined in `app/core/odot_mapping.yaml`. The template editor form should derive its field list from the same YAML (via the existing `/swppp/schema` endpoint) so there's a single source of truth. Do not hardcode field names in the frontend.

---

## Tests Required

New file: `tests/test_template_versioning.py`

Test classes:
- `TestTemplateVersionSchema` — table creation, UNIQUE constraint, column presence
- `TestTemplateVersionCRUD` — create, get, list, promote, supersede, archive
- `TestAutoPromote` — saving in auto mode immediately sets status to active
- `TestManualPromote` — saving in manual mode leaves draft, explicit promote activates
- `TestVersionNumbering` — version numbers increment per project, two projects independent
- `TestRevert` — revert creates new version with old data, history unchanged
- `TestTemplateEndpoints` — all API endpoints, auth, role restrictions
- `TestPreviewEndpoint` — returns PDF, 400 if no active template, watermark present
- `TestStatusTransition` — project status moves from setup_incomplete to active on first template save
- `TestTenantIsolation` — Company A PM cannot access Company B templates (404)

Minimum: 40 new tests. All existing 316 tests must continue to pass.

---

## Mandatory Reporting Section

> Agent must complete before marking IR-2 done.

**1. Full pytest output:**
```
[paste here]
```
Baseline: 316 tests. Expected after IR-2: 356+ tests.

**2. Template version schema proof:**
```
[paste: python -c "from web.auth.db import init_db; init_db()"]
```

**3. Auto-promote test proof:**
```
[paste: pytest tests/test_template_versioning.py::TestAutoPromote -v]
```

**4. Manual promote test proof:**
```
[paste: pytest tests/test_template_versioning.py::TestManualPromote -v]
```

**5. Tenant isolation proof:**
```
[paste: pytest tests/test_template_versioning.py::TestTenantIsolation -v]
```

**6. Manual smoke tests (Jake must verify on sw3p.pro):**
- [ ] Create a new project — confirm project list shows it with yellow status
- [ ] Navigate to Template tab — confirm "Setup incomplete" banner visible
- [ ] Fill out and save template — confirm banner disappears, status turns green
- [ ] Click "Preview Report" — confirm watermarked PDF opens in browser
- [ ] Edit template — confirm new version created, version history shows both
- [ ] Switch to manual promote mode — save new version — confirm it stays as draft
- [ ] Manually promote draft — confirm it becomes active
- [ ] Revert to version 1 — confirm version 3 created with version 1's data

---

*End of IR-2*
