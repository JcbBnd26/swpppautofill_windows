from __future__ import annotations

from pydantic import BaseModel, Field

# ── Rain ─────────────────────────────────────────────────────────────────


class RainDayItem(BaseModel):
    date: str = Field(max_length=10)
    rainfall_inches: float = Field(ge=0.0)


class RainFetchRequest(BaseModel):
    station: str = Field(max_length=50)
    start_date: str = Field(max_length=10)
    end_date: str = Field(max_length=10)
    threshold: float = Field(default=0.5, ge=0.0, le=10.0)


class RainFetchResponse(BaseModel):
    all_days: list[RainDayItem]
    rain_events: list[RainDayItem]
    failed_days: int
    missing_days: int
    station: str
    threshold: float


# ── Form Schema ──────────────────────────────────────────────────────────


class FieldInfo(BaseModel):
    key: str
    label: str
    required: bool


class QuestionInfo(BaseModel):
    text: str
    allow_na: bool


class CheckboxGroupInfo(BaseModel):
    key: str
    label: str
    has_notes: bool
    questions: list[QuestionInfo]


class FormSchemaResponse(BaseModel):
    fields: list[FieldInfo]
    checkbox_groups: list[CheckboxGroupInfo]


# ── Stations ─────────────────────────────────────────────────────────────


class StationItem(BaseModel):
    code: str
    name: str
    display: str


class StationListResponse(BaseModel):
    stations: list[StationItem]


# ── Sessions ─────────────────────────────────────────────────────────────


class SessionListItem(BaseModel):
    name: str
    updated_at: str


class SessionListResponse(BaseModel):
    sessions: list[SessionListItem]


class SessionSaveResponse(BaseModel):
    success: bool
    name: str


class SessionImportResponse(BaseModel):
    success: bool
    saved: bool
    name: str | None = None
    data: dict | None = None


# ── Generate ─────────────────────────────────────────────────────────────


class GenerateRequest(BaseModel):
    project_fields: dict[str, str]
    checkbox_states: dict[str, dict[str, str]] = {}
    notes_texts: dict[str, str] = {}
    start_date: str = Field(max_length=10)
    end_date: str = Field(max_length=10)
    rain_days: list[RainDayItem] = []
    original_inspection_type: str = Field(default="", max_length=200)
