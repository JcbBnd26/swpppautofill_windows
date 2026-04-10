# SWPPP AutoFill — Web Migration Execution Plan

## Document Purpose

This document is the complete specification for migrating SWPPP AutoFill from a Windows-only desktop application (Tkinter GUI + CLI) to a multi-tenant web application hosted behind a company tools portal. A coding agent should be able to build the entire system from this document without ambiguity.

The source repository is `swpppautofill_windows-main`. The existing `app/core/` module is the business logic layer and is reused as-is. Everything else (UI, entry points, packaging) is replaced by the web architecture described below.

---

## Table of Contents

1. Architecture Overview
2. Database Schema — Auth System
3. Auth Service API (15 endpoints)
4. Portal Frontend (3 screens)
5. SWPPP AutoFill API (12 endpoints)
6. SWPPP AutoFill Frontend (1 screen, 5 sections)
7. Server & Deployment
8. File System Layout
9. Security
10. Build Order & Phases
11. Appendix: Existing Core Module Reference

---

## 1. Architecture Overview

### System Diagram

```
Browser
  │
  ▼
Nginx (reverse proxy, SSL termination, static file serving)
  │
  ├── /auth/*          →  Auth Service (FastAPI)
  ├── /admin/*         →  Auth Service (FastAPI)
  ├── /swppp/api/*     →  SWPPP Service (FastAPI)
  ├── /swppp/          →  Static files (SWPPP frontend)
  ├── /                →  Static files (Portal frontend)
  └── /future-app/*    →  Future app services
```

### Design Principles

1. **Reverse proxy architecture.** Nginx routes traffic. Each app is an independent service. Adding a new app means adding a new FastAPI service and 3 lines of Nginx config.
2. **Auth is shared infrastructure.** The auth service sits in front of all apps. No app implements its own authentication. The auth middleware validates every request before it reaches any app.
3. **Core logic is untouched.** The existing `app/core/` Python package (fill.py, mesonet.py, rain_fill.py, model.py, etc.) is imported directly by the SWPPP FastAPI service. No modifications to core logic.
4. **Stateless generation.** The `/swppp/api/generate` endpoint receives all data needed in a single request. No server-side form state between API calls.
5. **Mobile deferred.** The frontend is designed for desktop and tablet browsers. A mobile-specific layout is a future phase.

### Technology Stack

| Layer | Technology | Why |
|---|---|---|
| Reverse proxy | Nginx | Industry standard, serves static files, handles SSL |
| Backend framework | FastAPI (Python) | Async-capable, auto-generates OpenAPI docs, same language as existing core |
| Process manager | Gunicorn with Uvicorn workers | Production-grade ASGI server for FastAPI |
| Database | SQLite | Sufficient for dozens of users, no external service needed, easy backup |
| Frontend | HTML + Tailwind CSS + Alpine.js | Lightweight, fast load on slow connections, no build step |
| SSL | Let's Encrypt + Certbot | Free, auto-renewing certificates |
| OS | Ubuntu 24.04 LTS | Long-term support, wide community |
| Hosting | DigitalOcean (or equivalent VPS) | Simple, predictable pricing |

---

## 2. Database Schema — Auth System

File: `/opt/tools/data/auth.db` (SQLite)

### Table: `apps`

Registers every tool available on the portal.

| Column | Type | Constraints | Purpose |
|---|---|---|---|
| `id` | TEXT | PRIMARY KEY | URL-safe slug, e.g., `swppp`, `plancheck` |
| `name` | TEXT | NOT NULL | Display name, e.g., "SWPPP AutoFill" |
| `description` | TEXT | NOT NULL | One-line description for the portal card |
| `route_prefix` | TEXT | NOT NULL, UNIQUE | URL path prefix, e.g., `/swppp` |
| `is_active` | INTEGER | NOT NULL, DEFAULT 1 | 1 = visible on portal, 0 = hidden (kill switch) |
| `created_at` | TEXT | NOT NULL | ISO 8601 timestamp |

Initial seed row:

```sql
INSERT INTO apps (id, name, description, route_prefix, is_active, created_at)
VALUES ('swppp', 'SWPPP AutoFill', 'Generate ODOT stormwater inspection PDFs', '/swppp', 1, '2026-01-01T00:00:00Z');
```

### Table: `users`

Every person who has claimed an invite code.

| Column | Type | Constraints | Purpose |
|---|---|---|---|
| `id` | TEXT | PRIMARY KEY | UUID v4, system-internal only |
| `display_name` | TEXT | NOT NULL | Human label, e.g., "Mike R." |
| `is_active` | INTEGER | NOT NULL, DEFAULT 1 | 1 = active, 0 = locked out everywhere |
| `is_admin` | INTEGER | NOT NULL, DEFAULT 0 | 1 = can access `/admin/*` routes |
| `created_at` | TEXT | NOT NULL | ISO 8601 timestamp, set when invite is claimed |
| `last_seen_at` | TEXT | NOT NULL | ISO 8601 timestamp, updated on each auth check |

No email column. No password column. Authentication is handled entirely through invite codes and session tokens.

### Table: `invite_codes`

One-time-use codes that create users.

| Column | Type | Constraints | Purpose |
|---|---|---|---|
| `id` | TEXT | PRIMARY KEY | The code itself, format: `TOOLS-XXXX-XXXX` (uppercase alphanumeric, 4+4 random characters) |
| `display_name` | TEXT | NOT NULL | Name entered by admin when generating. Copied to `users.display_name` on claim. |
| `status` | TEXT | NOT NULL, DEFAULT 'pending' | One of: `pending`, `claimed`, `revoked` |
| `claimed_by` | TEXT | NULLABLE, FOREIGN KEY → users.id | Set when claimed, null while pending/revoked |
| `app_permissions` | TEXT | NOT NULL | JSON array of app IDs, e.g., `["swppp"]` |
| `created_at` | TEXT | NOT NULL | ISO 8601 timestamp |
| `claimed_at` | TEXT | NULLABLE | ISO 8601 timestamp, set when claimed |

Code generation rules:
- Format: `TOOLS-{4 random uppercase alphanumeric}-{4 random uppercase alphanumeric}`
- Example: `TOOLS-4X7R-BN2M`
- Characters: A-Z and 0-9 only (no ambiguous characters like O/0, I/1 — exclude these)
- Uniqueness enforced by primary key

### Table: `user_app_access`

Many-to-many mapping of users to apps.

| Column | Type | Constraints | Purpose |
|---|---|---|---|
| `user_id` | TEXT | NOT NULL, FOREIGN KEY → users.id | Who |
| `app_id` | TEXT | NOT NULL, FOREIGN KEY → apps.id | What |
| `granted_at` | TEXT | NOT NULL | ISO 8601 timestamp |
| | | PRIMARY KEY (user_id, app_id) | No duplicate grants |

When an invite code is claimed, the system reads `invite_codes.app_permissions` (JSON array) and inserts one row per app into this table.

### Table: `sessions`

Active browser sessions. No expiration — sessions persist until explicitly revoked by an admin.

| Column | Type | Constraints | Purpose |
|---|---|---|---|
| `token` | TEXT | PRIMARY KEY | 64-character random hex string. Stored as a secure, HttpOnly cookie in the browser. |
| `user_id` | TEXT | NOT NULL, FOREIGN KEY → users.id | Session owner |
| `device_label` | TEXT | NULLABLE | Optional identifier, e.g., "Mike's iPhone". Derived from User-Agent header if possible. |
| `created_at` | TEXT | NOT NULL | ISO 8601 timestamp |
| `last_seen_at` | TEXT | NOT NULL | ISO 8601 timestamp, updated on each request |

Token generation: `secrets.token_hex(32)` produces 64 hex characters. This is cryptographically random and effectively unguessable.

Cookie settings:
- Name: `tools_session`
- Flags: `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`
- No `Max-Age` or `Expires` (session cookie persists until browser clears it or admin revokes)

Note: "Session cookie" in browser terms means the cookie is deleted when the browser is fully closed. However, modern mobile browsers rarely fully close, so this effectively persists. For true persistence across browser restarts on desktop, set a very long `Max-Age` (e.g., 10 years / 315360000 seconds). Since sessions are "until revoked," use the long `Max-Age` approach.

---

## 3. Auth Service API (15 Endpoints)

Base path: routed by Nginx, served by the auth FastAPI application.

### Middleware: Auth Check

This middleware runs BEFORE every request to every service (auth, SWPPP, future apps). Nginx can be configured to call an internal auth endpoint, or each FastAPI app can include a shared dependency.

**Recommended approach:** FastAPI dependency injection. Create a shared `get_current_user()` dependency that:

1. Reads the `tools_session` cookie from the request
2. Looks up the token in the `sessions` table
3. Joins to `users` table, confirms `is_active = 1`
4. Updates `sessions.last_seen_at` and `users.last_seen_at` to current timestamp
5. Returns the user object (id, display_name, is_admin, list of app_ids from `user_app_access`)
6. If any check fails: returns 401 with redirect to `/auth/login`

For app-specific access checks, a second dependency `require_app(app_id)` checks whether the current user has a row in `user_app_access` for that app. Returns 403 if not.

For admin checks, a dependency `require_admin()` checks `is_admin = 1`. Returns 403 if not.

### Public Endpoints (no auth required)

These endpoints are excluded from the auth middleware.

---

#### `GET /auth/login`

**Purpose:** Serve the code entry HTML page.

**Behavior:**
1. If the request already has a valid session cookie, redirect to `/` (portal)
2. If the URL has a `?code=XXXX-XXXX-XXXX` query parameter, pre-fill the input field
3. Otherwise, render a simple HTML page with: a text input for the invite code, a submit button, and an error message area

**Response:** HTML page (static file served by Nginx or rendered by FastAPI)

---

#### `POST /auth/claim`

**Purpose:** Validate an invite code, create a user, create a session.

**Request body:**
```json
{
  "code": "TOOLS-4X7R-BN2M"
}
```

**Behavior:**
1. Look up the code in `invite_codes` table
2. If not found or `status != 'pending'`: return 400 with error "Invalid or expired invite code"
3. Create a new user record:
   - `id`: generate UUID v4
   - `display_name`: copy from `invite_codes.display_name`
   - `is_active`: 1
   - `is_admin`: 0
   - `created_at`: now
   - `last_seen_at`: now
4. Insert rows into `user_app_access` for each app in `invite_codes.app_permissions`
5. Update the invite code: `status = 'claimed'`, `claimed_by = user.id`, `claimed_at = now`
6. Create a session: generate token, insert into `sessions` table
7. Set the `tools_session` cookie with the token
8. Return 200 with redirect URL to `/`

**Response:**
```json
{
  "success": true,
  "redirect": "/"
}
```

---

#### `POST /auth/logout`

**Purpose:** Destroy the current session.

**Behavior:**
1. Read the `tools_session` cookie
2. Delete the matching row from `sessions` table
3. Clear the `tools_session` cookie
4. Redirect to `/auth/login`

**Response:** 302 redirect to `/auth/login`

---

### Portal Endpoints (valid session required)

---

#### `GET /auth/me`

**Purpose:** Return the current user's identity and accessible apps.

**Behavior:**
1. Auth middleware resolves the current user
2. Query `user_app_access` joined with `apps` (where `apps.is_active = 1`) to get accessible app list

**Response:**
```json
{
  "user_id": "uuid-here",
  "display_name": "Jake",
  "is_admin": true,
  "apps": [
    {
      "id": "swppp",
      "name": "SWPPP AutoFill",
      "description": "Generate ODOT stormwater inspection PDFs",
      "route_prefix": "/swppp"
    }
  ]
}
```

---

### Admin Endpoints (valid session + `is_admin = 1`)

All admin endpoints return 403 if the user is not an admin.

---

#### `GET /admin/users`

**Purpose:** List all users with their details and permissions.

**Response:**
```json
{
  "users": [
    {
      "id": "uuid-here",
      "display_name": "Mike R.",
      "is_active": true,
      "is_admin": false,
      "created_at": "2026-04-01T12:00:00Z",
      "last_seen_at": "2026-04-09T08:30:00Z",
      "apps": ["swppp"]
    }
  ]
}
```

---

#### `PATCH /admin/users/{user_id}`

**Purpose:** Update a user's active status or admin flag.

**Request body (all fields optional):**
```json
{
  "is_active": false,
  "is_admin": true
}
```

**Behavior:**
- Setting `is_active` to false effectively revokes the user. Their existing sessions will fail the auth check on the next request because the middleware checks `users.is_active`.
- An admin cannot deactivate themselves (return 400).

**Response:**
```json
{
  "success": true
}
```

---

#### `GET /admin/users/{user_id}/sessions`

**Purpose:** List all active sessions for a specific user.

**Response:**
```json
{
  "sessions": [
    {
      "token_prefix": "a3f8....",
      "device_label": "Chrome on iPhone",
      "created_at": "2026-03-15T10:00:00Z",
      "last_seen_at": "2026-04-09T08:30:00Z"
    }
  ]
}
```

Note: `token_prefix` is the first 8 characters of the token. Never expose the full token in API responses. The prefix is enough for identification on the admin UI.

---

#### `DELETE /admin/users/{user_id}/sessions`

**Purpose:** Kill ALL sessions for a user. Forces re-authentication.

**Behavior:** Delete all rows from `sessions` where `user_id` matches.

**Response:**
```json
{
  "success": true,
  "deleted_count": 2
}
```

---

#### `DELETE /admin/sessions/{token_prefix}`

**Purpose:** Kill one specific session by its prefix.

**Behavior:** Find the session whose token starts with the given prefix. If exactly one match, delete it. If zero or multiple matches (unlikely with 8-char prefix), return 400.

**Response:**
```json
{
  "success": true
}
```

---

#### `POST /admin/invites`

**Purpose:** Generate a new invite code.

**Request body:**
```json
{
  "display_name": "Mike R.",
  "app_permissions": ["swppp"]
}
```

**Validation:**
- `display_name` is required, non-empty
- `app_permissions` must be a non-empty array of valid app IDs (check against `apps` table)

**Behavior:** Generate a code using the `TOOLS-XXXX-XXXX` format (excluding ambiguous characters O, 0, I, 1). Insert into `invite_codes` with `status = 'pending'`.

**Response:**
```json
{
  "code": "TOOLS-4X7R-BN2M",
  "link": "https://tools.yourcompany.com/auth/login?code=TOOLS-4X7R-BN2M"
}
```

The `link` is the pre-filled URL that the admin can copy and text to the person.

---

#### `GET /admin/invites`

**Purpose:** List all invite codes.

**Response:**
```json
{
  "invites": [
    {
      "id": "TOOLS-4X7R-BN2M",
      "display_name": "Mike R.",
      "status": "claimed",
      "app_permissions": ["swppp"],
      "created_at": "2026-04-01T12:00:00Z",
      "claimed_at": "2026-04-01T12:05:00Z",
      "claimed_by": "uuid-here"
    }
  ]
}
```

---

#### `DELETE /admin/invites/{code_id}`

**Purpose:** Revoke a pending invite code before it is used.

**Behavior:**
- If `status = 'pending'`: update to `status = 'revoked'`, return success
- If `status = 'claimed'`: return 400 "Code already claimed — manage access through the user"
- If `status = 'revoked'`: return 400 "Code already revoked"

**Response:**
```json
{
  "success": true
}
```

---

#### `POST /admin/users/{user_id}/apps`

**Purpose:** Grant a user access to an additional app.

**Request body:**
```json
{
  "app_id": "plancheck"
}
```

**Behavior:** Insert a row into `user_app_access`. If the row already exists (duplicate grant), return 200 with no error.

**Response:**
```json
{
  "success": true
}
```

---

#### `DELETE /admin/users/{user_id}/apps/{app_id}`

**Purpose:** Revoke a user's access to a specific app.

**Behavior:** Delete the row from `user_app_access`. The user will no longer see this app on the portal, and API requests to that app's routes will return 403.

**Validation:** An admin cannot remove their own access to the admin panel (but the admin panel is not an "app" — it's gated by `is_admin`, not `user_app_access`).

**Response:**
```json
{
  "success": true
}
```

---

#### `GET /admin/apps`

**Purpose:** List all registered apps. Used by the invite generation form to show app checkboxes.

**Response:**
```json
{
  "apps": [
    {
      "id": "swppp",
      "name": "SWPPP AutoFill",
      "description": "Generate ODOT stormwater inspection PDFs",
      "route_prefix": "/swppp",
      "is_active": true,
      "created_at": "2026-01-01T00:00:00Z"
    }
  ]
}
```

---

#### `POST /admin/apps`

**Purpose:** Register a new app on the portal.

**Request body:**
```json
{
  "id": "plancheck",
  "name": "PlanCheck",
  "description": "Automated plan review tracking",
  "route_prefix": "/plancheck"
}
```

**Validation:**
- `id` must be URL-safe (lowercase alphanumeric + hyphens only)
- `route_prefix` must start with `/` and be unique
- Both `id` and `route_prefix` must not already exist

**Response:**
```json
{
  "success": true
}
```

---

#### `PATCH /admin/apps/{app_id}`

**Purpose:** Update an app's metadata or toggle its active status.

**Request body (all fields optional):**
```json
{
  "name": "SWPPP AutoFill v2",
  "description": "Updated description",
  "is_active": false
}
```

**Behavior:** Setting `is_active` to false hides the app from the portal for all users. It does not delete permissions — reactivating makes it reappear for everyone who had access.

**Response:**
```json
{
  "success": true
}
```

---

## 4. Portal Frontend (3 Screens)

Technology: static HTML + Tailwind CSS + Alpine.js. Served by Nginx as static files.

### Screen 1: Code Entry (`/auth/login`)

**Layout:** Centered card on a neutral background.

**Elements:**
1. Company name or logo (optional) at top
2. Heading: "Enter your access code"
3. Text input: placeholder "TOOLS-XXXX-XXXX", auto-uppercase, centered text
4. Submit button: "Enter"
5. Error message area (hidden by default, shown in red below the button)

**Behavior:**
- On page load: check URL for `?code=` parameter. If present, pre-fill the input and auto-submit.
- On submit: POST to `/auth/claim` with the code. On success, redirect to `/`. On failure, show the error message returned by the API.
- If the user already has a valid session (check by calling `GET /auth/me`), redirect immediately to `/`.

### Screen 2: App Launcher (`/`)

**Layout:** Simple centered content. Header bar at top, card grid below.

**Header:**
- Left: "Company Tools" (or company name)
- Right: "Logged in as {display_name}" + "Logout" link + "Admin" link (only if `is_admin`)

**Body:**
- A grid of cards, one per app from `GET /auth/me` response
- Each card contains: app name (bold), description (one line), and is clickable (navigates to `route_prefix`)
- If only one app exists, still show the card (don't auto-redirect — the portal will have more apps eventually)

**Behavior:**
- On page load: call `GET /auth/me`. If 401, redirect to `/auth/login`. Otherwise, render the card grid from the `apps` array.

### Screen 3: Admin Panel (`/admin`)

**Layout:** Full-width page with tabs or sections.

**Header:** Same as portal, with "Back to Portal" link.

**Section A: Active Users**

A table with columns:

| Column | Content |
|---|---|
| Name | `display_name` |
| Status | Active / Inactive badge |
| Admin | Yes / No badge |
| Apps | Comma-separated app names |
| Last Seen | Relative time, e.g., "2 hours ago" |
| Actions | "Manage Apps" button, "Revoke" button, "View Sessions" button |

- "Revoke" button: calls `PATCH /admin/users/{id}` with `is_active: false`. Confirms with a dialog first.
- "Manage Apps" button: opens a modal/dropdown showing all apps with checkboxes. Checked = user has access. Toggle calls `POST` or `DELETE` on `/admin/users/{id}/apps/{app_id}`.
- "View Sessions" button: expands a sub-row showing session list from `GET /admin/users/{id}/sessions` with "Kill" buttons per session and "Kill All" button.

**Section B: Generate Invite**

A form with:
1. Text input: "Name" (required)
2. Checkboxes: one per app from `GET /admin/apps` (at least one required)
3. "Generate Code" button

On submit: calls `POST /admin/invites`. Displays the result in a prominent box with: the code in large monospace text, a "Copy Link" button that copies the full URL to clipboard, and a "Copy Code" button for just the code.

**Section C: Pending Invites**

A table with columns:

| Column | Content |
|---|---|
| Code | The invite code |
| Name | `display_name` |
| Apps | Comma-separated app names from `app_permissions` |
| Created | Relative time |
| Actions | "Cancel" button |

- "Cancel" button: calls `DELETE /admin/invites/{code_id}`. Removes the row from the table.
- Do not show claimed or revoked codes here (only `status = 'pending'`). Optionally, a separate "History" view could show all codes, but this is not required for v1.

---

## 5. SWPPP AutoFill API (12 Endpoints)

Base path: `/swppp/api/`

All endpoints require a valid session AND the user must have `swppp` in their `user_app_access`. This is enforced by the `require_app("swppp")` dependency.

### SWPPP Session Storage

File: `/opt/tools/data/swppp_sessions.db` (SQLite, separate from auth.db)

**Table: `saved_sessions`**

| Column | Type | Constraints | Purpose |
|---|---|---|---|
| `user_id` | TEXT | NOT NULL | From auth — the user who saved this |
| `name` | TEXT | NOT NULL | User-chosen name, e.g., "I-35 Bridge" |
| `data` | TEXT | NOT NULL | JSON blob — the full session dict |
| `created_at` | TEXT | NOT NULL | ISO 8601 |
| `updated_at` | TEXT | NOT NULL | ISO 8601 |
| | | PRIMARY KEY (user_id, name) | One session per name per user |

### Session JSON Structure

This is the exact structure stored in `saved_sessions.data` and used for import/export. It matches what the existing GUI's `_build_session_dict()` produces:

```json
{
  "version": 1,
  "project_fields": {
    "job_piece": "JP-101",
    "project_number": "PN-202",
    "contract_id": "C-303",
    "location_description_1": "Northbound lane",
    "location_description_2": "Bridge approach",
    "re_odot_contact_1": "Jane Doe",
    "re_odot_contact_2": "John Doe",
    "inspection_type": "Weekly"
  },
  "checkbox_states": {
    "Erosion_Minimization": {
      "BMPs are in place to minimize erosion?": "YES",
      "Areas of work are delineated and steep slope disturbance is minimized?": "N/A"
    }
  },
  "notes_texts": {
    "Erosion_Minimization": "Silt fence repaired on east side.",
    "BMP_Maintenance": ""
  },
  "generator_settings": {
    "year": "2026",
    "months": [1, 2, 3],
    "custom_dates_enabled": false,
    "custom_start_date": "",
    "custom_end_date": "",
    "rain_enabled": true,
    "rain_station": "NRMN - Norman"
  }
}
```

The `checkbox_states` keys are the group keys from `odot_mapping.yaml` → `checkboxes`. The values are the question text mapped to `"YES"`, `"NO"`, `"N/A"`, or `""` (unanswered). The `notes_texts` keys are the same group keys. Not all groups will have notes.

---

### Configuration Endpoints

---

#### `GET /swppp/api/form-schema`

**Purpose:** Return the complete form structure so the frontend can render all fields, checklist groups, and questions dynamically.

**Behavior:** Load `app/core/odot_mapping.yaml` via `load_mapping()`. Transform the `TemplateMap` into a frontend-friendly JSON structure.

**Response:**
```json
{
  "fields": [
    {
      "key": "job_piece",
      "label": "Job Piece",
      "required": false
    },
    {
      "key": "project_number",
      "label": "Project Number",
      "required": false
    },
    {
      "key": "contract_id",
      "label": "Contract ID",
      "required": false
    },
    {
      "key": "location_description_1",
      "label": "Location Description",
      "required": false
    },
    {
      "key": "location_description_2",
      "label": "Location Description_2",
      "required": false
    },
    {
      "key": "re_odot_contact_1",
      "label": "RE andor ODOT Contact",
      "required": false
    },
    {
      "key": "re_odot_contact_2",
      "label": "RE andor ODOT Contact_2",
      "required": false
    },
    {
      "key": "inspection_type",
      "label": "Type of Inspection",
      "required": false
    }
  ],
  "checkbox_groups": [
    {
      "key": "Erosion_Minimization",
      "label": "Erosion Minimization",
      "has_notes": true,
      "questions": [
        {
          "text": "BMPs are in place to minimize erosion?",
          "allow_na": false
        },
        {
          "text": "Areas of work are delineated and steep slope disturbance is minimized?",
          "allow_na": true
        }
      ]
    }
  ]
}
```

**Notes:**
- The `label` for checkbox groups is derived from the YAML key by replacing underscores with spaces. Example: `Erosion_Minimization` → `"Erosion Minimization"`.
- The `has_notes` field is `true` if the group has a `notes_field` defined in the YAML.
- The full list of checkbox groups and questions comes from `odot_mapping.yaml`. The example above is truncated — the actual response includes all 7 groups and all ~40 questions.
- This endpoint can be cached aggressively (the YAML doesn't change at runtime). Set `Cache-Control: public, max-age=86400`.

---

#### `GET /swppp/api/stations`

**Purpose:** Return the Mesonet station list for the station dropdown.

**Behavior:** Call `station_display_list()` from `app.core.mesonet_stations` or directly read the `STATIONS` dict.

**Response:**
```json
{
  "stations": [
    { "code": "ACME", "name": "Acme", "display": "ACME - Acme" },
    { "code": "ADAX", "name": "Ada", "display": "ADAX - Ada" },
    { "code": "NRMN", "name": "Norman", "display": "NRMN - Norman" }
  ]
}
```

**Notes:** The `display` field matches the format used by the existing GUI's combobox (`station_display_list()`). The frontend uses `display` for the dropdown text and `code` for the API call to fetch rain data. This endpoint can also be cached aggressively.

---

### Rainfall Endpoints

---

#### `POST /swppp/api/rain/fetch`

**Purpose:** Fetch daily rainfall data from Oklahoma Mesonet for a station and date range.

**Request body:**
```json
{
  "station": "NRMN",
  "start_date": "2026-01-01",
  "end_date": "2026-03-31",
  "threshold": 0.5
}
```

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `station` | string | yes | — | 4-character Mesonet station code |
| `start_date` | string | yes | — | ISO date (YYYY-MM-DD) |
| `end_date` | string | yes | — | ISO date (YYYY-MM-DD) |
| `threshold` | float | no | 0.5 | Rain event threshold in inches |

**Behavior:**
1. Parse station code (validate against known stations via `parse_station_code()`)
2. Parse dates
3. Call `fetch_rainfall(station, start, end)` from `app.core.mesonet`
4. Call `filter_rain_events(result.days, threshold)` to identify qualifying rain days
5. Return full daily data plus filtered events

**Response:**
```json
{
  "all_days": [
    { "date": "2026-01-05", "rainfall_inches": 0.12 },
    { "date": "2026-01-06", "rainfall_inches": 0.87 }
  ],
  "rain_events": [
    { "date": "2026-01-06", "rainfall_inches": 0.87 }
  ],
  "failed_days": 0,
  "missing_days": 2,
  "station": "NRMN",
  "threshold": 0.5
}
```

**Error handling:**
- Invalid station code: 400
- Invalid date range (start > end): 400
- Mesonet API unreachable: 502 with message "Unable to reach Mesonet. Try again or use CSV upload."

**Performance note:** This endpoint uses the existing parallel HTTP fetch (ThreadPoolExecutor with MAX_WORKERS=8). Expected response time is 2-5 seconds for a 3-month range. The endpoint returns synchronously (request-response). If performance proves insufficient, convert to an async pattern (see Appendix).

---

#### `POST /swppp/api/rain/parse-csv`

**Purpose:** Parse an uploaded Mesonet CSV file into the same rain day structure.

**Request:** Multipart form data with a single file field named `file`.

**Behavior:**
1. Read the uploaded file content as text
2. Call `parse_rainfall_csv(csv_text)` from `app.core.mesonet`
3. Apply the same threshold filter
4. Return the same response shape as `/rain/fetch`

**Request parameters:**

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `file` | file upload | yes | — | CSV file |
| `threshold` | float | no | 0.5 | Passed as form field or query parameter |

**Response:** Same structure as `/rain/fetch`.

**Error handling:**
- No file uploaded: 400
- CSV parsing failure (no RAIN column, no date columns): 400 with descriptive message
- Empty results: 200 with empty arrays (not an error)

---

### Session Endpoints

All session endpoints scope data to the current user (from the auth middleware). User A cannot see or modify User B's sessions.

---

#### `GET /swppp/api/sessions`

**Purpose:** List all saved session names for the current user.

**Response:**
```json
{
  "sessions": [
    {
      "name": "I-35 Bridge",
      "updated_at": "2026-04-01T12:00:00Z"
    },
    {
      "name": "US-77 Widening",
      "updated_at": "2026-03-15T09:00:00Z"
    }
  ]
}
```

Sorted alphabetically by name (case-insensitive).

---

#### `GET /swppp/api/sessions/{name}`

**Purpose:** Load a specific saved session.

**Response:** The full session JSON structure (see Session JSON Structure above).

**Error handling:** 404 if no session with that name exists for the current user.

---

#### `POST /swppp/api/sessions/{name}`

**Purpose:** Save (or overwrite) a named session.

**Request body:** The full session JSON structure.

**Behavior:**
- If a session with this name already exists for this user: overwrite `data` and update `updated_at`
- If it doesn't exist: create a new row
- Validate that `version` is present and equals 1
- Validate that `project_fields` is a dict, `checkbox_states` is a dict, etc. (basic shape validation, not field-level)

**Response:**
```json
{
  "success": true,
  "name": "I-35 Bridge"
}
```

---

#### `DELETE /swppp/api/sessions/{name}`

**Purpose:** Delete a saved session.

**Behavior:** Delete the row. Return 200 even if it didn't exist (idempotent).

**Response:**
```json
{
  "success": true
}
```

---

#### `GET /swppp/api/sessions/{name}/export`

**Purpose:** Download a session as a `.json` file.

**Behavior:**
1. Load the session data from the database
2. Return it as a file download with:
   - Content-Type: `application/json`
   - Content-Disposition: `attachment; filename="SWPPP_{name}.json"`
   - The filename replaces spaces with underscores and strips unsafe characters

**Response:** JSON file download.

**Error handling:** 404 if session not found.

---

#### `POST /swppp/api/sessions/import`

**Purpose:** Import a session from an uploaded JSON file.

**Request:** Multipart form data with:

| Field | Type | Required | Notes |
|---|---|---|---|
| `file` | file upload | yes | The JSON file |
| `name` | string | no | If provided, save under this name. If omitted, return the data without saving (load into form only). |

**Behavior:**
1. Parse the uploaded file as JSON
2. Validate the structure (must have `version`, `project_fields`, etc.)
3. If `name` is provided: save to database under that name for the current user, return confirmation
4. If `name` is omitted: return the parsed session data (the frontend loads it directly into the form)

**Response (with name):**
```json
{
  "success": true,
  "name": "I-35 Bridge",
  "saved": true
}
```

**Response (without name):**
```json
{
  "success": true,
  "saved": false,
  "data": { ... full session dict ... }
}
```

**Error handling:**
- Invalid JSON: 400
- Missing required keys: 400 with message indicating which keys are missing
- Wrong version: 400 "Unsupported session version"

---

### Generation Endpoint

---

#### `POST /swppp/api/generate`

**Purpose:** Generate the full batch of weekly inspection PDFs (plus optional rain event PDFs), bundled as a ZIP download.

**Request body:**
```json
{
  "project_fields": {
    "job_piece": "JP-101",
    "project_number": "PN-202",
    "contract_id": "C-303",
    "location_description_1": "Northbound lane",
    "location_description_2": "Bridge approach",
    "re_odot_contact_1": "Jane Doe",
    "re_odot_contact_2": "John Doe",
    "inspection_type": "Weekly"
  },
  "checkbox_states": {
    "Erosion_Minimization": {
      "BMPs are in place to minimize erosion?": "YES",
      "Areas of work are delineated and steep slope disturbance is minimized?": "N/A"
    }
  },
  "notes_texts": {
    "Erosion_Minimization": "Silt fence repaired on east side."
  },
  "start_date": "2026-01-01",
  "end_date": "2026-03-31",
  "rain_days": [
    { "date": "2026-01-06", "rainfall_inches": 0.87 },
    { "date": "2026-02-14", "rainfall_inches": 1.23 }
  ],
  "original_inspection_type": "Weekly"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `project_fields` | object | yes | Keys match `odot_mapping.yaml` field keys |
| `checkbox_states` | object | yes | Can be empty `{}` if no checkboxes answered |
| `notes_texts` | object | yes | Can be empty `{}` |
| `start_date` | string | yes | ISO date (YYYY-MM-DD) |
| `end_date` | string | yes | ISO date (YYYY-MM-DD) |
| `rain_days` | array | no | If omitted or empty, no rain event PDFs generated |
| `original_inspection_type` | string | no | Only needed if `rain_days` is non-empty. Used to build "Rain Event - Weekly" label. |

**Behavior:**

1. Load the YAML mapping via `load_mapping()`
2. Build `ProjectInfo` from `project_fields` via `build_project_info()`
3. Create a temporary directory for output files
4. Build `RunOptions` with:
   - `output_dir`: the temp directory
   - `start_date`: from request
   - `end_date`: from request
   - `date_format`: `"%m/%d/%Y"` (hardcoded, matches existing behavior)
   - `make_zip`: `False` (we bundle separately to include rain PDFs)
5. Compute weekly dates via `weekly_dates(start_date, end_date)`
6. Call `generate_batch()` with template path (`assets/template.pdf`), project, options, dates, mapping, checkbox_states, notes_texts
7. If `rain_days` is non-empty:
   - Convert each rain day dict to a `RainDay` dataclass
   - Filter to only dates within start_date..end_date
   - Call `generate_rain_batch()` with the same project/options/mapping/checkbox_states/notes_texts plus the rain_days and original_inspection_type
   - Append rain PDFs to the written list
8. Call `bundle_outputs_zip(all_written_paths, temp_dir)` to create `swppp_outputs.zip`
9. Return the ZIP file as a binary download
10. Clean up the temp directory after response is sent

**Response:**
- Content-Type: `application/zip`
- Content-Disposition: `attachment; filename="swppp_outputs.zip"`
- Body: raw ZIP bytes

**Error handling:**
- Missing `start_date` or `end_date`: 400
- `start_date > end_date`: 400
- Template PDF not found on server: 500 "Template not configured" (this is a server deployment error)
- PDF fill failure (missing fields in template): 500 with the error message from `_write_filled_pdf`
- No dates in range (e.g., end_date is before the first weekly date): 200 with empty ZIP (not an error, matches existing behavior)

**Temp directory management:** Use Python's `tempfile.mkdtemp()`. Wrap the entire generation in a try/finally that removes the temp directory. Alternatively, use FastAPI's `BackgroundTask` to clean up after the response is sent.

---

### Static Frontend

---

#### `GET /swppp/`

**Purpose:** Serve the SWPPP AutoFill single-page frontend.

**Behavior:** Nginx serves static files from `/opt/tools/frontend/swppp/`. The entry point is `index.html`. All JS, CSS, and asset files are in the same directory.

This is not a FastAPI endpoint — Nginx handles it directly.

---

## 6. SWPPP AutoFill Frontend (1 Screen, 5 Sections)

Technology: single HTML file + Tailwind CSS (via CDN) + Alpine.js (via CDN). No build step. No npm.

Route: `/swppp/` → served as `index.html`

### Overall Layout

Two-column layout on desktop (min-width: 1024px):
- **Left column (40% width):** Project Fields, Generator Settings, Rain Days
- **Right column (60% width):** Inspection Checklist (all groups fully expanded)

**Top toolbar:** fixed bar across the top with:
1. Left: "SWPPP AutoFill" title
2. Center: Session controls — Save, Load, Export, Import, Clear buttons
3. Right: "Back to Portal" link, user display name

**Bottom bar:** sticky bar at the bottom with:
1. The "Generate" button (prominent, full width or large)
2. Spinner/progress indicator (hidden by default, shown during generation)
3. Status message area

### Section 1: Project Fields

Rendered dynamically from `GET /swppp/api/form-schema` response.

For each item in the `fields` array:
- Label (from `field.label`)
- Text input (bound to a form state object keyed by `field.key`)

Layout: stacked vertically, one field per row. Label above input. Full width of the left column.

### Section 2: Generator Settings

**Year selector:** dropdown defaulting to current year, options: current year ± 2 years.

**Month checkboxes:** 12 checkboxes (Jan–Dec) in a 4×3 or 6×2 grid. Multiple can be selected.

**Custom date toggle:** a checkbox labeled "Use custom date range". When checked:
- Disables the month checkboxes (grayed out)
- Shows two date inputs: Start Date and End Date (format: MM/DD/YYYY)
- Date inputs should use the browser's native date picker (`<input type="date">`)

**Date range computation (frontend logic):**
- If custom dates are enabled: use the start and end dates directly
- If months are selected: start_date = first day of the earliest selected month in the selected year, end_date = last day of the latest selected month in the selected year

### Section 3: Rain Days

**Enable toggle:** checkbox labeled "Include Rain Event Reports". When unchecked, the entire section is collapsed/hidden.

When enabled:

1. **Station dropdown:** populated from `GET /swppp/api/stations`. Searchable/filterable is nice but not required. Displays the `display` field, stores the `code`.

2. **Fetch button:** "Fetch Rain Data" — calls `POST /swppp/api/rain/fetch` with the selected station and the current date range from Generator Settings. Shows a spinner on the button while waiting.

3. **Upload button:** "Upload CSV" — opens a file picker for `.csv` files. Uploads to `POST /swppp/api/rain/parse-csv`.

4. **Results display:** After data is received, show:
   - Summary line: "{N} rain events found above {threshold} in threshold"
   - List of rain event dates with amounts: "Jan 6, 2026 — 0.87 in"
   - This data is stored in the frontend state and included in the generate request

5. **Invalidation:** If the user changes the date range or station after fetching, show a warning: "Rain data may be outdated — refetch to update" and clear the ready state.

### Section 4: Inspection Checklist

Rendered dynamically from `GET /swppp/api/form-schema` response.

For each item in the `checkbox_groups` array:

**Group header:** bold text showing `group.label` (e.g., "Erosion Minimization"). Styled as a card or bordered section for visual separation.

**Questions:** ALL EXPANDED, NOT COLLAPSIBLE. Each question is a row:
- Question text on the left
- Toggle buttons on the right: `YES` | `NO` | `N/A` (or `YES` | `NO` if `allow_na` is false)
- Toggle behavior: tap one to select it (highlighted). Tap the same one again to deselect (returns to unanswered). Only one can be active per question.
- Visual states: selected button is filled/solid color, unselected buttons are outlined/muted

**Notes area:** If `group.has_notes` is true, show a text area below the questions labeled "Notes". Full width of the group card.

**Color coding (optional but recommended):** YES = green tint, NO = red tint, N/A = gray tint. Helps visual scanning of the form.

### Section 5: Generate (Sticky Bottom Bar)

**Generate button:**
- Label: "Generate PDFs"
- Disabled state: when no date range is configured (no months selected and no custom dates)
- Active state: enabled when at least a date range exists
- On click: collects ALL form state, sends `POST /swppp/api/generate`

**During generation:**
- Button shows spinner and "Generating..." text
- Button is disabled to prevent double-submit

**On success:**
- Browser triggers file download of the returned ZIP
- Status message: "Created {N} PDF files" (count the entries in the ZIP if possible, or just show "Download complete")

**On error:**
- Status message in red with the error text from the API
- Button re-enables

### Session Controls (Toolbar)

**Save button:**
1. If no session is currently loaded: prompt for a name (modal with text input + Save/Cancel)
2. If a session is already loaded (user loaded or saved earlier): save under the same name without prompting
3. Shift+click or "Save As" option: always prompts for a name
4. Collects form state into the session JSON structure, POSTs to `/swppp/api/sessions/{name}`
5. Shows brief confirmation: "Saved ✓"

**Load button:**
1. Calls `GET /swppp/api/sessions` to get the list
2. Shows a modal with the list of session names + timestamps
3. User clicks one → calls `GET /swppp/api/sessions/{name}`
4. Populates all form fields, checkboxes, notes, and generator settings from the response
5. Sets the "current session name" so subsequent Saves don't re-prompt

**Export button:**
1. If a session is loaded: navigates to `GET /swppp/api/sessions/{name}/export` (triggers download)
2. If no session is loaded but form has data: save as a temporary session first, then export. OR: build the session JSON client-side and trigger a download using JavaScript's `Blob` + `URL.createObjectURL()` (preferred — avoids server round-trip)

**Import button:**
1. Opens a file picker for `.json` files
2. Uploads to `POST /swppp/api/sessions/import` (without a name — load-only mode)
3. Populates the form from the returned data
4. Optionally prompts: "Save this session? (Name: ___)" — if user provides a name, calls the save endpoint

**Clear button:**
1. Confirms: "Clear all fields?" dialog
2. Resets all form fields, checkboxes, notes to empty/default
3. Clears rain data
4. Resets generator settings to defaults (current year, no months selected)
5. Clears the "current session name"

### Page Load Sequence

When the user navigates to `/swppp/`:

1. HTML loads, Tailwind and Alpine.js initialize
2. Call `GET /auth/me` — if 401, redirect to `/auth/login`
3. Call `GET /swppp/api/form-schema` — build the project fields and checklist
4. Call `GET /swppp/api/stations` — populate the station dropdown
5. Form is ready. All fields empty, no session loaded.

Steps 2–4 happen in parallel. Show a loading skeleton/spinner until all three resolve.

---

## 7. Server & Deployment

### VPS Specification

| Setting | Value |
|---|---|
| Provider | DigitalOcean (or equivalent) |
| Plan | 1 vCPU, 2 GB RAM, 50 GB SSD |
| OS | Ubuntu 24.04 LTS |
| Region | Dallas (or nearest to Oklahoma) |
| Monthly cost | ~$12-18 |

### Domain & DNS

- Register or configure a subdomain: `tools.yourcompany.com`
- Create a DNS A record pointing to the VPS public IP
- TTL: 300 seconds (5 minutes) for initial setup, increase to 3600 after confirmed working

### SSL

- Install Certbot: `sudo apt install certbot python3-certbot-nginx`
- Obtain certificate: `sudo certbot --nginx -d tools.yourcompany.com`
- Auto-renewal is configured automatically by Certbot (cron job)
- Verify renewal works: `sudo certbot renew --dry-run`

### Nginx Configuration

File: `/etc/nginx/sites-enabled/tools.conf`

```nginx
server {
    listen 80;
    server_name tools.yourcompany.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl;
    server_name tools.yourcompany.com;

    ssl_certificate /etc/letsencrypt/live/tools.yourcompany.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tools.yourcompany.com/privkey.pem;

    # -- Portal frontend --
    location = / {
        root /opt/tools/frontend/portal;
        try_files /index.html =404;
    }
    location /portal-assets/ {
        root /opt/tools/frontend/portal;
    }

    # -- Auth service --
    location /auth/ {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # -- Admin panel --
    location /admin/ {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # -- SWPPP API --
    location /swppp/api/ {
        proxy_pass http://127.0.0.1:8002;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Allow large responses (ZIP files)
        proxy_read_timeout 120;
        proxy_buffering off;
    }

    # -- SWPPP frontend --
    location /swppp/ {
        alias /opt/tools/frontend/swppp/;
        try_files $uri $uri/ /swppp/index.html;
    }

    # -- Future apps follow the same pattern --
    # location /plancheck/api/ { proxy_pass http://127.0.0.1:8003; ... }
    # location /plancheck/ { alias /opt/tools/frontend/plancheck/; ... }
}
```

### Python Environment

```bash
# System Python + venv
sudo apt install python3.12 python3.12-venv python3-pip

# Application virtual environment
python3.12 -m venv /opt/tools/venv
source /opt/tools/venv/bin/activate

# Install dependencies
pip install fastapi uvicorn[standard] gunicorn pypdf pydantic pyyaml python-dateutil requests aiosqlite python-multipart
```

### Systemd Services

**Auth service:** `/etc/systemd/system/tools-auth.service`

```ini
[Unit]
Description=Tools Portal Auth Service
After=network.target

[Service]
Type=exec
User=tools
Group=tools
WorkingDirectory=/opt/tools/auth
ExecStart=/opt/tools/venv/bin/gunicorn auth.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 2 \
    --bind 127.0.0.1:8001 \
    --access-logfile /var/log/tools/auth-access.log \
    --error-logfile /var/log/tools/auth-error.log
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**SWPPP service:** `/etc/systemd/system/tools-swppp.service`

```ini
[Unit]
Description=SWPPP AutoFill Service
After=network.target

[Service]
Type=exec
User=tools
Group=tools
WorkingDirectory=/opt/tools/swppp
ExecStart=/opt/tools/venv/bin/gunicorn swppp_api.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 2 \
    --bind 127.0.0.1:8002 \
    --access-logfile /var/log/tools/swppp-access.log \
    --error-logfile /var/log/tools/swppp-error.log
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Enable and start:**

```bash
sudo systemctl daemon-reload
sudo systemctl enable tools-auth tools-swppp
sudo systemctl start tools-auth tools-swppp
```

### Dedicated System User

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin tools
sudo chown -R tools:tools /opt/tools
sudo mkdir -p /var/log/tools
sudo chown tools:tools /var/log/tools
```

### Deployment Process

```bash
# SSH into server
ssh you@tools.yourcompany.com

# Pull latest code
cd /opt/tools
git pull

# Restart affected service
sudo systemctl restart tools-swppp  # or tools-auth, or both

# Check status
sudo systemctl status tools-swppp
sudo journalctl -u tools-swppp --since "5 minutes ago"
```

### Backup

| What | Method | Frequency |
|---|---|---|
| `auth.db` | `sqlite3 auth.db ".backup /opt/tools/backups/auth_$(date +%Y%m%d).db"` | Daily cron job |
| `swppp_sessions.db` | Same pattern | Daily cron job |
| Full server | DigitalOcean droplet snapshot | Weekly ($2-4/mo) |
| Code | GitHub repository | Every push |

Cron job example (`/etc/cron.d/tools-backup`):

```cron
0 3 * * * tools /opt/tools/scripts/backup.sh
```

Backup script retains 30 days of daily backups and deletes older ones.

---

## 8. File System Layout

```
/opt/tools/
├── venv/                          # Python virtual environment
├── data/
│   ├── auth.db                    # Auth system database
│   └── swppp_sessions.db         # SWPPP saved sessions database
├── auth/
│   ├── main.py                    # FastAPI app: auth endpoints + middleware
│   ├── db.py                      # Database connection + queries
│   ├── models.py                  # Pydantic request/response models
│   └── dependencies.py            # get_current_user(), require_app(), require_admin()
├── swppp/
│   ├── app/
│   │   └── core/                  # EXISTING CODE — copied from desktop repo
│   │       ├── __init__.py
│   │       ├── config_manager.py
│   │       ├── dates.py
│   │       ├── fill.py
│   │       ├── mesonet.py
│   │       ├── mesonet_stations.py
│   │       ├── model.py
│   │       ├── odot_mapping.yaml
│   │       ├── pdf_fields.py
│   │       ├── rain_fill.py
│   │       └── session.py         # NOT USED by web (replaced by DB sessions)
│   ├── assets/
│   │   └── template.pdf           # The ODOT inspection form template
│   ├── swppp_api/
│   │   ├── main.py                # FastAPI app: SWPPP endpoints
│   │   ├── db.py                  # Session storage queries
│   │   └── models.py              # Pydantic request/response models
│   └── tests/                     # EXISTING TESTS — copied from desktop repo
├── frontend/
│   ├── portal/
│   │   ├── index.html             # App launcher page
│   │   ├── login.html             # Code entry page
│   │   └── admin.html             # Admin panel
│   └── swppp/
│       └── index.html             # SWPPP AutoFill SPA (single file)
├── scripts/
│   └── backup.sh                  # Database backup script
└── README.md                      # Deployment notes
```

### Notes on existing code

- The entire `app/core/` directory is copied from the desktop repo into `/opt/tools/swppp/app/core/` without modification.
- The desktop-specific modules (`app/ui_gui/`, `app/ui_cli/`, `app/tools/`) are NOT copied. They are not needed.
- The existing `app/core/session.py` (file-based session save/load) is present in the directory but NOT used by the web app. The web app uses database-backed sessions via `swppp_api/db.py`. The file is left in place to avoid breaking imports if any core module references it, but no web endpoint calls it.
- The existing test suite (`tests/`) is copied and should continue to pass, as the core modules are unchanged.

---

## 9. Security

### Server Hardening

1. **SSH key-only authentication.** Disable password auth in `/etc/ssh/sshd_config`: `PasswordAuthentication no`.
2. **Firewall (UFW).** Allow only ports 22 (SSH), 80 (HTTP → redirect to HTTPS), 443 (HTTPS). All other ports blocked.
   ```bash
   sudo ufw default deny incoming
   sudo ufw allow 22/tcp
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   sudo ufw enable
   ```
3. **Fail2ban.** Install and enable for SSH and Nginx. Blocks IPs after 5 failed attempts.
4. **Automatic security updates.** Enable `unattended-upgrades` for Ubuntu security patches.
5. **Non-root service user.** The `tools` user has no login shell and no sudo access. Services run as this user.

### Application Security

1. **Session tokens.** 64-character cryptographically random hex. HttpOnly + Secure + SameSite=Lax cookies. Never exposed in URLs or API responses (admin panel shows only 8-char prefixes).
2. **HTTPS everywhere.** Nginx redirects all HTTP to HTTPS. HSTS header set: `Strict-Transport-Security: max-age=31536000`.
3. **CORS.** Not needed — frontend and API are on the same domain. If needed later, restrict to the tools domain only.
4. **Input validation.** All FastAPI endpoints use Pydantic models for request validation. Invalid input returns 400 before reaching business logic.
5. **Temp file cleanup.** The generate endpoint creates temp directories. Always cleaned up via try/finally or BackgroundTask. A cron job should also sweep `/tmp` for orphaned `swppp_*` directories older than 1 hour as a safety net.
6. **Rate limiting.** Not required for v1 (trusted users only via invite codes). Can be added at the Nginx level later if needed.
7. **SQL injection.** Use parameterized queries for all SQLite operations. Never interpolate user input into SQL strings.

### Admin Bootstrap

On first deployment, the system has no users and no invite codes. The setup process:

1. Run a one-time setup script: `python /opt/tools/scripts/init_admin.py`
2. The script:
   - Creates the database tables (if not exist)
   - Seeds the `apps` table with the SWPPP entry
   - Generates the first invite code with `is_admin` flag (special case — the claimed user gets `is_admin = 1`)
   - Prints the code and link to the terminal
3. The deployer (you) claims that code, becoming the first admin user
4. All subsequent users are created through the admin panel

The init script should be idempotent — safe to run multiple times without creating duplicates.

---

## 10. Build Order & Phases

### Phase 1: Auth System + Portal

**Deliverables:**
1. SQLite schema creation script for auth.db (all 5 tables)
2. Auth FastAPI app with all 15 endpoints
3. Auth middleware (get_current_user, require_app, require_admin dependencies)
4. Portal frontend: login page, app launcher, admin panel
5. Admin bootstrap script (init_admin.py)

**Test criteria:**
- Generate an invite code via the init script
- Claim it in a browser → see the portal with one SWPPP card
- Admin panel: generate a second invite, claim it in a different browser, verify user appears in the user list
- Revoke the second user → verify they're redirected to login on next page load
- Grant/revoke app permissions and verify portal card appears/disappears

### Phase 2: SWPPP API Backend

**Deliverables:**
1. SQLite schema creation for swppp_sessions.db
2. SWPPP FastAPI app with all 12 endpoints
3. Integration with existing `app/core/` modules (no modifications to core)
4. Endpoint: form-schema returns correct structure from odot_mapping.yaml
5. Endpoint: stations returns full station list
6. Endpoint: rain/fetch calls Mesonet and returns data
7. Endpoint: rain/parse-csv accepts uploaded CSV
8. Endpoints: session CRUD (list, get, save, delete)
9. Endpoints: session export/import
10. Endpoint: generate produces correct ZIP with PDFs

**Test criteria:**
- `GET /swppp/api/form-schema` returns all 8 fields and all 7 checkbox groups with all ~40 questions
- `GET /swppp/api/stations` returns ~121 stations
- `POST /swppp/api/rain/fetch` with station NRMN and a known date range returns rainfall data
- `POST /swppp/api/generate` with a complete request body returns a valid ZIP containing correctly filled PDFs
- Compare a generated PDF from the web API against one generated by the desktop CLI with the same inputs — field values should be identical
- Session save → load round-trip preserves all data exactly
- Session export → import round-trip preserves all data exactly
- All endpoints return 401 without a valid session cookie
- All endpoints return 403 without SWPPP app permission

### Phase 3: SWPPP Frontend

**Deliverables:**
1. Single-page HTML app at `/swppp/index.html`
2. Dynamic form rendering from form-schema API
3. Generator settings (year/month/custom dates)
4. Rain days section with fetch and CSV upload
5. Full checklist with YES/NO/N/A toggles (all groups expanded)
6. Generate button with ZIP download
7. Session toolbar: Save, Load, Export, Import, Clear
8. Two-column desktop layout

**Test criteria:**
- Page loads and renders all form fields and checklist questions
- Can fill out the form entirely and generate a ZIP
- Can save a session, clear the form, load the session, and all fields are restored
- Can export a session JSON, import it on a different browser/user, and all fields are restored
- Rain fetch returns data and displays rain events
- CSV upload works and displays the same rain event format
- Generate with rain events produces a ZIP containing both weekly and rain event PDFs
- All text inputs accept and preserve the full range of characters users might enter

### Phase 4: Server Deployment

**Deliverables:**
1. VPS provisioned and hardened (SSH keys, UFW, Fail2ban)
2. Domain DNS configured
3. Nginx installed and configured with the routing rules above
4. SSL certificate obtained and auto-renewal verified
5. Python venv created with all dependencies
6. Systemd services created and enabled
7. Database initialized, admin bootstrap script run
8. Backup cron job configured
9. Smoke test: full end-to-end workflow from code entry to PDF download

**Test criteria:**
- `https://tools.yourcompany.com` loads the login page
- Invite code claim → portal → SWPPP → fill form → generate → download ZIP
- Admin panel: create user, revoke user, manage permissions
- Server reboot → services restart automatically
- `certbot renew --dry-run` succeeds

---

## 11. Appendix

### Async Rain Fetch (Future Enhancement)

If the rain fetch endpoint proves too slow for synchronous request-response, convert to this pattern:

1. `POST /swppp/api/rain/fetch` returns immediately with a job ID:
   ```json
   { "job_id": "abc123", "status": "processing" }
   ```
2. Backend processes the fetch in a background task
3. Frontend polls `GET /swppp/api/rain/status/{job_id}` every 2 seconds:
   ```json
   { "job_id": "abc123", "status": "processing", "progress": 45 }
   ```
4. When complete:
   ```json
   { "job_id": "abc123", "status": "complete", "data": { ...rain results... } }
   ```

This requires adding a `jobs` table to track background tasks. The endpoint contract changes (response is a job reference instead of direct data), so the frontend fetch logic needs updating too. Not needed for v1 but documented here so the pattern is clear.

### Existing Core Module Reference

These are the files from the desktop repo that the web app imports directly. No modifications.

| File | Purpose | Called By |
|---|---|---|
| `config_manager.py` | Loads YAML mapping, builds ProjectInfo and RunOptions | form-schema endpoint, generate endpoint |
| `dates.py` | `weekly_dates()` generator — yields inspection dates | generate endpoint |
| `fill.py` | `generate_batch()` — creates weekly inspection PDFs | generate endpoint |
| `mesonet.py` | `fetch_rainfall()`, `parse_rainfall_csv()`, `filter_rain_events()` | rain endpoints |
| `mesonet_stations.py` | `STATIONS` dict, `station_display_list()`, `parse_station_code()` | stations endpoint, rain fetch validation |
| `model.py` | Pydantic models: TemplateMap, ProjectInfo, RunOptions, CheckboxItem, etc. | all endpoints |
| `odot_mapping.yaml` | Form field definitions, checkbox groups, questions | form-schema endpoint, generate endpoint |
| `pdf_fields.py` | `populate_checkbox_targets()` — runtime checkbox field detection | generate endpoint (called by fill.py) |
| `rain_fill.py` | `generate_rain_batch()` — creates rain event PDFs | generate endpoint |
| `session.py` | File-based session save/load (UNUSED in web app) | not called |

### Endpoint Summary (All 27)

| # | Method | Path | Auth | Purpose |
|---|---|---|---|---|
| 1 | GET | `/auth/login` | none | Code entry page |
| 2 | POST | `/auth/claim` | none | Claim invite code |
| 3 | POST | `/auth/logout` | session | Destroy session |
| 4 | GET | `/auth/me` | session | Current user info |
| 5 | GET | `/admin/users` | admin | List users |
| 6 | PATCH | `/admin/users/{id}` | admin | Update user |
| 7 | GET | `/admin/users/{id}/sessions` | admin | List user sessions |
| 8 | DELETE | `/admin/users/{id}/sessions` | admin | Kill all sessions |
| 9 | DELETE | `/admin/sessions/{prefix}` | admin | Kill one session |
| 10 | POST | `/admin/invites` | admin | Generate invite |
| 11 | GET | `/admin/invites` | admin | List invites |
| 12 | DELETE | `/admin/invites/{code}` | admin | Revoke invite |
| 13 | POST | `/admin/users/{id}/apps` | admin | Grant app access |
| 14 | DELETE | `/admin/users/{id}/apps/{app}` | admin | Revoke app access |
| 15 | GET | `/admin/apps` | admin | List apps |
| 16 | POST | `/admin/apps` | admin | Register app |
| 17 | PATCH | `/admin/apps/{id}` | admin | Update app |
| 18 | GET | `/swppp/api/form-schema` | swppp | Form structure |
| 19 | GET | `/swppp/api/stations` | swppp | Mesonet stations |
| 20 | POST | `/swppp/api/rain/fetch` | swppp | Fetch rainfall |
| 21 | POST | `/swppp/api/rain/parse-csv` | swppp | Parse rain CSV |
| 22 | GET | `/swppp/api/sessions` | swppp | List sessions |
| 23 | GET | `/swppp/api/sessions/{name}` | swppp | Load session |
| 24 | POST | `/swppp/api/sessions/{name}` | swppp | Save session |
| 25 | DELETE | `/swppp/api/sessions/{name}` | swppp | Delete session |
| 26 | GET | `/swppp/api/sessions/{name}/export` | swppp | Export session JSON |
| 27 | POST | `/swppp/api/sessions/import` | swppp | Import session JSON |
