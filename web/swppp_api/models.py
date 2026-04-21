from __future__ import annotations

from datetime import date as _date

from pydantic import BaseModel, Field, model_validator

# Validation caps (Fix 6A)
_MAX_GENERATE_RANGE_DAYS = 365  # ~1 year of weekly inspections
_MAX_RAIN_RANGE_DAYS = 730  # 2 years of rain history
_MAX_RAIN_DAYS_LIST = 500  # upper bound on rain_days items
_MAX_FIELD_VALUE_LEN = 500  # per-value cap on project_fields
_MAX_DICT_KEYS = 100  # max keys in any submitted dict

# ── Rain ─────────────────────────────────────────────────────────────────


class RainDayItem(BaseModel):
    date: str = Field(max_length=10)
    rainfall_inches: float = Field(ge=0.0)


class RainFetchRequest(BaseModel):
    station: str = Field(max_length=50)
    start_date: str = Field(max_length=10)
    end_date: str = Field(max_length=10)
    threshold: float = Field(default=0.5, ge=0.0, le=10.0)

    @model_validator(mode="after")
    def _validate_dates(self) -> "RainFetchRequest":
        try:
            start = _date.fromisoformat(self.start_date)
        except ValueError:
            raise ValueError(f"start_date is not a valid ISO date: {self.start_date!r}")
        try:
            end = _date.fromisoformat(self.end_date)
        except ValueError:
            raise ValueError(f"end_date is not a valid ISO date: {self.end_date!r}")
        if end < start:
            raise ValueError("end_date must not precede start_date")
        span = (end - start).days
        if span > _MAX_RAIN_RANGE_DAYS:
            raise ValueError(
                f"Date range spans {span} days; maximum is {_MAX_RAIN_RANGE_DAYS} "
                f"({_MAX_RAIN_RANGE_DAYS // 365} years)"
            )
        return self


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

    @model_validator(mode="after")
    def _validate_dates_and_bounds(self) -> "GenerateRequest":
        # Validate date strings
        try:
            start = _date.fromisoformat(self.start_date)
        except ValueError:
            raise ValueError(f"start_date is not a valid ISO date: {self.start_date!r}")
        try:
            end = _date.fromisoformat(self.end_date)
        except ValueError:
            raise ValueError(f"end_date is not a valid ISO date: {self.end_date!r}")

        # Logical order
        if end < start:
            raise ValueError("end_date must not precede start_date")

        # Range cap — prevents accidental multi-year generations
        span = (end - start).days
        if span > _MAX_GENERATE_RANGE_DAYS:
            raise ValueError(
                f"Date range spans {span} days; maximum is {_MAX_GENERATE_RANGE_DAYS} "
                f"({_MAX_GENERATE_RANGE_DAYS // 7} weekly inspections). "
                f"Split large ranges into separate requests."
            )

        # rain_days list size cap
        if len(self.rain_days) > _MAX_RAIN_DAYS_LIST:
            raise ValueError(
                f"rain_days contains {len(self.rain_days)} items; "
                f"maximum is {_MAX_RAIN_DAYS_LIST}"
            )

        # project_fields: cap key count and per-value length
        if len(self.project_fields) > _MAX_DICT_KEYS:
            raise ValueError(
                f"project_fields contains {len(self.project_fields)} keys; "
                f"maximum is {_MAX_DICT_KEYS}"
            )
        for k, v in self.project_fields.items():
            if len(v) > _MAX_FIELD_VALUE_LEN:
                raise ValueError(
                    f"project_fields[{k!r}] value exceeds {_MAX_FIELD_VALUE_LEN} characters"
                )

        # notes_texts: cap per-value length
        for k, v in self.notes_texts.items():
            if len(v) > _MAX_FIELD_VALUE_LEN:
                raise ValueError(
                    f"notes_texts[{k!r}] value exceeds {_MAX_FIELD_VALUE_LEN} characters"
                )

        return self
