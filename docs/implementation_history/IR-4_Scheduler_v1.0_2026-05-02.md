# IR-4: Scheduler + Auto-Weekly + Rain Event + Reconciliation Engine
**Version:** 1.0  
**Date:** 2026-05-02  
**Status:** Ready for implementation  
**Depends on:** IR-2 complete (341 tests), IR-3 complete (364 tests)  
**Repo:** https://github.com/JcbBnd26/swpppautofill_windows

---

## Objective

After IR-4, the system automatically generates SWPPP reports on a daily schedule. For every active project with `auto_weekly_enabled=1`, the reconciliation engine checks what reports *should* exist vs. what *does* exist, and fills the gaps. Weekly reports file on the project's configured day-of-week. Rain event reports file whenever a rain threshold-exceeding day is detected and not yet reported. Both types appear in the project's Mailbox. The scheduler runs as a systemd timer on the production server at 6:00 AM Pacific daily.

---

## Pre-Work

Verify before writing any code:
1. IR-3 deployed to `sw3p.pro` and manual smoke tests passed
2. Full test suite green: `python -m pytest tests/ -q` (364 tests)
3. Read existing `web/auth/db.py` to confirm `project_run_log` table does NOT yet exist
4. Read existing `web/scripts/systemd/tools-auth.service` to match the service file format

---

## Implementation

### 1. Schema

Add to `web/auth/db.py` `SCHEMA_SQL`:

```sql
CREATE TABLE IF NOT EXISTS project_run_log (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id),
    run_date        TEXT NOT NULL,
    status          TEXT NOT NULL,
    error_type      TEXT,
    error_message   TEXT,
    reports_filed   INTEGER NOT NULL DEFAULT 0,
    duration_ms     INTEGER,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_log_project
    ON project_run_log(project_id, run_date DESC);
```

**Valid `status` values:** `success`, `partial`, `failed`, `skipped`

---

### 2. Database Functions

Add to `web/auth/db.py`:

- `get_projects_due_for_run(conn) -> list[dict]` — returns all projects where `auto_weekly_enabled=1` AND `status='active'` AND (`project_end_date` is null OR `project_end_date` >= today) AND (`paused_until` is null OR `paused_until` < today)
- `get_mailbox_entries_for_project(conn, project_id) -> list[dict]` — all mailbox entries for a project, used by reconciliation to determine what's already filed
- `create_project_run_log(conn, project_id, run_date, status, **kwargs) -> str` — returns log id
- `get_project_run_log(conn, project_id, limit=30) -> list[dict]` — recent run history for a project
- `update_project_run_state(conn, project_id, last_run_at, last_run_status, last_successful_run_at=None) -> None` — updates scheduler state fields on the project record

---

### 3. Scheduler Module

New file: `web/scheduler/run_due_reports.py`

This is the core of IR-4. The entire scheduler is one callable entry point.

#### Entry Point

```python
def run_due_reports(conn: sqlite3.Connection, *, dry_run: bool = False) -> dict:
    """
    Reconciliation engine. Checks every active project and files any missing reports.
    Returns a summary dict: {projects_processed, reports_filed, failures, skipped}
    dry_run=True logs what would happen without writing anything.
    """
```

#### Reconciliation Logic (per project)

For each project returned by `get_projects_due_for_run()`:

1. **Weekly report check:**
   - Determine all Fridays (or configured `schedule_day_of_week`) between `project_start_date` and today
   - Query mailbox for existing `auto_weekly` entries for this project
   - Any week with no entry = missing weekly report
   - If >10 missing reports for one project: log a warning and require `force=True` flag to proceed — do not auto-file more than 10 without explicit override

2. **Rain event check:**
   - Query Mesonet for the last 14 days of rain data for `rain_station_code`
   - Any day with rainfall >= `rain_threshold_inches` = potential rain event
   - Query mailbox for existing `auto_rain_event` entries matching those dates
   - Any rain event day with no entry = missing rain event report

3. **Generate missing reports:**
   - Fetch the active template version for the project (`active_template_version_id`)
   - Call existing PDF generation pipeline with the template data + appropriate date
   - Set `generation_mode`:
     - `scheduled` if the report is being filed on its expected day
     - `retroactive` if it's being backfilled for a past date
   - Write the PDF to `web/data/reports/{project_id}/` (dev) or DigitalOcean Spaces path (prod — placeholder for IR-7)
   - Create a `mailbox_entries` row
   - **Isolation:** wrap each project in try/except — one project failing must not stop others

4. **Update project run state:**
   - Call `update_project_run_state()` with current timestamp and status
   - Write a `project_run_log` row for this project's run

5. **Heartbeat ping:**
   - After the full run (all projects processed), ping `HEALTHCHECKS_URL` env var if set
   - `requests.get(os.environ["HEALTHCHECKS_URL"], timeout=5)` — fire and forget, never raise on failure

#### Confirmation Gate (>10 missing reports)

```python
if len(missing_weekly) > 10 and not force:
    log.warning(
        "Project %s has %d missing weekly reports — skipping. "
        "Run with force=True to backfill.",
        project_id, len(missing_weekly)
    )
    # Log as 'skipped' in project_run_log
    continue
```

This is the runaway protection. `force=True` is passed by the admin manual-run endpoint, not by the normal cron.

---

### 4. Scheduler Entry Script

New file: `web/scheduler/main.py`

```python
"""
Scheduler entry point — called by systemd timer daily.
Usage: python -m web.scheduler.main [--dry-run] [--force]
"""
import argparse
import logging
import sys
from web.auth.db import connect
from web.log_config import configure_logging
from web.scheduler.run_due_reports import run_due_reports

configure_logging()
log = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="Allow backfilling >10 reports per project")
    args = parser.parse_args()

    log.info("Scheduler starting — dry_run=%s force=%s", args.dry_run, args.force)
    with connect() as conn:
        summary = run_due_reports(conn, dry_run=args.dry_run, force=args.force)
    log.info("Scheduler complete: %s", summary)
    sys.exit(0)

if __name__ == "__main__":
    main()
```

---

### 5. Admin Manual-Run Endpoint

Add to `web/auth/main.py`:

```
POST /admin/run-due-reports
```

- **Auth:** `require_platform_admin` dependency — platform admin only
- **Body:** `{"force": false}` (optional, defaults to false)
- **Behavior:** calls `run_due_reports(conn, force=body.force)` synchronously
- **Returns:**
```json
{
  "projects_processed": 12,
  "reports_filed": 3,
  "failures": 0,
  "skipped": 1,
  "duration_ms": 4521
}
```
- **Use case:** manual override when a cron run failed or needs immediate catch-up. This is the "run reconciliation now" button from the PM dashboard spec (IR-5 will wire the frontend; IR-4 just builds the endpoint).

---

### 6. Infrastructure — systemd Timer

New file: `web/scripts/systemd/tools-scheduler.service`

```ini
[Unit]
Description=SW3P Report Scheduler
After=network.target

[Service]
Type=oneshot
User=root
WorkingDirectory=/opt/tools/repo
Environment="TOOLS_DATA_DIR=/opt/tools/data"
EnvironmentFile=-/opt/tools/repo/.env
ExecStart=/opt/tools/repo/.venv/bin/python -m web.scheduler.main
StandardOutput=journal
StandardError=journal
SyslogIdentifier=tools-scheduler

[Install]
WantedBy=multi-user.target
```

New file: `web/scripts/systemd/tools-scheduler.timer`

```ini
[Unit]
Description=SW3P Report Scheduler — daily at 6:00 AM
Requires=tools-scheduler.service

[Timer]
OnCalendar=*-*-* 06:00:00
AccuracySec=1min
Persistent=true

[Install]
WantedBy=timers.target
```

**`Persistent=true`** is critical — if the server was down at 6:00 AM, systemd will fire the timer immediately on next boot to catch up. This is the systemd-native equivalent of our "no ceiling on catch-up" design decision.

**`EnvironmentFile=-/opt/tools/repo/.env`** — the `-` prefix means "don't fail if the file doesn't exist." The `.env` file is where `HEALTHCHECKS_URL` and future Spaces credentials live. Never hardcode these.

---

### 7. Production Deploy Steps

**These require `APPROVE_PROD_DEPLOY` before execution.**

After code is pushed to the repo, on the production server:

```bash
cd /opt/tools/repo
git pull --ff-only

# Install new systemd units
cp web/scripts/systemd/tools-scheduler.service /etc/systemd/system/
cp web/scripts/systemd/tools-scheduler.timer /etc/systemd/system/

# Enable and start the timer
systemctl daemon-reload
systemctl enable tools-scheduler.timer
systemctl start tools-scheduler.timer

# Verify
systemctl status tools-scheduler.timer --no-pager
systemctl list-timers tools-scheduler.timer --no-pager

# Restart existing services (picks up schema changes)
systemctl restart tools-auth tools-swppp
```

**Verify the timer is scheduled:**
```bash
systemctl list-timers --all | grep scheduler
```
Expected output shows next trigger time at 06:00:00 tomorrow.

---

### 8. Healthchecks.io Setup

1. Create a free account at healthchecks.io
2. Create a new check: name "SW3P Daily Scheduler", period 25 hours, grace 1 hour
3. Copy the ping URL (format: `https://hc-ping.com/{uuid}`)
4. Add to `/opt/tools/repo/.env` on the production server:
   ```
   HEALTHCHECKS_URL=https://hc-ping.com/{your-uuid}
   ```
5. The scheduler pings this URL at the end of every successful full run
6. If Healthchecks.io doesn't receive a ping within 25 hours, it emails you

---

## Tests Required

New file: `tests/test_scheduler.py`

Test classes:
- `TestProjectRunLogSchema` — table creation, index presence, column presence
- `TestProjectRunLogCRUD` — create, get, list run log entries
- `TestGetProjectsDueForRun` — returns active+enabled projects, excludes paused, excludes archived, excludes setup_incomplete, excludes past end_date
- `TestReconciliationWeekly` — weekly report due today is generated, already-filed week is skipped (idempotency), generation_mode=scheduled when on-time, generation_mode=retroactive when backfilling
- `TestReconciliationRainEvent` — rain event above threshold generates report, rain event below threshold skipped, already-filed rain event skipped (idempotency)
- `TestConfirmationGate` — >10 missing reports skips without force=True, proceeds with force=True
- `TestPerProjectIsolation` — one project raising an exception does not stop other projects from running
- `TestRunSummary` — summary dict contains correct counts after a run
- `TestAdminRunEndpoint` — POST /admin/run-due-reports requires platform admin, returns summary, non-admin gets 403, unauthenticated gets 401
- `TestDryRun` — dry_run=True logs actions but creates no mailbox_entries and no run_log rows
- `TestHeartbeat` — heartbeat URL is pinged after successful run, not pinged if run raises, missing env var doesn't crash

Minimum: 40 new tests. All existing 364 tests must continue to pass.

**Note on Mesonet calls in tests:** mock `app.core.mesonet` at the test boundary — do not make real HTTP calls in the test suite. Use `unittest.mock.patch` or pytest fixtures to inject controlled rain data.

---

## Mandatory Reporting Section

> Agent must complete before marking IR-4 done.

**1. Full pytest output:**
```
[paste here]
```
Baseline: 364 tests. Expected after IR-4: 404+ tests.

**2. Schema proof:**
```
[paste: python -c "from web.auth.db import init_db; init_db()"]
```

**3. Reconciliation idempotency proof:**
```
[paste: pytest tests/test_scheduler.py::TestReconciliationWeekly -v]
```

**4. Per-project isolation proof:**
```
[paste: pytest tests/test_scheduler.py::TestPerProjectIsolation -v]
```

**5. Admin endpoint proof:**
```
[paste: pytest tests/test_scheduler.py::TestAdminRunEndpoint -v]
```

**6. Production deploy evidence (requires APPROVE_PROD_DEPLOY):**
```
[paste: systemctl list-timers tools-scheduler.timer --no-pager]
[paste: systemctl status tools-scheduler.timer --no-pager]
```

**7. Manual smoke tests (Jake must verify):**
- [ ] SSH to production, run `systemctl list-timers | grep scheduler` — confirm next trigger at 06:00 AM
- [ ] Call `POST /admin/run-due-reports` via the admin panel or curl — confirm summary response
- [ ] Check `project_run_log` table has a new row after the manual run
- [ ] If any active projects exist: confirm Mailbox shows a new entry after the run
- [ ] Check Healthchecks.io dashboard — confirm a ping was received after the manual run

---

*End of IR-4*
