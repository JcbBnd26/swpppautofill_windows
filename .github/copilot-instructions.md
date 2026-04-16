# Copilot Instructions — SWPPP AutoFill

## Project Overview

This is a Windows-first Python desktop app that generates weekly ODOT clean-water (SWPPP) inspection PDFs from a fillable AcroForm template (`assets/template.pdf`). It has a Tkinter GUI for daily use, a Typer CLI for scripted runs, inspection helpers for PDF field analysis, and a web frontend + API layer (`web/`) for remote access.

## Tech Stack & Constraints

- **Python 3.10+**, Windows only
- **Key deps:** pypdf, typer, pydantic, pyyaml, python-dateutil, requests
- **GUI framework:** Tkinter (no Qt, no web UI)
- **PDF handling:** pypdf — the template is a real AcroForm with named text fields and many unnamed button/checkbox fields (e.g. `undefined`, `undefined_2`, …)
- **Entry points:** `swppp` (CLI), `swppp-gui` (GUI), `swppp-inspect` (inspection tool)
- **Virtual env:** `.venv` in the project root; activate with `.venv\Scripts\Activate.ps1`

## Project Structure

- `app/core/` — business logic: PDF filling (`fill.py`), data model (`model.py`), date helpers (`dates.py`), checkbox/field mapping (`pdf_fields.py`), config loading (`config_manager.py`), Mesonet rain data (`mesonet.py`, `mesonet_stations.py`, `rain_fill.py`)
- `app/ui_gui/` — Tkinter GUI (`main.py`)
- `app/ui_cli/` — Typer CLI (`main.py`)
- `app/tools/` — PDF inspection utility (`inspect.py`)
- `assets/` — the fillable PDF template
- `tests/` — pytest test suite
- `tmp_output/` — scratch/output directory
- `web/auth/` — FastAPI auth server (login, sessions, admin)
- `web/swppp_api/` — FastAPI SWPPP API server (form schema, stations, PDF generation)
- `web/frontend/` — Alpine.js + Tailwind CSS web UI (static HTML served by nginx in prod)
- `web/scripts/` — deploy scripts, nginx config, systemd units

## Environments

- **Two UIs:** Tkinter desktop (`app/ui_gui/`) and web frontend (`web/frontend/`). When a user reports a bug, confirm which interface they're looking at.
- **Two API servers:** auth on `:8001` (`web/auth/main.py`) and SWPPP on `:8002` (`web/swppp_api/main.py`).
- **Two databases — completely separate, share no data:** local dev uses `web/data/auth.db`; production uses `/opt/tools/data/auth.db` (via `TOOLS_DATA_DIR` env var). Invite codes, sessions, and users generated locally do not exist in production.
- **Dev mode:** `$env:TOOLS_DEV_MODE="1"` — single server on `:8001`, SWPPP sub-app mounted via `app.mount("", _swppp_app)`. HTML served by FastAPI.
- **Production:** nginx on `sw3p.pro:443` serves static HTML, proxies `/auth/*` → `:8001`, `/swppp/api/*` → `:8002`. Both services share the same `auth.db` for session validation.
- **SSH:** `ssh -i ~/.ssh/swppp-vps-deploy root@143.110.229.161`

## Coding Style

- Keep it simple and direct — this is a small desktop tool, not an enterprise app.
- Use `from __future__ import annotations` at the top of every module.
- Use Pydantic models for structured data (see `app/core/model.py`).
- YAML for config; avoid JSON configs where YAML already exists.
- Prefer small, focused functions. No deep class hierarchies.
- Type hints on function signatures. No unnecessary docstrings — only comment non-obvious logic.
- Use `pathlib.Path` — never `os.path`.
- Import order: standard library → third-party → local, separated by blank lines.
- `snake_case` for functions/variables/modules, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants.
- One logger per module: `log = logging.getLogger(__name__)`. Use lazy formatting (`log.info("Found %d rows", n)`) — never f-strings in log calls.
- If a file grows past ~500 lines and is doing multiple jobs, split it. If it's one genuinely complex job, leave it alone.
- Use specific exception types, not bare `except Exception`. Log exceptions before re-raising.

## Testing

- Run tests with: `pytest` (from project root with venv active)
- Tests live in `tests/` and cover fill logic, model validation, checkbox mapping, and template integration.
- When changing core logic, verify existing tests still pass before considering the work done.

## Key Gotchas

- Many checkbox fields in the PDF template are unnamed (`undefined`, `undefined_2`, …). The checkbox mapping is auto-derived at runtime from the template's button layout — see `pdf_fields.py`.
- The Mesonet rain data feature (`mesonet.py`) chunks large date ranges into ≤99-day requests to avoid the email-only path.
- Missing Mesonet data is indicated by values < -990 — treat these as missing, never as zero.
- Always use `pypdf` (not PyPDF2 or other forks).
- Local and production databases are completely independent (`web/data/auth.db` vs `/opt/tools/data/auth.db`). Never assume data created locally exists in production.

## When Making Changes

- Prefer editing existing files over creating new ones.
- Don't refactor surrounding code unless asked.
- After any code change, run `pytest` to confirm nothing broke.
- PowerShell is the terminal — use `;` to chain commands, not `&&`.
- When a tool or approach fails three times in a row, stop and switch strategies — don't try a fourth variation of the same broken approach.

## Debugging & Production Safety

- **Check data before infrastructure.** When something fails, the first diagnostic should always be: does the expected data exist? Is the row in the database? Is the file on disk? Is the env var set? Only after confirming the data layer is correct should you move to network, middleware, or service-level investigation. Run `SELECT` queries and read logs before theorizing about CORS, CSRF, or middleware.
- **Confirm the target before modifying.** Before making any change, explicitly state which environment (local vs. production) and which component (Tkinter vs. web, auth vs. SWPPP API) is being modified. When the user reports a bug, ask which interface they're using if it's ambiguous.
- **Never mutate production state during diagnostics.** All production investigation must be read-only: `SELECT` queries, `GET`/`curl` requests, log reads. No `POST`, `UPDATE`, `DELETE`, or claiming invite codes during debugging — unless the user explicitly authorizes it.
- **Errors are evidence — never discard them.** Any `catch` or `except` block that hides the original error message is a bug, not a convenience. When writing error handlers, always preserve and surface the original error. When debugging, if the user reports a generic error message, suspect the error handler before suspecting the infrastructure.

## Production Deployment Protocol

Use the following rules when the user wants to deploy to production or asks the agent to guide or execute a production rollout.

- **Start in read-only mode.** Do not mutate production until the user explicitly approves the deploy. Use the exact approval gate `APPROVE_PROD_DEPLOY` unless the user provides a different token for the session.
- **Do not auto-rollback.** If a deploy step fails after production has been mutated, stop, collect evidence, and wait for explicit rollback approval. Use the exact approval gate `APPROVE_PROD_ROLLBACK` unless the user provides a different token for the session.
- **Never copy repo database files into production data.** Production databases live in `/opt/tools/data`, not in `/opt/tools/repo/web/data`. Never overwrite `/opt/tools/data/*.db` with files from the repo.
- **Never run write SQL during diagnostics.** For production database checks, use read-only inspection such as `PRAGMA table_info(...)` and `SELECT` queries only, unless the user explicitly approves a deploy or rollback step that requires mutation.
- **Do not use the full provision script for normal rollouts.** For code deploys, do not run `/opt/tools/repo/web/scripts/deploy.sh`. Prefer `git pull --ff-only` plus targeted service restarts.
- **Do not use destructive git recovery commands without approval.** Do not run `git reset --hard`, force pulls, or checkout older commits unless rollback has been explicitly approved.
- **Stop on a dirty production worktree.** If `git status --short` on the production server shows unexpected local changes, stop before deploy and report the exact files.
- **Treat logs and command failures as evidence.** Preserve exact stderr/stdout from failed commands and include it in the report back to the user.

### Production Preflight

Before any production deploy, gather this read-only evidence first:

- `ssh -i ~/.ssh/swppp-vps-deploy root@143.110.229.161`
- `cd /opt/tools/repo`
- `git status --short`
- `systemctl status tools-auth tools-swppp --no-pager`
- `journalctl -u tools-auth --since "1 hour ago" --no-pager | tail -40`
- `journalctl -u tools-swppp --since "1 hour ago" --no-pager | tail -40`
- `sqlite3 /opt/tools/data/auth.db "PRAGMA table_info(users);"`

After preflight, report a compact status summary covering:

- target confirmed
- repo clean or dirty
- auth service health
- SWPPP service health
- whether `password_hash` exists on `users`
- blockers, if any
- recommended next action: `DEPLOY` or `STOP`

If blockers exist, recommend `STOP` and wait for user input.

### Approved Deploy Sequence

Only after explicit approval, perform production mutation in this order:

1. Back up `/opt/tools/data/auth.db`
2. Back up `/opt/tools/data/swppp_sessions.db`
3. `cd /opt/tools/repo`
4. `git pull --ff-only`
5. `systemctl restart tools-auth tools-swppp`

Run one step at a time and inspect the result before continuing. If any step fails, stop immediately and report the failure.

After deploy, report:

- backup status
- pull status
- auth restart status
- SWPPP restart status
- created backup filenames
- current `HEAD` commit
- next action: `VERIFY` or `STOP`

If a failure occurred after production was mutated, say whether rollback is recommended and why, then wait for explicit rollback approval.

### Post-Deploy Verification

After a successful deploy, verify with:

- `systemctl status tools-auth tools-swppp --no-pager`
- `journalctl -u tools-auth --since "5 min ago" --no-pager | tail -80`
- `journalctl -u tools-swppp --since "5 min ago" --no-pager | tail -80`
- `sqlite3 /opt/tools/data/auth.db "PRAGMA table_info(users);"`
- `curl -I https://sw3p.pro/auth/login`
- `curl -I https://sw3p.pro/`

Do not assume the migration failed just because no migration log line appears. If `password_hash` already existed, the migration is correctly a no-op. The authoritative schema check is `PRAGMA table_info(users)`.

If browser access is unavailable, mark these as manual smoke tests rather than claiming they were completed:

- logged-out visit to `https://sw3p.pro/auth/login`
- logged-in visit to `https://sw3p.pro/auth/login` redirects to `/`
- valid invite link with `?code=` claims and redirects cleanly
- password login succeeds and session persists after redirect

### Rollback Protocol

Only after explicit rollback approval:

- identify the backup filenames created in this deploy session
- identify the last known good commit with `git log --oneline -5`
- restore the database backup only if the failure requires DB restore
- check out the approved previous commit
- restart `tools-auth` and `tools-swppp`
- rerun the same verification checks used post-deploy

Do not invent a rollback target. If the correct commit is ambiguous, stop and ask the user to confirm it.

### Required Reporting Style

- Be concise and operational.
- Do not skip approval gates.
- Do not continue after a failed required step.
- Do not claim success without evidence.
- When production browser tests cannot be run from the current environment, explicitly mark them as manual follow-up.
