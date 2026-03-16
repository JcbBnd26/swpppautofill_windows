# ============================================================
#  Tests for app.core.rain_fill — Rain Event PDF Generation
# ============================================================

from datetime import date
from pathlib import Path

import pytest

from app.core.config_manager import build_project_info, build_run_options, load_mapping
from app.core.mesonet import RainDay
from app.core.rain_fill import generate_rain_batch

TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "template.pdf"
MAPPING_FILE = (
    Path(__file__).resolve().parents[1] / "app" / "core" / "odot_mapping.yaml"
)


@pytest.fixture
def mapping():
    return load_mapping(MAPPING_FILE)


@pytest.fixture
def project():
    return build_project_info(
        {
            "job_piece": "JP-001",
            "project_number": "PN-999",
            "contract_id": "C-123",
            "location_description_1": "Highway 66 Bridge",
            "inspection_type": "Weekly Walkthrough",
        }
    )


@pytest.fixture
def rain_days():
    return [
        RainDay(date=date(2025, 4, 10), rainfall_inches=0.75),
        RainDay(date=date(2025, 4, 15), rainfall_inches=1.20),
        RainDay(date=date(2025, 4, 22), rainfall_inches=0.55),
    ]


@pytest.fixture
def options(tmp_path):
    return build_run_options(
        output_dir=str(tmp_path),
        start_date="2025-04-01",
        end_date="2025-04-30",
        date_format="%m/%d/%Y",
        make_zip=False,
    )


class TestGenerateRainBatch:

    @pytest.mark.skipif(not TEMPLATE.exists(), reason="template.pdf not present")
    def test_creates_correct_number_of_pdfs(self, mapping, project, rain_days, options):
        written = generate_rain_batch(
            template_path=str(TEMPLATE),
            project=project,
            options=options,
            rain_days=rain_days,
            mapping=mapping,
            original_inspection_type="Weekly Walkthrough",
        )
        pdfs = [p for p in written if p.endswith(".pdf")]
        assert len(pdfs) == 3

    @pytest.mark.skipif(not TEMPLATE.exists(), reason="template.pdf not present")
    def test_filenames_follow_pattern(self, mapping, project, rain_days, options):
        written = generate_rain_batch(
            template_path=str(TEMPLATE),
            project=project,
            options=options,
            rain_days=rain_days,
            mapping=mapping,
            original_inspection_type="Weekly",
        )
        names = [Path(p).name for p in written]
        assert "rain_event_01_20250410.pdf" in names
        assert "rain_event_02_20250415.pdf" in names
        assert "rain_event_03_20250422.pdf" in names

    @pytest.mark.skipif(not TEMPLATE.exists(), reason="template.pdf not present")
    def test_inspection_type_prepended(self, mapping, project, rain_days, options):
        from pypdf import PdfReader

        written = generate_rain_batch(
            template_path=str(TEMPLATE),
            project=project,
            options=options,
            rain_days=rain_days[:1],
            mapping=mapping,
            original_inspection_type="Weekly Walkthrough",
        )
        reader = PdfReader(written[0])
        fields = reader.get_fields() or {}
        type_field = fields.get("Type of Inspection")
        if type_field:
            val = type_field.get("/V", "")
            assert "Rain Event" in val

    @pytest.mark.skipif(not TEMPLATE.exists(), reason="template.pdf not present")
    def test_empty_original_type(self, mapping, project, rain_days, options):
        from pypdf import PdfReader

        written = generate_rain_batch(
            template_path=str(TEMPLATE),
            project=project,
            options=options,
            rain_days=rain_days[:1],
            mapping=mapping,
            original_inspection_type="",
        )
        reader = PdfReader(written[0])
        fields = reader.get_fields() or {}
        type_field = fields.get("Type of Inspection")
        if type_field:
            val = type_field.get("/V", "")
            assert val == "Rain Event" or "Rain Event" in val

    @pytest.mark.skipif(not TEMPLATE.exists(), reason="template.pdf not present")
    def test_correct_date_in_pdf(self, mapping, project, options):
        from pypdf import PdfReader

        single_day = [RainDay(date=date(2025, 7, 4), rainfall_inches=0.80)]
        written = generate_rain_batch(
            template_path=str(TEMPLATE),
            project=project,
            options=options,
            rain_days=single_day,
            mapping=mapping,
            original_inspection_type="Test",
        )
        reader = PdfReader(written[0])
        fields = reader.get_fields() or {}
        date_field = fields.get("Date")
        if date_field:
            val = date_field.get("/V", "")
            assert "07/04/2025" in val

    def test_empty_rain_days_returns_empty(self, mapping, project, options):
        written = generate_rain_batch(
            template_path=str(TEMPLATE),
            project=project,
            options=options,
            rain_days=[],
            mapping=mapping,
            original_inspection_type="Weekly",
        )
        assert written == []

    @pytest.mark.skipif(not TEMPLATE.exists(), reason="template.pdf not present")
    def test_zip_bundle_created(self, mapping, project, rain_days, tmp_path):
        """bundle_outputs_zip combines PDFs into a single ZIP."""
        from app.core.fill import bundle_outputs_zip

        opts = build_run_options(
            output_dir=str(tmp_path),
            start_date="2025-04-01",
            end_date="2025-04-30",
            date_format="%m/%d/%Y",
            make_zip=False,
        )
        written = generate_rain_batch(
            template_path=str(TEMPLATE),
            project=project,
            options=opts,
            rain_days=rain_days,
            mapping=mapping,
            original_inspection_type="Weekly",
        )
        written = bundle_outputs_zip(written, tmp_path)
        zips = [p for p in written if p.endswith(".zip")]
        assert len(zips) == 1
        assert "swppp_outputs.zip" in zips[0]
