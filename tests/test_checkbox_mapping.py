from pathlib import Path

from pypdf import PdfReader

from app.core.config_manager import (build_project_info, build_run_options,
                                     load_mapping)
from app.core.dates import weekly_dates
from app.core.fill import generate_batch
from app.core.pdf_fields import (build_audit_mapping_document,
                                 extract_checkbox_rows,
                                 populate_checkbox_targets)


def test_extract_checkbox_rows_matches_config_count() -> None:
    rows = extract_checkbox_rows(Path("assets/template.pdf"))
    mapping = load_mapping(Path("app/core/config_example.yaml"))
    item_count = sum(len(group.pdf_fields) for group in mapping.checkboxes.values())

    assert len(rows) == item_count == 38
    assert rows[0].yes is not None
    assert rows[0].no is not None
    assert rows[1].na is not None


def test_generate_batch_fills_checkbox_values(tmp_path) -> None:
    mapping = load_mapping(Path("app/core/config_example.yaml"))
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
        }
    }

    written = generate_batch(
        template_path="assets/template.pdf",
        project=project,
        options=options,
        dates=list(weekly_dates(options.start_date, options.end_date)),
        mapping=mapping,
        checkbox_states=checkbox_states,
    )

    fields = PdfReader(written[0]).get_fields() or {}

    assert fields["undefined"].value == "/On"
    assert fields["undefined_2"].value == "/Off"
    assert fields["undefined_3"].value == "/NA"
    assert fields["undefined_4"].value == "/Off"
    assert fields["undefined_5"].value == "/On"


def test_populate_checkbox_targets_assigns_inferred_fields() -> None:
    mapping = load_mapping(Path("app/core/config_example.yaml"))

    populate_checkbox_targets(mapping, Path("assets/template.pdf"))

    first_row = mapping.checkboxes["Erosion_Minimization"].pdf_fields[0]
    second_row = mapping.checkboxes["Erosion_Minimization"].pdf_fields[1]

    assert first_row.yes_field == "undefined"
    assert first_row.no_field == "undefined_2"
    assert first_row.na_field is None
    assert second_row.yes_field == "undefined_3"
    assert second_row.na_field == "undefined_3"


def test_build_audit_mapping_document_includes_checkbox_targets() -> None:
    mapping = load_mapping(Path("app/core/config_example.yaml"))

    document = build_audit_mapping_document(mapping, Path("assets/template.pdf"))
    first_row = document["checkboxes"]["Erosion_Minimization"]["pdf_fields"][0]
    second_row = document["checkboxes"]["Erosion_Minimization"]["pdf_fields"][1]

    assert first_row["yes_field"] == "undefined"
    assert first_row["no_field"] == "undefined_2"
    assert first_row["yes_value"] == "/On"
    assert second_row["yes_field"] == "undefined_3"
    assert second_row["no_value"] == "/NO"
    assert second_row["na_value"] == "/NA"