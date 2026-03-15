SWPPP AutoFill is a Windows-first Python app for generating weekly ODOT clean-water inspection PDFs from a fillable template. It includes a Tkinter GUI for day-to-day use, a Typer CLI for scripted runs, and inspection helpers for understanding the underlying PDF form fields.

## Current status

The app now fills the verified AcroForm text and date fields in `assets/template.pdf` and auto-detects the checklist button rows from the live PDF structure. The checkbox wiring is derived from the template button layout at runtime, so the current YAML stays readable while the code targets the real unnamed button fields behind the form.

## Requirements

- Windows with Python 3.10+
- Tkinter available in the Python installation
- A valid fillable PDF template at `assets/template.pdf`

## Install

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

After installation, the entry points are:

- `swppp` for the CLI
- `swppp-gui` for the GUI
- `swppp-inspect` for PDF field inspection

## CLI usage

Show the available options:

```powershell
swppp run --help
```

Example run:

```powershell
swppp run assets/template.pdf --output-dir .\out --start-date 2025-01-01 --end-date 2025-01-29 --project-json '{"job_piece":"JP-101","project_number":"PN-202","contract_id":"C-303","location_description_1":"Northbound lane","location_description_2":"Bridge approach","re_odot_contact_1":"Jane Doe","re_odot_contact_2":"John Doe","inspection_type":"Weekly"}'
```

You can also load project values from a YAML or JSON file with `--project-file`.

## GUI usage

Run:

```powershell
swppp-gui
```

The GUI reads its field definitions from `app/core/odot_mapping.yaml`, prompts for the date range and project details, and writes one PDF per inspection date plus an optional ZIP bundle.

## Template inspection

Inspect the template field values:

```powershell
swppp-inspect assets/template.pdf
```

Inspect field metadata:

```powershell
swppp-inspect assets/template.pdf --details
```

Inspect the checkbox row mapping inferred from the PDF structure:

```powershell
swppp-inspect assets/template.pdf --checkbox-rows
```

Export an audited YAML file with the inferred checkbox targets filled in:

```powershell
swppp-inspect assets/template.pdf --export-checkbox-mapping .\audit-mapping.yaml
```

Use a different source mapping file when exporting:

```powershell
swppp-inspect assets/template.pdf --config .\my-mapping.yaml --export-checkbox-mapping .\audit-mapping.yaml
```

## Configuration

The YAML mapping lives in `app/core/odot_mapping.yaml`.

- `fields` maps your project model keys to visible labels and PDF field names.
- `date_fields` controls which PDF date fields are populated and how they are formatted.
- `checkboxes` defines the logical checklist questions and whether each row allows `N/A`.

The runtime auto-detects the real checkbox field names from the template button layout, so you do not currently need to hardcode `undefined_*` field names into the YAML.
If you want a concrete audit artifact, export the inferred targets and review the generated YAML file.

## Development

Run the tests:

```powershell
python -m pytest
```

The current test suite covers:

- mapping/model normalization
- core fill behavior
- integration against the real template for text/date fields
- checkbox row extraction and inferred mapping coverage

## Troubleshooting

- If `pypdf` cannot read the template, verify that `assets/template.pdf` is the real form and not a placeholder file.
- If generation fails with missing mapped form fields, run `swppp-inspect assets/template.pdf --details` and compare the reported field names to `app/core/odot_mapping.yaml`.
- If checklist behavior looks wrong, run `swppp-inspect assets/template.pdf --checkbox-rows` to inspect the inferred checkbox row ordering.
- If you want to review the exact inferred checkbox targets, run `swppp-inspect assets/template.pdf --export-checkbox-mapping .\audit-mapping.yaml` and inspect the generated YAML.
