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
