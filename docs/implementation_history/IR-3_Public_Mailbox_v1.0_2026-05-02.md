# IR-3: Public Mailbox
**Version:** 1.0  
**Date:** 2026-05-02  
**Status:** Ready for implementation  
**Depends on:** IR-1 complete (projects table + API)  
**Does NOT depend on:** IR-2 (template versioning), IR-4 (scheduler) — Mailbox works with zero reports and grows naturally as reports arrive  
**Repo:** https://github.com/JcbBnd26/swpppautofill_windows

---

## Objective

After IR-3, anyone can go to `sw3p.pro/mailbox`, type a project number, and see that project's filed SWPPP reports. They can download individual reports, select multiple, or download everything as a ZIP. The page remembers the last project number via cookie. Archived projects show a distinct view with a single archive download.

No reports will exist yet (scheduler is IR-4). The Mailbox must handle the empty state gracefully and be fully functional the moment reports start arriving in IR-4.

---

## Pre-Work

Verify before writing any code:
1. IR-2 deployed to `sw3p.pro` and manual smoke tests passed
2. Full test suite green: `python -m pytest tests/ -q` (341 tests)

---

## Implementation

### 1. Schema

Add to `web/auth/db.py` `SCHEMA_SQL`:

```sql
CREATE TABLE IF NOT EXISTS mailbox_entries (
    id                      TEXT PRIMARY KEY,
    project_id              TEXT NOT NULL REFERENCES projects(id),
    company_id              TEXT NOT NULL REFERENCES companies(id),
    report_date             TEXT NOT NULL,
    report_type             TEXT NOT NULL,
    generation_mode         TEXT NOT NULL DEFAULT 'scheduled',
    file_path               TEXT NOT NULL,
    file_size_bytes         INTEGER,
    template_version_id     TEXT REFERENCES project_template_versions(id),
    rain_data_json          TEXT,
    created_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mailbox_project
    ON mailbox_entries(project_id, report_date DESC);
```

**Valid `report_type` values:** `auto_weekly`, `auto_rain_event`, `manual_upload`  
**Valid `generation_mode` values:** `scheduled`, `retroactive`  
**Note:** `generation_mode` is internal only — never rendered publicly.

---

### 2. Database Functions

Add to `web/auth/db.py`:

- `get_mailbox_entries(conn, project_id, sort_order='desc') -> list[dict]` — all entries for a project, sorted by `report_date`. `sort_order`: `'desc'` (newest first, default) or `'asc'` (oldest first).
- `get_mailbox_entry(conn, entry_id) -> dict | None`
- `create_mailbox_entry(conn, project_id, company_id, report_date, report_type, file_path, **kwargs) -> str` — returns entry id. Used by IR-4 scheduler and manual upload.
- `get_mailbox_entry_count(conn, project_id) -> int` — for the project list "Mailbox count" column.

---

### 3. Pydantic Models

Add to `web/auth/models.py`:

```python
class MailboxEntryPublic(BaseModel):
    id: str
    report_date: str
    report_type: str  # 'auto_weekly', 'auto_rain_event', 'manual_upload'
    file_size_bytes: int | None

class MailboxProjectView(BaseModel):
    project_number: str
    project_name: str
    site_address: str
    status: str  # 'active', 'paused', 'setup_incomplete', 'archived'
    archived_at: str | None
    not_document_path: str | None
    archive_zip_path: str | None
    entries: list[MailboxEntryPublic]
    total_count: int
```

---

### 4. API Endpoints

These endpoints live in `web/auth/main.py` and are **fully public — no auth required**.

```
GET  /mailbox/{project_number}
     — Returns MailboxProjectView for the given project number.
     — sort_order query param: 'desc' (default) or 'asc'
     — Returns HTTP 404 if project number not found
     — Never exposes: company_id, generation_mode, template_version_id,
       internal file paths, or any PM/company settings

GET  /mailbox/{project_number}/download/{entry_id}
     — Serves the PDF file for a single report entry
     — Content-Disposition: attachment (opens in OS native PDF viewer)
     — Returns HTTP 404 if entry not found or doesn't belong to project

POST /mailbox/{project_number}/download/batch
     — Body: {"entry_ids": ["id1", "id2", ...]}
     — Returns a ZIP file containing the selected PDFs
     — Max 50 entries per batch request
     — ZIP filename: project-{number}-reports-{date}.zip
     — Content-Disposition: attachment

GET  /mailbox/{project_number}/download/all
     — Returns a ZIP of ALL reports for the project
     — For archived projects: returns archive_zip_path if it exists,
       otherwise generates on the fly
     — ZIP filename: project-{number}-all-reports-{date}.zip

GET  /mailbox/{project_number}/not
     — Serves the NOT (Notice of Termination) PDF if uploaded
     — Returns HTTP 404 if no NOT on file
     — Content-Disposition: attachment

GET  /mailbox
     — Serves the Mailbox HTML page (no auth, no data)
```

**Security rules for all Mailbox endpoints:**
- Never expose internal file system paths in responses — serve files directly, don't return paths
- Never expose company_id, company name, PM names, or any contractor-internal data
- `generation_mode` field never appears in any public response
- Rate limit: 60 requests/minute per IP (basic DDoS protection)

---

### 5. Frontend

**`web/frontend/mailbox/index.html`** — the entire Mailbox UI. Single page, no login required.

#### Layout — Search State (on first visit, no cookie)

Centered card:
- SW3P logo / wordmark at top
- Heading: "SWPPP Inspection Records"
- Subheading: "Enter a project number to view inspection reports"
- Project number input field + "View Reports" button
- Footer: "sw3p.pro — Stormwater compliance made simple"

#### Layout — Active Project View (after search or cookie auto-load)

Header bar:
- Project number (large) + project name
- Site address
- "Different project?" link — clears cookie, returns to search state

Report list:
- Sort toggle: "Newest First ↔ Oldest First" (default: newest first)
- Each row: date (formatted, project timezone) | type badge (Weekly / Rain Event / Upload) | file size | Download button
- Checkboxes on each row for multi-select
- "Select All" checkbox in header
- Action bar (appears when ≥1 selected): "Download Selected (ZIP)" button + count indicator
- "Download All Reports (ZIP)" button (always visible)
- Empty state: "No reports have been filed for this project yet. Check back after the next scheduled inspection."

#### Layout — Archived Project View

Replaces the normal report list when `status === 'archived'`:
- **"Project Archived"** banner with archive date
- NOT status:
  - ✅ Green badge: "Notice of Termination on file" + "Download NOT" button
  - ⚠️ Yellow badge: "Notice of Termination not on file"
- **"Download Complete Archive (ZIP)"** button (single prominent CTA)
  - If `archive_zip_path` exists: serves pre-generated ZIP immediately
  - If not yet ready: shows "Archive being prepared…" spinner, polls `/mailbox/{number}` every 3 seconds until `archive_zip_path` is populated
- No individual report browsing on archived projects

#### Cookie Behavior

On successful project load:
```javascript
document.cookie = "sw3p_last_project=" + projectNumber + "; max-age=31536000; SameSite=Lax"
```

On page load:
```javascript
const last = getCookie("sw3p_last_project");
if (last) fetchProject(last);  // auto-loads last project
```

Cookie max-age: 1 year. SameSite=Lax (no Secure flag needed — project numbers are not sensitive).

#### Mobile-First Layout

The Mailbox is the primary mobile surface. Design at 375px portrait first:
- Search card: full-width, generous padding, large input + button
- Report list: stacked rows, date + type on one line, download button full-width below
- Touch targets: ≥44px on all interactive elements
- "Download All" button: full-width, prominent
- Archived view: same single-column stacking
- Desktop: widens to max 800px centered, report list becomes a proper table

#### Type Badge Colors
- **Weekly:** blue
- **Rain Event:** amber/yellow
- **Upload:** gray

---

### 6. Nginx Route

Add to `web/scripts/nginx/tools.conf`:

```nginx
location /mailbox {
    proxy_pass http://127.0.0.1:8001;  # auth service port
}
```

The Mailbox HTML page and all `/mailbox/*` API routes are served by the auth service.

---

### 7. File Serving

PDFs are served directly from the filesystem (DigitalOcean Spaces in production, local `web/data/reports/` in dev). Use FastAPI's `FileResponse`:

```python
return FileResponse(
    path=resolved_file_path,
    media_type="application/pdf",
    filename=f"swppp-{project_number}-{entry.report_date}.pdf",
    headers={"Content-Disposition": f"attachment; filename=..."}
)
```

**Never** return the raw file path in a JSON response. Always proxy through the download endpoint.

**Dev file storage:** `web/data/reports/{project_id}/{entry_id}.pdf`  
**Prod file storage:** DigitalOcean Spaces (IR-7 sets this up — for now, local filesystem is fine)

---

### 8. ZIP Generation

Batch and "download all" ZIPs are generated in memory (not written to disk) and streamed back:

```python
import zipfile, io

buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
    for entry in entries:
        zf.write(file_path, arcname=f"{entry.report_date}-{entry.report_type}.pdf")
buf.seek(0)
return StreamingResponse(buf, media_type="application/zip",
    headers={"Content-Disposition": f"attachment; filename={zip_filename}"})
```

Exception: archived project ZIPs (which include JSON + NOT) are pre-generated and stored on disk. IR-7 handles that. For IR-3, the archived project "Download All" can fall back to the in-memory pattern if `archive_zip_path` is null.

---

## Tests Required

New file: `tests/test_mailbox.py`

Test classes:
- `TestMailboxSchema` — table creation, index presence, column presence
- `TestMailboxCRUD` — create entry, get entry, list entries, sort order, count
- `TestMailboxEndpoints` — all public endpoints, 404 on missing project, correct data shape
- `TestMailboxSecurity` — no internal fields in responses (company_id, generation_mode, file_path, PM names)
- `TestMailboxDownload` — single PDF download, correct Content-Disposition header
- `TestMailboxBatchZip` — batch ZIP returns valid ZIP, correct filenames, max 50 limit enforced
- `TestMailboxDownloadAll` — all-reports ZIP contains correct entries
- `TestMailboxSortOrder` — desc default, asc param works
- `TestMailboxEmptyState` — valid project with zero entries returns empty list, not 404
- `TestMailboxArchivedView` — archived project returns correct fields, NOT status, archive_zip_path
- `TestMailboxNoAuth` — all endpoints return data without any session cookie

Minimum: 35 new tests. All existing 341 tests must continue to pass.

---

## Mandatory Reporting Section

> Agent must complete before marking IR-3 done.

**1. Full pytest output:**
```
[paste here]
```
Baseline: 341 tests. Expected after IR-3: 376+ tests.

**2. Mailbox schema proof:**
```
[paste: python -c "from web.auth.db import init_db; init_db()"]
```

**3. Security proof — no internal fields exposed:**
```
[paste: pytest tests/test_mailbox.py::TestMailboxSecurity -v]
```

**4. No-auth proof:**
```
[paste: pytest tests/test_mailbox.py::TestMailboxNoAuth -v]
```

**5. Manual smoke tests (Jake must verify on sw3p.pro):**
- [ ] Go to `sw3p.pro/mailbox` — confirm search card loads without login
- [ ] Enter a valid project number — confirm project view loads
- [ ] Return to `sw3p.pro/mailbox` — confirm cookie auto-loads the last project
- [ ] Clear cookie — confirm returns to search state
- [ ] Enter an invalid project number — confirm clean 404 / "not found" message
- [ ] On a phone — confirm layout is clean and all buttons are tappable
- [ ] Confirm no company name, PM name, or internal data is visible on the page

---

*End of IR-3*
