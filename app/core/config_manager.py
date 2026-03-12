from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from app.core.model import ProjectInfo, RunOptions, TemplateMap


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def load_mapping(path: Path) -> TemplateMap:
    return TemplateMap.model_validate(_read_yaml(path))


def load_project_data(path: Path) -> dict[str, Any]:
    if path.suffix.lower() in {".yaml", ".yml"}:
        return _read_yaml(path)
    return json.loads(path.read_text(encoding="utf-8"))


def build_project_info(data: Mapping[str, Any] | None) -> ProjectInfo:
    return ProjectInfo.model_validate(dict(data or {}))


def load_project_info(path: Path) -> ProjectInfo:
    return build_project_info(load_project_data(path))


def build_run_options(
    *,
    output_dir: str,
    start_date: str,
    end_date: str,
    date_format: str = "%m/%d/%Y",
    make_zip: bool = True,
) -> RunOptions:
    return RunOptions(
        output_dir=output_dir,
        start_date=start_date,
        end_date=end_date,
        date_format=date_format,
        make_zip=make_zip,
    )
