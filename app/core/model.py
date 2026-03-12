from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FieldMapping(BaseModel):
    label: str
    pdf_field_name: str | None = None
    required: bool = False

    @classmethod
    def from_raw(cls, raw: Any) -> "FieldMapping":
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, str):
            return cls(label=raw, pdf_field_name=raw)
        if isinstance(raw, dict):
            data = dict(raw)
            if "label" not in data and "pdf_field_name" in data:
                data["label"] = str(data["pdf_field_name"])
            return cls(**data)
        raise TypeError(f"Unsupported field mapping: {raw!r}")

    @property
    def target_name(self) -> str:
        return self.pdf_field_name or self.label


class DateFieldMapping(BaseModel):
    label: str | None = None
    pdf_field_name: str
    format: str | None = None

    @classmethod
    def from_raw(cls, raw: Any) -> "DateFieldMapping":
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, str):
            return cls(label=raw, pdf_field_name=raw)
        if isinstance(raw, dict):
            return cls(**raw)
        raise TypeError(f"Unsupported date field mapping: {raw!r}")

    @property
    def target_name(self) -> str:
        return self.pdf_field_name


class CheckboxItem(BaseModel):
    text: str
    allow_na: bool = True
    yes_field: str | None = None
    no_field: str | None = None
    na_field: str | None = None
    yes_value: str = "/On"
    no_value: str = "/On"
    na_value: str = "/On"

    @classmethod
    def from_raw(cls, raw: Any) -> "CheckboxItem":
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, str):
            return cls(text=raw)
        if isinstance(raw, dict):
            return cls(**raw)
        raise TypeError(f"Unsupported checkbox mapping: {raw!r}")

    @property
    def has_targets(self) -> bool:
        return bool(self.yes_field or self.no_field or self.na_field)


class CheckboxGroup(BaseModel):
    pdf_fields: list[CheckboxItem] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)

    @field_validator("pdf_fields", mode="before")
    @classmethod
    def _normalize_pdf_fields(cls, value: Any) -> list[CheckboxItem]:
        if not value:
            return []
        return [CheckboxItem.from_raw(item) for item in value]


class TemplateMap(BaseModel):
    description: str | None = None
    fields: dict[str, FieldMapping] = Field(default_factory=dict)
    date_fields: list[DateFieldMapping] = Field(default_factory=list)
    checkbox_fields: list[str] = Field(default_factory=list)
    checkboxes: dict[str, CheckboxGroup] = Field(default_factory=dict)

    @field_validator("fields", mode="before")
    @classmethod
    def _normalize_fields(cls, value: Any) -> dict[str, FieldMapping]:
        value = value or {}
        return {key: FieldMapping.from_raw(raw) for key, raw in value.items()}

    @field_validator("date_fields", mode="before")
    @classmethod
    def _normalize_date_fields(cls, value: Any) -> list[DateFieldMapping]:
        value = value or []
        return [DateFieldMapping.from_raw(item) for item in value]

    @field_validator("checkboxes", mode="before")
    @classmethod
    def _normalize_checkboxes(cls, value: Any) -> dict[str, CheckboxGroup]:
        value = value or {}
        normalized: dict[str, CheckboxGroup] = {}
        for key, raw in value.items():
            if isinstance(raw, CheckboxGroup):
                normalized[key] = raw
            else:
                normalized[key] = CheckboxGroup(**raw)
        return normalized


class RunOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_dir: str = ""
    start_date: str
    end_date: str
    date_format: str = "%m/%d/%Y"
    make_zip: bool = True
    debug: bool = False
    verbose: bool = False
    ocr_enabled: bool = True
    output_format: str = "json"
    save_intermediate: bool = False


class ProjectInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    job_piece: str | None = None
    project_number: str | None = None
    contract_id: str | None = None
    location_description_1: str | None = None
    location_description_2: str | None = None
    re_odot_contact_1: str | None = None
    re_odot_contact_2: str | None = None
    inspection_type: str | None = None
