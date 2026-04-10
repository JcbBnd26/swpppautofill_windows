# Implementation Record (IR) — Template

> **What this is:** The standard post-implementation document for any significant body of work. When the user says "give me an IR" or "IR," generate a new markdown document following this structure exactly — filling every mandatory section and including conditional sections when they apply.
>
> **How to use it:** Copy this skeleton into a new file named after the work (e.g., `IR_Web_Migration.md`). Replace all `{placeholders}` with real content. If a conditional section `(if applicable)` genuinely doesn't apply, keep the heading and write a one-line justification for the omission. Do not silently omit sections. Blank sections are not acceptable.
>
> **Audience:** This document is consumed by a planning and review agent AND a non-technical Builder who wants to understand every decision. Write for both: precise enough to audit, clear enough to teach.
>
> **Principle:** Aim small, miss small. Precision in this record prevents ambiguity in review.

---

## Header Block

```
# {Project Name} — {Work Title} Implementation Record

**Document purpose:** {One sentence — what this record captures and who it's for.}
**Date range:** {Start date – End date}
**Source specification:** {Link or name of the plan/spec/ticket that drove this work, with size context (line count, section count).}
**Starting state:** {Concrete snapshot — test count, feature set, deployment status.}
**Final state:** {Same dimensions as starting state so the delta is obvious.}
```

---

## 0. Summary Card

> Complete this section LAST. It is the 30-second executive view of the entire implementation.

| Field | Value |
|-------|-------|
| **Project name** | `{name}` |
| **Date range** | `{start – end}` |
| **Source specification** | `{filename}` — {line count} lines, {section count} sections |
| **Starting state** | {test count} tests, {architecture summary}, {deployment status} |
| **Final state** | {test count} tests, {architecture summary}, {deployment status} |
| **Total files created** | {N} |
| **Total files modified** | {N} |
| **Total lines added** | {N} |
| **Lines modified in pre-existing code** | {N — if 0, state "0 — constraint honored"} |
| **Net new dependencies** | {count}: {comma-separated names} |
| **Known limitations carried forward** | {count} — see §Appendix B |
| **Open bugs** | {count} — see §Appendix A |

---

## Table of Contents

Number every section. Mark conditional sections with `(if applicable)` and add a one-line note if omitted.

```
1. Pre-Implementation Baseline
2. Dependency Manifest
3. Environment & Configuration Reference
4. Phase N: {Phase Title}              ← repeat for each phase
5. Architecture Overview
6. API Endpoint Inventory              (if applicable)
7. API Request/Response Examples       (if applicable)
8. Security Posture Summary
9. Data & Storage
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

**Purpose:** Freeze the "before" picture so the delta is provable. A reviewer must be able to reconstruct the starting state from this section alone.

### 1a. Code Inventory

One row per module or significant file that existed before this work began.

| Component | Path | Purpose | Will Be Modified? |
|-----------|------|---------|--------------------|
| `{name}` | `{path}` | `{key function/class}` — {one-line description} | {Yes / No / Wrapped} |

### 1b. Test Inventory

| File | Tests | Coverage Area |
|------|-------|---------------|
| `{test_file}` | {count} | {what it covers} |
| **Total** | **{N}** | |

### 1c. Design Constraints

Each must state the constraint AND its source. These become audit checkpoints in §1d.

```
- {Constraint statement} — Source: {spec section / business rule / tech limitation}
```

### 1d. Constraint Compliance Statement

> Complete AFTER implementation. Come back and confirm: Was each constraint honored? If any were violated, explain why.

| # | Constraint | Honored? | Evidence / Notes |
|---|-----------|----------|------------------|
| 1 | {constraint text} | {Yes / No} | {proof — e.g., "0 lines changed in app/core/" or "violated because X, approved by Y"} |

---

## 2. Dependency Manifest

**Purpose:** If a dependency breaks six months from now, this section tells you what was pinned, why it was added, and what uses it.

### 2a. Runtime Dependencies

| Package | Version | Added In | Purpose |
|---------|---------|----------|---------|
| `{package}` | `{pinned version}` | {Phase N or "pre-existing"} | {what it does in this project} |

### 2b. Dev / Test Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `{package}` | `{version constraint}` | {what it does} |

### 2c. Deployment-Only Dependencies (if applicable)

Packages installed on the server but not in the project's dependency file (e.g., Nginx, Certbot, system packages).

| Package | Installed Via | Purpose |
|---------|--------------|---------|
| `{package}` | `{apt/pip/manual}` | {role in deployment} |

### 2d. Full Dependency Snapshot

> Paste or reference the full `requirements.txt`, `pyproject.toml [dependencies]`, or `package.json` as it exists at end of implementation.

```
{paste here or state file path}
```

---

## 3. Environment & Configuration Reference

**Purpose:** Single source of truth for every env var and config file. If someone asks "what happens if I don't set X?" this section answers it.

### 3a. Environment Variables

| Variable | Purpose | Default | Required in Prod? | Failure Mode if Missing |
|----------|---------|---------|-------------------|------------------------|
| `{VAR_NAME}` | {what it controls} | `{default or "none"}` | {Yes/No} | {what breaks} |

### 3b. Configuration Files

| File | Format | Purpose | Read By | Who Edits It |
|------|--------|---------|---------|-------------|
| `{path}` | {YAML/JSON/PDF/INI/conf} | {what it configures} | {service/module} | {Dev / Admin / Auto} |

---

## 4. Phase N: {Phase Title}

> **Repeat this entire section for each phase.** Number phases sequentially. Each phase is self-contained — a reviewer reading only one phase should understand what happened.

### Scope

One paragraph. What this phase delivers and where its boundary is.

### Files Created / Modified

| File | Lines | Created / Modified | Purpose |
|------|-------|--------------------|---------|
| `{path}` | {N} | Created | {what it does} |
| `{path}` | {N} | Modified | {what changed and why} |

### Database Schemas (if applicable)

Use the compact notation format:

```
{table} — {col1} ({PK}), {col2} ({type}), {col3} ({FK→table}), {col4}, ...
```

If the schema is complex, also include the full CREATE TABLE statement:

```sql
{SQL here}
```

### Architectural Decisions

One subsection per decision. Every decision MUST have all four fields:

**Decision: {Short title — what was chosen.}**
- **What:** {What was decided}
- **Rationale:** {Why this over the alternatives. Reference constraints, trade-offs, scale assumptions.}
- **Tradeoff:** {What was given up or what risk was accepted}
- **Consequences:** {Any downstream effects — especially things that made later work harder or easier. If none, say "None observed."}

### Issues Encountered & Fixes

One subsection per issue. Format:

**Issue {N}: {Short description of the symptom.}**
- **Symptom:** {What you saw — exact error message or wrong behavior}
- **Root cause:** {What was actually wrong}
- **Bug category:** {Use a category from the Bug Category Reference in Appendix A. If a new category is needed, add it there.}
- **Fix:** {What was changed, in which file(s)}
- **Lesson:** {One-sentence generalizable takeaway for future work}

### Test Coverage After Phase

{Count} tests across {N} classes/files covering: {comma-separated list of what's tested.}

---

## 5. Architecture Overview

**Purpose:** The big picture. How do the major pieces connect? A reviewer unfamiliar with the project should understand the system shape from this section alone.

### 5a. System Diagram

ASCII diagram showing services, databases, external APIs, reverse proxies, and how requests flow from client to backend.

```
{diagram here}
```

### 5b. Service Inventory

| Service | Port | Framework | Workers | Database | Purpose |
|---------|------|-----------|---------|----------|---------|
| `{name}` | {N} | {e.g., FastAPI} | {N} | `{db file}` | {one-line} |

### 5c. Cross-Service Communication

How do services talk to each other? Be explicit about the mechanism.

| From | To | Mechanism | What Is Exchanged |
|------|----|-----------|--------------------|
| `{service A}` | `{service B}` | {e.g., shared DB / HTTP call / cookie passthrough via proxy} | {e.g., session token, user record} |

**Auth dependency chain:** {Describe specifically: Does service B call service A's API to validate sessions? Does it share the database? What happens if the auth service is down — does the downstream service fail open or closed?}

### 5d. File System Layout (Production)

```
{directory tree here}
```

---

## 6. API Endpoint Inventory (if applicable)

**Purpose:** Complete route map across all services. Grouped by service so a reviewer can audit access control at a glance.

| # | Method | Path | Auth Level | Handler | Purpose |
|---|--------|------|------------|---------|---------|
| 1 | `{GET/POST/...}` | `{/path}` | {Public / Session / Admin / App-scoped} | `{function_name()}` | {one-line} |

### Endpoint Summary

| Service | Auth Level | Count |
|---------|------------|-------|
| {service} — {level} | {description} | {N} |
| **Total** | | **{N}** |

---

## 7. API Request/Response Examples (if applicable)

**Purpose:** Show the actual data shapes so a reviewer doesn't have to read code. One example per distinct endpoint *pattern* — not every endpoint, but enough that a reader can infer the rest.

### {Endpoint pattern name} — `{METHOD} {path}`

**Request:**
```json
{
  "field": "value"
}
```

**Response (success):**
```json
{
  "field": "value"
}
```

**Response (error — {which error case}):**
```json
{
  "detail": "error message"
}
```

> Pick representative endpoints that show each distinct request/response shape. Skip endpoints whose shape is obvious from the inventory table.

---

## 8. Security Posture Summary

**Purpose:** Consolidate ALL security decisions into one auditable section. Do not scatter security info across phases — collect it here even if decisions were made in different phases.

### 8a. Authentication & Authorization Model

| Layer | Mechanism | Details |
|-------|-----------|---------|
| Identity | {e.g., invite codes, passwords, OAuth} | {how users are created/identified} |
| Session | {e.g., cookies, JWT, API keys} | {lifetime, storage, revocation mechanism} |
| Authorization | {e.g., role-based, app-scoped} | {how permissions are checked per request} |
| Admin | {e.g., admin flag on user record} | {safeguards — e.g., cannot self-deactivate} |

### 8b. Attack Surface Summary

Pre-populated rows — fill every one. Add rows for project-specific surfaces.

| Surface | Mitigation | Residual Risk |
|---------|-----------|---------------|
| Unauthenticated endpoints | {what's exposed, why} | {risk level + justification} |
| File uploads | {size limits, type checks} | {what's not checked} |
| User-supplied strings in DB | {sanitization, length limits} | {injection risk?} |
| Cookie handling | {flags: HttpOnly, Secure, SameSite, Max-Age} | {theft/fixation risk} |
| Cross-site requests (CSRF) | {SameSite cookies? CSRF tokens?} | {what's not covered} |
| Upstream API failures | {error wrapping, status codes} | {fail open or closed?} |
| Brute-force / rate limiting | {Nginx limit_req? App-level?} | {what's unthrottled} |
| Request body size | {Nginx client_max_body_size? App-level?} | {what's unlimited} |

### 8c. Security Headers

| Header | Value | Set Where |
|--------|-------|-----------|
| `{header}` | `{value}` | {Nginx / app middleware / both} |

### 8d. Secrets & Credentials

| Secret | Where Stored | How Rotated |
|--------|-------------|-------------|
| `{e.g., session tokens}` | `{e.g., SQLite sessions table}` | `{e.g., admin revocation}` |

### 8e. Explicitly Unprotected Areas

> Be honest. List things that are known to be unprotected and why that's acceptable (or not).

```
- {Gap} — Acceptable because {reason}
- {Gap} — Tracked for future work because {reason}
```

---

## 9. Data & Storage

### 9a. Database Inventory

| Database | Engine | Location | Tables | Shared By |
|----------|--------|----------|--------|-----------|
| `{name}` | {SQLite/Postgres/etc.} | `{path}` | {count}: {table names} | {which services} |

### 9b. Backup Strategy

| What | Method | Schedule | Retention |
|------|--------|----------|-----------|
| `{database}` | `{e.g., sqlite3 .backup}` | `{e.g., daily 02:00 UTC}` | `{e.g., 30 days}` |

### 9c. Data Lifecycle

What data is created, how long it lives, and how it gets cleaned up.

| Data Type | Created By | Lifetime | Cleanup Mechanism |
|-----------|-----------|----------|-------------------|
| `{e.g., temp PDF dirs}` | `{endpoint}` | `{e.g., request duration + 60 min safety net}` | `{e.g., BackgroundTask + hourly cron}` |

---

## 10. Deployment

### 10a. Provisioning Steps

| Step | Action | Idempotent? | Notes |
|------|--------|-------------|-------|
| {N} | {what it does} | {Yes / No — how} | {gotchas} |

### 10b. Rollback Plan

> If this deployment fails or introduces a critical bug, what are the exact steps to revert?

**Service rollback:**
```
1. {step}
2. {step}
```

**Data rollback:** {How to revert database changes. If the work only added new tables/columns with no destructive schema changes, state that explicitly.}

**Client rollback:** {If a desktop app or SPA was replaced, how does the user get back to the previous version?}

### 10c. Monitoring & Observability

| What | How | Where Logs Go |
|------|-----|---------------|
| Application errors | {e.g., Gunicorn stderr, structured logging} | `{path or service}` |
| Request latency | {e.g., access logs, APM} | `{path or service}` |
| Disk / DB size | {e.g., cron + alert} | `{path or service}` |
| Service health | {e.g., systemd watchdog, health endpoint} | `{path or service}` |

---

## 11. Test Suite Inventory

### 11a. Final Counts

| File | Tests | Lines | Phase Added | What It Covers |
|------|-------|-------|-------------|----------------|
| `{test_file}` | {count} | {lines} | {Phase N or "pre-existing"} | {coverage area} |
| **Total** | **{N}** | **{N}** | | |

### 11b. Test Classes

Group by test file. List each class and its test count.

```
{File}: {ClassName} ({N}), {ClassName} ({N}), ...
```

### 11c. Test Infrastructure

| Component | Location | Purpose |
|-----------|----------|---------|
| `{fixture/helper name}` | `{file}` | {what it does} |

### 11d. Test Isolation Strategy

One paragraph describing: how tests get fresh state, how network access is controlled, how databases are isolated, and how mock targets are chosen (especially if lazy imports are involved).

### 11e. What Is NOT Tested

> Explicitly list functionality that has no automated test coverage and why. This is as important as what IS tested.

| Gap | Why Not Tested | Risk Level |
|-----|---------------|------------|
| `{e.g., frontend JavaScript}` | `{e.g., no browser test framework in CI}` | {Low / Med / High} |

---

## 12. Performance Baseline

**Purpose:** Establish "normal" so regressions are detectable. Measured values preferred; if not formally benchmarked, say so.

| Operation | Typical Latency | Worst Case | Conditions | Measurement Method |
|-----------|----------------|------------|------------|-------------------|
| `{endpoint or operation}` | `{e.g., 200ms}` | `{e.g., 5s}` | `{e.g., single user, 3-month range}` | `{e.g., curl -w, browser devtools, "observed, not benchmarked"}` |

---

## 13. Change Delta Summary

**Purpose:** Prove what was and wasn't touched. This answers "did you modify core logic?" with numbers, not assertions.

### By Directory

| Directory | Files Added | Files Modified | Lines Added | Lines Removed | Net Change |
|-----------|-------------|---------------|-------------|---------------|------------|
| `{dir/}` | {N} | {N} | {+N} | {-N} | {±N} |

### Untouched Areas

Explicitly list directories/modules that were NOT modified, with file and line counts, to prove constraints were honored.

```
- `{path/}` — {N} files, {N} lines — 0 changes
```

---

## 14. User-Facing Behavior

**Purpose:** What does the user actually experience? All preceding sections are technical internals. This section describes the product from the outside.

### Workflow: {Workflow Name}

For each major user workflow:

1. **Entry point:** How the user gets here (URL, button click, command).
2. **Steps:** What the user sees and does, in order. Write as: "User does {action} → sees {result}."
3. **Timing:** How long each step takes (cross-reference §12 Performance Baseline).
4. **Output:** What the user gets at the end (file download, confirmation, redirect).
5. **Error states:** What happens when things go wrong (network error, invalid input, server down).

> Repeat for each distinct workflow.

---

## Appendix A: Issue & Fix Registry

**Purpose:** Consolidated master table of EVERY issue encountered across all phases. Phase sections have the narrative detail; this appendix has the searchable index.

| # | Issue | Phase | Bug Category | Root Cause (1 sentence) | Fix (1 sentence) | Files Changed |
|---|-------|-------|-------------|------------------------|-------------------|---------------|
| {N} | {symptom} | {N} | {category} | {cause} | {fix} | `{files}` |

### Bug Category Reference

Use these categories consistently across all IRs. If a new category is needed, add it here with a definition.

| Category | Definition |
|----------|-----------|
| Middleware ordering | Two components mutate the same response/request; last-write-wins causes wrong behavior |
| Response mutation | A component modifies the response after the intended handler has already set it |
| Import resolution | Module path, lazy import, or circular import causes AttributeError or wrong binding |
| Test isolation | Shared state between tests causes order-dependent pass/fail |
| State pollution | Prior operation leaves residual state that corrupts subsequent operations |
| Schema mismatch | Data shape from one layer doesn't match expectations of another |
| Route conflict | URL patterns overlap; declaration order determines which handler fires |
| Validation gap | Input accepted by one layer but rejected or mishandled by a downstream layer |
| Type coercion | Implicit type conversion produces wrong value silently |
| Race condition | Timing-dependent behavior between concurrent operations |
| Off-by-one | Boundary or range calculation is wrong by exactly one |
| Config / env error | Missing or wrong environment variable, config file, or feature flag |
| Dependency conflict | Package version incompatibility or missing transitive dependency |

---

## Appendix B: Known Limitations & Future Work

**Purpose:** What's explicitly NOT done and why. Numbered list — each item must have all four fields.

```
{N}. **{Limitation title}.**
    What: {Description of the gap.}
    Why deferred: {Why it's acceptable now.}
    Trigger to revisit: {What condition would make this a priority.}
    Estimated effort: {Rough size — hours/days/sprint.}
```

---

*End of template. Every section must be present in the final document. "N/A — {one-line justification}" is acceptable. Blank sections are not.*
