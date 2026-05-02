from __future__ import annotations

# ============================================================
#  SWPPP Scheduler – Reconciliation Engine
#
#  Drives automated weekly + rain-event PDF generation for
#  all active projects with auto_weekly_enabled=1.
#
#  Entry point: run_due_reports(conn, *, dry_run, force) -> dict
# ============================================================

import logging
import os
import sqlite3
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# How many unfiled dates triggers the "too many gaps" safety gate.
_GATE_THRESHOLD = 10

# How many days back to look for rain events.
_RAIN_LOOKBACK_DAYS = 14


def _get_scheduled_dates(
    project: dict[str, Any],
    filed_dates: set[str],
    today: date,
) -> list[date]:
    """Return weekly scheduled dates from project_start_date up to today (inclusive)
    that have not yet been filed.

    schedule_day_of_week: 0=Monday … 6=Sunday (Python weekday convention).
    """
    start_raw = project.get("project_start_date")
    if not start_raw:
        return []
    try:
        start = date.fromisoformat(start_raw)
    except ValueError:
        log.warning(
            "Project %s has invalid project_start_date %r — skipping scheduled dates",
            project["id"],
            start_raw,
        )
        return []

    dow = int(project.get("schedule_day_of_week", 5))  # default Friday

    # Find the first occurrence of dow on or after start.
    days_ahead = (dow - start.weekday()) % 7
    first = start + timedelta(days=days_ahead)

    due: list[date] = []
    current = first
    while current <= today:
        if current.isoformat() not in filed_dates:
            due.append(current)
        current += timedelta(weeks=1)

    return due


def _get_rain_event_dates(
    project: dict[str, Any],
    filed_dates: set[str],
    today: date,
) -> list[date]:
    """Fetch Mesonet rainfall for the last _RAIN_LOOKBACK_DAYS and return days
    that meet or exceed rain_threshold_inches and have not been filed."""
    from app.core.mesonet import fetch_rainfall

    station = project.get("rain_station_code", "")
    if not station:
        return []

    threshold = float(project.get("rain_threshold_inches", 0.5))
    lookback_start = today - timedelta(days=_RAIN_LOOKBACK_DAYS - 1)

    try:
        result = fetch_rainfall(station, lookback_start, today)
    except Exception as exc:
        log.warning(
            "Mesonet fetch failed for project %s station %s: %s",
            project["id"],
            station,
            exc,
        )
        return []

    qualifying: list[date] = []
    for rain_day in result.days:
        # Values < -990 mean missing — never treat as zero.
        if rain_day.rainfall_inches < -990:
            continue
        if rain_day.rainfall_inches >= threshold:
            if rain_day.date.isoformat() not in filed_dates:
                qualifying.append(rain_day.date)

    return qualifying


def _resolve_data_dir() -> Path:
    """Return the base data directory (mirrors auth/main.py _resolve_mailbox_file_path logic)."""
    tools_data = os.environ.get("TOOLS_DATA_DIR", "")
    if tools_data:
        return Path(tools_data)
    # Fallback for local dev: project root / web / data
    return Path(__file__).resolve().parent.parent / "data"


def _generate_pdf_for_date(
    *,
    project: dict[str, Any],
    template_data: dict[str, Any],
    report_date: date,
    report_type: str,  # "weekly" | "rain_event"
    out_dir: Path,
    dry_run: bool,
) -> Path | None:
    """Generate a single PDF for one date. Returns the written path, or None on failure."""
    if dry_run:
        log.info(
            "[dry-run] Would generate %s PDF for project %s on %s",
            report_type,
            project["id"],
            report_date,
        )
        return None

    from app.core.config_manager import build_project_info, build_run_options, load_mapping
    from app.core.fill import generate_batch
    from app.core.mesonet import RainDay
    from app.core.rain_fill import generate_rain_batch

    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    template_path = PROJECT_ROOT / "assets" / "template.pdf"
    mapping_path = PROJECT_ROOT / "app" / "core" / "odot_mapping.yaml"

    # Build project_fields from template_data (same pattern as auth/main.py preview).
    field_names = [
        "job_piece",
        "project_number",
        "contract_id",
        "location_description_1",
        "location_description_2",
        "re_odot_contact_1",
        "re_odot_contact_2",
        "inspection_type",
        "inspected_by",
        "reviewed_by",
    ]
    project_fields: dict[str, str] = {}
    for field_name in field_names:
        value = template_data.get(field_name)
        if value is not None:
            project_fields[field_name] = str(value)
    extra_fields = template_data.get("extra_fields", {})
    if extra_fields:
        project_fields.update(extra_fields)

    checkbox_states = template_data.get("checkboxes", {})

    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        mapping = load_mapping(mapping_path)
        project_obj = build_project_info(project_fields)
        date_str = report_date.isoformat()
        options = build_run_options(
            output_dir=str(out_dir),
            start_date=date_str,
            end_date=date_str,
            make_zip=False,
        )

        if report_type == "rain_event":
            rain_day = RainDay(date=report_date, rainfall_inches=0.0)
            original_type = str(template_data.get("inspection_type", ""))
            created = generate_rain_batch(
                template_path=str(template_path),
                project=project_obj,
                options=options,
                rain_days=[rain_day],
                mapping=mapping,
                checkbox_states=checkbox_states or None,
                notes_texts=None,
                original_inspection_type=original_type,
            )
        else:
            created = generate_batch(
                template_path=str(template_path),
                project=project_obj,
                options=options,
                dates=[report_date],
                mapping=mapping,
                checkbox_states=checkbox_states or None,
                notes_texts=None,
            )

        if not created:
            log.error(
                "generate_batch returned empty list for project %s date %s",
                project["id"],
                report_date,
            )
            return None

        return Path(created[0])

    except Exception as exc:
        log.error(
            "PDF generation error for project %s date %s: %s",
            project["id"],
            report_date,
            exc,
            exc_info=True,
        )
        return None


def _process_project(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    today: date,
    *,
    dry_run: bool,
    force: bool,
) -> dict[str, int]:
    """Process one project. Returns {"filed": N, "skipped": N, "failed": N}."""
    from web.auth import db

    project_id = project["id"]

    # Require an active template version to generate PDFs.
    active_version = db.get_active_template_version(conn, project_id)
    if not active_version:
        log.warning("Project %s has no active template version — skipping", project_id)
        db.create_project_run_log(
            conn,
            project_id=project_id,
            run_date=today.isoformat(),
            status="skipped",
            error_type="no_active_template",
            error_message="No active template version found",
        )
        db.update_project_run_state(
            conn, project_id, last_run_at=_utc_now(), last_run_status="skipped"
        )
        return {"filed": 0, "skipped": 1, "failed": 0}

    template_data: dict[str, Any] = active_version["template_data"]
    template_version_id: str = active_version["id"]

    # Collect all already-filed dates for this project.
    existing = db.get_mailbox_entries(conn, project_id, sort_order="asc")
    filed_dates: set[str] = {e["report_date"] for e in existing}

    # Compute missing weekly dates.
    missing_weekly = _get_scheduled_dates(project, filed_dates, today)

    # Safety gate: if there are more than _GATE_THRESHOLD unfiled dates and
    # force is not set, skip to avoid runaway retroactive generation.
    if len(missing_weekly) > _GATE_THRESHOLD and not force:
        msg = (
            f"{len(missing_weekly)} unfiled weekly dates found; "
            f"exceeds gate threshold ({_GATE_THRESHOLD}). Use --force to override."
        )
        log.warning("Project %s: %s", project_id, msg)
        db.create_project_run_log(
            conn,
            project_id=project_id,
            run_date=today.isoformat(),
            status="skipped",
            error_type="gate_threshold",
            error_message=msg,
        )
        db.update_project_run_state(
            conn, project_id, last_run_at=_utc_now(), last_run_status="skipped"
        )
        return {"filed": 0, "skipped": 1, "failed": 0}

    # Compute missing rain-event dates.
    missing_rain = _get_rain_event_dates(project, filed_dates, today)

    # Nothing to do.
    if not missing_weekly and not missing_rain:
        log.info("Project %s: up to date, nothing to file", project_id)
        db.create_project_run_log(
            conn,
            project_id=project_id,
            run_date=today.isoformat(),
            status="ok",
            reports_filed=0,
        )
        db.update_project_run_state(
            conn,
            project_id,
            last_run_at=_utc_now(),
            last_run_status="ok",
            last_successful_run_at=_utc_now(),
        )
        return {"filed": 0, "skipped": 0, "failed": 0}

    data_dir = _resolve_data_dir()
    reports_dir = data_dir / "reports" / project_id

    filed_count = 0
    failed_count = 0

    all_dates: list[tuple[date, str]] = [
        (d, "weekly") for d in missing_weekly
    ] + [(d, "rain_event") for d in missing_rain]

    for report_date, report_type in all_dates:
        generation_mode = "scheduled" if report_date == today else "retroactive"

        with tempfile.TemporaryDirectory(prefix="swppp_sched_") as tmpdir:
            pdf_path = _generate_pdf_for_date(
                project=project,
                template_data=template_data,
                report_date=report_date,
                report_type=report_type,
                out_dir=Path(tmpdir),
                dry_run=dry_run,
            )

        if dry_run:
            # Count as filed for reporting purposes.
            filed_count += 1
            continue

        if pdf_path is None:
            failed_count += 1
            db.create_project_run_log(
                conn,
                project_id=project_id,
                run_date=report_date.isoformat(),
                status="failed",
                error_type="pdf_generation_error",
                error_message="generate_batch returned no output",
            )
            continue

        # Move PDF to stable location.
        reports_dir.mkdir(parents=True, exist_ok=True)
        dest_name = f"{report_type}_{report_date.isoformat()}.pdf"
        dest_path = reports_dir / dest_name

        try:
            import shutil

            shutil.move(str(pdf_path), str(dest_path))
        except Exception as exc:
            log.error(
                "Failed to move PDF for project %s date %s: %s",
                project_id,
                report_date,
                exc,
            )
            failed_count += 1
            continue

        # Compute relative path for storage (relative to data_dir).
        try:
            relative_path = str(dest_path.relative_to(data_dir))
        except ValueError:
            relative_path = str(dest_path)

        file_size = dest_path.stat().st_size if dest_path.exists() else None

        db.create_mailbox_entry(
            conn,
            project_id=project_id,
            company_id=project["company_id"],
            report_date=report_date.isoformat(),
            report_type=report_type,
            file_path=relative_path,
            generation_mode=generation_mode,
            file_size_bytes=file_size,
            template_version_id=template_version_id,
        )

        log.info(
            "Filed %s report for project %s on %s (%s)",
            report_type,
            project_id,
            report_date,
            generation_mode,
        )
        filed_count += 1

    final_status = "ok" if failed_count == 0 else "partial_failure"
    db.create_project_run_log(
        conn,
        project_id=project_id,
        run_date=today.isoformat(),
        status=final_status,
        reports_filed=filed_count,
    )
    last_successful = _utc_now() if failed_count == 0 else None
    db.update_project_run_state(
        conn,
        project_id,
        last_run_at=_utc_now(),
        last_run_status=final_status,
        last_successful_run_at=last_successful,
    )

    return {"filed": filed_count, "skipped": 0, "failed": failed_count}


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def run_due_reports(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    force: bool = False,
    company_id: str | None = None,
) -> dict[str, int]:
    """Main entry point for the scheduler.

    Iterates over all active projects due for a run, reconciles weekly and
    rain-event dates, generates PDFs, and files them in the mailbox.

    If company_id is provided, only projects belonging to that company are processed.

    Returns a summary dict: {projects_processed, reports_filed, failures, skipped}.
    """
    from web.auth import db

    projects = db.get_projects_due_for_run(conn)
    if company_id is not None:
        projects = [p for p in projects if p.get("company_id") == company_id]
    log.info(
        "Scheduler starting: %d project(s) due, dry_run=%s force=%s",
        len(projects),
        dry_run,
        force,
    )

    today = date.today()
    total_filed = 0
    total_failures = 0
    total_skipped = 0

    for project in projects:
        project_id = project["id"]
        try:
            result = _process_project(
                conn, project, today, dry_run=dry_run, force=force
            )
            total_filed += result["filed"]
            total_failures += result["failed"]
            total_skipped += result["skipped"]
        except Exception as exc:
            # Per-project isolation: one project failure must not stop others.
            log.error(
                "Unhandled error processing project %s: %s",
                project_id,
                exc,
                exc_info=True,
            )
            total_failures += 1
            try:
                db.create_project_run_log(
                    conn,
                    project_id=project_id,
                    run_date=today.isoformat(),
                    status="failed",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                db.update_project_run_state(
                    conn, project_id, last_run_at=_utc_now(), last_run_status="failed"
                )
            except Exception:
                log.error(
                    "Failed to record run log for project %s after unhandled error",
                    project_id,
                    exc_info=True,
                )

    # Optional heartbeat ping (e.g. healthchecks.io).
    hc_url = os.environ.get("HEALTHCHECKS_URL", "")
    if hc_url:
        try:
            import requests

            requests.get(hc_url, timeout=5)
            log.info("Heartbeat sent to %s", hc_url)
        except Exception as exc:
            log.warning("Heartbeat failed: %s", exc)

    summary = {
        "projects_processed": len(projects),
        "reports_filed": total_filed,
        "failures": total_failures,
        "skipped": total_skipped,
    }
    log.info("Scheduler complete: %s", summary)
    return summary
