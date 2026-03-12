from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.core.model import TemplateMap


@dataclass
class CheckboxChoiceTarget:
    field_name: str
    on_value: str


@dataclass
class CheckboxRowMapping:
    page: int
    y: float
    yes: CheckboxChoiceTarget | None = None
    no: CheckboxChoiceTarget | None = None
    na: CheckboxChoiceTarget | None = None


def _iter_button_groups(template_path: Path):
    reader = PdfReader(str(template_path))
    groups = []
    seen: set[tuple[str, int]] = set()

    for page_index, page in enumerate(reader.pages):
        annotations = page.get("/Annots", [])
        if hasattr(annotations, "get_object"):
            annotations = annotations.get_object()

        for annotation_ref in annotations:
            annotation = annotation_ref.get_object()
            parent_ref = annotation.get("/Parent")
            parent = parent_ref.get_object() if parent_ref else annotation
            field_name = str(parent.get("/T") or annotation.get("/T") or "")
            field_type = str(parent.get("/FT") or annotation.get("/FT") or "")
            if field_type != "/Btn" or not field_name:
                continue

            key = (field_name, page_index)
            if key in seen:
                continue
            seen.add(key)

            rect = annotation.get("/Rect", [0, 0, 0, 0])
            refs = parent.get("/Kids") or [annotation_ref]
            states = sorted(
                {
                    str(state)
                    for ref in refs
                    for state in ((ref.get_object().get("/AP", {}).get("/N", {}) or {}).keys())
                    if str(state) != "/Off"
                }
            )
            groups.append(
                {
                    "page": page_index,
                    "name": field_name,
                    "x": float(rect[0]),
                    "y": round(float(rect[1]), 2),
                    "states": states,
                }
            )

    groups.sort(key=lambda item: (item["page"], -item["y"], item["x"], item["name"]))
    return groups


def extract_checkbox_rows(template_path: Path) -> list[CheckboxRowMapping]:
    rows: list[dict] = []
    for group in _iter_button_groups(template_path):
        if rows and rows[-1]["page"] == group["page"] and abs(rows[-1]["y"] - group["y"]) < 0.2:
            rows[-1]["groups"].append(group)
        else:
            rows.append({"page": group["page"], "y": group["y"], "groups": [group]})

    extracted: list[CheckboxRowMapping] = []
    for row in rows:
        mapping = CheckboxRowMapping(page=row["page"], y=row["y"])
        groups = row["groups"]

        if len(groups) == 1:
            group = groups[0]
            for state in group["states"]:
                upper_state = state.upper()
                target = CheckboxChoiceTarget(field_name=group["name"], on_value=state)
                if "YES" in upper_state:
                    mapping.yes = target
                elif "NO" in upper_state:
                    mapping.no = target
                elif "NA" in upper_state:
                    mapping.na = target
        else:
            ordered_groups = sorted(groups, key=lambda item: item["x"])
            choices = ["yes", "no", "na"]
            for choice, group in zip(choices, ordered_groups):
                setattr(
                    mapping,
                    choice,
                    CheckboxChoiceTarget(field_name=group["name"], on_value=group["states"][0]),
                )

        extracted.append(mapping)

    return extracted


def populate_checkbox_targets(mapping: TemplateMap, template_path: Path) -> TemplateMap:
    checkbox_items = [item for group in mapping.checkboxes.values() for item in group.pdf_fields]
    if not checkbox_items or all(item.has_targets for item in checkbox_items):
        return mapping

    rows = extract_checkbox_rows(template_path)
    if len(rows) != len(checkbox_items):
        raise ValueError(
            f"Template checkbox rows ({len(rows)}) do not match checklist items ({len(checkbox_items)})"
        )

    for item, row in zip(checkbox_items, rows):
        if row.yes is None or row.no is None:
            raise ValueError(f"Unable to infer YES/NO targets for checkbox row near y={row.y}")
        if item.allow_na and row.na is None:
            raise ValueError(f"Unable to infer N/A target for checkbox row near y={row.y}")

        item.yes_field = row.yes.field_name
        item.yes_value = row.yes.on_value
        item.no_field = row.no.field_name
        item.no_value = row.no.on_value
        item.na_field = row.na.field_name if row.na else None
        item.na_value = row.na.on_value if row.na else "/On"

    return mapping


def build_audit_mapping_document(mapping: TemplateMap, template_path: Path) -> dict[str, Any]:
    populated = populate_checkbox_targets(mapping, template_path)
    return populated.model_dump(exclude_none=True)