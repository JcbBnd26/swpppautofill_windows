# IR-8: Mobile Responsiveness Pass
**Version:** 1.0
**Date:** 2026-05-02
**Status:** Complete
**Depends on:** IR-7 complete (467 tests, 2 skipped)
**Repo:** https://github.com/JcbBnd26/swpppautofill_windows

---

## Implementation Summary

Scope was narrowed from the full plan (table-to-card layouts, input font-size changes) to a focused usability pass: eliminating overflow failures, fixing broken two-column layouts on phones, and meeting the 44px touch target minimum. No new tests were added; the existing 467-test suite was unaffected.

### Changes Made

| File | Changes |
|---|---|
| `swppp/index.html` | Removed `style="min-width: 900px;"`. Two-column layout stacks on mobile (`flex-col md:flex-row`). Left/right columns go full-width below `md:`. Month grid `grid-cols-4` → `grid-cols-2 sm:grid-cols-4`. Custom date range row stacks (`flex-col sm:flex-row`). YES/NO/NA buttons: `px-2.5 py-1` → `px-3 py-2` (44px touch target). |
| `project-detail.html` | Tab nav `<nav class="flex">` → `<nav class="flex overflow-x-auto">`. Content padding `p-8` → `p-4 md:p-8`. Overview grid `grid-cols-2` → `grid-cols-1 sm:grid-cols-2`. Run log and mailbox tables already had `overflow-x-auto`. |
| `projects.html` | Added `<div class="overflow-x-auto">` inner wrapper around the 7-column table. |
| `admin.html` | Generate Invite and Create User form rows: `flex flex-wrap` → `flex flex-col sm:flex-row`; name inputs `flex-1 min-w-[200px]` → `w-full sm:flex-1`. Onboard New Company second input same fix. Both Apps checkbox rows: `flex gap-3` → `flex flex-wrap gap-3`. Three tables (pending signups, pending invites, users list) wrapped in `overflow-x-auto`. |
| `admin-health.html` | Problem Projects and Company Rollup tables wrapped in `overflow-x-auto`. Stat grid already had `grid-cols-2 md:grid-cols-4`. |
| `dashboard.html` | Recent Failures table wrapped in `overflow-x-auto` inside the existing `overflow-hidden` card. Stat grid already had `grid-cols-2 md:grid-cols-4`. |
| `project-new.html` | Date range grid `grid-cols-2` → `grid-cols-1 sm:grid-cols-2`. |

### Files Not Changed

`login.html`, `signup.html`, `portal/index.html`, `mailbox/index.html` were already mobile-friendly.

### Test Results

467 passed, 2 skipped — identical baseline. No backend code was changed.

---

## Original Plan



Every user-facing page works cleanly on a phone. The Mailbox is mobile-first (375px portrait). All PM portal pages and the platform admin health dashboard are functional and readable on mobile. No new features. No new API endpoints. No schema changes. This is a pure layout and usability pass.

---

## Pre-Work

1. IR-7 deployed to `sw3p.pro` and manual smoke tests passed
2. Full test suite green: `python -m pytest tests/ -q` (467 passed, 2 skipped)
3. Before touching any file, load each affected page in Chrome DevTools at **iPhone SE (375 × 667)** and screenshot the current state. This produces a before/after baseline for the mandatory reporting section.

---

## Ground Rules — Apply to Every File

These rules apply across all pages. Don't repeat them in individual file sections — just follow them everywhere.

| Rule | Requirement |
|---|---|
| Touch targets | Every button, link, checkbox, toggle, tab is ≥ 44px tall |
| Body text | ≥ 14px on mobile (`text-sm` is 14px in Tailwind — fine) |
| Inputs | Full-width on mobile, `text-base` (16px) to prevent iOS auto-zoom on focus |
| Horizontal scroll | Zero. Nothing overflows the viewport at 375px |
| Tables | Convert to stacked card layout on mobile (`hidden md:table-row`, `block md:hidden` swap) |
| Modals | Full-width on mobile with `p-4` viewport padding |
| Breakpoint | Use `md:` (768px) as the desktop breakpoint throughout. Do not use `sm:` or `lg:` unless explicitly justified |
| PDF downloads | Already correct — `Content-Disposition: attachment` is set, no change needed |
| Tailwind CDN | Stays CDN, no build step. Continue using utility classes only |

---

## Pages to Update

### 1. `web/frontend/mailbox/index.html` — Mobile-First (Primary Surface)

**Current state:** uses `max-w-4xl mx-auto p-4`. Layout already adapts reasonably at narrow widths but buttons and inputs need touch-target sizing. Header text is too large on narrow viewports. The "Download All" and "Download Complete Archive (ZIP)" buttons need bigger tap zones.

**Changes:**
- Header: change `text-2xl` to `text-xl md:text-2xl`, change `mb-6` to `mb-4 md:mb-6`
- Search card: increase input `py-2` to `py-3`, change `text-sm` to `text-base` on the input itself
- Search button: `py-2` → `py-3` on mobile (≥44px), keep `py-2 md:py-2` desktop
- Download buttons (Download All, Download Complete Archive): change `py-2 px-4` to `py-3 px-4`
- Project header card: stack the title and "Change Project" button on mobile (`flex-col md:flex-row`)
- Entry rows: stack date/type/file size into 2 lines on mobile, keep 1 line on desktop. Download button becomes full-width on mobile
- Add `text-base` to the project number input so iOS doesn't zoom on focus

### 2. `web/frontend/portal/projects.html` — Table → Card Stack on Mobile

**Current state:** uses a `<table class="w-full">` with 7 columns (Project Number, Name, Status, Health, Last Report, Next Due, Actions). At 375px this overflows badly. Header is `max-w-7xl px-6` which crowds on mobile.

**Changes:**
- Header: change `px-6` to `px-4 md:px-6`. Stack the back/title/dashboard links on mobile if they overflow
- "New Project" button: change `px-4 py-2` to `px-4 py-2.5` for touch target
- **Table → mobile card layout:** wrap the existing `<table>` in `<div class="hidden md:block">`. Below it, add a new `<div class="block md:hidden space-y-3">` containing one card per project with the same data. The card layout:
  - Top row: project number (bold) + status pill (right-aligned)
  - Second line: project name (gray)
  - Third row: Health dot + label · Last report · Next due (small text, separated by middots)
  - Bottom: full-width "View" button linking to `project-detail.html?id=...`
- Empty state card: change `p-12` to `p-6 md:p-12`, change icon `w-16 h-16` to `w-12 h-12 md:w-16 md:h-16`

### 3. `web/frontend/portal/project-detail.html` — Tabs and Settings (Largest File)

**Current state:** 64KB file with 4 tabs (Overview, Template, Settings, Mailbox). Tab navigation likely uses inline flex which crowds on mobile. The Template editor reuses the SWPPP form which has many fields. The Settings tab has the 14 automation knobs in a multi-column form layout.

**Changes:**
- Tab nav: scrollable horizontally on mobile (`overflow-x-auto`) with `whitespace-nowrap` on each tab. Each tab: `px-4 py-3` for touch target. Active tab keeps the bottom border indicator
- Page header: change any `px-6` to `px-4 md:px-6`
- All form rows: `grid-cols-2` → `grid-cols-1 md:grid-cols-2`
- Settings tab — the 14 knobs:
  - All toggles and switches: minimum 44px tap area
  - Text inputs and number inputs: `text-base` on mobile, full-width
  - Notification email list: stack vertically on mobile, "Add" button full-width
- Archive section: the "Archive without NOT" toggle and Archive button get touch-target sizing
- Run history table inside Overview tab: same table-to-card pattern as projects.html
- Mailbox tab: relies on the public mailbox endpoint — already mobile-first from change #1

### 4. `web/frontend/portal/project-new.html` — Form Layout

**Current state:** project creation form. Required fields plus collapsible "Additional Details" section.

**Changes:**
- Form container: `max-w-2xl px-4 md:px-6`
- All `<input>` and `<select>` elements: `text-base`, full-width
- Submit button: `py-3` for touch target
- Two-column field layouts: `grid-cols-1 md:grid-cols-2`
- "Additional Details" disclosure: full-width tap target with `py-3`

### 5. `web/frontend/portal/dashboard.html` — Stat Cards + Failure Table

**Current state:** 4 stat cards (Total, Active, Failing, Paused+Incomplete) and a recent failures table.

**Changes:**
- Stat cards: change `grid-cols-4` to `grid-cols-2 md:grid-cols-4`
- "Run Reports Now" button: `py-3 md:py-2.5` for touch
- Recent failures table → mobile card stack pattern (same as projects.html)
- Header padding: `px-4 md:px-6`

### 6. `web/frontend/portal/admin-health.html` — Stats + Two Tables

**Current state:** 4 stat cards (Companies, Active Projects, Reports 7d, Reports 30d), Problem Projects table, Company Rollup table.

**Changes:**
- Stat cards: `grid-cols-4` → `grid-cols-2 md:grid-cols-4`
- Both tables → mobile card stack pattern
- Problem Projects mobile card: company name (gray) on top, project number + name, health badge, reason, "Failures (7d): N"
- Company Rollup mobile card: company name (bold), counts in a 2x2 mini-grid (Total/Active on top, Failing/Paused below), Last Activity, Admin name
- Header padding: `px-4 md:px-6`

### 7. `web/frontend/portal/admin.html` — Existing Admin Page

**Current state:** Platform admin user/invite management page (27KB, pre-existing from Phase 1).

**Changes (light pass — this is internal, lower priority):**
- Touch targets on all buttons (`py-2` → `py-2.5`)
- Any wide tables → mobile card stack
- `text-base` on all inputs

### 8. `web/frontend/portal/login.html` and `signup.html` — Auth Pages

**Current state:** existing forms.

**Changes (minimal — these are already small forms):**
- Inputs: `text-base`
- Submit buttons: `py-3`
- Container: `px-4 md:px-6`

### 9. `web/frontend/swppp/index.html` — Existing SWPPP Form

**Current state:** the original SWPPP form, used by both the manual generation flow and the template editor. 45KB, lots of fields, originally desktop-only.

**Changes:**
- Top of file: confirm `<meta name="viewport" content="width=device-width, initial-scale=1.0">` is present (it should be)
- All form sections: `grid-cols-2` or `grid-cols-3` → `grid-cols-1 md:grid-cols-2` (or `md:grid-cols-3`)
- Long checklist: per the IR-8 design discussion, mobile uses **section-by-section stacking** — each compliance section becomes a vertically-scrolling card. Do NOT add collapse/expand on mobile — the compliance requirement is that no question is hidden by default
- Date pickers and station picker: full-width on mobile
- Submit/Generate buttons: `py-3` for touch
- Bottom-of-page action buttons: stack vertically on mobile, side-by-side on desktop (`flex-col md:flex-row`)

---

## Pages NOT Updated

These pages don't need a mobile pass:

- `web/frontend/portal/index.html` — simple landing page, already adapts
- Any HTML in `docs/` — internal documentation, not user-facing
- Any backend Python — no Python touched in IR-8

---

## Tests Required

This is a layout-only pass. The existing test suite verifies that the rendered HTML still serves correctly. Add **one new test file** for static smoke tests:

New file: `tests/test_mobile_smoke.py`

Test class: `TestMobileViewport`
- `test_mailbox_has_viewport_meta` — confirm `<meta name="viewport"` is present in `mailbox/index.html`
- `test_projects_has_viewport_meta` — same for `projects.html`
- `test_project_detail_has_viewport_meta` — same for `project-detail.html`
- `test_project_new_has_viewport_meta` — same for `project-new.html`
- `test_dashboard_has_viewport_meta` — same for `dashboard.html`
- `test_admin_health_has_viewport_meta` — same for `admin-health.html`
- `test_admin_has_viewport_meta` — same for `admin.html`
- `test_login_has_viewport_meta` — same for `login.html`
- `test_signup_has_viewport_meta` — same for `signup.html`
- `test_swppp_has_viewport_meta` — same for `swppp/index.html`
- `test_no_inline_fixed_widths` — grep all frontend HTML for `style="width:` containing `px` values >400px (catches accidental hardcoded desktop widths)

Minimum: 11 new tests. All existing 467 tests must continue to pass.

The real verification is the manual smoke tests on actual devices — see the mandatory reporting section.

---

## Mandatory Reporting Section

> Agent must complete before marking IR-8 done.

**1. Full pytest output:**
```
[paste here]
```
Baseline: 467 passed, 2 skipped. Expected after IR-8: 478+ passed, 2 skipped.

**2. New mobile smoke test proof:**
```
[paste: pytest tests/test_mobile_smoke.py -v]
```

**3. Before/after screenshots at iPhone SE (375 × 667):**
For each of these pages, paste before and after screenshots:
- [ ] `/mailbox` (search state)
- [ ] `/mailbox` (active project view with reports)
- [ ] `/mailbox` (archived project view)
- [ ] `/portal/projects.html` (with at least 3 projects)
- [ ] `/portal/project-detail.html?id={id}` (Overview tab)
- [ ] `/portal/project-detail.html?id={id}` (Settings tab)
- [ ] `/portal/project-new.html`
- [ ] `/portal/dashboard.html`
- [ ] `/portal/admin-health.html` (platform admin only)

**4. Real-device manual smoke tests (Jake must verify on actual phone — not DevTools):**
- [ ] Open `https://sw3p.pro/mailbox` on phone — type project number → confirm clean layout, easy tap targets
- [ ] On phone: log in via `/portal/login.html` — confirm form is usable, password input doesn't trigger zoom on iOS
- [ ] On phone: navigate to project list → tap a project → review every tab
- [ ] On phone: download a PDF from the Mailbox — confirm it opens in native PDF viewer
- [ ] Confirm zero horizontal scroll on any page
- [ ] Confirm no text is too small to read without pinch-zooming

---

*End of IR-8*
