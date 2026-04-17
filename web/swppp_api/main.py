from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse

from web.auth.dependencies import require_app
from web.swppp_api import db as session_db
from web.swppp_api.models import (
    CheckboxGroupInfo,
    FieldInfo,
    FormSchemaResponse,
    GenerateRequest,
    QuestionInfo,
    RainDayItem,
    RainFetchRequest,
    RainFetchResponse,
    SessionImportResponse,
    SessionListItem,
    SessionListResponse,
    SessionSaveResponse,
    StationItem,
    StationListResponse,
)

log = logging.getLogger(__name__)

# ── Paths relative to project root ───────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PDF = PROJECT_ROOT / "assets" / "template.pdf"
MAPPING_YAML = PROJECT_ROOT / "app" / "core" / "odot_mapping.yaml"

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_SESSION_NAME = 200

DEV_MODE = os.environ.get("TOOLS_DEV_MODE", "0") == "1"
BASE_URL = os.environ.get("TOOLS_BASE_URL", "http://localhost:8001")


# ── Lifespan ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(application: FastAPI):
    session_db.init_db()
    yield


app = FastAPI(title="SWPPP API", lifespan=lifespan)

# All endpoints require swppp app access
_require_swppp = require_app("swppp")


# ── Middleware: CSRF origin check ────────────────────────────────────────

from web.middleware import create_csrf_middleware

_csrf_check = create_csrf_middleware(expected_origin=BASE_URL, dev_mode=DEV_MODE)


@app.middleware("http")
async def csrf_origin_check(request: Request, call_next):
    return await _csrf_check(request, call_next)


# ── Helpers ──────────────────────────────────────────────────────────────

_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9 _\-.]{1,200}$")


def _cleanup_dir(path: str) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        log.warning("Failed to clean up temp dir %s", path)


def _validate_session_name(name: str) -> None:
    if not name:
        raise HTTPException(status_code=400, detail="Session name is required")
    if len(name) > MAX_SESSION_NAME:
        raise HTTPException(
            status_code=400,
            detail=f"Session name too long (max {MAX_SESSION_NAME} chars)",
        )
    if not _SESSION_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=(
                "Session name may contain only letters, numbers, spaces, "
                "underscores, hyphens, and periods"
            ),
        )


# ── GET /swppp/api/form-schema ───────────────────────────────────────────


@app.get("/swppp/api/form-schema", response_model=FormSchemaResponse)
def get_form_schema(user: dict = Depends(_require_swppp)):
    from app.core.config_manager import load_mapping

    mapping = load_mapping(MAPPING_YAML)

    fields = [
        FieldInfo(
            key=key,
            label=fm.label,
            required=fm.required,
        )
        for key, fm in mapping.fields.items()
    ]

    checkbox_groups = []
    for group_key, group in mapping.checkboxes.items():
        questions = [
            QuestionInfo(text=item.text, allow_na=item.allow_na)
            for item in group.pdf_fields
        ]
        checkbox_groups.append(
            CheckboxGroupInfo(
                key=group_key,
                label=group_key.replace("_", " "),
                has_notes=group.notes_field is not None,
                questions=questions,
            )
        )

    return FormSchemaResponse(fields=fields, checkbox_groups=checkbox_groups)


# ── GET /swppp/api/stations ──────────────────────────────────────────────


@app.get("/swppp/api/stations", response_model=StationListResponse)
def get_stations(user: dict = Depends(_require_swppp)):
    from app.core.mesonet_stations import STATIONS

    items = sorted(
        [
            StationItem(code=code, name=name, display=f"{code} - {name}")
            for code, name in STATIONS.items()
        ],
        key=lambda s: s.code,
    )
    return StationListResponse(stations=items)


# ── POST /swppp/api/rain/fetch ───────────────────────────────────────────


@app.post("/swppp/api/rain/fetch", response_model=RainFetchResponse)
def rain_fetch(req: RainFetchRequest, user: dict = Depends(_require_swppp)):
    from app.core.mesonet import fetch_rainfall, filter_rain_events
    from app.core.mesonet_stations import parse_station_code

    try:
        station_code = parse_station_code(req.station)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid station code")
    if not station_code:
        raise HTTPException(status_code=400, detail="Invalid station code")

    try:
        start = date.fromisoformat(req.start_date)
        end = date.fromisoformat(req.end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    if end < start:
        raise HTTPException(
            status_code=400, detail="End date must not precede start date"
        )

    try:
        result = fetch_rainfall(station_code, start, end)
    except Exception as exc:
        log.error("Rain data fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail="Rain data fetch failed")

    events = filter_rain_events(result.days, threshold=req.threshold)

    return RainFetchResponse(
        all_days=[
            RainDayItem(
                date=rd.date.isoformat(),
                rainfall_inches=rd.rainfall_inches,
            )
            for rd in result.days
        ],
        rain_events=[
            RainDayItem(
                date=rd.date.isoformat(),
                rainfall_inches=rd.rainfall_inches,
            )
            for rd in events
        ],
        failed_days=result.failed,
        missing_days=result.missing,
        station=station_code,
        threshold=req.threshold,
    )


# ── POST /swppp/api/rain/parse-csv ──────────────────────────────────────


@app.post("/swppp/api/rain/parse-csv")
async def rain_parse_csv(
    file: UploadFile,
    threshold: float = 0.5,
    user: dict = Depends(_require_swppp),
):
    from app.core.mesonet import filter_rain_events, parse_rainfall_csv

    if threshold < 0 or threshold > 10:
        raise HTTPException(
            status_code=422, detail="Threshold must be between 0 and 10"
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (5 MB limit)")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 text")

    try:
        all_days = parse_rainfall_csv(text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"CSV parse error: {exc}")

    events = filter_rain_events(all_days, threshold=threshold)

    return {
        "all_days": [
            {"date": rd.date.isoformat(), "rainfall_inches": rd.rainfall_inches}
            for rd in all_days
        ],
        "rain_events": [
            {"date": rd.date.isoformat(), "rainfall_inches": rd.rainfall_inches}
            for rd in events
        ],
        "threshold": threshold,
    }


# ── Session CRUD ─────────────────────────────────────────────────────────


@app.get("/swppp/api/sessions", response_model=SessionListResponse)
def list_sessions(user: dict = Depends(_require_swppp)):
    with session_db.connect() as conn:
        rows = session_db.list_sessions(conn, user["id"])
    return SessionListResponse(sessions=[SessionListItem(**r) for r in rows])


@app.post("/swppp/api/sessions/import", response_model=SessionImportResponse)
async def import_session(
    file: UploadFile,
    save: bool = False,
    user: dict = Depends(_require_swppp),
):
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (5 MB limit)")
    try:
        data = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=400, detail="Session data must be a JSON object"
        )

    name = data.get("session_name") or Path(file.filename or "imported").stem

    if save:
        with session_db.connect() as conn:
            session_db.save_session(conn, user["id"], name, data)

    return SessionImportResponse(success=True, saved=save, name=name, data=data)


@app.get("/swppp/api/sessions/{name}")
def get_session(name: str, user: dict = Depends(_require_swppp)):
    _validate_session_name(name)
    with session_db.connect() as conn:
        data = session_db.get_session(conn, user["id"], name)
    if data is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return data


@app.put("/swppp/api/sessions/{name}", response_model=SessionSaveResponse)
async def save_session(
    name: str,
    body: dict[str, Any],
    user: dict = Depends(_require_swppp),
):
    _validate_session_name(name)
    with session_db.connect() as conn:
        session_db.save_session(conn, user["id"], name, body)
    return SessionSaveResponse(success=True, name=name)


@app.get("/swppp/api/sessions/{name}/export")
def export_session(
    name: str,
    background_tasks: BackgroundTasks,
    user: dict = Depends(_require_swppp),
):
    _validate_session_name(name)
    with session_db.connect() as conn:
        data = session_db.get_session(conn, user["id"], name)
    if data is None:
        raise HTTPException(status_code=404, detail="Session not found")

    content = json.dumps(data, indent=2).encode("utf-8")
    tmp_path = _write_temp_json(name, content)
    background_tasks.add_task(os.unlink, tmp_path)
    return FileResponse(
        path=tmp_path,
        media_type="application/json",
        filename=f"{name}.json",
    )


@app.delete("/swppp/api/sessions/{name}")
def delete_session(name: str, user: dict = Depends(_require_swppp)):
    _validate_session_name(name)
    with session_db.connect() as conn:
        session_db.delete_session(conn, user["id"], name)
    return {"success": True}


def _write_temp_json(name: str, content: bytes) -> str:
    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=".json", prefix=f"session_{name}_"
    )
    tmp.write(content)
    tmp.close()
    return tmp.name


# ── POST /swppp/api/generate ────────────────────────────────────────────


@app.post("/swppp/api/generate")
def generate_pdf(
    req: GenerateRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(_require_swppp),
):
    from app.core.config_manager import (
        build_project_info,
        build_run_options,
        load_mapping,
    )
    from app.core.dates import weekly_dates
    from app.core.fill import bundle_outputs_zip, generate_batch
    from app.core.mesonet import RainDay
    from app.core.rain_fill import generate_rain_batch

    mapping = load_mapping(MAPPING_YAML)
    project = build_project_info(req.project_fields)

    tmpdir = tempfile.mkdtemp(prefix="swppp_gen_")
    background_tasks.add_task(_cleanup_dir, tmpdir)

    options = build_run_options(
        output_dir=tmpdir,
        start_date=req.start_date,
        end_date=req.end_date,
        make_zip=False,
    )

    try:
        dates = list(weekly_dates(req.start_date, req.end_date))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not dates:
        raise HTTPException(status_code=400, detail="No inspection dates in range")

    try:
        created = generate_batch(
            template_path=str(TEMPLATE_PDF),
            project=project,
            options=options,
            dates=dates,
            mapping=mapping,
            checkbox_states=req.checkbox_states or None,
            notes_texts=req.notes_texts or None,
        )
    except Exception as exc:
        log.error("PDF generation failed: %s", exc)
        raise HTTPException(status_code=500, detail="PDF generation failed")

    if req.rain_days:
        try:
            rain_day_objects = [
                RainDay(
                    date=date.fromisoformat(rd.date),
                    rainfall_inches=rd.rainfall_inches,
                )
                for rd in req.rain_days
            ]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid rain day date: {exc}")
        try:
            rain_created = generate_rain_batch(
                template_path=str(TEMPLATE_PDF),
                project=project,
                options=options,
                rain_days=rain_day_objects,
                mapping=mapping,
                checkbox_states=req.checkbox_states or None,
                notes_texts=req.notes_texts or None,
                original_inspection_type=req.original_inspection_type or "",
            )
        except Exception as exc:
            log.error("Rain PDF generation failed: %s", exc)
            raise HTTPException(status_code=500, detail="Rain PDF generation failed")
        created.extend(rain_created)

    if not created:
        raise HTTPException(status_code=500, detail="No PDFs were generated")

    try:
        all_files = bundle_outputs_zip(created, Path(tmpdir))
    except Exception as exc:
        log.error("ZIP bundling failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create ZIP bundle")

    zip_path = next((p for p in all_files if p.endswith(".zip")), None)

    if not zip_path:
        raise HTTPException(status_code=500, detail="Failed to create ZIP bundle")

    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename="swppp_outputs.zip",
    )


# ── Dev-mode: serve SWPPP frontend ──────────────────────────────────────

SWPPP_FRONTEND_DIR = PROJECT_ROOT / "web" / "frontend" / "swppp"

if DEV_MODE:

    @app.get("/swppp/mesonet_map.png")
    def swppp_map_image():
        img_path = SWPPP_FRONTEND_DIR / "mesonet_map.png"
        if img_path.exists():
            return FileResponse(img_path, media_type="image/png")
        return HTMLResponse("<h1>Map image not found</h1>", status_code=404)

    @app.get("/swppp/")
    def swppp_index():
        html_path = SWPPP_FRONTEND_DIR / "index.html"
        if html_path.exists():
            return FileResponse(html_path, media_type="text/html")
        return HTMLResponse("<h1>SWPPP frontend not found</h1>", status_code=500)
