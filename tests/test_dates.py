"""Tests for row date resolution against a table's reporting period."""

from datetime import date

from official_foreign_travel.models.report import (
    CostCell,
    CostGroup,
    Costs,
    Period as ModelPeriod,
    Report,
    Sponsor,
    Traveler,
    TravelSegment,
)
from official_foreign_travel.parsing.dates import (
    parse_month_day,
    parse_month_day_swapped,
    recover_empty_dates,
    resolve_segment_dates,
)
from official_foreign_travel.parsing.header import Period


def make_period(start, end):
    return Period(start=start, end=end, year=end.year, quarter=None, raw="")


def _model_period(start, end):
    return ModelPeriod(start=start, end=end, year=end.year, quarter=None)


class TestParseMonthDay:
    def test_plain_md(self):
        assert parse_month_day("8/25") == (8, 25)
        assert parse_month_day_swapped("8/25") == (8, 25, False)

    def test_swapped_dm_when_first_gt_12(self):
        """'14/10' is European D/M (Oct 14), not invalid M/D."""
        assert parse_month_day("14/10") == (10, 14)
        assert parse_month_day_swapped("14/10") == (10, 14, True)

    def test_swapped_dm_november(self):
        assert parse_month_day("22/11") == (11, 22)
        assert parse_month_day_swapped("22/11") == (11, 22, True)

    def test_no_swap_when_both_le_12(self):
        """'2/8' is ambiguous; we keep M/D interpretation (Feb 8)."""
        assert parse_month_day_swapped("2/8") == (2, 8, False)

    def test_no_swap_when_first_le_12(self):
        """'3/39' has a valid month but invalid day -- don't swap."""
        assert parse_month_day_swapped("3/39") == (3, 39, False)

    def test_swap_with_invalid_day_still_returns_swapped(self):
        """'64/12' swaps to (12, 64) which is still invalid, but the swap is
        recorded so the caller can flag the right reason."""
        assert parse_month_day_swapped("64/12") == (12, 64, True)

    def test_trailing_period_stripped(self):
        assert parse_month_day("8/25.") == (8, 25)

    def test_unparseable_returns_none(self):
        assert parse_month_day("not-a-date") is None
        assert parse_month_day_swapped("not-a-date") is None


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

    def test_same_month_inversion_is_swapped(self):
        """Same-month day inversion (arrival 8/28, departure 8/25) is a source
        column swap: swap the dates and flag it, rather than leaving a
        backwards-in-time trip."""
        period = make_period(date(2018, 7, 1), date(2018, 9, 30))
        result = resolve_segment_dates("8/28", "8/25", period)
        assert result.arrival == date(2018, 8, 25)
        assert result.departure == date(2018, 8, 28)
        assert "ARRIVAL_DEPARTURE_SWAPPED" in result.flags
        assert "DEPARTURE_BEFORE_ARRIVAL" not in result.flags

    def test_same_month_inversion_real_world_case(self):
        """The G. Cannon 1994 UK row: arrival 8/21, departure 8/15 in source,
        with the next row showing 8/15 → 8/21 for Israel. The UK row's columns
        were reversed; we swap them."""
        period = make_period(date(1994, 7, 1), date(1994, 9, 30))
        result = resolve_segment_dates("8/21", "8/15", period)
        assert result.arrival == date(1994, 8, 15)
        assert result.departure == date(1994, 8, 21)
        assert "ARRIVAL_DEPARTURE_SWAPPED" in result.flags

    def test_cross_month_inversion_year_rollover_not_swapped(self):
        """Arrival 12/28, departure 1/2: dep_month < arr_month triggers year
        rollover (not a column swap). The rollover already produces a sensible
        forward-time trip; ARRIVAL_DEPARTURE_SWAPPED should not fire."""
        period = make_period(date(2018, 10, 1), date(2018, 12, 31))
        result = resolve_segment_dates("12/28", "1/2", period)
        assert result.arrival == date(2018, 12, 28)
        assert result.departure == date(2019, 1, 2)
        assert "YEAR_ROLLOVER_APPLIED" in result.flags
        assert "ARRIVAL_DEPARTURE_SWAPPED" not in result.flags

    def test_unparseable_arrival_text(self):
        period = make_period(date(2018, 7, 1), date(2018, 9, 30))
        result = resolve_segment_dates("not-a-date", "8/28", period)
        assert result.arrival is None
        assert result.departure is None
        assert "ARRIVAL_DATE_UNPARSEABLE" in result.flags

    def test_empty_arrival_raw_flagged_as_cell_empty(self):
        """A genuinely empty arrival cell (US-departure leg, no foreign
        arrival to record) is flagged ARRIVAL_CELL_EMPTY, not
        ARRIVAL_DATE_UNPARSEABLE -- the distinction lets downstream code
        tell source-missing from parse-failure."""
        period = make_period(date(2018, 7, 1), date(2018, 9, 30))
        result = resolve_segment_dates("", "8/28", period)
        assert result.arrival is None
        assert result.departure is None
        assert "ARRIVAL_CELL_EMPTY" in result.flags
        assert "ARRIVAL_DATE_UNPARSEABLE" not in result.flags

    def test_empty_departure_raw_flagged_as_cell_empty(self):
        period = make_period(date(2018, 7, 1), date(2018, 9, 30))
        result = resolve_segment_dates("8/25", "", period)
        assert result.arrival is None
        assert result.departure is None
        assert "DEPARTURE_CELL_EMPTY" in result.flags
        assert "DEPARTURE_DATE_UNPARSEABLE" not in result.flags

    def test_whitespace_only_arrival_treated_as_empty(self):
        period = make_period(date(2018, 7, 1), date(2018, 9, 30))
        result = resolve_segment_dates("   ", "8/28", period)
        assert "ARRIVAL_CELL_EMPTY" in result.flags

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
        """A date with an unambiguously invalid day (overshoots by > 1, not
        Feb 30) stays flagged. 9/32 overshoots Sept's 30 days by 2 -- not
        recoverable by the day-clamp rule."""
        period = make_period(date(2018, 7, 1), date(2018, 9, 30))
        result = resolve_segment_dates("9/32", "10/1", period)
        assert result.arrival is None
        assert "ARRIVAL_DATE_INVALID" in result.flags
        assert "DATE_DAY_CLAMPED_TO_MONTH_END" not in result.flags

    def test_feb29_non_leap_year_inferred_as_march1(self):
        """2005q2may16-027 Trinidad delegation: arr=2/26, dep=2/29, 2005
        is not a leap year. Feb 29 doesn't exist → infer March 1."""
        period = make_period(date(2005, 4, 1), date(2005, 6, 30))
        result = resolve_segment_dates("2/26", "2/29", period)
        assert result.arrival == date(2005, 2, 26)
        assert result.departure == date(2005, 3, 1)
        assert "DEPARTURE_DATE_INFERRED_LEAP_YEAR" in result.flags
        assert "DEPARTURE_DATE_INVALID" not in result.flags

    def test_feb29_leap_year_resolves_normally(self):
        """Feb 29 in a leap year (2008) resolves normally -- no recovery needed."""
        period = make_period(date(2008, 4, 1), date(2008, 6, 30))
        result = resolve_segment_dates("2/26", "2/29", period)
        assert result.arrival == date(2008, 2, 26)
        assert result.departure == date(2008, 2, 29)
        assert "DEPARTURE_DATE_INFERRED_LEAP_YEAR" not in result.flags
        assert "DEPARTURE_DATE_INVALID" not in result.flags

    def test_feb29_non_leap_year_with_year_rollover_to_leap_year(self):
        """A 2/29 departure in a non-leap arrival year, where dep_month <
        arr_month (year-rollover) and arrival.year + 1 is a leap year, rolls
        to Feb 29 of the next year. E.g. 2019q2may20-001 Engel Colombia:
        arr=3/28, dep=2/29, 2019 not leap, 2020 leap → 2020-02-29."""
        period = make_period(date(2019, 1, 1), date(2019, 3, 31))
        result = resolve_segment_dates("3/28", "2/29", period)
        assert result.arrival == date(2019, 3, 28)
        assert result.departure == date(2020, 2, 29)
        assert "YEAR_ROLLOVER_APPLIED" in result.flags
        assert "DEPARTURE_DATE_INVALID" not in result.flags

    def test_feb29_non_leap_year_year_rollover_not_leap_stays_invalid(self):
        """A 2/29 departure in a non-leap arrival year, where dep_month <
        arr_month but arrival.year + 1 is NOT a leap year, stays invalid.
        E.g. 2009q4nov19-009 Schmidt Ireland: arr=6/28, dep=2/29, 2009 not
        leap, 2010 not leap → stays DEPARTURE_DATE_INVALID."""
        period = make_period(date(2009, 7, 1), date(2009, 9, 30))
        result = resolve_segment_dates("6/28", "2/29", period)
        assert result.arrival == date(2009, 6, 28)
        assert result.departure is None
        assert "DEPARTURE_DATE_INVALID" in result.flags
        assert "DEPARTURE_DATE_INFERRED_LEAP_YEAR" not in result.flags

    def test_invalid_departure_not_feb29_stays_invalid(self):
        """Other invalid dates that aren't day-clampable (e.g. 13/13, 9/32)
        are not leap-year or day-clamp recoveries -- stay DEPARTURE_DATE_INVALID.
        9/31 and 11/31 ARE day-clampable (see TestDateDayClampedToMonthEnd)."""
        period = make_period(date(2005, 7, 1), date(2005, 9, 30))
        result = resolve_segment_dates("8/29", "13/13", period)
        assert result.arrival == date(2005, 8, 29)
        assert result.departure is None
        assert "DEPARTURE_DATE_INVALID" in result.flags
        assert "DEPARTURE_DATE_INFERRED_LEAP_YEAR" not in result.flags
        assert "DATE_DAY_CLAMPED_TO_MONTH_END" not in result.flags

    def test_dm_swap_resolves_within_period(self):
        """'14/10' (D/M, Oct 14) resolves to a date inside the Oct 1997 period."""
        period = make_period(date(1997, 10, 1), date(1997, 10, 31))
        result = resolve_segment_dates("14/10", "19/10", period)
        assert result.arrival == date(1997, 10, 14)
        assert result.departure == date(1997, 10, 19)
        assert "DATE_DAY_MONTH_SWAPPED" in result.flags

    def test_dm_swap_on_arrival_only(self):
        """A table mixing conventions: arrival '22/11' (D/M), departure '24/11'
        (ambiguous, treated as M/D Nov 24). Both resolve to November."""
        period = make_period(date(2000, 10, 1), date(2000, 12, 31))
        result = resolve_segment_dates("22/11", "24/11", period)
        assert result.arrival == date(2000, 11, 22)
        assert result.departure == date(2000, 11, 24)
        assert "DATE_DAY_MONTH_SWAPPED" in result.flags

    def test_dm_swap_with_year_rollover(self):
        """'30/11' (D/M Nov 30) → '2/12' (M/D Dec 2): cross-month within period."""
        period = make_period(date(2000, 10, 1), date(2000, 12, 31))
        result = resolve_segment_dates("30/11", "2/12", period)
        assert result.arrival == date(2000, 11, 30)
        assert result.departure == date(2000, 12, 2)
        assert "DATE_DAY_MONTH_SWAPPED" in result.flags

    def test_dm_swap_not_applied_when_both_le_12(self):
        """Both '2/8' and '2/10' are ambiguous; no swap, no flag."""
        period = make_period(date(2018, 7, 1), date(2018, 9, 30))
        result = resolve_segment_dates("2/8", "2/10", period)
        assert "DATE_DAY_MONTH_SWAPPED" not in result.flags

    def test_feb_29_in_non_leap_year_stays_invalid(self):
        """'2/29' in 1998 (non-leap) with arrival in February: recovered to
        March 1 via DEPARTURE_DATE_INFERRED_LEAP_YEAR. No swap attempted
        (second number 29 > 12)."""
        period = make_period(date(1998, 1, 1), date(1998, 3, 31))
        result = resolve_segment_dates("2/26", "2/29", period)
        assert result.arrival == date(1998, 2, 26)
        assert result.departure == date(1998, 3, 1)
        assert "DEPARTURE_DATE_INFERRED_LEAP_YEAR" in result.flags
        assert "DEPARTURE_DATE_INVALID" not in result.flags
        assert "DATE_DAY_MONTH_SWAPPED" not in result.flags


# --- day-clamp recovery tests ---


class TestDateDayClampedToMonthEnd:
    """Recovery for source typos where the day overshoots month-end by a
    small amount: ``9/31`` → ``9/30``, ``11/31`` → ``11/30``, ``2/30`` in
    a non-leap year → ``2/28``, ``2/30`` in a leap year → ``2/29``.
    Flagged ``DATE_DAY_CLAMPED_TO_MONTH_END`` instead of
    ``*_DATE_INVALID``. 3 reports in corpus (2006q1mar07-018,
    2019q1feb07-005, 2013q2may06-003)."""

    def test_sept_31_clamped_to_sept_30(self):
        """2006q1mar07-018: dep=9/31. Sept has 30 days; 31 = 30 + 1 → 9/30."""
        period = make_period(date(2005, 7, 1), date(2005, 9, 30))
        result = resolve_segment_dates("8/29", "9/31", period)
        assert result.arrival == date(2005, 8, 29)
        assert result.departure == date(2005, 9, 30)
        assert "DATE_DAY_CLAMPED_TO_MONTH_END" in result.flags
        assert "DEPARTURE_DATE_INVALID" not in result.flags

    def test_nov_31_clamped_to_nov_30(self):
        """2019q1feb07-005: dep=11/31. Nov has 30 days; 31 = 30 + 1 → 11/30."""
        period = make_period(date(2018, 10, 1), date(2018, 12, 31))
        result = resolve_segment_dates("10/27", "11/31", period)
        assert result.arrival == date(2018, 10, 27)
        assert result.departure == date(2018, 11, 30)
        assert "DATE_DAY_CLAMPED_TO_MONTH_END" in result.flags
        assert "DEPARTURE_DATE_INVALID" not in result.flags

    def test_feb_30_non_leap_clamped_to_feb_28(self):
        """2013q2may06-003: dep=2/30 in 2013 (non-leap). Feb 30 doesn't
        exist; 30 is 2 past Feb 28. Source treated Feb as a 30-day month
        → clamp to Feb 28."""
        period = make_period(date(2013, 1, 1), date(2013, 3, 31))
        result = resolve_segment_dates("1/28", "2/30", period)
        assert result.arrival == date(2013, 1, 28)
        assert result.departure == date(2013, 2, 28)
        assert "DATE_DAY_CLAMPED_TO_MONTH_END" in result.flags
        assert "DEPARTURE_DATE_INVALID" not in result.flags

    def test_feb_30_leap_year_clamped_to_feb_29(self):
        """dep=2/30 in 2020 (leap). Feb has 29 days; 30 = 29 + 1 → 2/29
        (strict off-by-one rule, not the Feb-30 special case)."""
        period = make_period(date(2020, 1, 1), date(2020, 3, 31))
        result = resolve_segment_dates("1/28", "2/30", period)
        assert result.arrival == date(2020, 1, 28)
        assert result.departure == date(2020, 2, 29)
        assert "DATE_DAY_CLAMPED_TO_MONTH_END" in result.flags
        assert "DEPARTURE_DATE_INVALID" not in result.flags

    def test_arrival_side_recovery(self):
        """The recovery also fires on the arrival side: arr=9/31 → 9/30."""
        period = make_period(date(2005, 7, 1), date(2005, 9, 30))
        result = resolve_segment_dates("9/31", "10/5", period)
        assert result.arrival == date(2005, 9, 30)
        assert result.departure == date(2005, 10, 5)
        assert "DATE_DAY_CLAMPED_TO_MONTH_END" in result.flags
        assert "ARRIVAL_DATE_INVALID" not in result.flags

    def test_sept_32_not_recoverable(self):
        """9/32 overshoots by 2, not 1 -- not recoverable, stays invalid."""
        period = make_period(date(2005, 7, 1), date(2005, 9, 30))
        result = resolve_segment_dates("8/29", "9/32", period)
        assert result.arrival == date(2005, 8, 29)
        assert result.departure is None
        assert "DEPARTURE_DATE_INVALID" in result.flags
        assert "DATE_DAY_CLAMPED_TO_MONTH_END" not in result.flags

    def test_13_13_not_recoverable(self):
        """Both numbers > 12 -- not a day-clamp case, stays invalid."""
        period = make_period(date(2005, 7, 1), date(2005, 9, 30))
        result = resolve_segment_dates("8/29", "13/13", period)
        assert result.arrival == date(2005, 8, 29)
        assert result.departure is None
        assert "DEPARTURE_DATE_INVALID" in result.flags
        assert "DATE_DAY_CLAMPED_TO_MONTH_END" not in result.flags

    def test_idempotent_under_reparse(self):
        """The recovery is deterministic from the raw text -- re-parsing
        the same raw produces the same flag set."""
        period = make_period(date(2005, 7, 1), date(2005, 9, 30))
        first = resolve_segment_dates("8/29", "9/31", period)
        second = resolve_segment_dates("8/29", "9/31", period)
        assert first.arrival == second.arrival
        assert first.departure == second.departure
        assert first.flags == second.flags


# --- recover_empty_dates tests ---

_EMPTY = CostCell(amount=None, raw="...........")
_SET = CostCell(amount=None, raw="")  # amount doesn't matter for date recovery


def _seg(arrival_raw, departure_raw, country, flags=None):
    costs = Costs(
        per_diem=CostGroup(foreign_currency=_EMPTY, us_dollar=_EMPTY),
        transportation=CostGroup(foreign_currency=_EMPTY, us_dollar=_EMPTY),
        other=CostGroup(foreign_currency=_EMPTY, us_dollar=_EMPTY),
        total=CostGroup(foreign_currency=_EMPTY, us_dollar=_EMPTY),
    )
    return TravelSegment(
        arrival_raw=arrival_raw,
        departure_raw=departure_raw,
        country_raw=country,
        costs=costs,
        flags=list(flags or []),
    )


def _report(period, travelers):
    # Report expects a models.report.Period; convert from header.Period if needed.
    if period is not None and not isinstance(period, ModelPeriod):
        period = _model_period(period.start, period.end)
    return Report(
        report_id="test-000",
        source_file="test.txt",
        table_index=0,
        sponsor=Sponsor(type="committee", name="COMMITTEE ON TEST", raw="COMMITTEE ON TEST"),
        period=period,
        header_raw="",
        travelers=travelers,
    )


def _resolve_segment(seg, period):
    """Apply resolve_segment_dates to a segment, mirroring assemble.py's flow."""
    resolved = resolve_segment_dates(seg.arrival_raw, seg.departure_raw, period)
    seg.arrival_date = resolved.arrival
    seg.departure_date = resolved.departure
    seg.flags.extend(resolved.flags)


class TestRecoverEmptyDates:
    """Post-pass that reclassifies recoverable ARRIVAL_CELL_EMPTY / DEPARTURE_CELL_EMPTY.

    Mirrors the pattern in the corpus: empty cells are usually US departure/return
    legs (intentionally blank) or connecting flights (sibling carries the missing
    date) or single-date transits (same-day arrival/departure).
    """

    def test_us_departure_leg_arrival_empty(self):
        """Empty arrival on a 'United States' segment is a US-departure leg."""
        period = make_period(date(2018, 10, 1), date(2018, 12, 31))
        seg = _seg("", "12/17", "United States")
        _resolve_segment(seg, period)
        r = _report(period, [Traveler(name="A", segments=[seg])])
        recover_empty_dates(r)
        assert "ARRIVAL_CELL_EMPTY" not in seg.flags
        assert "US_DEPARTURE_LEG" in seg.flags
        assert seg.arrival_date is None
        assert seg.departure_date is None  # not inferred -- US legs are intentionally blank

    def test_us_return_leg_departure_empty(self):
        """Empty departure on a 'USA' segment is a US-return leg."""
        period = make_period(date(2018, 10, 1), date(2018, 12, 31))
        seg = _seg("12/17", "", "USA")
        _resolve_segment(seg, period)
        r = _report(period, [Traveler(name="A", segments=[seg])])
        recover_empty_dates(r)
        assert "DEPARTURE_CELL_EMPTY" not in seg.flags
        assert "US_RETURN_LEG" in seg.flags
        assert seg.arrival_date is None
        assert seg.departure_date is None

    def test_foreign_arrival_empty_inferred_from_prev_sibling(self):
        """Arrival empty on a foreign segment: use prev segment's departure."""
        period = make_period(date(2018, 10, 1), date(2018, 12, 31))
        seg0 = _seg("11/16", "11/20", "Ireland")
        seg1 = _seg("", "11/22", "Scotland")
        for s in (seg0, seg1):
            _resolve_segment(s, period)
        r = _report(period, [Traveler(name="A", segments=[seg0, seg1])])
        recover_empty_dates(r)
        assert "ARRIVAL_CELL_EMPTY" not in seg1.flags
        assert "DATE_INFERRED_FROM_SIBLING" in seg1.flags
        assert seg1.arrival_date == date(2018, 11, 20)

    def test_foreign_departure_empty_inferred_from_next_sibling(self):
        """Departure empty on a foreign segment: use next segment's arrival."""
        period = make_period(date(2018, 10, 1), date(2018, 12, 31))
        seg0 = _seg("11/16", "", "Ireland")
        seg1 = _seg("11/20", "11/22", "Scotland")
        for s in (seg0, seg1):
            _resolve_segment(s, period)
        r = _report(period, [Traveler(name="A", segments=[seg0, seg1])])
        recover_empty_dates(r)
        assert "DEPARTURE_CELL_EMPTY" not in seg0.flags
        assert "DATE_INFERRED_FROM_SIBLING" in seg0.flags
        assert seg0.departure_date == date(2018, 11, 20)

    def test_foreign_arrival_empty_no_prev_falls_back_to_same_day(self):
        """First segment, arrival empty, no prev: same-day arrival = own departure."""
        period = make_period(date(2018, 10, 1), date(2018, 12, 31))
        seg = _seg("", "11/20", "Ireland")
        _resolve_segment(seg, period)
        r = _report(period, [Traveler(name="A", segments=[seg])])
        recover_empty_dates(r)
        assert "ARRIVAL_CELL_EMPTY" not in seg.flags
        assert "DATE_INFERRED_SAME_DAY" in seg.flags
        assert seg.arrival_date == date(2018, 11, 20)
        assert seg.departure_date == date(2018, 11, 20)

    def test_foreign_departure_empty_no_next_falls_back_to_same_day(self):
        """Last segment, departure empty, no next: same-day departure = own arrival."""
        period = make_period(date(2018, 10, 1), date(2018, 12, 31))
        seg = _seg("11/20", "", "Ireland")
        _resolve_segment(seg, period)
        r = _report(period, [Traveler(name="A", segments=[seg])])
        recover_empty_dates(r)
        assert "DEPARTURE_CELL_EMPTY" not in seg.flags
        assert "DATE_INFERRED_SAME_DAY" in seg.flags
        assert seg.arrival_date == date(2018, 11, 20)
        assert seg.departure_date == date(2018, 11, 20)

    def test_adjacent_pair_both_empty_left_flagged(self):
        """The Walseth pattern: seg i has dep_empty, seg i+1 has arr_empty.
        Neither sibling has the missing date -- same-day inference would
        manufacture 0-day stays, so both stay flagged."""
        period = make_period(date(2018, 10, 1), date(2018, 12, 31))
        seg0 = _seg("5/14", "5/16", "Poland")
        seg1 = _seg("5/14", "", "Netherlands")
        seg2 = _seg("", "5/21", "Czech Republic")
        for s in (seg0, seg1, seg2):
            _resolve_segment(s, period)
        r = _report(period, [Traveler(name="A", segments=[seg0, seg1, seg2])])
        recover_empty_dates(r)
        # seg0 is fully populated, no recovery needed
        assert "DEPARTURE_CELL_EMPTY" in seg1.flags
        assert "ARRIVAL_CELL_EMPTY" in seg2.flags
        # Neither should have been reclassified to a recovery flag
        assert "DATE_INFERRED_FROM_SIBLING" not in seg1.flags
        assert "DATE_INFERRED_FROM_SIBLING" not in seg2.flags
        assert "DATE_INFERRED_SAME_DAY" not in seg1.flags
        assert "DATE_INFERRED_SAME_DAY" not in seg2.flags

    def test_consecutive_dep_empty_inferred_from_next_arrival(self):
        """The Kevin Long pattern: all segments have dep_empty but valid
        arrivals. Each segment's departure is inferred from the next
        segment's arrival (resolved from its raw text, since its
        arrival_date is None due to its own empty departure). The last
        segment falls back to same-day."""
        period = make_period(date(2018, 10, 1), date(2018, 12, 31))
        seg0 = _seg("11/18", "", "Jordan")
        seg1 = _seg("11/19", "", "Kuwait")
        seg2 = _seg("11/20", "", "Bahrain")
        for s in (seg0, seg1, seg2):
            _resolve_segment(s, period)
        r = _report(period, [Traveler(name="A", segments=[seg0, seg1, seg2])])
        recover_empty_dates(r)
        # seg0 departure inferred from seg1's arrival
        assert "DEPARTURE_CELL_EMPTY" not in seg0.flags
        assert "DATE_INFERRED_FROM_SIBLING" in seg0.flags
        assert seg0.departure_date == date(2018, 11, 19)
        # seg1 departure inferred from seg2's arrival
        assert "DEPARTURE_CELL_EMPTY" not in seg1.flags
        assert "DATE_INFERRED_FROM_SIBLING" in seg1.flags
        assert seg1.departure_date == date(2018, 11, 20)
        # seg2 is last, no next sibling -- same-day fallback
        assert "DEPARTURE_CELL_EMPTY" not in seg2.flags
        assert "DATE_INFERRED_SAME_DAY" in seg2.flags

    def test_consecutive_arr_empty_inferred_from_prev_departure(self):
        """Mirror of the Kevin Long pattern on the arrival side: all segments
        have arr_empty but valid departures. Each segment's arrival is
        inferred from the previous segment's departure (resolved from its
        raw text). The first segment falls back to same-day."""
        period = make_period(date(2018, 10, 1), date(2018, 12, 31))
        seg0 = _seg("", "11/19", "Jordan")
        seg1 = _seg("", "11/20", "Kuwait")
        seg2 = _seg("", "11/21", "Bahrain")
        for s in (seg0, seg1, seg2):
            _resolve_segment(s, period)
        r = _report(period, [Traveler(name="A", segments=[seg0, seg1, seg2])])
        recover_empty_dates(r)
        # seg0 is first, no prev sibling -- same-day fallback
        assert "ARRIVAL_CELL_EMPTY" not in seg0.flags
        assert "DATE_INFERRED_SAME_DAY" in seg0.flags
        # seg1 arrival inferred from seg0's departure
        assert "ARRIVAL_CELL_EMPTY" not in seg1.flags
        assert "DATE_INFERRED_FROM_SIBLING" in seg1.flags
        assert seg1.arrival_date == date(2018, 11, 19)
        # seg2 arrival inferred from seg1's departure
        assert "ARRIVAL_CELL_EMPTY" not in seg2.flags
        assert "DATE_INFERRED_FROM_SIBLING" in seg2.flags
        assert seg2.arrival_date == date(2018, 11, 20)

    def test_idempotent_on_revalidation(self):
        """Running recover_empty_dates twice doesn't accumulate recovery flags."""
        period = make_period(date(2018, 10, 1), date(2018, 12, 31))
        seg = _seg("", "11/20", "United States")
        _resolve_segment(seg, period)
        r = _report(period, [Traveler(name="A", segments=[seg])])
        recover_empty_dates(r)
        recover_empty_dates(r)
        assert seg.flags.count("US_DEPARTURE_LEG") == 1
        assert "ARRIVAL_CELL_EMPTY" not in seg.flags

    def test_us_country_variants_recognized(self):
        """US detection recognizes 'United States', 'USA', 'U.S.'."""
        period = make_period(date(2018, 10, 1), date(2018, 12, 31))
        for country in ("United States", "USA", "U.S."):
            seg = _seg("", "11/20", country)
            _resolve_segment(seg, period)
            r = _report(period, [Traveler(name="A", segments=[seg])])
            recover_empty_dates(r)
            assert "US_DEPARTURE_LEG" in seg.flags, f"failed for {country!r}"
            assert "ARRIVAL_CELL_EMPTY" not in seg.flags

    def test_foreign_segment_with_no_period_stays_flagged(self):
        """When period is None (no year inference possible), don't infer."""
        seg = _seg("", "11/20", "Ireland")
        _resolve_segment(seg, None)
        r = _report(None, [Traveler(name="A", segments=[seg])])
        recover_empty_dates(r)
        # ARRIVAL_CELL_EMPTY may have been replaced with NO_PERIOD_FOR_YEAR_INFERENCE
        # by resolve_segment_dates -- either way, no recovery flag should be added
        assert "DATE_INFERRED_SAME_DAY" not in seg.flags
        assert "DATE_INFERRED_FROM_SIBLING" not in seg.flags
        assert "US_DEPARTURE_LEG" not in seg.flags
