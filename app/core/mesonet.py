# ============================================================
#  Oklahoma Mesonet – HTTP Client & CSV Parser
#
#  Fetches daily rainfall data from Mesonet's public MDF
#  (Mesonet Data File) endpoint, which provides end-of-day
#  CSV snapshots for all stations without requiring CAPTCHA.
#
#  RAIN values in MDF files are cumulative mm since midnight
#  UTC.  The 23:55 observation gives the daily total.
# ============================================================

from __future__ import annotations

import csv
import io
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import requests

log = logging.getLogger(__name__)

# ============================================================
#  Mesonet API Constants
# ============================================================

MESONET_API_BASE = "https://api.prod.mesonet.org/index.php"
MDF_EXPORT_URL = f"{MESONET_API_BASE}/export/mesonet_data_files"

# Delay between sequential day requests (seconds) – be polite
REQUEST_DELAY = 0.25

REQUEST_TIMEOUT = 30  # seconds per HTTP request

# MDF RAIN column is in millimetres; we convert to inches.
_MM_PER_INCH = 25.4


# ============================================================
#  Data Model
# ============================================================


@dataclass
class RainDay:
    """A single day of rainfall data from Mesonet."""

    date: date
    rainfall_inches: float


# ============================================================
#  HTTP Fetch  (MDF endpoint – no CAPTCHA required)
# ============================================================


def _fetch_day_rain_mm(station: str, day: date) -> float | None:
    """Fetch the cumulative daily rainfall (mm) for *station* on *day*.

    Returns ``None`` when the station is not found in the response or
    the RAIN value is a missing-data sentinel (< -990).
    """
    params = {
        "date": f"{day.isoformat()}T23:55:00Z",
        "type": "mdf",
        "format": "csv",
    }
    resp = requests.get(MDF_EXPORT_URL, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    reader = csv.DictReader(io.StringIO(resp.text))
    target = station.upper()
    for row in reader:
        if row.get("STID", "").upper() == target:
            try:
                val = float(row["RAIN"])
            except (ValueError, KeyError):
                return None
            if val < -990:
                return None
            return val
    return None


def fetch_rainfall(
    station: str,
    start: date,
    end: date,
    progress: Callable[[int, int], None] | None = None,
) -> list[RainDay]:
    """Fetch daily rainfall for one station over a date range.

    Makes one lightweight HTTP GET per day against the public MDF
    CSV endpoint.  Returns a list of ``RainDay`` objects with
    rainfall converted to inches.  Days with missing data are
    silently skipped.

    *progress*, if provided, is called as ``progress(day_num, total_days)``
    after each day is fetched.
    """
    if start > end:
        raise ValueError("start date must be on or before end date")

    results: list[RainDay] = []
    current = start
    day_num = 0
    total_days = (end - start).days + 1

    while current <= end:
        if day_num > 0:
            time.sleep(REQUEST_DELAY)
        day_num += 1

        log.info("Fetching day %d/%d: %s", day_num, total_days, current)
        try:
            rain_mm = _fetch_day_rain_mm(station, current)
        except requests.RequestException as exc:
            log.warning("HTTP error for %s: %s", current, exc)
            current += timedelta(days=1)
            continue

        if rain_mm is not None:
            results.append(
                RainDay(
                    date=current,
                    rainfall_inches=rain_mm / _MM_PER_INCH,
                )
            )

        if progress is not None:
            progress(day_num, total_days)

        current += timedelta(days=1)

    return results


# ============================================================
#  CSV Parsing  (for manually-downloaded CSVs)
# ============================================================


def _find_rain_column(headers: list[str]) -> int:
    """Find the index of the RAIN column in the CSV header."""
    normalized = [h.strip().upper() for h in headers]
    if "RAIN" in normalized:
        return normalized.index("RAIN")
    for i, h in enumerate(normalized):
        if "RAIN" in h:
            return i
    raise ValueError(f"Could not find RAIN column in CSV headers: {headers}")


def _find_date_columns(headers: list[str]) -> tuple[int, int, int]:
    """Find the indices of YEAR, MONTH, DAY columns."""
    normalized = [h.strip().upper() for h in headers]
    try:
        year_idx = normalized.index("YEAR")
        month_idx = normalized.index("MONTH")
        day_idx = normalized.index("DAY")
        return year_idx, month_idx, day_idx
    except ValueError:
        pass
    raise ValueError(f"Could not find YEAR/MONTH/DAY columns in CSV headers: {headers}")


def parse_rainfall_csv(csv_text: str) -> list[RainDay]:
    """Parse a manually-downloaded Mesonet daily-data CSV into ``RainDay`` objects.

    Expects columns: STID, YEAR, MONTH, DAY, RAIN (inches).
    Skips rows where rainfall is < -990 (missing data) or empty.
    """
    if not csv_text or not csv_text.strip():
        return []

    reader = csv.reader(io.StringIO(csv_text.strip()))
    rows = list(reader)

    if len(rows) < 2:
        return []

    headers = rows[0]
    rain_idx = _find_rain_column(headers)
    year_idx, month_idx, day_idx = _find_date_columns(headers)

    results: list[RainDay] = []
    for row in rows[1:]:
        if len(row) <= max(rain_idx, year_idx, month_idx, day_idx):
            continue

        rain_str = row[rain_idx].strip()
        if not rain_str:
            continue

        try:
            rainfall = float(rain_str)
        except ValueError:
            continue

        if rainfall < -990:
            continue

        try:
            day_date = date(
                int(row[year_idx].strip()),
                int(row[month_idx].strip()),
                int(row[day_idx].strip()),
            )
        except (ValueError, IndexError):
            continue

        results.append(RainDay(date=day_date, rainfall_inches=rainfall))

    return results


def parse_rainfall_csv_file(file_path: str | Path) -> list[RainDay]:
    """Read a local CSV file and parse it as Mesonet rainfall data."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_rainfall_csv(text)


# ============================================================
#  Filtering
# ============================================================


def filter_rain_events(
    rain_days: list[RainDay],
    threshold: float = 0.5,
) -> list[RainDay]:
    """Return only days where daily rainfall exceeds *threshold* (inches).

    Default threshold is 0.5 inches (strict greater-than).
    """
    return [rd for rd in rain_days if rd.rainfall_inches > threshold]
