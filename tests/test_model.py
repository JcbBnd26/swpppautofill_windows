from app.core.model import CheckboxItem, TemplateMap


def test_template_map_normalizes_legacy_shapes() -> None:
    mapping = TemplateMap.model_validate(
        {
            "fields": {
                "job_piece": "Job Piece",
                "project_number": {
                    "label": "Project Number",
                    "pdf_field_name": "Project Number",
                },
            },
            "date_fields": ["Date"],
            "checkboxes": {
                "Example": {
                    "pdf_fields": [
                        "Question A",
                        {"text": "Question B", "allow_na": False},
                    ]
                }
            },
        }
    )

    assert mapping.fields["job_piece"].label == "Job Piece"
    assert mapping.fields["job_piece"].target_name == "Job Piece"
    assert mapping.date_fields[0].target_name == "Date"
    assert mapping.checkboxes["Example"].pdf_fields[0].text == "Question A"
    assert mapping.checkboxes["Example"].pdf_fields[1].allow_na is False


def test_template_map_preserves_explicit_pdf_targets() -> None:
    mapping = TemplateMap.model_validate(
        {
            "fields": {
                "job_piece": {
                    "label": "Job Piece",
                    "pdf_field_name": "JP_Field",
                }
            },
            "date_fields": [
                {
                    "label": "Inspection Date",
                    "pdf_field_name": "Date_Field",
                    "format": "%Y-%m-%d",
                }
            ],
        }
    )

    assert mapping.fields["job_piece"].target_name == "JP_Field"
    assert mapping.date_fields[0].target_name == "Date_Field"
    assert mapping.date_fields[0].format == "%Y-%m-%d"


def test_checkbox_item_defaults_are_safe() -> None:
    """Explicit targets without value overrides must default to /Off (no-render)."""
    item = CheckboxItem(
        text="Sample question",
        yes_field="field_yes",
        no_field="field_no",
        na_field="field_na",
    )
    assert item.yes_value == "/On"
    assert item.no_value == "/Off"
    assert item.na_value == "/Off"
