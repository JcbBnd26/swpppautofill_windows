from datetime import datetime

import pytest
from pypdf import PdfWriter

from app.core.fill import _build_field_updates, generate_batch
from app.core.model import ProjectInfo, RunOptions, TemplateMap


def _write_blank_pdf(path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with path.open("wb") as stream:
        writer.write(stream)


def test_build_field_updates_uses_explicit_targets() -> None:
    project = ProjectInfo(job_piece="JP-100", project_number="PN-200")
    mapping = TemplateMap.model_validate(
        {
            "fields": {
                "job_piece": {"label": "Job Piece", "pdf_field_name": "JP_Field"},
                "project_number": {
                    "label": "Project Number",
                    "pdf_field_name": "PN_Field",
                },
            },
            "date_fields": [{"pdf_field_name": "Date_Field", "format": "%Y-%m-%d"}],
        }
    )

    updates = _build_field_updates(
        project_dict=project.model_dump(exclude_none=True),
        mapping=mapping,
        dt=datetime(2025, 1, 8),
        default_date_format="%m/%d/%Y",
    )

    assert updates == {
        "JP_Field": "JP-100",
        "PN_Field": "PN-200",
        "Date_Field": "2025-01-08",
    }


def test_generate_batch_requires_fillable_fields(tmp_path) -> None:
    template_path = tmp_path / "blank.pdf"
    _write_blank_pdf(template_path)

    project = ProjectInfo(job_piece="JP-100")
    options = RunOptions(
        output_dir=str(tmp_path / "out"),
        start_date="2025-01-01",
        end_date="2025-01-01",
        make_zip=False,
    )
    mapping = TemplateMap.model_validate(
        {
            "fields": {
                "job_piece": {"label": "Job Piece", "pdf_field_name": "JP_Field"},
            }
        }
    )

    with pytest.raises(ValueError, match="does not contain fillable AcroForm fields"):
        generate_batch(
            template_path=str(template_path),
            project=project,
            options=options,
            dates=[datetime(2025, 1, 1)],
            mapping=mapping,
        )


def test_generate_batch_returns_empty_when_no_dates(tmp_path) -> None:
    template_path = tmp_path / "blank.pdf"
    _write_blank_pdf(template_path)

    created = generate_batch(
        template_path=str(template_path),
        project=ProjectInfo(),
        options=RunOptions(
            output_dir=str(tmp_path / "out"),
            start_date="2025-01-01",
            end_date="2025-01-01",
            make_zip=False,
        ),
        dates=[],
        mapping=TemplateMap(),
    )

    assert created == []
