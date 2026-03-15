from pathlib import Path

from pypdf import PdfReader

from app.core.config_manager import build_project_info, build_run_options, load_mapping
from app.core.dates import weekly_dates
from app.core.fill import generate_batch

TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "template.pdf"
MAPPING_FILE = (
    Path(__file__).resolve().parents[1] / "app" / "core" / "odot_mapping.yaml"
)


def test_real_template_text_fields_fill(tmp_path) -> None:
    template_path = TEMPLATE
    mapping_path = MAPPING_FILE

    assert template_path.exists()
    assert mapping_path.exists()

    mapping = load_mapping(mapping_path)
    project = build_project_info(
        {
            "job_piece": "JP-101",
            "project_number": "PN-202",
            "contract_id": "C-303",
            "location_description_1": "Northbound lane",
            "location_description_2": "Bridge approach",
            "re_odot_contact_1": "Jane Doe",
            "re_odot_contact_2": "John Doe",
            "inspection_type": "Weekly",
        }
    )
    options = build_run_options(
        output_dir=str(tmp_path / "out"),
        start_date="2025-01-01",
        end_date="2025-01-01",
        make_zip=False,
    )

    written = generate_batch(
        template_path=str(template_path),
        project=project,
        options=options,
        dates=list(weekly_dates(options.start_date, options.end_date)),
        mapping=mapping,
    )

    assert len(written) == 1

    reader = PdfReader(written[0])
    fields = reader.get_fields() or {}

    assert fields["Date"].value == "01/01/2025"
    assert fields["Job Piece"].value == "JP-101"
    assert fields["Project Number"].value == "PN-202"
    assert fields["Contract ID"].value == "C-303"
    assert fields["Location Description"].value == "Northbound lane"
    assert fields["Location Description_2"].value == "Bridge approach"
    assert fields["RE andor ODOT Contact"].value == "Jane Doe"
    assert fields["RE andor ODOT Contact_2"].value == "John Doe"
    assert fields["Type of Inspection"].value == "Weekly"
