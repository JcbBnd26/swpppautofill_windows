from pathlib import Path

from pypdf import PdfReader

from app.core.config_manager import build_project_info, build_run_options, load_mapping
from app.core.dates import weekly_dates
from app.core.fill import generate_batch
from app.core.pdf_fields import (
    build_audit_mapping_document,
    extract_checkbox_rows,
    populate_checkbox_targets,
)

TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "template.pdf"
MAPPING_FILE = (
    Path(__file__).resolve().parents[1] / "app" / "core" / "odot_mapping.yaml"
)


def test_extract_checkbox_rows_matches_config_count() -> None:
    rows = extract_checkbox_rows(TEMPLATE)
    mapping = load_mapping(MAPPING_FILE)
    item_count = sum(len(group.pdf_fields) for group in mapping.checkboxes.values())

    assert len(rows) == item_count == 38
    assert rows[0].yes is not None
    assert rows[0].no is not None
    assert rows[1].na is not None


def test_generate_batch_fills_checkbox_values(tmp_path) -> None:
    mapping = load_mapping(MAPPING_FILE)
    project = build_project_info({"job_piece": "JP-101"})
    options = build_run_options(
        output_dir=str(tmp_path / "out"),
        start_date="2025-01-01",
        end_date="2025-01-01",
        make_zip=False,
    )
    checkbox_states = {
        "Erosion_Minimization": {
            "BMPs are in place to minimize erosion?": "YES",
            "Areas of work are delineated and steep slope disturbance is minimized?": "N/A",
            "Soils are stabilized where work has stopped for 14 days?": "NO",
        },
        "Solid_And_Hazardous_Waste": {
            "Building products and chemicals (pesticides, herbicides, fertilizer, etc.) are covered?": "YES",
            "Solid and hazardous waste is stored and disposed of properly?": "NO",
        },
        "Documentation_And_SWPPP": {
            "Notice is posted with permit number, contact information, project description and SWPPP location?": "NO",
            "Was the SWPPP inspected on this visit?": "YES",
        },
    }

    # Populate the mapping so we can look up expected field names
    populate_checkbox_targets(mapping, TEMPLATE)

    written = generate_batch(
        template_path=str(TEMPLATE),
        project=project,
        options=options,
        dates=list(weekly_dates(options.start_date, options.end_date)),
        mapping=mapping,
        checkbox_states=checkbox_states,
    )

    fields = PdfReader(written[0]).get_fields() or {}

    # --- Erosion_Minimization (rows 0-2) ---
    assert fields["undefined"].value == "/On"
    assert fields["undefined_2"].value == "/Off"
    assert fields["undefined_3"].value == "/NA"
    assert fields["undefined_4"].value == "/Off"
    assert fields["undefined_5"].value == "/On"

    # --- Solid_And_Hazardous_Waste (mid-range group) ---
    sw_items = mapping.checkboxes["Solid_And_Hazardous_Waste"].pdf_fields
    sw_first = sw_items[0]  # "Building products..." -> YES (separate fields)
    assert fields[sw_first.yes_field].value == sw_first.yes_value
    assert fields[sw_first.no_field].value == "/Off"
    sw_second = sw_items[
        1
    ]  # "Solid and hazardous waste..." -> NO (single multi-state field)
    assert fields[sw_second.no_field].value == sw_second.no_value

    # --- Documentation_And_SWPPP (late group) ---
    doc_items = mapping.checkboxes["Documentation_And_SWPPP"].pdf_fields
    doc_first = doc_items[0]  # "Notice is posted..." -> NO (separate fields)
    assert fields[doc_first.no_field].value == doc_first.no_value
    assert fields[doc_first.yes_field].value == "/Off"
    doc_second = doc_items[
        1
    ]  # "Was the SWPPP inspected..." -> YES (single multi-state field)
    assert fields[doc_second.yes_field].value == doc_second.yes_value


def test_populate_checkbox_targets_assigns_inferred_fields() -> None:
    mapping = load_mapping(MAPPING_FILE)

    populate_checkbox_targets(mapping, TEMPLATE)

    first_row = mapping.checkboxes["Erosion_Minimization"].pdf_fields[0]
    second_row = mapping.checkboxes["Erosion_Minimization"].pdf_fields[1]

    assert first_row.yes_field == "undefined"
    assert first_row.no_field == "undefined_2"
    assert first_row.na_field is None
    assert second_row.yes_field == "undefined_3"
    assert second_row.na_field == "undefined_3"


def test_build_audit_mapping_document_includes_checkbox_targets() -> None:
    mapping = load_mapping(MAPPING_FILE)

    document = build_audit_mapping_document(mapping, TEMPLATE)
    first_row = document["checkboxes"]["Erosion_Minimization"]["pdf_fields"][0]
    second_row = document["checkboxes"]["Erosion_Minimization"]["pdf_fields"][1]

    assert first_row["yes_field"] == "undefined"
    assert first_row["no_field"] == "undefined_2"
    assert first_row["yes_value"] == "/On"
    assert second_row["yes_field"] == "undefined_3"
    assert second_row["no_value"] == "/NO"
    assert second_row["na_value"] == "/NA"
