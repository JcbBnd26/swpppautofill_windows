import json
import logging
from datetime import date as date_cls
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import typer
import yaml
from pydantic import ValidationError

from app.core.config_manager import (
    build_project_info,
    build_run_options,
    load_mapping,
    load_project_info,
)
from app.core.dates import weekly_dates
from app.core.fill import bundle_outputs_zip, generate_batch
from app.core.mesonet import fetch_rainfall, filter_rain_events
from app.core.mesonet_stations import STATIONS
from app.core.rain_fill import generate_rain_batch

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _setup_logging() -> None:
    log_path = Path.home() / "swppp_autofill.log"
    handler = RotatingFileHandler(log_path, maxBytes=1_048_576, backupCount=2)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    )
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.DEBUG)


@app.callback(invoke_without_command=True)
def _init(ctx: typer.Context) -> None:
    _setup_logging()


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
    station: Optional[str] = typer.Option(
        None, help="Mesonet station code (e.g. NRMN) for rain day PDFs"
    ),
    rain_threshold: float = typer.Option(
        0.5, help="Rainfall threshold in inches (default 0.5)"
    ),
    rain_csv: Optional[Path] = typer.Option(
        None, help="Path to a Mesonet rainfall CSV instead of fetching"
    ),
):
    if config is None:
        config = Path(__file__).parent.parent / "core" / "odot_mapping.yaml"
    try:
        mapping = load_mapping(config)
    except (FileNotFoundError, yaml.YAMLError, ValidationError) as exc:
        typer.echo(f"Error loading config '{config}': {exc}", err=True)
        raise typer.Exit(1) from exc

    data = {}
    if project_file:
        try:
            project = load_project_info(project_file)
        except (
            FileNotFoundError,
            json.JSONDecodeError,
            yaml.YAMLError,
            ValidationError,
        ) as exc:
            typer.echo(f"Error loading project file '{project_file}': {exc}", err=True)
            raise typer.Exit(1) from exc
    elif project_json:
        try:
            data = json.loads(project_json)
        except json.JSONDecodeError as exc:
            typer.echo(f"Invalid JSON for --project-json: {exc}", err=True)
            raise typer.Exit(1) from exc
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
        make_zip=False,
    )
    dates = list(weekly_dates(options.start_date, options.end_date))
    written = generate_batch(
        template_path=str(template),
        project=project,
        options=options,
        dates=dates,
        mapping=mapping,
    )

    # --- Rain day PDFs ---
    if station or rain_csv:
        from app.core.mesonet import parse_rainfall_csv_file

        if rain_csv:
            typer.echo(f"\nLoading rain data from {rain_csv} ...")
            try:
                all_days = parse_rainfall_csv_file(rain_csv)
            except FileNotFoundError as exc:
                typer.echo(f"Error: {exc}", err=True)
                raise typer.Exit(1) from exc
        else:
            code = station.upper()
            if code not in STATIONS:
                typer.echo(
                    f"Warning: '{code}' is not a known Mesonet station code.",
                    err=True,
                )
            try:
                s = date_cls.fromisoformat(start_date)
                e = date_cls.fromisoformat(end_date)
            except ValueError as exc:
                typer.echo(f"Invalid date format: {exc}", err=True)
                raise typer.Exit(1) from exc
            typer.echo(f"\nFetching rain data for {code} ({s} to {e}) ...")
            result = fetch_rainfall(code, s, e)
            all_days = result.days
            if result.failed:
                typer.echo(f"  Warning: {result.failed} day(s) failed to fetch.")
            if result.missing:
                typer.echo(f"  Warning: {result.missing} day(s) had missing data.")

        events = filter_rain_events(all_days, threshold=rain_threshold)
        typer.echo(
            f"  {len(events)} rain day(s) with {rain_threshold}+ inches "
            f"out of {len(all_days)} total."
        )
        for rd in sorted(events, key=lambda r: r.date):
            typer.echo(f'    {rd.date}  —  {rd.rainfall_inches:.2f}"')

        if events:
            rain_written = generate_rain_batch(
                template_path=str(template),
                project=project,
                options=options,
                rain_days=events,
                mapping=mapping,
                original_inspection_type=project.inspection_type or "",
            )
            written.extend(rain_written)

    if not no_zip:
        written = bundle_outputs_zip(written, output_dir)

    typer.echo("\nCreated:")
    for p in written:
        typer.echo(f"  - {p}")


if __name__ == "__main__":
    app()
