"""Tests for row date resolution against a table's reporting period."""

from datetime import date

from official_foreign_travel.parsing.dates import resolve_segment_dates
from official_foreign_travel.parsing.header import Period


def make_period(start, end):
    return Period(start=start, end=end, year=end.year, quarter=None, raw="")


class TestResolveSegmentDates:
    def test_simple_case_within_period(self):
        period = make_period(date(2018, 7, 1), date(2018, 9, 30))
        result = resolve_segment_dates("8/25", "8/28", period)
        assert result.arrival == date(2018, 8, 25)
        assert result.departure == date(2018, 8, 28)
        assert result.flags == []

    def test_year_rollover_within_trip(self):
        """Arrival 12/28, departure 1/2: departure year must roll forward."""
        period = make_period(date(2018, 10, 1), date(2018, 12, 31))
        result = resolve_segment_dates("12/28", "1/2", period)
        assert result.arrival == date(2018, 12, 28)
        assert result.departure == date(2019, 1, 2)
        assert "YEAR_ROLLOVER_APPLIED" in result.flags

    def test_period_published_in_january_for_prior_quarter(self):
        """A report filed in Jan 2019 covering Oct-Dec 2018."""
        period = make_period(date(2018, 10, 1), date(2018, 12, 31))
        result = resolve_segment_dates("10/19", "10/22", period)
        assert result.arrival == date(2018, 10, 19)
        assert result.departure == date(2018, 10, 22)

    def test_departure_before_arrival_is_flagged_not_dropped(self):
        """A genuinely inverted date in the source is kept, not silently discarded."""
        period = make_period(date(2018, 7, 1), date(2018, 9, 30))
        result = resolve_segment_dates("8/28", "8/25", period)
        assert result.arrival == date(2018, 8, 28)
        assert result.departure == date(2018, 8, 25)
        assert "DEPARTURE_BEFORE_ARRIVAL" in result.flags

    def test_unparseable_arrival_text(self):
        period = make_period(date(2018, 7, 1), date(2018, 9, 30))
        result = resolve_segment_dates("not-a-date", "8/28", period)
        assert result.arrival is None
        assert result.departure is None
        assert "ARRIVAL_DATE_UNPARSEABLE" in result.flags

    def test_missing_period_flags_rather_than_guesses(self):
        result = resolve_segment_dates("8/25", "8/28", None)
        assert result.arrival is None
        assert result.departure is None
        assert "NO_PERIOD_FOR_YEAR_INFERENCE" in result.flags

    def test_prior_year_table_inside_a_later_filename(self):
        """A 2011 Q2 table appearing inside a file named for 2012 (header year wins)."""
        period = make_period(date(2011, 4, 1), date(2011, 6, 30))
        result = resolve_segment_dates("5/15", "5/18", period)
        assert result.arrival == date(2011, 5, 15)
        assert result.departure == date(2011, 5, 18)

    def test_invalid_calendar_date_flagged(self):
        period = make_period(date(2018, 7, 1), date(2018, 9, 30))
        result = resolve_segment_dates("9/31", "10/1", period)
        assert result.arrival is None
        assert "ARRIVAL_DATE_INVALID" in result.flags
