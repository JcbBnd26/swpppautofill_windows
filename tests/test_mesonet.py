# ============================================================
#  Tests for app.core.mesonet — CSV parsing & filtering
# ============================================================

from datetime import date

import pytest

from app.core.mesonet import (
    FetchResult,
    RainDay,
    fetch_rainfall,
    filter_rain_events,
    parse_rainfall_csv,
)

# ============================================================
#  Sample CSV fixtures
# ============================================================

SAMPLE_CSV = """\
STID,YEAR,MONTH,DAY,RAIN
NRMN,2025,3,1,0.75
NRMN,2025,3,2,0.00
NRMN,2025,3,3,1.20
NRMN,2025,3,4,0.10
NRMN,2025,3,5,0.51
"""

SAMPLE_CSV_WITH_MISSING = """\
STID,YEAR,MONTH,DAY,RAIN
NRMN,2025,6,1,0.75
NRMN,2025,6,2,-999.00
NRMN,2025,6,3,-996.00
NRMN,2025,6,4,0.60
"""

SAMPLE_CSV_EMPTY_RAIN = """\
STID,YEAR,MONTH,DAY,RAIN
NRMN,2025,7,1,
NRMN,2025,7,2,0.80
"""


# ============================================================
#  parse_rainfall_csv tests
# ============================================================


class TestParseRainfallCsv:

    def test_parses_normal_rows(self):
        days = parse_rainfall_csv(SAMPLE_CSV)
        assert len(days) == 5
        assert days[0].date == date(2025, 3, 1)
        assert days[0].rainfall_inches == 0.75
        assert days[2].rainfall_inches == 1.20
        assert days[4].rainfall_inches == 0.51

    def test_skips_missing_data(self):
        days = parse_rainfall_csv(SAMPLE_CSV_WITH_MISSING)
        assert len(days) == 2
        dates = [d.date for d in days]
        assert date(2025, 6, 1) in dates
        assert date(2025, 6, 4) in dates

    def test_skips_empty_rainfall(self):
        days = parse_rainfall_csv(SAMPLE_CSV_EMPTY_RAIN)
        assert len(days) == 1
        assert days[0].date == date(2025, 7, 2)

    def test_empty_input(self):
        assert parse_rainfall_csv("") == []
        assert parse_rainfall_csv("   ") == []

    def test_header_only(self):
        assert parse_rainfall_csv("STID,YEAR,MONTH,DAY,RAIN\n") == []


# ============================================================
#  filter_rain_events tests
# ============================================================


class TestFilterRainEvents:

    def test_filters_above_threshold(self):
        days = parse_rainfall_csv(SAMPLE_CSV)
        events = filter_rain_events(days)
        # 0.75, 1.20, 0.51 are > 0.5
        assert len(events) == 3
        amounts = [e.rainfall_inches for e in events]
        assert 0.75 in amounts
        assert 1.20 in amounts
        assert 0.51 in amounts

    def test_exact_threshold_included(self):
        """Exactly 0.5 should qualify (greater-than-or-equal)."""
        days = [RainDay(date=date(2025, 1, 1), rainfall_inches=0.5)]
        events = filter_rain_events(days)
        assert len(events) == 1

    def test_just_above_threshold(self):
        days = [RainDay(date=date(2025, 1, 1), rainfall_inches=0.51)]
        events = filter_rain_events(days)
        assert len(events) == 1

    def test_custom_threshold(self):
        days = [
            RainDay(date=date(2025, 1, 1), rainfall_inches=1.0),
            RainDay(date=date(2025, 1, 2), rainfall_inches=2.0),
        ]
        events = filter_rain_events(days, threshold=1.5)
        assert len(events) == 1
        assert events[0].rainfall_inches == 2.0

    def test_all_missing_produces_empty(self):
        days = parse_rainfall_csv(SAMPLE_CSV_WITH_MISSING)
        # Only 2 days have valid data (0.75 and 0.60), both > 0.5
        events = filter_rain_events(days)
        assert len(events) == 2

    def test_empty_input(self):
        assert filter_rain_events([]) == []


# ============================================================
#  fetch_rainfall failure tracking
# ============================================================


class TestFetchRainfallFailures:

    def test_counts_failed_days(self, monkeypatch):
        """HTTP failures should be counted in FetchResult.failed."""
        import requests as _requests

        from app.core import mesonet as _mod

        def _fake_fetch(station, utc_day, utc_time):
            # Simulate HTTP error for the end-of-day fetch on Jan 2
            if utc_day == date(2025, 1, 2) and utc_time == "23:55:00Z":
                raise _requests.ConnectionError("simulated")
            return 1.27  # ~0.05 inches

        monkeypatch.setattr(_mod, "_fetch_rain_mm_at", _fake_fetch)

        result = fetch_rainfall("FAKE", date(2025, 1, 1), date(2025, 1, 3))

        assert isinstance(result, FetchResult)
        assert result.failed == 1
        assert result.missing == 0
        assert len(result.days) == 2

    def test_counts_missing_days(self, monkeypatch):
        """Days with None rain data should be counted in FetchResult.missing."""
        from app.core import mesonet as _mod

        def _fake_fetch(station, utc_day, utc_time):
            # Return None for the end-of-day fetch on Jan 2 (missing data)
            if utc_day == date(2025, 1, 2) and utc_time == "23:55:00Z":
                return None
            return 1.27  # ~0.05 inches

        monkeypatch.setattr(_mod, "_fetch_rain_mm_at", _fake_fetch)

        result = fetch_rainfall("FAKE", date(2025, 1, 1), date(2025, 1, 3))

        assert isinstance(result, FetchResult)
        assert result.missing == 1
        assert result.failed == 0
        assert len(result.days) == 2
