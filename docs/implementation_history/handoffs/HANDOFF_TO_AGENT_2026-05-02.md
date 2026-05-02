# Handoff to Coding Agent — 2026-05-02

Claude is out of tokens. This document gives you everything needed to continue without interruption.

---

## Current State

| Item | Status |
|---|---|
| Phase 1 (hardening Tiers 1–6) | ✅ Complete |
| Pre-flight (viewer role removal) | ✅ Complete |
| IR-1 (Projects table + API) | ✅ Complete — 316 tests |
| IR-2 (Template versioning + UI) | ✅ Complete — 341 tests |
| IR-3 (Public Mailbox) | ✅ Complete — 364 tests |
| IR-4 (Scheduler) | ⏳ Your next task |

---

## Your Next Task — IR-4

Read these documents in order before writing a single line of code:

1. `docs/implementation_history/IR-4_Scheduler_v1.0_2026-05-02.md` — the full spec
2. `IR-4 Amendment — Agent Gotchas` (below in this document) — corrections to the spec

Then verify pre-work:
- IR-3 deployed to `sw3p.pro` and smoke tests passed
- `python -m pytest tests/ -q` shows 364 passing

---

## IR-4 Amendment — Read Before Coding

### 1. `connect()` already exists
`web/auth/db.py` already exports `connect()`. Use it directly in `web/scheduler/main.py`. Do not redefine it.

### 2. `dates.py` weekly_dates() does not filter by weekday
Compute scheduled weekly dates independently in the scheduler:
```python
def get_scheduled_weekly_dates(start_date, end_date, day_of_week):
    dates = []
    current = start_date
    while current <= end_date:
        if current.weekday() == day_of_week:
            dates.append(current)
        current += timedelta(days=1)
    return dates
```

### 3. Use existing `get_mailbox_entries()` from IR-3
Do not create a duplicate. Call `get_mailbox_entries(conn, project_id)` directly.

### 4. Systemd service file — match existing services exactly
Read `web/scripts/systemd/tools-auth.service` before writing the scheduler service. Match its `User=`, `WorkingDirectory=`, `ExecStart=` path, and `EnvironmentFile=` exactly. The IR-4 sample used wrong values (`User=root`, wrong venv path).

### 5. Mesonet — always mocked in tests
Never real HTTP in tests. Mock at `app.core.mesonet.fetch_rain_data`.

### 6. `generation_mode='retroactive'` needs no schema change
`mailbox_entries.generation_mode` is unconstrained TEXT. Just write the value.

---

## IR Sequence After IR-4

| IR | Scope |
|---|---|
| IR-5 | PM dashboard + health view |
| IR-6 | Platform admin health dashboard |
| IR-7 | Archive flow — NOT upload, archive ZIP, DigitalOcean Spaces |
| IR-8 | Mobile responsiveness pass |

Full specs for IR-5 through IR-8 have not been written yet. When IR-4 is complete, paste results into the next Claude session and ask for IR-5.

---

## Key Facts

- **PowerShell terminal** — chain commands with `;` not `&&`
- **Production SSH:** `ssh -i ~/.ssh/swppp-vps-deploy root@143.110.229.161`
- **Production repo:** `/opt/tools/repo`
- **Production data:** `/opt/tools/data/` — never copy repo DB files here
- **Deploy:** `git pull --ff-only` + `systemctl restart tools-auth tools-swppp`
- **Approval gates:** `APPROVE_PROD_DEPLOY` before any production mutation
- **Never use `deploy.sh`** for normal rollouts
- **Two databases:** local `web/data/auth.db` / production `/opt/tools/data/auth.db` — completely separate

## Product Principles

1. Annoyance not complexity
2. Contractor-facing tool — ODOT consumes outputs, never a user
3. The app replaces paperwork, not inspections
4. Design for how regulation is actually enforced
5. Don't lock out the future ODOT-side product

---

*Claude will be back. When IR-4 is done, paste the test results into the next session.*
