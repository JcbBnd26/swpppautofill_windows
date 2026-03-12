import json
from pathlib import Path
from typing import Optional

import typer

from app.core.config_manager import (
    build_project_info,
    build_run_options,
    load_mapping,
    load_project_info,
)
from app.core.dates import weekly_dates
from app.core.fill import generate_batch

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command("run")
def run(
    template: Path = typer.Argument(..., help="Path to PDF template"),
    output_dir: Path = typer.Option(..., "--output-dir", help="Output folder"),
    start_date: str = typer.Option(..., help="Start date, e.g. 2025-11-01"),
    end_date: str = typer.Option(..., help="End date, e.g. 2025-12-01"),
    config: Optional[Path] = typer.Option(None, help="YAML mapping file"),
    project_json: Optional[str] = typer.Option(
        None, help="Inline JSON for project fields"
    ),
    project_file: Optional[Path] = typer.Option(
        None, help="YAML/JSON file for project fields"
    ),
    date_format: str = typer.Option("%m/%d/%Y", help="Date format to write into PDF"),
    no_zip: bool = typer.Option(False, help="Do not create a ZIP of outputs"),
):
    if config is None:
        config = Path(__file__).parent.parent / "core" / "config_example.yaml"
    mapping = load_mapping(config)

    data = {}
    if project_file:
        project = load_project_info(project_file)
    elif project_json:
        data = json.loads(project_json)
        project = build_project_info(data)
    else:
        typer.echo("Enter project info (press Enter to keep blank):")

        def ask(name, default=""):
            return typer.prompt(name, default=default)

        data["job_piece"] = ask("Job Piece")
        data["project_number"] = ask("Project Number")
        data["contract_id"] = ask("Contract ID")
        data["location_description_1"] = ask("Location Description (top)")
        data["location_description_2"] = ask("Location Description (bottom)")
        data["re_odot_contact_1"] = ask("RE and/or ODOT Contact (top)")
        data["re_odot_contact_2"] = ask("RE and/or ODOT Contact (bottom)")
        data["inspection_type"] = ask("Type of Inspection")
        project = build_project_info(data)

    options = build_run_options(
        start_date=start_date,
        end_date=end_date,
        date_format=date_format,
        output_dir=str(output_dir),
        make_zip=not no_zip,
    )
    dates = list(weekly_dates(options.start_date, options.end_date))
    written = generate_batch(
        template_path=str(template),
        project=project,
        options=options,
        dates=dates,
        mapping=mapping,
    )
    typer.echo("\nCreated:")
    for p in written:
        typer.echo(f"  - {p}")


if __name__ == "__main__":
    app()
