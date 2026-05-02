# sw3p.pro Tools Platform — Hardening Tier 5 Implementation Record

**Document purpose:** Agent-facing specification for seven reliability and observability improvements; consumed by the VS Code + GitHub Copilot coding agent.
**Date range:** TBD — to be filled on completion.
**Source specification:** R&O audit of post-Tier-4 codebase. Eight findings; seven addressed here; one deferred.
**Starting state:** Both services running; logging infrastructure present but likely silenced in production; no health endpoints; exception tracebacks lost at API boundary.
**Final state:** Logging configured and active; full tracebacks on all errors; startup validation; health endpoints on both services; partial failure logging on Mesonet fetches.

---

## 0. Summary Card

| Field | Value |
|-------|-------|
| **Project name** | `sw3p.pro Tools Platform` |
| **Date range** | TBD |
| **Source specification** | Post-Tier-4 R&O audit — 8 findings, 7 addressed |
| **Starting state** | Logging silenced in prod; no health endpoints; tracebacks lost on errors |
| **Final state** | All 7 items resolved; observability functional in production |
| **Total files created** | 0 |
| **Total files modified** | 4 (`web/auth/main.py`, `web/swppp_api/main.py`, `app/core/mesonet.py`, `web/scripts/systemd/tools-auth.service` + `tools-swppp.service`) |
| **Total lines added** | ~80 (estimate) |
| **Lines modified in pre-existing code** | ~15 (estimate) |
| **Net new dependencies** | 0 |
| **Known limitations carried forward** | 1 — see §Appendix B |
| **Open bugs** | 0 at time of authoring |

---

## Table of Contents

```
1.  Pre-Implementation Baseline
2.  Dependency Manifest
3.  Environment & Configuration Reference
5A. Fix: Logging Level Not Configured
5B. Fix: Exception Tracebacks Lost at API Boundary
5C. Fix: No Startup Validation of Critical Files
5D. Fix: No Health Check Endpoints
5E. Fix: Session DB Errors Not Logged
5F. Fix: No Summary Log on Partial Mesonet Failure
5G. Fix: Latent None Risk in Mesonet Retry Loop
5.  Architecture Overview
6.  API Endpoint Inventory
7.  API Request/Response Examples
8.  Security Posture Summary
9.  Data & Storage
10. Deployment
11. Test Suite Inventory
12. Performance Baseline
13. Change Delta Summary
14. User-Facing Behavior
Appendix A: Issue & Fix Registry
Appendix B: Known Limitations & Future Work
```

---

## 1. Pre-Implementation Baseline

### 1a. Code Inventory

| Component | Path | Purpose | Will Be Modified? |
|-----------|------|---------|-------------------|
| `main.py` (auth) | `web/auth/main.py` | Auth service: routes, middleware, lifespan | **Yes** — logging config, health endpoint |
| `main.py` (swppp) | `web/swppp_api/main.py` | SWPPP API: routes, middleware, lifespan | **Yes** — logging config, startup validation, health endpoint, error logging, session error wrapping |
| `mesonet.py` | `app/core/mesonet.py` | Mesonet HTTP client and CSV parser | **Yes** — retry loop fix, partial failure log |
| `tools-auth.service` | `web/scripts/systemd/tools-auth.service` | systemd unit for auth service | **Yes** — add `TOOLS_LOG_LEVEL` env var |
| `tools-swppp.service` | `web/scripts/systemd/tools-swppp.service` | systemd unit for SWPPP service | **Yes** — add `TOOLS_LOG_LEVEL` env var |
| `db.py` (auth) | `web/auth/db.py` | Auth database logic | No |
| `db.py` (swppp) | `web/swppp_api/db.py` | SWPPP session database logic | No |
| `middleware.py` | `web/middleware.py` | Shared CSRF middleware factory | No |

### 1b. Test Inventory

| File | Tests | Coverage Area |
|------|-------|---------------|
| `tests/test_auth.py` | ~120 (post-Tier-4) | Auth service end-to-end |
| `tests/test_swppp_api.py` | 57 | SWPPP API endpoints |
| `tests/test_mesonet.py` | 13 | Mesonet fetch and CSV parsing |
| **Total (relevant)** | **~190** | |

### 1c. Design Constraints

```
- No new Python package dependencies.
  Source: Deployment simplicity; all fixes use stdlib logging only.

- Log level must be configurable via environment variable.
  Source: Dev mode needs DEBUG; production needs INFO; the code
  must not hardcode either.

- Health endpoints must not require authentication.
  Source: They are called by monitoring tools and systemd watchdogs,
  not by users. Gating them behind a session would defeat their purpose.

- Health endpoints must check real connectivity, not just "process is alive."
  Source: A running process with a missing database file is not healthy.

- No changes to SWPPP PDF logic, auth session logic, or test structure.
  Source: Tier 5 scope is observability layer only.

- All changes to the session CRUD error handling must preserve existing behavior
  on the happy path — only the error path is instrumented.
  Source: No regressions in existing 57 SWPPP API tests.
```

### 1d. Constraint Compliance Statement

> To be completed by agent after implementation.

| # | Constraint | Honored? | Evidence / Notes |
|---|-----------|----------|------------------|
| 1 | No new Python dependencies | TBD | |
| 2 | Log level via env var | TBD | |
| 3 | Health endpoints unauthenticated | TBD | |
| 4 | Health checks real connectivity | TBD | |
| 5 | No changes outside observability layer | TBD | |
| 6 | No regressions in existing tests | TBD | |

---

## 2. Dependency Manifest

### 2a. Runtime Dependencies

No new dependencies. All changes use Python's stdlib `logging` module.

### 2b. Dev / Test Dependencies

No changes.

### 2c. Full Dependency Snapshot

See `pyproject.toml`. No changes in Tier 5.

---

## 3. Environment & Configuration Reference

### 3a. Environment Variables

One new variable added. All others unchanged.

| Variable | Purpose | Default | Required in Prod? | Failure Mode if Missing |
|----------|---------|---------|-------------------|------------------------|
| `TOOLS_LOG_LEVEL` | Python root logger level | `INFO` | Recommended | Defaults to INFO — acceptable but invisible |
| `TOOLS_DEV_MODE` | Disables CSRF + secure cookie | `0` | Yes | See Tier 4 |
| `TOOLS_BASE_URL` | CSRF origin check | `http://localhost:8001` | Yes | See Tier 4 |
| `TOOLS_DATA_DIR` | Database storage path | `web/data/` | Yes | See Tier 4 |

### 3b. Configuration Files

| File | Format | Purpose | Read By | Who Edits It |
|------|--------|---------|---------|-------------|
| `tools-auth.service` | systemd unit | Service runtime config including log level | systemd | Dev (deployed via script) |
| `tools-swppp.service` | systemd unit | Service runtime config including log level | systemd | Dev (deployed via script) |

---

## 5A. Fix: Logging Level Not Configured

### Scope

Python's root logger defaults to `WARNING`. Every `log.info()` call in `web/auth/main.py`, `web/auth/db.py`, and elsewhere produces no output in production because no code explicitly sets the log level. This fix configures logging at service startup using a `TOOLS_LOG_LEVEL` environment variable, with a sensible default of `INFO`. The format is set to include timestamp, level, logger name, and message — giving each log line enough context to be useful without being verbose.

> **Why this matters architecturally:** You have invested effort in writing meaningful log calls throughout the codebase — invite claims, login events, admin actions, migration completions. None of that is currently visible in production. This fix is the switch that turns the lights on.

### Files Modified

| File | Created / Modified | Purpose |
|------|--------------------|---------|
| `web/auth/main.py` | Modified | Add logging configuration at module level |
| `web/swppp_api/main.py` | Modified | Add logging configuration at module level |
| `web/scripts/systemd/tools-auth.service` | Modified | Add `TOOLS_LOG_LEVEL=INFO` env var |
| `web/scripts/systemd/tools-swppp.service` | Modified | Add `TOOLS_LOG_LEVEL=INFO` env var |

### Exact Code Change — Both `main.py` files

**Add near the top of each file, after the existing `import os` and before the first `log = logging.getLogger(...)` call:**

```python
# ── Logging configuration ─────────────────────────────────────────────
# Must be called before any logger is used. Reads TOOLS_LOG_LEVEL from
# the environment so dev (DEBUG) and prod (INFO) can differ without
# code changes. basicConfig() is a no-op if the root logger already has
# handlers — safe to call in both services.

_LOG_LEVEL = os.environ.get("TOOLS_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
```

> **Placement note:** This block must appear *before* any `log = logging.getLogger(__name__)` call at module level. Python's `basicConfig()` configures the root logger; all named loggers in the module hierarchy inherit from it. The `%(name)s` field in the format string tells you which module emitted each line — essential when logs from multiple modules interleave.

### Exact Config Change — systemd service files

**`tools-auth.service` — add one line to the `[Service]` block:**

**BEFORE:**
```ini
Environment=TOOLS_DEV_MODE=0
Environment=TOOLS_BASE_URL=https://sw3p.pro
```

**AFTER:**
```ini
Environment=TOOLS_DEV_MODE=0
Environment=TOOLS_BASE_URL=https://sw3p.pro
Environment=TOOLS_LOG_LEVEL=INFO
```

Apply the identical change to `tools-swppp.service`.

### Architectural Decision

**Decision: Use `logging.basicConfig()` at module level, not inside `lifespan()`.**
- **What:** Logging is configured when the module is imported, not when the app starts serving.
- **Rationale:** Gunicorn imports modules before the lifespan hook runs. If any log call fires during module import (e.g., in `db.init_db()` called from lifespan), it would still use the unconfigured root logger. Module-level configuration guarantees logs are captured from the very first line.
- **Tradeoff:** Slightly less explicit than lifespan configuration, but more correct.
- **Consequences:** None. `basicConfig()` is idempotent — if Gunicorn or the test framework has already configured logging, this call is a no-op.

### Acceptance Tests

Logging configuration is infrastructure, not testable in the unit test suite. Acceptance criteria is manual:

```
1. Deploy updated service files.
2. sudo systemctl daemon-reload
3. sudo systemctl restart tools-auth tools-swppp
4. Make a login request via the browser.
5. Run: journalctl -u tools-auth -n 20
   Expected: Lines like "2026-04-21T... INFO     web.auth.main: Password login: user_id=..."
6. Confirm TOOLS_LOG_LEVEL=DEBUG produces debug output:
   sudo systemctl edit tools-auth --force
   (temporarily set TOOLS_LOG_LEVEL=DEBUG, restart, check for debug lines, revert)
```

---

## 5B. Fix: Exception Tracebacks Lost at API Boundary

### Scope

Every `log.error()` call inside an `except` block in `web/swppp_api/main.py` logs only the exception *message*, discarding the *traceback*. In production, a PDF generation failure would appear as `"PDF generation failed: list index out of range"` with no indication of which file, which line, or what call chain led there. The fix adds `exc_info=True` to each of these four calls — a single keyword argument that instructs Python's logging system to attach the full traceback to the log record.

> **Why this matters:** A traceback is the difference between "something failed" and "line 94 of fill.py, inside `_write_filled_pdf`, when calling `PdfWriter.clone_document_from_reader`." The first is noise; the second is diagnosis.

### Files Modified

| File | Created / Modified | Purpose |
|------|--------------------|---------|
| `web/swppp_api/main.py` | Modified | Add `exc_info=True` to four error log calls |

### Exact Code Changes — `web/swppp_api/main.py`

There are four sites. Apply the same change to each: add `, exc_info=True` as the final argument.

**Site 1 — rain data fetch:**

BEFORE:
```python
    except Exception as exc:
        log.error("Rain data fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail="Rain data fetch failed")
```
AFTER:
```python
    except Exception as exc:
        log.error("Rain data fetch failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Rain data fetch failed")
```

**Site 2 — PDF batch generation:**

BEFORE:
```python
    except Exception as exc:
        log.error("PDF generation failed: %s", exc)
        raise HTTPException(status_code=500, detail="PDF generation failed")
```
AFTER:
```python
    except Exception as exc:
        log.error("PDF generation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="PDF generation failed")
```

**Site 3 — rain PDF generation:**

BEFORE:
```python
        except Exception as exc:
            log.error("Rain PDF generation failed: %s", exc)
            raise HTTPException(status_code=500, detail="Rain PDF generation failed")
```
AFTER:
```python
        except Exception as exc:
            log.error("Rain PDF generation failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail="Rain PDF generation failed")
```

**Site 4 — ZIP bundling:**

BEFORE:
```python
    except Exception as exc:
        log.error("ZIP bundling failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create ZIP bundle")
```
AFTER:
```python
    except Exception as exc:
        log.error("ZIP bundling failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create ZIP bundle")
```

### Acceptance Tests

Traceback logging is tested implicitly by 5A's smoke test. The specific acceptance criterion:

```
1. With 5A deployed (logging active), trigger a generation error deliberately:
   e.g., temporarily rename assets/template.pdf to assets/template.pdf.bak
2. Attempt PDF generation via the UI.
3. Run: journalctl -u tools-swppp -n 30
   Expected: Full Python traceback visible in the log output, not just the message.
4. Restore the template file.
```

---

## 5C. Fix: No Startup Validation of Critical Files

### Scope

The SWPPP service requires two files at runtime: `assets/template.pdf` (the PDF template) and `app/core/odot_mapping.yaml` (the field mapping). These paths are resolved at module import time, but their existence is never checked until an inspector actually submits a generate request — which could be hours or days after deployment. A misconfigured deployment silently runs a broken service. This fix adds an explicit check in the `lifespan()` startup hook that raises a `RuntimeError` immediately if either file is missing, failing the service before it accepts any requests.

> **Why this matters architecturally:** "Fail fast" is a reliability principle. A service that refuses to start with a clear error message is infinitely easier to debug than one that starts successfully and fails mysteriously later. This also surfaces deployment mistakes (wrong working directory, missing `assets/` folder) at the exact moment they happen rather than when a user is affected.

### Files Modified

| File | Created / Modified | Purpose |
|------|--------------------|---------|
| `web/swppp_api/main.py` | Modified | Add startup validation in `lifespan()` |

### Exact Code Change — `web/swppp_api/main.py`

**BEFORE:**
```python
@asynccontextmanager
async def lifespan(application: FastAPI):
    session_db.init_db()
    yield
```

**AFTER:**
```python
@asynccontextmanager
async def lifespan(application: FastAPI):
    # Validate critical files exist before accepting requests.
    # Fail fast with a clear message rather than failing at request time.
    _required = {
        "PDF template": TEMPLATE_PDF,
        "ODOT mapping YAML": MAPPING_YAML,
    }
    missing = [f"{label}: {path}" for label, path in _required.items() if not path.exists()]
    if missing:
        raise RuntimeError(
            "SWPPP service cannot start — required files missing:\n"
            + "\n".join(f"  {m}" for m in missing)
        )
    log.info("Startup check passed: template=%s mapping=%s", TEMPLATE_PDF, MAPPING_YAML)

    session_db.init_db()
    yield
```

### Acceptance Tests — added to `TestGenerate` class or new class `TestStartupValidation`

```python
class TestStartupValidation:
    """Verify service refuses to start if critical files are missing."""

    def test_lifespan_raises_if_template_missing(self, tmp_path, monkeypatch):
        """lifespan() must raise RuntimeError when TEMPLATE_PDF does not exist."""
        import web.swppp_api.main as swppp_main
        import asyncio

        monkeypatch.setattr(swppp_main, "TEMPLATE_PDF", tmp_path / "nonexistent.pdf")

        async def _run():
            async with swppp_main.lifespan(swppp_main.app):
                pass

        with pytest.raises(RuntimeError, match="required files missing"):
            asyncio.get_event_loop().run_until_complete(_run())

    def test_lifespan_raises_if_mapping_missing(self, tmp_path, monkeypatch):
        """lifespan() must raise RuntimeError when MAPPING_YAML does not exist."""
        import web.swppp_api.main as swppp_main
        import asyncio

        monkeypatch.setattr(swppp_main, "MAPPING_YAML", tmp_path / "nonexistent.yaml")

        async def _run():
            async with swppp_main.lifespan(swppp_main.app):
                pass

        with pytest.raises(RuntimeError, match="required files missing"):
            asyncio.get_event_loop().run_until_complete(_run())
```

---

## 5D. Fix: No Health Check Endpoints

### Scope

Neither service exposes a health check endpoint. This means there is no way to programmatically verify that a service is not just running but *functional* — connected to its database, critical files present — without making a real authenticated request. This fix adds `GET /health` to both services. The auth health endpoint checks that the database is reachable. The SWPPP health endpoint checks both its own database and that `TEMPLATE_PDF` and `MAPPING_YAML` exist. Both endpoints are unauthenticated and return a JSON body with a status field and a timestamp.

### Files Modified

| File | Created / Modified | Purpose |
|------|--------------------|---------|
| `web/auth/main.py` | Modified | Add `GET /health` endpoint |
| `web/swppp_api/main.py` | Modified | Add `GET /health` endpoint |

### Exact Code Change — `web/auth/main.py`

Add after the existing `# ── Public Endpoints ──` section, as the first route:

```python
@app.get("/health")
def health_check(conn: sqlite3.Connection = Depends(db.get_db)):
    """Unauthenticated health check. Verifies DB connectivity.
    Returns 200 if healthy, 503 if the database is unreachable.
    """
    try:
        conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        log.error("Health check: DB connectivity failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=f"Database unreachable: {exc}",
        )
    return {
        "status": "ok",
        "service": "tools-auth",
        "db": str(db.DB_PATH),
        "timestamp": db._now(),
    }
```

### Exact Code Change — `web/swppp_api/main.py`

Add after the `csrf_origin_check` middleware registration, as the first route:

```python
@app.get("/swppp/api/health")
def health_check():
    """Unauthenticated health check. Verifies DB connectivity and critical files.
    Returns 200 if healthy, 503 if any check fails.
    """
    issues: list[str] = []

    # Check critical files
    for label, path in [("template", TEMPLATE_PDF), ("mapping", MAPPING_YAML)]:
        if not path.exists():
            issues.append(f"{label} file missing: {path}")

    # Check DB connectivity
    try:
        with session_db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        issues.append(f"DB unreachable: {exc}")

    if issues:
        log.error("Health check failed: %s", "; ".join(issues))
        raise HTTPException(
            status_code=503,
            detail={"status": "unhealthy", "issues": issues},
        )

    return {
        "status": "ok",
        "service": "tools-swppp",
        "db": str(session_db.DB_PATH),
        "timestamp": session_db._now(),
    }
```

### Architectural Decision

**Decision: Health endpoints return 503 on failure, not 500.**
- **What:** When a dependency (DB, file) is unavailable, the endpoint returns HTTP 503 Service Unavailable.
- **Rationale:** 503 has a specific semantic meaning: "the server is currently unable to handle the request." Monitoring tools, load balancers, and systemd health checks all understand 503 as "this service is down." 500 means "an unexpected error occurred" — less precise and potentially confused with application bugs.
- **Tradeoff:** None. 503 is unambiguously correct here.
- **Consequences:** Any future monitoring integration can use standard 503 detection without special configuration.

### Acceptance Tests — `TestHealthEndpoint` in each test file

```python
class TestHealthEndpoint:
    """Verify health endpoint returns correct status."""

    def test_health_returns_200_when_healthy(self, client):
        """GET /health must return 200 with status=ok when DB is available."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "tools-auth"
        assert "timestamp" in data

    def test_health_is_unauthenticated(self, client):
        """GET /health must not require a session cookie."""
        # Call with no cookies set — must still return 200
        response = client.get("/health", cookies={})
        assert response.status_code == 200
```

A parallel `TestSwpppHealthEndpoint` class goes in `tests/test_swppp_api.py` covering `GET /swppp/api/health`, including a test that monkeypatches `TEMPLATE_PDF` to a nonexistent path and verifies a 503 is returned.

---

## 5E. Fix: Session DB Errors Not Logged

### Scope

The SWPPP session CRUD endpoints (`list_sessions`, `get_session`, `save_session`, `delete_session`, `export_session`) call `with session_db.connect()` directly with no error handling at the route level. If SQLite throws — disk full, locked database, permissions error, corrupt file — FastAPI catches the exception and returns a generic 500 response. No log entry is written. An inspector's work could be lost with no trace in the logs of what happened or why. This fix wraps each session CRUD route in a try/except that logs the error with a full traceback before re-raising.

### Files Modified

| File | Created / Modified | Purpose |
|------|--------------------|---------|
| `web/swppp_api/main.py` | Modified | Wrap session CRUD routes with logging try/except |

### Exact Code Changes — `web/swppp_api/main.py`

The pattern is identical for each endpoint. Apply to: `list_sessions`, `get_session`, `save_session`, `delete_session`, `export_session`.

**Template — apply to each session route:**

BEFORE (example using `list_sessions`):
```python
@app.get("/swppp/api/sessions", response_model=SessionListResponse)
def list_sessions(user: dict = Depends(_require_swppp)):
    with session_db.connect() as conn:
        rows = session_db.list_sessions(conn, user["id"])
    return SessionListResponse(sessions=[SessionListItem(**r) for r in rows])
```

AFTER:
```python
@app.get("/swppp/api/sessions", response_model=SessionListResponse)
def list_sessions(user: dict = Depends(_require_swppp)):
    try:
        with session_db.connect() as conn:
            rows = session_db.list_sessions(conn, user["id"])
    except Exception as exc:
        log.error(
            "Session list failed: user_id=%s error=%s",
            user["id"],
            exc,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to retrieve sessions")
    return SessionListResponse(sessions=[SessionListItem(**r) for r in rows])
```

Apply the same pattern to the remaining four session endpoints, using descriptive log messages:
- `list_sessions` → `"Session list failed: user_id=%s"`
- `get_session` → `"Session get failed: user_id=%s name=%s"`
- `save_session` → `"Session save failed: user_id=%s name=%s"`
- `delete_session` → `"Session delete failed: user_id=%s name=%s"`
- `export_session` → `"Session export failed: user_id=%s name=%s"`

> **Important:** The `raise HTTPException(...)` after the log call is required. Do not swallow the exception. FastAPI needs an HTTPException to return a clean JSON error response rather than an unhandled 500. The log call fires before the re-raise so the traceback is captured even though the route ultimately returns an HTTP error.

---

## 5F. Fix: No Summary Log on Partial Mesonet Failure

### Scope

When `fetch_rainfall()` completes with some failed or missing days, the individual failures are logged at DEBUG/INFO level inside the parallel fetch loop. There is no summary log entry at WARNING level when the overall result is degraded. An inspector gets back fewer rain days than expected with no server-side record that the fetch was partial. This fix adds a single warning log line after assembly when `failed > 0` or `missing > 0`, giving operators a clear signal that Mesonet data was incomplete for a specific request.

### Files Modified

| File | Created / Modified | Purpose |
|------|--------------------|---------|
| `app/core/mesonet.py` | Modified | Add summary warning log after fetch assembly |

### Exact Code Change — `app/core/mesonet.py`

**Add immediately before the `return FetchResult(...)` line at the end of `fetch_rainfall()`:**

BEFORE:
```python
    return FetchResult(days=results, failed=failed, missing=missing)
```

AFTER:
```python
    if failed > 0 or missing > 0:
        log.warning(
            "Mesonet fetch incomplete: station=%s range=%s..%s "
            "returned=%d failed=%d missing=%d",
            station,
            start.isoformat(),
            end.isoformat(),
            len(results),
            failed,
            missing,
        )
    return FetchResult(days=results, failed=failed, missing=missing)
```

> **Why WARNING, not ERROR:** A partial result is not a crash — the function returns a valid `FetchResult` with whatever data it could get. ERROR implies the operation failed entirely. WARNING correctly signals "the operation completed but with degraded output," which is exactly what this is.

### Acceptance Tests — added to `tests/test_mesonet.py`

```python
def test_partial_failure_emits_warning(caplog):
    """fetch_rainfall with failed days must emit a WARNING log."""
    import logging
    from unittest.mock import patch
    from app.core.mesonet import fetch_rainfall
    from datetime import date

    # Simulate all HTTP requests failing
    with patch("app.core.mesonet._fetch_rain_mm_at", side_effect=Exception("timeout")):
        with caplog.at_level(logging.WARNING, logger="app.core.mesonet"):
            result = fetch_rainfall("NRMN", date(2024, 1, 1), date(2024, 1, 3))

    assert result.failed > 0
    assert any("Mesonet fetch incomplete" in r.message for r in caplog.records)
```

---

## 5G. Fix: Latent None Risk in Mesonet Retry Loop

### Scope

In `_fetch_rain_mm_at()`, the retry loop initializes `last_exc` as `None` and assigns it only inside the `except` block. The `else` clause of the for loop — which fires when the loop exits without a `break` — then executes `raise last_exc`. The type annotation `# type: ignore[misc]` on that line is the original author acknowledging that mypy flags this as unsafe. In practice it is unreachable: if the loop exits without `break` it means every attempt raised an exception, so `last_exc` was always assigned. But defensive code should not rely on reasoning about unreachable paths. This fix eliminates the ambiguity by restructuring so the raise is unambiguously correct.

### Files Modified

| File | Created / Modified | Purpose |
|------|--------------------|---------|
| `app/core/mesonet.py` | Modified | Restructure retry loop to remove `None` ambiguity |

### Exact Code Change — `app/core/mesonet.py`

**BEFORE:**
```python
    last_exc: requests.RequestException | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = requests.get(MDF_EXPORT_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                log.debug("Retry %d for %s %s: %s", attempt + 1, utc_day, utc_time, exc)
                time.sleep(_RETRY_DELAY)
    else:
        raise last_exc  # type: ignore[misc]
```

**AFTER:**
```python
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = requests.get(MDF_EXPORT_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.RequestException as exc:
            if attempt < _MAX_RETRIES:
                log.debug("Retry %d for %s %s: %s", attempt + 1, utc_day, utc_time, exc)
                time.sleep(_RETRY_DELAY)
            else:
                raise
```

> **Why this is better:** The `raise` (bare, no argument) inside the `except` block re-raises the *current* exception — `exc` — which is always a `requests.RequestException`. It is unambiguously typed, mypy-clean, and eliminates the `last_exc` variable entirely. The logic is identical: on the final attempt, instead of storing the exception and re-raising it in the `else` clause, we re-raise it directly where it's caught.

### Acceptance Tests

This is a code correctness fix, not a behavior change. The existing `tests/test_mesonet.py` tests for retry behavior should continue to pass unchanged. No new tests are required beyond confirming the existing suite is green.

---

## 5. Architecture Overview

### 5a. System Diagram

```
Internet
    │
    ▼
[Nginx :443]  ← HTTPS, HSTS, security headers, rate limiting
    │
    ├── /auth/* ──────────────────────────────► [tools-auth :8001]
    │   /admin/*                                 FastAPI + Gunicorn
    │   / (portal)                               SQLite: auth.db
    │   GET /health  ◄── monitoring/manual        logging: journald + /var/log/tools/
    │
    ├── /swppp/ (static HTML) ──────────────────► served by Nginx directly
    │
    └── /swppp/api/* ─────────────────────────► [tools-swppp :8002]
        GET /swppp/api/health ◄── monitoring      FastAPI + Gunicorn
                                                  SQLite: swppp_sessions.db
                                                  logging: journald + /var/log/tools/
```

### 5b. Service Inventory

| Service | Port | Framework | Workers | Database | Purpose |
|---------|------|-----------|---------|----------|---------|
| `tools-auth` | 8001 | FastAPI/Gunicorn | 2 | `auth.db` | Identity, sessions, invites, admin |
| `tools-swppp` | 8002 | FastAPI/Gunicorn | 2 | `swppp_sessions.db` | PDF generation, weather data, user sessions |

### 5c. Cross-Service Communication

Unchanged from Tier 4. See Tier 4 IR §5c.

### 5d. Log Destinations

| Service | Log Type | Destination | View Command |
|---------|----------|-------------|-------------|
| tools-auth | Application (Python) | journald | `journalctl -u tools-auth -f` |
| tools-auth | HTTP access | `/var/log/tools/auth-access.log` | `tail -f /var/log/tools/auth-access.log` |
| tools-auth | Gunicorn errors | `/var/log/tools/auth-error.log` | `tail -f /var/log/tools/auth-error.log` |
| tools-swppp | Application (Python) | journald | `journalctl -u tools-swppp -f` |
| tools-swppp | HTTP access | `/var/log/tools/swppp-access.log` | `tail -f /var/log/tools/swppp-access.log` |
| tools-swppp | Gunicorn errors | `/var/log/tools/swppp-error.log` | `tail -f /var/log/tools/swppp-error.log` |

**Rule of thumb:** Application logic issues → journald. HTTP-level issues (wrong status codes, slow requests) → access log files.

---

## 6. API Endpoint Inventory

Two new endpoints added. All existing endpoints unchanged.

| # | Method | Path | Auth Level | Handler | Purpose |
|---|--------|------|------------|---------|---------|
| — | — | *(all existing endpoints unchanged — see Tier 4 IR §6)* | — | — | — |
| NEW | GET | `/health` | **Public** | `health_check()` | Auth service health: DB connectivity |
| NEW | GET | `/swppp/api/health` | **Public** | `health_check()` | SWPPP service health: DB + critical files |

---

## 7. API Request/Response Examples

### Health check — `GET /health` (auth service)

**Response (healthy):**
```json
{
  "status": "ok",
  "service": "tools-auth",
  "db": "/opt/tools/data/auth.db",
  "timestamp": "2026-04-21T14:32:01.123456+00:00"
}
```

**Response (DB unreachable — HTTP 503):**
```json
{
  "detail": "Database unreachable: unable to open database file"
}
```

### Health check — `GET /swppp/api/health` (SWPPP service)

**Response (healthy):**
```json
{
  "status": "ok",
  "service": "tools-swppp",
  "db": "/opt/tools/data/swppp_sessions.db",
  "timestamp": "2026-04-21T14:32:01.654321+00:00"
}
```

**Response (template file missing — HTTP 503):**
```json
{
  "detail": {
    "status": "unhealthy",
    "issues": ["template file missing: /opt/tools/repo/assets/template.pdf"]
  }
}
```

---

## 8. Security Posture Summary

### 8a. Health Endpoint Access

Health endpoints are intentionally unauthenticated. They expose no sensitive data — only service name, DB file path, and timestamp. The DB path is an operational detail visible to anyone with server access anyway. If this is ever a concern (e.g., path disclosure), the response can be simplified to `{"status": "ok"}` with no other fields.

### 8b. Log Content Policy

Log entries must never include passwords, session tokens, or full invite codes. Review the existing log calls — they are already compliant (logging `user_id`, `code` prefix or length, not raw values). New log calls added in Tier 5 follow the same policy.

### 8c. All Other Security Posture

Unchanged from Tier 4. See Tier 4 IR §8.

---

## 9. Data & Storage

No schema changes in Tier 5. No new tables, columns, or migrations.

See Tier 4 IR §9 for database inventory and backup strategy.

---

## 10. Deployment

### 10a. Provisioning Steps

| Step | Action | Idempotent? | Notes |
|------|--------|-------------|-------|
| 1 | `git pull` on VPS | Yes | Pull Tier 5 changes |
| 2 | `sudo systemctl daemon-reload` | Yes | Picks up updated service files |
| 3 | `sudo systemctl restart tools-auth tools-swppp` | Yes | Applies new env vars + code |
| 4 | Verify logging active | Yes | `journalctl -u tools-auth -n 20` — look for INFO lines |
| 5 | Verify health endpoints | Yes | `curl https://sw3p.pro/health` and `curl https://sw3p.pro/swppp/api/health` |
| 6 | `pytest tests/ -v` | Yes | Full suite; paste output as evidence |

### 10b. Rollback Plan

**Service rollback:**
```
1. git revert HEAD   (or git checkout {previous-commit} -- web/auth/main.py web/swppp_api/main.py app/core/mesonet.py)
2. git checkout {previous-commit} -- web/scripts/systemd/
3. sudo systemctl daemon-reload
4. sudo systemctl restart tools-auth tools-swppp
```

**Data rollback:** No schema changes in Tier 5. Rollback is code-only with no data concerns.

**Client rollback:** N/A.

### 10c. Monitoring & Observability

> This is the tier that installs the instrumentation. Here is the full picture after Tier 5.

| What | How | Where to Look |
|------|-----|---------------|
| Service alive | `systemctl is-active tools-auth tools-swppp` | systemd |
| Service healthy (functional) | `curl https://sw3p.pro/health` | HTTP response |
| Application errors | `log.error(..., exc_info=True)` | `journalctl -u tools-{service}` |
| Auth events (login, invite, admin) | `log.info(...)` — now active | `journalctl -u tools-auth` |
| Mesonet partial failures | `log.warning("Mesonet fetch incomplete...")` | `journalctl -u tools-swppp` |
| HTTP request log | Gunicorn access log | `/var/log/tools/{service}-access.log` |
| Disk usage | Manual: `du -sh /opt/tools/data/ /opt/tools/backups/` | SSH |

---

## 11. Test Suite Inventory

### 11a. Final Counts

| File | Tests Before | Tests After | Phase Added | What It Covers |
|------|-------------|-------------|-------------|----------------|
| `tests/test_auth.py` | ~120 | ~126 | Tier 5 | + health endpoint (public, 200, DB check) |
| `tests/test_swppp_api.py` | 57 | ~65 | Tier 5 | + health endpoint, startup validation |
| `tests/test_mesonet.py` | 13 | ~15 | Tier 5 | + partial failure warning log |
| **Total** | **~190** | **~206** | | |

### 11b. New Test Classes

```
tests/test_auth.py:
  TestHealthEndpoint          (~3 tests)  — 5D: auth health check behavior

tests/test_swppp_api.py:
  TestSwpppHealthEndpoint     (~4 tests)  — 5D: SWPPP health check, file-missing 503
  TestStartupValidation       (~2 tests)  — 5C: lifespan raises on missing files

tests/test_mesonet.py:
  (new standalone test)       (~2 tests)  — 5F: warning log on partial failure
```

### 11c. Test Infrastructure

Unchanged from Tier 4. The `caplog` fixture (pytest built-in) is used for log assertion tests.

### 11d. Test Isolation Strategy

Log assertion tests use `caplog.at_level()` scoped to the specific logger under test. This prevents log level changes in one test from affecting others. All other isolation strategies unchanged from Tier 4.

### 11e. What Is NOT Tested

| Gap | Why Not Tested | Risk Level |
|-----|---------------|------------|
| `logging.basicConfig()` effect in production | `basicConfig()` is no-op if test framework already configured logging | Low — manual smoke test covers this |
| Nginx health route passthrough | No Nginx in test suite | Low — verified manually on deploy |

---

## 12. Performance Baseline

| Operation | Typical Latency | Notes |
|-----------|----------------|-------|
| `GET /health` (auth) | < 10ms | Single `SELECT 1` query |
| `GET /swppp/api/health` | < 15ms | `SELECT 1` + two `Path.exists()` calls |
| All existing endpoints | Unchanged | No hot-path code modified |

---

## 13. Change Delta Summary

### By Directory

| Directory | Files Added | Files Modified | Notes |
|-----------|-------------|---------------|-------|
| `web/auth/` | 0 | 1 (`main.py`) | Logging config + health endpoint |
| `web/swppp_api/` | 0 | 1 (`main.py`) | Logging config + startup validation + health endpoint + error logging + session error wrapping |
| `app/core/` | 0 | 1 (`mesonet.py`) | Retry loop fix + partial failure log |
| `web/scripts/systemd/` | 0 | 2 (both `.service` files) | Add `TOOLS_LOG_LEVEL=INFO` |
| `tests/` | 0 | 2 (`test_auth.py`, `test_swppp_api.py`, `test_mesonet.py`) | New test classes |

### Untouched Areas

```
- `web/auth/db.py`         — 0 changes
- `web/swppp_api/db.py`    — 0 changes
- `web/middleware.py`       — 0 changes
- `web/auth/dependencies.py`— 0 changes
- `web/auth/models.py`      — 0 changes
- `web/swppp_api/models.py` — 0 changes
- `web/scripts/nginx/`      — 0 changes
- `assets/`                 — 0 changes
- `pyproject.toml`          — 0 changes
```

---

## 14. User-Facing Behavior

### No user-visible changes.

All fixes in Tier 5 are internal instrumentation. From an inspector's perspective, the app behaves identically. The only observable difference:

- If the SWPPP service is misconfigured (missing template or mapping file), the service **fails to start** rather than starting and returning a confusing error on first use. This is a better failure mode but inspectors would never encounter it in a correct deployment.

- Health endpoints are new but are not linked in the UI. They are operational tools for administrators.

---

## Appendix A: Issue & Fix Registry

| # | Issue | Phase | Bug Category | Root Cause | Fix | Files Changed |
|---|-------|-------|-------------|------------|-----|---------------|
| 1 | All `log.info()` calls silenced in production | 5A | Config / env error | No `logging.basicConfig()` call; Python root logger defaults to WARNING | Configure logging at module level using `TOOLS_LOG_LEVEL` env var | `web/auth/main.py`, `web/swppp_api/main.py`, both `.service` files |
| 2 | Exception tracebacks lost at API boundary | 5B | Validation gap | `log.error("...", exc)` logs message only; `exc_info=True` not set | Add `exc_info=True` to all four error log calls in SWPPP API | `web/swppp_api/main.py` |
| 3 | No startup validation of critical files | 5C | Validation gap | `TEMPLATE_PDF` and `MAPPING_YAML` paths resolved but not checked until first request | Add existence check in `lifespan()` with `RuntimeError` on failure | `web/swppp_api/main.py` |
| 4 | No health check endpoints | 5D | Validation gap | Neither service exposes a functional health route | Add `GET /health` (auth) and `GET /swppp/api/health` (SWPPP) | `web/auth/main.py`, `web/swppp_api/main.py` |
| 5 | Session DB errors produce no log entries | 5E | Validation gap | Session CRUD routes have no try/except; errors propagate silently to Gunicorn | Wrap each session route body with `try/except` + `log.error(..., exc_info=True)` | `web/swppp_api/main.py` |
| 6 | No warning when Mesonet fetch returns partial data | 5F | Validation gap | `fetch_rainfall()` returns failed/missing counts but logs nothing at WARNING when either is non-zero | Add `log.warning("Mesonet fetch incomplete: ...")` before return when `failed > 0 or missing > 0` | `app/core/mesonet.py` |
| 7 | Latent `None` risk in retry loop | 5G | Type coercion | `last_exc` initialized to `None`; raised in `else` clause with `# type: ignore[misc]` acknowledging the risk | Restructure: bare `raise` inside `except` on final attempt; eliminate `last_exc` variable | `app/core/mesonet.py` |

### Bug Category Reference

See Tier 4 IR Appendix A for full category definitions.

---

## Appendix B: Known Limitations & Future Work

```
1. No structured/machine-readable logging format.
   What: Log output is human-readable text. Log aggregation tools (e.g.,
   Loki, Datadog, CloudWatch) work better with JSON-structured logs where
   each field (user_id, status_code, duration_ms) is a discrete key.
   Why deferred: The deployment is a single VPS with two services and a
   small team. journalctl + grep is sufficient at current scale. Structured
   logging would add complexity without immediate operational benefit.
   Trigger to revisit: Team grows, multiple VPS instances, or a log
   aggregation tool is adopted.
   Estimated effort: 1 day (python-json-logger or structlog integration).
```

---

## Mandatory Reporting Section

> **The agent must complete this section before marking Tier 5 done.**
> Summaries are not accepted. Paste exact output.

### Required Evidence

**1. Full `pytest` output — copy/paste verbatim:**
```
(paste here)
```

**2. `journalctl -u tools-auth -n 30` output after making a login request:**
```
(paste here — must show INFO-level log lines, not empty)
```

**3. `curl https://sw3p.pro/health` output:**
```
(paste here)
```

**4. `curl https://sw3p.pro/swppp/api/health` output:**
```
(paste here)
```

**5. Constraint compliance table** — return to §1d and fill in every row.

---

*End of IR_HARDENING_TIER5.md. Every section must be confirmed before this record is closed.*
