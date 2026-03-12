# ============================================================
#  SWPPP AutoFill – Rain Event PDF Generation
#
#  Generates one inspection PDF per qualifying rain day,
#  reusing all existing form data from the main inspection.
#  The only field that changes is "Type of Inspection",
#  which gets prepended with "Rain Event".
# ============================================================

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.fill import (_build_field_updates, _date_to_string,
                           _project_to_dict, _write_filled_pdf)
from app.core.mesonet import RainDay
from app.core.model import ProjectInfo, RunOptions, TemplateMap
from app.core.pdf_fields import populate_checkbox_targets


def generate_rain_batch(
    *,
    template_path: str,
    project: ProjectInfo,
    options: RunOptions,
    rain_days: list[RainDay],
    mapping: TemplateMap,
    checkbox_states: Optional[Dict[str, Any]] = None,
    original_inspection_type: str = "",
) -> List[str]:
    """
    Generate one inspection PDF per qualifying rain day.

    All fields carry over from the main inspection form.
    The "inspection_type" field is overridden to:
        "Rain Event - {original}" or just "Rain Event" if original is blank.

    Files are named: rain_event_01_YYYYMMDD.pdf
    """
    template = Path(template_path)
    if not template.exists():
        raise FileNotFoundError(f"Template PDF not found: {template}")

    outdir = Path(options.output_dir or ".").resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    if not rain_days:
        return []

    created: List[str] = []

    # Build the rain event inspection type
    original = (original_inspection_type or "").strip()
    rain_type = f"Rain Event - {original}" if original else "Rain Event"

    # Clone project and override inspection_type
    project_dict = _project_to_dict(project)
    project_dict["inspection_type"] = rain_type

    checks = checkbox_states or {}
    populate_checkbox_targets(mapping, template)

    # Sort rain days by date for consistent output ordering
    sorted_days = sorted(rain_days, key=lambda rd: rd.date)

    for idx, rain_day in enumerate(sorted_days, start=1):
        dt = datetime.combine(rain_day.date, datetime.min.time())
        date_for_name = _date_to_string(dt, "%Y%m%d")

        base_name = f"rain_event_{idx:02d}_{date_for_name}"
        pdf_out = outdir / f"{base_name}.pdf"

        field_updates = _build_field_updates(
            project_dict=project_dict,
            mapping=mapping,
            dt=dt,
            default_date_format=options.date_format,
            checkbox_states=checks,
        )
        _write_filled_pdf(template, pdf_out, field_updates)
        created.append(str(pdf_out))

    # Optional ZIP bundle
    if getattr(options, "make_zip", False) and created:
        zip_path = outdir / "rain_events.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path_str in created:
                p = Path(path_str)
                zf.write(p, arcname=p.name)
        created.append(str(zip_path))

    return created
