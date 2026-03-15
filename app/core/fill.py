# ============================================================
#  SWPPP AutoFill – Core Fill Logic
#
#  CURRENT BEHAVIOR (V1):
#    - Makes one PDF copy of the template per inspection date.
#    - Names them like: inspection_01_20250101.pdf
#    - Optionally bundles them into swppp_outputs.zip
#
#  FUTURE BEHAVIOR (V2+):
#    - Actually writes project fields and YES/NO/N/A selections
#      into the PDF (checkmarks, text, etc.) using a PDF library.
#
#  NOTE:
#    Right now this is focused on getting a stable pipeline:
#      GUI -> generate_batch(...) -> real files on disk.
#    So the PDFs are still visually blank copies of template.pdf.
# ============================================================

from __future__ import annotations

import logging
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from pypdf import PdfReader, PdfWriter

from app.core.model import ProjectInfo, RunOptions, TemplateMap
from app.core.pdf_fields import populate_checkbox_targets

log = logging.getLogger(__name__)

# ============================================================
#  Small Helpers
# ============================================================


def _project_to_dict(project: ProjectInfo) -> Dict[str, Any]:
    if hasattr(project, "model_dump"):
        return project.model_dump(exclude_none=True)
    return project.__dict__.copy()


def _date_to_string(dt: Any, date_format: str) -> str:
    """
    Best-effort conversion of our weekly_dates output to a string.

    weekly_dates(...) usually yields datetime.date objects.
    We support:
      - datetime / date -> strftime
      - anything else   -> str()
    """
    if isinstance(dt, (datetime,)):
        return dt.strftime(date_format)
    # datetime.date or similar has .strftime too
    if hasattr(dt, "strftime"):
        try:
            return dt.strftime(date_format)
        except Exception:
            pass
    return str(dt)


def _build_field_updates(
    project_dict: Dict[str, Any],
    mapping: TemplateMap,
    dt: Any,
    default_date_format: str,
    checkbox_states: Optional[Dict[str, Dict[str, str]]] = None,
    notes_texts: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    field_updates: Dict[str, str] = {}

    for model_key, field_mapping in mapping.fields.items():
        value = project_dict.get(model_key, "")
        field_updates[field_mapping.target_name] = "" if value is None else str(value)

    for date_field in mapping.date_fields:
        field_updates[date_field.target_name] = _date_to_string(
            dt,
            date_field.format or default_date_format,
        )

    checkbox_states = checkbox_states or {}
    for group_key, group in mapping.checkboxes.items():
        answers = checkbox_states.get(group_key, {})
        for item in group.pdf_fields:
            for field_name in (item.yes_field, item.no_field, item.na_field):
                if field_name:
                    field_updates[field_name] = "/Off"

            answer = answers.get(item.text, "")
            if answer == "YES" and item.yes_field:
                field_updates[item.yes_field] = item.yes_value
            elif answer == "NO" and item.no_field:
                field_updates[item.no_field] = item.no_value
            elif answer == "N/A" and item.na_field:
                field_updates[item.na_field] = item.na_value

    notes = notes_texts or {}
    for group_key, group in mapping.checkboxes.items():
        if group.notes_field and group_key in notes:
            field_updates[group.notes_field] = notes[group_key]

    return field_updates


def _write_filled_pdf(
    template: Path, pdf_out: Path, field_updates: Dict[str, str]
) -> None:
    try:
        reader = PdfReader(str(template))
    except Exception as exc:
        raise ValueError(
            f"Template PDF is not readable by pypdf: {template}. {exc}"
        ) from exc

    available_fields = set((reader.get_fields() or {}).keys())
    if field_updates and not available_fields:
        raise ValueError(
            f"Template PDF does not contain fillable AcroForm fields: {template}"
        )

    missing_fields = sorted(
        name for name in field_updates if name not in available_fields
    )
    if missing_fields:
        preview = ", ".join(missing_fields[:10])
        suffix = "" if len(missing_fields) <= 10 else ", ..."
        raise ValueError(
            f"Template PDF is missing mapped form fields: {preview}{suffix}"
        )

    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    for page in writer.pages:
        writer.update_page_form_field_values(page, field_updates, auto_regenerate=False)

    with pdf_out.open("wb") as stream:
        writer.write(stream)


# ============================================================
#  Main Entry Point
# ============================================================


def generate_batch(
    *,
    template_path: str,
    project: ProjectInfo,
    options: RunOptions,
    dates: Iterable[Any],
    mapping: TemplateMap,
    checkbox_states: Optional[Dict[str, Any]] = None,
    notes_texts: Optional[Dict[str, str]] = None,
) -> List[str]:
    """
    Generate one inspection PDF per date.

    Inputs:
      - template_path : path to the base inspection form PDF
      - project       : ProjectInfo with all text fields
      - options       : RunOptions (needs output_dir, date_format, make_zip)
      - dates         : iterable of dates from weekly_dates(...)
      - mapping       : TemplateMap from YAML (not fully used yet)
      - checkbox_states:
            Nested dict from the GUI:
              { group_key: { "Question text": "YES"/"NO"/"N/A" or "" }, ... }

        CURRENT IMPLEMENTATION:
            - For each date:
                    * fill mapped AcroForm text/date fields
                    * write inspection_XX_YYYYMMDD.pdf
      - If options.make_zip is True:
          * create swppp_outputs.zip containing all created PDFs

    RETURNS:
      - List of created file paths (PDFs, plus ZIP if created)

    This keeps the whole flow alive without crashing. Once we
    agree on a PDF-writing approach, we will:
      - replace the plain copy with "fill PDF fields / draw overlay" logic.
    """

    template = Path(template_path)
    if not template.exists():
        raise FileNotFoundError(f"Template PDF not found: {template}")

    outdir = Path(options.output_dir or ".").resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    created: List[str] = []

    project_dict = _project_to_dict(project)
    checks = checkbox_states or {}
    populate_checkbox_targets(mapping, template)

    # Materialize dates so we can loop reliably
    date_list = list(dates)

    if not date_list:
        # No dates = nothing to do (fail gently)
        return created

    for idx, dt in enumerate(date_list, start=1):
        date_for_name = _date_to_string(dt, "%Y%m%d")

        base_name = f"inspection_{idx:02d}_{date_for_name}"
        pdf_out = outdir / f"{base_name}.pdf"

        field_updates = _build_field_updates(
            project_dict=project_dict,
            mapping=mapping,
            dt=dt,
            default_date_format=options.date_format,
            checkbox_states=checks,
            notes_texts=notes_texts,
        )
        try:
            _write_filled_pdf(template, pdf_out, field_updates)
        except OSError as exc:
            log.error("Failed to write %s: %s", pdf_out, exc)
            continue
        created.append(str(pdf_out))

    # --------------------------------------------------------
    #  Optional ZIP bundle of all created PDFs
    # --------------------------------------------------------
    if getattr(options, "make_zip", False):
        zip_path = outdir / "swppp_outputs.zip"
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for path_str in created:
                    p = Path(path_str)
                    # store relative to output dir so ZIP is clean
                    zf.write(p, arcname=p.name)
            created.append(str(zip_path))
        except OSError as exc:
            log.error("Failed to create ZIP %s: %s", zip_path, exc)

    return created
