"""Resolve a row's raw M/D arrival/departure text into full dates.

Row dates carry no year of their own; the year must be inferred from the
table's reporting period. This also handles trips that cross a calendar
year boundary (e.g. arrival 12/28, departure 1/2) and tables whose period
itself spans two years.
"""

import re
from dataclasses import dataclass
from datetime import date as date_cls
from typing import Optional

from .header import Period

DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})$")
WINDOW_PAD_DAYS = 60


@dataclass
class ResolvedDates:
    arrival: Optional[date_cls]
    departure: Optional[date_cls]
    flags: list[str]


def _try_date(year: int, month: int, day: int) -> Optional[date_cls]:
    try:
        return date_cls(year, month, day)
    except ValueError:
        return None


def parse_month_day(raw: str) -> Optional[tuple[int, int]]:
    """Parse a raw 'M/D' token into (month, day), or None if unparseable."""
    match = DATE_RE.match(raw.strip().rstrip("."))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _best_fit_year(month: int, day: int, years: list[int], period: Period) -> Optional[date_cls]:
    """Pick whichever candidate year places (month, day) closest to the table's period."""
    assert period.end is not None  # callers only reach here after checking this
    period_end = period.end

    candidates = [d for d in (_try_date(y, month, day) for y in years) if d is not None]
    if not candidates:
        return None

    if period.start is not None:
        window_start = period.start.toordinal() - WINDOW_PAD_DAYS
    else:
        window_start = period_end.toordinal() - WINDOW_PAD_DAYS
    window_end = period_end.toordinal() + WINDOW_PAD_DAYS

    in_window = [d for d in candidates if window_start <= d.toordinal() <= window_end]
    if in_window:
        return min(in_window, key=lambda d: abs(d.toordinal() - period_end.toordinal()))
    return min(candidates, key=lambda d: abs(d.toordinal() - period_end.toordinal()))


def resolve_segment_dates(
    arrival_raw: str, departure_raw: str, period: Optional[Period]
) -> ResolvedDates:
    """
    Resolve a segment's raw M/D dates into full dates using the table's period.

    Args:
        arrival_raw: Raw "M/D" arrival text
        departure_raw: Raw "M/D" departure text
        period: The table's reporting period (may be None if the header was unparseable)

    Returns:
        ResolvedDates with arrival/departure (either may be None) and flags.
        Dates are never dropped for looking "wrong" (e.g. departure before
        arrival) -- that is flagged, not discarded.
    """
    flags: list[str] = []
    arrival_md = parse_month_day(arrival_raw)
    departure_md = parse_month_day(departure_raw)

    if arrival_md is None:
        flags.append("ARRIVAL_DATE_UNPARSEABLE")
    if departure_md is None:
        flags.append("DEPARTURE_DATE_UNPARSEABLE")
    if arrival_md is None or departure_md is None:
        return ResolvedDates(arrival=None, departure=None, flags=flags)

    if period is None or period.end is None:
        flags.append("NO_PERIOD_FOR_YEAR_INFERENCE")
        return ResolvedDates(arrival=None, departure=None, flags=flags)

    arr_month, arr_day = arrival_md
    dep_month, dep_day = departure_md

    year_candidates = sorted({period.end.year, period.end.year - 1, period.end.year + 1})
    if period.start is not None:
        year_candidates.append(period.start.year)
    arrival = _best_fit_year(arr_month, arr_day, sorted(set(year_candidates)), period)
    if arrival is None:
        flags.append("ARRIVAL_DATE_INVALID")
        return ResolvedDates(arrival=None, departure=None, flags=flags)

    departure = _try_date(arrival.year, dep_month, dep_day)
    if departure is not None and dep_month < arr_month:
        # Genuine month-order inversion (e.g. arrival Dec, departure Jan)
        # implies the trip crosses into the next calendar year. A same-month
        # day inversion is a data error, not a year boundary -- leave it flagged.
        rolled = _try_date(arrival.year + 1, dep_month, dep_day)
        if rolled is not None:
            departure = rolled
            flags.append("YEAR_ROLLOVER_APPLIED")

    if departure is None:
        flags.append("DEPARTURE_DATE_INVALID")
        return ResolvedDates(arrival=arrival, departure=None, flags=flags)

    if departure < arrival:
        flags.append("DEPARTURE_BEFORE_ARRIVAL")

    return ResolvedDates(arrival=arrival, departure=departure, flags=flags)
