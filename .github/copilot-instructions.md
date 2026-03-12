# Copilot Instructions — SWPPP AutoFill

## Project Overview

This is a Windows-first Python desktop app that generates weekly ODOT clean-water (SWPPP) inspection PDFs from a fillable AcroForm template (`assets/template.pdf`). It has a Tkinter GUI for daily use, a Typer CLI for scripted runs, and inspection helpers for PDF field analysis.

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

## When Making Changes

- Prefer editing existing files over creating new ones.
- Don't refactor surrounding code unless asked.
- After any code change, run `pytest` to confirm nothing broke.
- PowerShell is the terminal — use `;` to chain commands, not `&&`.
