# IR-9: Dashboard as Projects Overview
**Version:** 1.0
**Date:** 2026-05-03
**Status:** Ready for implementation
**Depends on:** IR-8 complete (478 passed, 2 skipped)
**Repo:** https://github.com/JcbBnd26/swpppautofill_windows

---

## Objective

Repurpose the existing `dashboard.html` page from a generic "company dashboard" into a proper **projects overview** for PMs. No new pages. No new tables. No structural changes. Extend the existing `GET /companies/{company_id}/dashboard` endpoint with three additional data sets, then surface them in the existing dashboard page below the stat cards.

After IR-9, when a PM logs in and lands on the dashboard, they see at a glance:
- The four existing stat cards (unchanged)
- **Reports filed this week** count (new)
- **Upcoming this week** — projects with reports due in the next 7 days (new)
- **Recent activity** — last 10 mailbox entries across all company projects (replaces "Recent failures")
- **Templates due for review** — projects past their template review cadence (new)

---

## Pre-Work

1. IR-8 deployed to `sw3p.pro` and manual smoke tests passed
2. Full test suite green: `python -m pytest tests/ -q` (478 passed, 2 skipped)

---

## Implementation

### 1. Database Functions

Extend `get_company_dashboard()` in `web/auth/db.py` to include four additional data sets:

1. **`reports_filed_this_week`** (int) — `COUNT(*)` from `mailbox_entries` WHERE `company_id = ?` AND `created_at >= start_of_week`. Use Monday 00:00 as the week boundary in the project's primary timezone.
2. **`recent_activity`** (list, max 10) — last 10 mailbox entries across all projects in the company. JOIN `mailbox_entries` with `projects` to surface `project_number` and `project_name` alongside each entry. Order by `created_at DESC`. Each row: `entry_id`, `project_id`, `project_number`, `project_name`, `report_date`, `report_type`, `created_at`.
3. **`upcoming_this_week`** (list) — projects where `auto_weekly_enabled = 1` AND `status = 'active'` AND `schedule_day_of_week` falls within the next 7 days from today. Each row: `project_id`, `project_number`, `project_name`, `next_due_date`. Compute `next_due_date` in Python from `schedule_day_of_week` (don't try to do day-of-week arithmetic in SQLite).
4. **`templates_due_for_review`** (list) — projects where `template_review_cadence` is not `'never'` AND `template_last_reviewed_at` is older than the cadence threshold (or null and project has been `active` for longer than the threshold). Each row: `project_id`, `project_number`, `project_name`, `template_last_reviewed_at`, `cadence`.

Cadence thresholds:
- `monthly` → 30 days
- `quarterly` → 90 days
- `never` → exclude entirely

The existing fields (`total_projects`, `active`, `failing`, `paused`, `setup_incomplete`, `recent_failures`) stay exactly as they are. **Do not remove or rename `recent_failures`** — the platform admin dashboard still references it indirectly through the same data shape.

### 2. Pydantic Models

Extend `CompanyDashboardResponse` in `web/auth/models.py` with four new fields:

```python
class RecentActivityEntry(BaseModel):
    entry_id: str
    project_id: str
    project_number: str
    project_name: str
    report_date: str
    report_type: str  # 'auto_weekly', 'auto_rain_event', 'manual_upload'
    created_at: str

class UpcomingProjectEntry(BaseModel):
    project_id: str
    project_number: str
    project_name: str
    next_due_date: str  # ISO date

class TemplateReviewDueEntry(BaseModel):
    project_id: str
    project_number: str
    project_name: str
    template_last_reviewed_at: str | None
    cadence: str  # 'monthly' | 'quarterly'

class CompanyDashboardResponse(BaseModel):
    # ── Existing fields (unchanged) ──
    total_projects: int
    active: int
    failing: int
    paused: int
    setup_incomplete: int
    recent_failures: list[ProjectFailureSummary]
    # ── New in IR-9 ──
    reports_filed_this_week: int
    recent_activity: list[RecentActivityEntry]
    upcoming_this_week: list[UpcomingProjectEntry]
    templates_due_for_review: list[TemplateReviewDueEntry]
```

The new fields are additive. Existing API consumers won't break — they'll just ignore the new fields. This is a non-breaking change.

### 3. API Endpoint

`GET /companies/{company_id}/dashboard` — no signature change, just returns more data in the same response model. Auth rules unchanged (any company member, platform admin bypasses).

### 4. Frontend Changes

In `web/frontend/portal/dashboard.html`:

1. **Add a 5th stat card** to the existing grid: "Reports This Week" with the count and a small "filed" label below. Keep the existing 4-card layout — change `grid-cols-2 md:grid-cols-4` to `grid-cols-2 md:grid-cols-5`.

2. **Below the stat cards, add three new sections in this order:**
   - **"Upcoming This Week"** — table of projects with `next_due_date`. Empty state: "No reports due this week — you're all caught up."
   - **"Recent Activity"** — table of the last 10 mailbox entries. Columns: Date | Project # / Name | Type badge. Empty state: "No reports filed yet."
   - **"Templates Due for Review"** — table of projects past their review threshold. Columns: Project # / Name | Last Reviewed | Cadence | Action ("Review →" linking to `project-detail.html?id={id}#template`). Empty state: "All templates are up to date."

3. **Rename the existing "Recent failures" section to "Recent failures (auto-weekly)"** for clarity. Move it below the three new sections — it's important but not the lead. If `recent_failures` is empty, hide the entire section.

4. **Mobile pass for the new sections** — apply the same table-to-card-stack pattern from IR-8.

### 5. Naming

The page filename stays `dashboard.html`. The page header text stays "Dashboard." This is intentional — the page is the dashboard for company operations, and the fact that it's project-centric is implicit in what's on it.

---

## Tests Required

Extend `tests/test_pm_dashboard.py` with one new test class:

`TestCompanyDashboardOverview` — at least 6 tests:

1. `test_response_includes_new_fields` — confirms `reports_filed_this_week`, `recent_activity`, `upcoming_this_week`, `templates_due_for_review` are present in the response
2. `test_reports_filed_this_week_counts_correctly` — seed mailbox entries dated this week and last week, confirm only this week's are counted
3. `test_recent_activity_returns_max_10` — seed 15 mailbox entries, confirm only the 10 most recent are returned
4. `test_recent_activity_ordered_newest_first` — confirm `created_at DESC` ordering
5. `test_upcoming_this_week_includes_due_projects` — seed projects with `schedule_day_of_week` within next 7 days, confirm they appear; seed one outside that window, confirm it doesn't
6. `test_templates_due_for_review_respects_cadence` — seed projects with `quarterly` cadence and `template_last_reviewed_at` 95 days ago (due) vs 30 days ago (not due), confirm correct filtering
7. `test_templates_never_cadence_excluded` — projects with `template_review_cadence='never'` are never returned

Minimum: 7 new tests. All existing 478 tests must continue to pass.

---

## Mandatory Reporting Section

> Agent must complete before marking IR-9 done.

**1. Full pytest output:**
```
[paste here]
```
Baseline: 478 passed, 2 skipped. Expected after IR-9: 485+ passed, 2 skipped.

**2. New dashboard fields proof:**
```
[paste: pytest tests/test_pm_dashboard.py::TestCompanyDashboardOverview -v]
```

**3. Backwards-compatibility check:**
Confirm existing `TestGetCompanyDashboard` tests still pass — proves the existing fields/contract weren't broken.
```
[paste: pytest tests/test_pm_dashboard.py::TestGetCompanyDashboard -v]
```

**4. Manual smoke tests (Jake must verify on sw3p.pro):**
- [ ] Log in as PM → land on dashboard → confirm 5 stat cards visible
- [ ] Confirm "Reports This Week" count matches what you'd expect
- [ ] Confirm "Upcoming This Week" lists projects with reports due in the next 7 days
- [ ] Confirm "Recent Activity" shows the most recent mailbox entries
- [ ] If any project has a stale template, confirm "Templates Due for Review" surfaces it
- [ ] On phone — confirm all four sections stack cleanly with no horizontal scroll

---

*End of IR-9*
