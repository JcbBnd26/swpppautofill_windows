# ============================================================
#  Oklahoma Mesonet – HTTP Client & CSV Parser
#
#  Fetches daily rainfall data from Mesonet's public MDF
#  (Mesonet Data File) endpoint, which provides end-of-day
#  CSV snapshots for all stations without requiring CAPTCHA.
#
#  RAIN values in MDF files are cumulative mm since midnight
#  UTC.  Because Oklahoma is UTC−6 (CST) or UTC−5 (CDT), a
#  single 23:55 UTC snapshot misses rain that falls during the
#  local evening.  We therefore combine two UTC observations
#  per local day to reconstruct the full local-day total.
# ============================================================

from __future__ import annotations

import csv
import io
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

log = logging.getLogger(__name__)

# ============================================================
#  Mesonet API Constants
# ============================================================

MESONET_API_BASE = "https://api.prod.mesonet.org/index.php"
MDF_EXPORT_URL = f"{MESONET_API_BASE}/export/mesonet_data_files"

REQUEST_TIMEOUT = 30  # seconds per HTTP request
_RETRY_DELAY = 2  # seconds between retries
_MAX_RETRIES = 1  # number of retries on transient HTTP errors

# Maximum parallel requests – polite but fast
MAX_WORKERS = 8

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


@dataclass
class FetchResult:
    """Result of a fetch_rainfall call, including failure and missing counts."""

    days: list[RainDay]
    failed: int
    missing: int


# ============================================================
#  Local-time helpers
# ============================================================


def _utc_offset_hours(day: date) -> int:
    """UTC offset in hours for Oklahoma on *day* (−6 CST or −5 CDT).

    US Central Time DST runs from the 2nd Sunday of March to the
    1st Sunday of November.
    """
    year = day.year
    # 2nd Sunday of March: find March 1 weekday, compute 2nd Sunday
    mar1_wd = date(year, 3, 1).weekday()  # Mon=0, Sun=6
    dst_start = date(year, 3, (13 - mar1_wd) % 7 + 8)
    # 1st Sunday of November
    nov1_wd = date(year, 11, 1).weekday()
    dst_end = date(year, 11, (6 - nov1_wd) % 7 + 1)
    if dst_start <= day < dst_end:
        return -5  # CDT
    return -6  # CST


def _boundary_utc_time(day: date) -> str:
    """MDF observation time nearest local midnight at the start of *day*.

    Oklahoma local midnight = ``-offset``:00 UTC.  We use the ``:55``
    observation of the preceding hour so it falls just before midnight.
    """
    hour = -_utc_offset_hours(day) - 1  # 5 for CST, 4 for CDT
    return f"{hour:02d}:55:00Z"


# ============================================================
#  HTTP Fetch  (MDF endpoint – no CAPTCHA required)
# ============================================================


def _fetch_rain_mm_at(station: str, utc_day: date, utc_time: str) -> float | None:
    """Fetch cumulative RAIN (mm) for *station* at a specific UTC time.

    Returns ``None`` when the station is not found in the response or
    the RAIN value is a missing-data sentinel (< -990).
    Raises ``requests.RequestException`` on HTTP errors.
    """
    params = {
        "date": f"{utc_day.isoformat()}T{utc_time}",
        "type": "mdf",
        "format": "csv",
    }
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = requests.get(MDF_EXPORT_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.RequestException as exc:
            if attempt < _MAX_RETRIES:
                log.debug("Retry %d for %s %s: %s", attempt + 1, utc_day, utc_time, exc)
                time.sleep(_RETRY_DELAY)
            else:
                raise

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
) -> FetchResult:
    """Fetch daily rainfall for one station, adjusted for local Oklahoma time.

    For each local day, two parts of the UTC timeline are combined:

    * **Daytime portion** – ``RAIN@23:55 UTC(D) − RAIN@boundary(D)``
      covers local midnight through ~6 PM.
    * **Evening portion** – ``RAIN@boundary(D+1)``
      covers ~6 PM through local midnight.

    All required MDF observations are fetched in parallel (bounded by
    ``MAX_WORKERS``) for speed, then assembled sequentially.
    """
    if start > end:
        raise ValueError("start date must be on or before end date")

    total_days = (end - start).days + 1

    # ------------------------------------------------------------------
    #  1. Build the set of unique (utc_day, utc_time) observation keys.
    #     For N local days we need:
    #       - N end-of-day observations: (D, "23:55:00Z") for each D
    #       - N+1 boundary observations: boundary(start) .. boundary(end+1)
    #     Total ≈ 2N + 1 HTTP requests, all fired in parallel.
    # ------------------------------------------------------------------
    ObsKey = tuple[date, str]  # (utc_day, utc_time)

    eod_keys: list[ObsKey] = []
    boundary_keys: list[ObsKey] = []
    all_keys: set[ObsKey] = set()

    current = start
    while current <= end:
        eod_key: ObsKey = (current, "23:55:00Z")
        bnd_key: ObsKey = (current, _boundary_utc_time(current))
        eod_keys.append(eod_key)
        all_keys.add(eod_key)
        boundary_keys.append(bnd_key)
        all_keys.add(bnd_key)
        current += timedelta(days=1)

    # One extra boundary for the evening portion of the last local day
    next_after_end = end + timedelta(days=1)
    last_bnd: ObsKey = (next_after_end, _boundary_utc_time(next_after_end))
    boundary_keys.append(last_bnd)
    all_keys.add(last_bnd)

    # ------------------------------------------------------------------
    #  2. Fetch all observations in parallel.
    # ------------------------------------------------------------------
    # result_map values: float | None (data), or "error" sentinel
    _ERROR = "error"
    result_map: dict[ObsKey, float | None | str] = {}
    completed = 0

    def _do_fetch(key: ObsKey) -> tuple[ObsKey, float | None]:
        return key, _fetch_rain_mm_at(station, key[0], key[1])

    key_list = list(all_keys)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_do_fetch, k): k for k in key_list}
        for future in as_completed(futures):
            key = futures[future]
            try:
                _, val = future.result()
                result_map[key] = val
            except requests.RequestException as exc:
                log.warning("HTTP error for %s %s: %s", key[0], key[1], exc)
                result_map[key] = _ERROR

            completed += 1
            if progress is not None:
                # Map completed fetches → approximate day progress
                approx_day = min(
                    int(completed / len(key_list) * total_days) + 1,
                    total_days,
                )
                progress(approx_day, total_days)

    # ------------------------------------------------------------------
    #  3. Assemble local-day rainfall from the fetched observations.
    # ------------------------------------------------------------------
    results: list[RainDay] = []
    failed = 0
    missing = 0

    for day_idx in range(total_days):
        day = start + timedelta(days=day_idx)
        eod_key = eod_keys[day_idx]
        morn_key = boundary_keys[day_idx]
        eve_key = boundary_keys[day_idx + 1]

        eod_val = result_map.get(eod_key)
        morn_val = result_map.get(morn_key)
        eve_val = result_map.get(eve_key)

        if _ERROR in (eod_val, morn_val, eve_val):
            failed += 1
            continue

        if eod_val is not None and morn_val is not None and eve_val is not None:
            local_rain_mm = (eod_val - morn_val) + eve_val
            results.append(
                RainDay(
                    date=day,
                    rainfall_inches=max(local_rain_mm, 0.0) / _MM_PER_INCH,
                )
            )
        else:
            missing += 1
            log.info("Missing data for %s on %s", station, day)

    if failed > 0 or missing > 0:
        log.warning(
            "Mesonet fetch incomplete: station=%s range=%s..%s "
            "returned=%d failed=%d missing=%d",
            station,
            start.isoformat(),
            end.isoformat(),
            len(results),
            failed,
            missing,
        )
    return FetchResult(days=results, failed=failed, missing=missing)


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
    """Return only days where daily rainfall meets or exceeds *threshold* (inches).

    Default threshold is 0.5 inches (greater-than-or-equal).
    """
    return [rd for rd in rain_days if rd.rainfall_inches >= threshold]
