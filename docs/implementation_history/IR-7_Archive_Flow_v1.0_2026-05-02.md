# IR-7: Archive Flow — Implementation Record

## Summary

Implemented the full project archive flow: PM-initiated archive with optional Notice of Termination (NOT) upload, background ZIP generation, public ZIP download, late NOT upload, company_admin-only unarchive, and archive status polling.

## Test Results

- `tests/test_archive.py`: **21 passed**
- Full suite: **467 passed, 2 skipped** (pre-existing IR-2 skips — intentional)

## Files Changed

### `web/auth/db.py`
Appended 4 new functions after `get_platform_dashboard()`:
- `archive_project(conn, project_id, user_id, not_document_path=None)` — sets `status='archived'`, clears `auto_weekly_enabled`, records `archived_at`/`archived_by_user_id`, optionally records NOT path, calls `archive_template_versions_for_project()`
- `unarchive_project(conn, project_id)` — resets `status='active'`, clears `archived_at`/`archived_by_user_id`/`archive_zip_path`. Does NOT re-enable `auto_weekly_enabled`
- `set_archive_zip_path(conn, project_id, zip_path)` — records the completed ZIP filesystem path
- `add_not_document(conn, project_id, user_id, not_path)` — records NOT path + timestamps, clears `archive_zip_path` to trigger ZIP regeneration

### `web/auth/models.py`
- Extended `MailboxProjectView` with: `is_archived: bool = False`, `archived_at: str | None = None`, `archive_zip_ready: bool = False`, `not_on_file: bool = False`
- Appended 4 new models: `ProjectArchiveRequest`, `ProjectArchiveResponse`, `ProjectArchiveStatusResponse`, `NotUploadResponse`

### `web/auth/main.py`
- Added `shutil`, `BackgroundTasks`, `File`, `Form`, `UploadFile` to imports
- Added 4 new models to the models import block
- Updated `GET /mailbox/{project_number}` to include archive fields in response
- Added private `_generate_archive_zip(project_id)` background task helper — opens its own DB connection, builds ZIP with `project-data.json` (schema_version 1.0), all mailbox PDFs, and NOT document if present; stores at `{data_dir}/archives/{project_id}/archive_{project_id}.zip`
- Added private `_require_project_member(company_id, project_id, user, conn, *, allowed_roles)` helper
- Added 5 new endpoints in `# ── Archive Flow (IR-7)` section:
  - `POST /companies/{cid}/projects/{pid}/archive` (202, pm or company_admin, multipart)
  - `GET /companies/{cid}/projects/{pid}/archive/status` (any company member)
  - `POST /companies/{cid}/projects/{pid}/unarchive` (company_admin only)
  - `POST /companies/{cid}/projects/{pid}/not` (pm or company_admin, multipart)
  - `GET /mailbox/{number}/archive/download` (public, 202 while preparing, 200+FileResponse when ready)

### `web/frontend/portal/project-detail.html`
- Added archive section to bottom of Settings tab (inside settings `<div>`, after the `</form>`)
- Active project view: "Archive without NOT" toggle + NOT file upload + red Archive button
- Archived project view: yellow banner, NOT status, late-upload form, ZIP spinner/download button, Unarchive button (company_admin only)
- Added JS state: `archiveWithoutNot`, `archiving`, `archiveError`, `archiveZipReady`, `archiveZipPoller`, `unarchiving`, `unarchiveError`, `uploadingNot`, `notUploadError`, `userRole`
- Extended `loadProject()`: populates `archiveZipReady`, fetches user role from `/auth/companies/{cid}/members`, starts archive poller if project archived but ZIP not ready
- Added JS functions: `archiveProject()`, `unarchiveProject()`, `uploadLateNot()`, `_startArchivePoller()`, `_stopArchivePoller()`

### `web/frontend/mailbox/index.html` (public mailbox)
- Restructured active project view: archived projects show banner + NOT status + ZIP download/spinner; active projects show existing report list
- Added `archiveZipPoller` state
- `searchProject()` starts archive poller when project is archived and ZIP not ready
- `backToSearch()` stops the poller on navigation
- Added `_startArchivePoller(projectNumber)` and `_stopArchivePoller()` — polls `GET /mailbox/{number}/archive/download` every 2s, stops when 200 is returned

### `tests/test_archive.py` (new file)
21 tests across 5 classes:
- `TestArchiveProject` (8): pm/admin can archive, non-member 403, status archived, auto_weekly disabled, archive with NOT file, 409 if already archived, template versions archived
- `TestArchiveStatus` (2): zip not ready, zip ready after path set
- `TestUnarchive` (4): admin can unarchive, pm 403, status resets to active, 400 if not archived
- `TestNotUpload` (3): upload NOT for archived project, 400 for active, clears archive_zip_path
- `TestMailboxArchivedView` (4): is_archived flag in response, 202 while preparing, FileResponse when ready, 404 when not archived

## Key Design Decisions

- **Background task opens its own connection** — `_generate_archive_zip` calls `sqlite3.connect(db.DB_PATH)` directly; the HTTP response has already been sent when the task runs
- **ZIP path is a filesystem string**, not a URL — `archive_zip_ready` is derived from `bool(archive_zip_path)`
- **NOT files** stored at `{data_dir}/not/{project_id}/{filename}`; **archive ZIPs** at `{data_dir}/archives/{project_id}/archive_{project_id}.zip`
- **Unarchive does NOT re-enable auto_weekly** — intentional; PM must explicitly re-enable
- **pm cannot unarchive** — checked via `allowed_roles=("company_admin",)` in `_require_project_member`
- **Tests mock `_generate_archive_zip`** for the three cases that require the ZIP to not be ready yet (TestClient runs background tasks synchronously)
