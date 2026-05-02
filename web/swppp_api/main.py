from __future__ import annotations

import io
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
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import NameObject

from web.auth.dependencies import require_app
from web.log_config import configure_logging
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

# ── Logging configuration ─────────────────────────────────────────────
# JSON-formatted logs for automated tooling (Tier 6 Fix 6C).
# Reads TOOLS_LOG_LEVEL from environment so dev (DEBUG) and prod (INFO)
# can differ without code changes.

_LOG_LEVEL = os.environ.get("TOOLS_LOG_LEVEL", "INFO")
configure_logging(_LOG_LEVEL)

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
    log.info("SWPPP service starting: dev_mode=%s base_url=%s", DEV_MODE, BASE_URL)

    # Validate critical files exist before accepting requests.
    # Fail fast with a clear message rather than failing at request time.
    _required = {
        "PDF template": TEMPLATE_PDF,
        "ODOT mapping YAML": MAPPING_YAML,
    }
    missing = [
        f"{label}: {path}" for label, path in _required.items() if not path.exists()
    ]
    if missing:
        raise RuntimeError(
            "SWPPP service cannot start — required files missing:\n"
            + "\n".join(f"  {m}" for m in missing)
        )
    log.info("Startup check passed: template=%s mapping=%s", TEMPLATE_PDF, MAPPING_YAML)

    session_db.init_db()
    log.info("Database initialized: path=%s", session_db.DB_PATH)
    yield
    log.info("SWPPP service shutting down")


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


def add_preview_watermark(pdf_bytes: bytes) -> bytes:
    """Add "PREVIEW — NOT FILED" watermark diagonally across every page.

    Takes PDF bytes as input, returns watermarked PDF bytes.
    The watermark is semi-transparent and rotated 45 degrees.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()

    for page in reader.pages:
        # Get page dimensions
        media_box = page.mediabox
        width = float(media_box.width)
        height = float(media_box.height)

        # Calculate center position for diagonal watermark
        center_x = width / 2
        center_y = height / 2

        # Create watermark text annotation
        # Using pypdf's transformation to rotate text 45 degrees
        watermark_text = "PREVIEW — NOT FILED"

        # Add watermark as content stream overlay
        # Create a transformation matrix for diagonal placement
        transform = Transformation().rotate(45).translate(center_x, center_y)

        # Create a simple watermark overlay
        # Note: pypdf 6.10.2 supports adding annotations and transformations
        # For a visible watermark, we add it to the content stream
        watermark_content = (
            f"q\n"  # Save graphics state
            f"BT\n"  # Begin text
            f"/Helvetica 60 Tf\n"  # Font and size
            f"0.8 g\n"  # Gray color (80% gray for semi-transparency effect)
            f"1 0 0 1 {center_x - 200} {center_y} Tm\n"  # Text matrix (position)
            f"45 rotate\n"  # Rotate 45 degrees
            f"({watermark_text}) Tj\n"  # Show text
            f"ET\n"  # End text
            f"Q\n"  # Restore graphics state
        ).encode()

        # Merge watermark with page content
        # Add watermark to page's content stream
        if "/Contents" in page:
            # Append watermark to existing content
            page[NameObject("/Contents")].append_filtered_stream(watermark_content)

        writer.add_page(page)

    # Write to bytes
    output_buffer = io.BytesIO()
    writer.write(output_buffer)
    return output_buffer.getvalue()


# ── GET /swppp/api/health ────────────────────────────────────────────────


@app.get("/swppp/api/health")
def health_check():
    """Unauthenticated health check. Verifies DB connectivity and critical files.
    Returns 200 if healthy, 503 if any check fails.
    """
    issues: list[str] = []

    # Check critical files
    for label, path in [("template", TEMPLATE_PDF), ("mapping", MAPPING_YAML)]:
        if not path.exists():
            issues.append(f"{label} file missing: {path}")

    # Check DB connectivity
    try:
        with session_db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        issues.append(f"DB unreachable: {exc}")

    if issues:
        log.error("Health check failed: %s", "; ".join(issues))
        raise HTTPException(
            status_code=503,
            detail={"status": "unhealthy", "issues": issues},
        )

    return {
        "status": "ok",
        "service": "tools-swppp",
        "db": str(session_db.DB_PATH),
        "timestamp": session_db._now(),
    }


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

    try:
        result = fetch_rainfall(station_code, start, end)
    except Exception as exc:
        log.error("Rain data fetch failed: %s", exc, exc_info=True)
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
    try:
        with session_db.connect() as conn:
            rows = session_db.list_sessions(conn, user["id"])
    except Exception as exc:
        log.error(
            "Session list failed: user_id=%s error=%s",
            user["id"],
            exc,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to retrieve sessions")
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
        try:
            with session_db.connect() as conn:
                session_db.save_session(conn, user["id"], name, data)
        except Exception as exc:
            log.error(
                "Session save failed: user_id=%s name=%s error=%s",
                user["id"],
                name,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail="Failed to save session")

    return SessionImportResponse(success=True, saved=save, name=name, data=data)


@app.get("/swppp/api/sessions/{name}")
def get_session(name: str, user: dict = Depends(_require_swppp)):
    _validate_session_name(name)
    try:
        with session_db.connect() as conn:
            data = session_db.get_session(conn, user["id"], name)
    except Exception as exc:
        log.error(
            "Session get failed: user_id=%s name=%s error=%s",
            user["id"],
            name,
            exc,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to retrieve session")
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
    try:
        with session_db.connect() as conn:
            session_db.save_session(conn, user["id"], name, body)
    except Exception as exc:
        log.error(
            "Session save failed: user_id=%s name=%s error=%s",
            user["id"],
            name,
            exc,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to save session")
    return SessionSaveResponse(success=True, name=name)


@app.get("/swppp/api/sessions/{name}/export")
def export_session(
    name: str,
    background_tasks: BackgroundTasks,
    user: dict = Depends(_require_swppp),
):
    _validate_session_name(name)
    try:
        with session_db.connect() as conn:
            data = session_db.get_session(conn, user["id"], name)
    except Exception as exc:
        log.error(
            "Session export failed: user_id=%s name=%s error=%s",
            user["id"],
            name,
            exc,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to export session")
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
    try:
        with session_db.connect() as conn:
            session_db.delete_session(conn, user["id"], name)
    except Exception as exc:
        log.error(
            "Session delete failed: user_id=%s name=%s error=%s",
            user["id"],
            name,
            exc,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to delete session")
    return {"success": True}
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
        log.error("PDF generation failed: %s", exc, exc_info=True)
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
            log.error("Rain PDF generation failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail="Rain PDF generation failed")
        created.extend(rain_created)

    if not created:
        raise HTTPException(status_code=500, detail="No PDFs were generated")

    try:
        all_files = bundle_outputs_zip(created, Path(tmpdir))
    except Exception as exc:
        log.error("ZIP bundling failed: %s", exc, exc_info=True)
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
