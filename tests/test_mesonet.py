# ============================================================
#  Tests for app.core.mesonet — CSV parsing & filtering
# ============================================================

from datetime import date

import pytest

from app.core.mesonet import RainDay, filter_rain_events, parse_rainfall_csv

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

    def test_exact_threshold_excluded(self):
        """Exactly 0.5 should NOT qualify (strict greater-than)."""
        days = [RainDay(date=date(2025, 1, 1), rainfall_inches=0.5)]
        events = filter_rain_events(days)
        assert len(events) == 0

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
