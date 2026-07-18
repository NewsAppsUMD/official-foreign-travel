"""Resolve a row's raw M/D arrival/departure text into full dates.

Row dates carry no year of their own; the year must be inferred from the
table's reporting period. This also handles trips that cross a calendar
year boundary (e.g. arrival 12/28, departure 1/2) and tables whose period
itself spans two years.

``recover_empty_dates`` is a post-pass that runs after ``resolve_segment_dates``
over a whole traveler's segments. It reclassifies the recoverable
``ARRIVAL_CELL_EMPTY`` / ``DEPARTURE_CELL_EMPTY`` cases:

- A segment whose country is the United States and that has only one date
  filled is a domestic departure or return leg -- the other cell is
  intentionally blank in the source. Reclassified to ``US_DEPARTURE_LEG``
  or ``US_RETURN_LEG`` (no date inferred).
- A foreign segment whose adjacent sibling has the missing date filled
  (previous segment's departure, or next segment's arrival) is inferred
  from that sibling -- connecting flights typically land and depart the
  same day. Reclassified to ``DATE_INFERRED_FROM_SIBLING``.
- A foreign segment with no useful sibling but its own other date present
  is inferred as a same-day arrival/departure. Reclassified to
  ``DATE_INFERRED_SAME_DAY``.
- Pairs of adjacent segments that each have one date empty (so neither
  sibling has the missing date) are left flagged -- same-day inference
  would manufacture a 0-day stay, and there is no other signal to use.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date as date_cls
from typing import TYPE_CHECKING, Optional

from .header import Period

if TYPE_CHECKING:
    from ..models.report import Report, TravelSegment

DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})$")
WINDOW_PAD_DAYS = 60

US_COUNTRY_TOKENS = {"united states", "united states of america", "usa", "us"}


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
    """Parse a raw 'M/D' token into (month, day), or None if unparseable.

    A first number > 12 with a second number in 1-12 is interpreted as the
    European D/M convention (e.g. "14/10" → Oct 14). Use ``parse_month_day_swapped``
    if you need to know whether the swap was applied.
    """
    result = parse_month_day_swapped(raw)
    if result is None:
        return None
    return result[0], result[1]


def parse_month_day_swapped(raw: str) -> Optional[tuple[int, int, bool]]:
    """Parse a raw 'M/D' token into (month, day, swapped), or None if unparseable.

    ``swapped`` is True when the source's first number was reinterpreted as the
    day (D/M convention) because it exceeded 12 while the second number was a
    valid month 1-12.
    """
    match = DATE_RE.match(raw.strip().rstrip("."))
    if not match:
        return None
    first, second = int(match.group(1)), int(match.group(2))
    if first > 12 and 1 <= second <= 12:
        return second, first, True
    return first, second, False


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


def _clamped_day(month: int, day: int, year: int) -> Optional[int]:
    """Return the day clamped to month-end if the source day overshoots
    days-in-month by a small, typo-like amount; otherwise None.

    Two shapes qualify:
    - ``day == days_in_month + 1``: the source wrote one past month-end
      (e.g. ``9/31`` → ``9/30``, ``11/31`` → ``11/30``, ``2/30`` in a
      leap year → ``2/29``).
    - ``month == 2`` and ``day == 30`` in a non-leap year: the source
      treated Feb as a 30-day month → clamp to ``2/28``.

    Feb 29 in a non-leap year is excluded: the existing leap-year
    recovery (``DEPARTURE_DATE_INFERRED_LEAP_YEAR`` / ``YEAR_ROLLOVER``)
    handles that shape, and clamping it to Feb 28 would mask the
    year-rollover signal.
    """
    if not (1 <= month <= 12):
        return None
    days_in_month = calendar.monthrange(year, month)[1]
    if month == 2 and day == 29 and days_in_month == 28:
        return None
    if day == days_in_month + 1:
        return days_in_month
    if month == 2 and day == 30 and days_in_month == 28:
        return 28
    return None


def _best_fit_year_with_day_clamp(
    month: int, day: int, years: list[int], period: Period
) -> tuple[Optional[date_cls], bool]:
    """Like ``_best_fit_year``, but if no candidate year accepts
    (month, day) as-is, retry with the day clamped to month-end.

    Returns ``(date, clamped)`` where ``clamped`` is True when the
    returned date came from a clamped day.
    """
    direct = _best_fit_year(month, day, years, period)
    if direct is not None:
        return direct, False

    assert period.end is not None
    period_end = period.end
    clamped_candidates: list[date_cls] = []
    for y in years:
        cd = _clamped_day(month, day, y)
        if cd is None:
            continue
        d = _try_date(y, month, cd)
        if d is not None:
            clamped_candidates.append(d)
    if not clamped_candidates:
        return None, False

    if period.start is not None:
        window_start = period.start.toordinal() - WINDOW_PAD_DAYS
    else:
        window_start = period_end.toordinal() - WINDOW_PAD_DAYS
    window_end = period_end.toordinal() + WINDOW_PAD_DAYS

    in_window = [d for d in clamped_candidates if window_start <= d.toordinal() <= window_end]
    if in_window:
        return (
            min(in_window, key=lambda d: abs(d.toordinal() - period_end.toordinal())),
            True,
        )
    return (
        min(clamped_candidates, key=lambda d: abs(d.toordinal() - period_end.toordinal())),
        True,
    )


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
    arrival_parsed = parse_month_day_swapped(arrival_raw)
    departure_parsed = parse_month_day_swapped(departure_raw)

    if arrival_parsed is None:
        flags.append(
            "ARRIVAL_CELL_EMPTY" if not arrival_raw.strip() else "ARRIVAL_DATE_UNPARSEABLE"
        )
    if departure_parsed is None:
        flags.append(
            "DEPARTURE_CELL_EMPTY" if not departure_raw.strip() else "DEPARTURE_DATE_UNPARSEABLE"
        )
    if arrival_parsed is None or departure_parsed is None:
        return ResolvedDates(arrival=None, departure=None, flags=flags)

    if period is None or period.end is None:
        flags.append("NO_PERIOD_FOR_YEAR_INFERENCE")
        return ResolvedDates(arrival=None, departure=None, flags=flags)

    arr_month, arr_day, arr_swapped = arrival_parsed
    dep_month, dep_day, dep_swapped = departure_parsed

    # The D/M convention is a table-level property: if either date is
    # unambiguous D/M (first number > 12), the table is D/M and the other
    # date should also be re-interpreted as D/M even if its raw form was
    # ambiguous (both numbers ≤ 12). E.g. arrival "30/11" forces departure
    # "2/12" to be read as Dec 2, not Feb 12.
    if arr_swapped and not dep_swapped and 1 <= dep_month <= 12 and 1 <= dep_day <= 12:
        dep_month, dep_day = dep_day, dep_month
        dep_swapped = True
    if dep_swapped and not arr_swapped and 1 <= arr_month <= 12 and 1 <= arr_day <= 12:
        arr_month, arr_day = arr_day, arr_month
        arr_swapped = True

    if arr_swapped or dep_swapped:
        flags.append("DATE_DAY_MONTH_SWAPPED")

    year_candidates = sorted({period.end.year, period.end.year - 1, period.end.year + 1})
    if period.start is not None:
        year_candidates.append(period.start.year)
    arrival, arr_clamped = _best_fit_year_with_day_clamp(
        arr_month, arr_day, sorted(set(year_candidates)), period
    )
    if arrival is None:
        flags.append("ARRIVAL_DATE_INVALID")
        return ResolvedDates(arrival=None, departure=None, flags=flags)
    if arr_clamped:
        flags.append("DATE_DAY_CLAMPED_TO_MONTH_END")

    departure = _try_date(arrival.year, dep_month, dep_day)
    if dep_month < arr_month:
        # Genuine month-order inversion (e.g. arrival Dec, departure Jan)
        # implies the trip crosses into the next calendar year. A same-month
        # day inversion is a data error, not a year boundary -- leave it flagged.
        # This also covers Feb 29 in a non-leap arrival year where the next
        # year is a leap year: _try_date(arrival.year, 2, 29) returns None, but
        # _try_date(arrival.year + 1, 2, 29) succeeds. E.g.
        # 2019q2may20-001 Engel Colombia: arr=3/28, dep=2/29, 2019 not leap,
        # 2020 leap → 2020-02-29. (The only dates invalid in one year but
        # valid in the next are Feb 29, so removing the `departure is not
        # None` guard only affects leap-day year-rollover cases.)
        rolled = _try_date(arrival.year + 1, dep_month, dep_day)
        if rolled is not None:
            departure = rolled
            flags.append("YEAR_ROLLOVER_APPLIED")

    if departure is None:
        # Feb 29 in a non-leap year: the source wrote 2/29 but the year
        # doesn't have a Feb 29. If the arrival is in February of the same
        # year, infer departure = March 1 (the day after Feb 28, which is
        # what Feb 29 would map to in a non-leap year). E.g.
        # 2005q2may16-027 Trinidad delegation: arr=2/26, dep=2/29, 2005
        # is not a leap year → departure inferred as 2005-03-01.
        if (
            dep_month == 2
            and dep_day == 29
            and arrival is not None
            and arrival.month == 2
        ):
            inferred = _try_date(arrival.year, 3, 1)
            if inferred is not None:
                flags.append("DEPARTURE_DATE_INFERRED_LEAP_YEAR")
                return ResolvedDates(arrival=arrival, departure=inferred, flags=flags)

        # Day-clamp recovery: source wrote one past month-end (e.g. 9/31,
        # 11/31) or treated Feb as a 30-day month (2/30 in non-leap). Try
        # clamping in arrival.year first; for the year-rollover case
        # (dep_month < arr_month), also try arrival.year + 1 so a clamped
        # Feb 30 in a cross-year trip rolls forward instead of producing
        # a departure before the arrival.
        clamp_years = (
            [arrival.year, arrival.year + 1]
            if dep_month < arr_month
            else [arrival.year]
        )
        for y in clamp_years:
            cd = _clamped_day(dep_month, dep_day, y)
            if cd is None:
                continue
            cand = _try_date(y, dep_month, cd)
            if cand is None or cand < arrival:
                continue
            departure = cand
            if y != arrival.year:
                flags.append("YEAR_ROLLOVER_APPLIED")
            flags.append("DATE_DAY_CLAMPED_TO_MONTH_END")
            return ResolvedDates(arrival=arrival, departure=departure, flags=flags)

        flags.append("DEPARTURE_DATE_INVALID")
        return ResolvedDates(arrival=arrival, departure=None, flags=flags)

    if departure < arrival:
        # Same-month day inversion (e.g. arrival 11/16, departure 11/11) is
        # overwhelmingly a source column swap -- the arrival/departure columns
        # were reversed for this row, not a genuine backwards-in-time trip.
        # Every one of the 54 corpus cases that fired DEPARTURE_BEFORE_ARRIVAL
        # before this fix was same-month. Swap the two dates and record it;
        # cross-month inversions (which would have triggered YEAR_ROLLOVER
        # above and shouldn't reach here inverted) stay flagged.
        if dep_month == arr_month and dep_day < arr_day:
            arrival, departure = departure, arrival
            flags.append("ARRIVAL_DEPARTURE_SWAPPED")
        else:
            flags.append("DEPARTURE_BEFORE_ARRIVAL")

    return ResolvedDates(arrival=arrival, departure=departure, flags=flags)


def _is_us_country(country_raw: str) -> bool:
    # Strip trailing dot-fill, then collapse internal periods (so "U.S."
    # and "U.S.A." normalize to "us" and "usa") before matching.
    normalized = (
        country_raw.strip().rstrip(".").strip().lower().replace(".", "")
    )
    return normalized in US_COUNTRY_TOKENS


def _resolve_single_date(raw: str, period: Optional[Period]) -> Optional[date_cls]:
    """Resolve a single M/D raw to a full date using the period for year inference.

    Mirrors the year-candidate logic of ``resolve_segment_dates`` but for one
    date in isolation -- used by ``recover_empty_dates`` when the only
    available signal for the missing date is the segment's own other date.
    """
    parsed = parse_month_day_swapped(raw)
    if parsed is None or period is None or period.end is None:
        return None
    month, day, _ = parsed
    year_candidates = sorted({period.end.year, period.end.year - 1, period.end.year + 1})
    if period.start is not None:
        year_candidates.append(period.start.year)
    return _best_fit_year(month, day, sorted(set(year_candidates)), period)


RECOVERY_FLAGS = (
    "US_DEPARTURE_LEG",
    "US_RETURN_LEG",
    "DATE_INFERRED_FROM_SIBLING",
    "DATE_INFERRED_SAME_DAY",
)


def recover_empty_dates(report: "Report") -> None:
    """Reclassify recoverable ARRIVAL_CELL_EMPTY / DEPARTURE_CELL_EMPTY flags.

    Mutates segments in place. Idempotent: clears its own recovery flags
    before running so revalidation (e.g. after a correction) doesn't
    accumulate stale recovery tags.
    """
    for traveler in report.travelers:
        for seg in traveler.segments:
            for flag in RECOVERY_FLAGS:
                if flag in seg.flags:
                    seg.flags.remove(flag)

    for traveler in report.travelers:
        segments = traveler.segments
        n = len(segments)
        for i, seg in enumerate(segments):
            # Base recovery on the underlying condition (empty raw text), not
            # on the ARRIVAL_CELL_EMPTY / DEPARTURE_CELL_EMPTY flags -- those
            # are removed on the first recovery, so a second pass would
            # otherwise miss the segment entirely.
            arr_empty = not seg.arrival_raw.strip()
            dep_empty = not seg.departure_raw.strip()
            if not arr_empty and not dep_empty:
                continue

            is_us = _is_us_country(seg.country_raw)

            if arr_empty:
                if is_us:
                    if "ARRIVAL_CELL_EMPTY" in seg.flags:
                        seg.flags.remove("ARRIVAL_CELL_EMPTY")
                    seg.flags.append("US_DEPARTURE_LEG")
                    continue
                # Re-resolve the present departure date -- resolve_segment_dates
                # returned both dates as None when arrival was unparseable.
                own_dep = _resolve_single_date(seg.departure_raw, report.period)
                # Resolve the previous segment's departure from its raw text --
                # its departure_date is None when it has an empty arrival
                # (resolve_segment_dates returns None for both dates when either
                # is empty), even though its departure_raw may be valid.
                prev_dep = (
                    _resolve_single_date(segments[i - 1].departure_raw, report.period)
                    if i > 0 and segments[i - 1].departure_raw.strip()
                    else None
                )
                # Only skip sibling inference if the previous segment's
                # departure is empty -- its arrival being empty doesn't affect
                # our ability to infer the current arrival from its departure.
                prev_dep_empty = i > 0 and not segments[i - 1].departure_raw.strip()
                if prev_dep is not None and not prev_dep_empty:
                    seg.arrival_date = prev_dep
                    if own_dep is not None:
                        seg.departure_date = own_dep
                    if "ARRIVAL_CELL_EMPTY" in seg.flags:
                        seg.flags.remove("ARRIVAL_CELL_EMPTY")
                    seg.flags.append("DATE_INFERRED_FROM_SIBLING")
                    continue
                if not prev_dep_empty and own_dep is not None:
                    seg.arrival_date = own_dep
                    seg.departure_date = own_dep
                    if "ARRIVAL_CELL_EMPTY" in seg.flags:
                        seg.flags.remove("ARRIVAL_CELL_EMPTY")
                    seg.flags.append("DATE_INFERRED_SAME_DAY")
                continue

            # dep_empty
            if is_us:
                if "DEPARTURE_CELL_EMPTY" in seg.flags:
                    seg.flags.remove("DEPARTURE_CELL_EMPTY")
                seg.flags.append("US_RETURN_LEG")
                continue
            own_arr = _resolve_single_date(seg.arrival_raw, report.period)
            # Resolve the next segment's arrival from its raw text -- its
            # arrival_date is None when it has an empty departure, even though
            # its arrival_raw may be valid.
            next_arr = (
                _resolve_single_date(segments[i + 1].arrival_raw, report.period)
                if i < n - 1 and segments[i + 1].arrival_raw.strip()
                else None
            )
            # Only skip sibling inference if the next segment's arrival is
            # empty -- its departure being empty doesn't affect our ability to
            # infer the current departure from its arrival.
            next_arr_empty = i < n - 1 and not segments[i + 1].arrival_raw.strip()
            if next_arr is not None and not next_arr_empty:
                seg.departure_date = next_arr
                if own_arr is not None:
                    seg.arrival_date = own_arr
                if "DEPARTURE_CELL_EMPTY" in seg.flags:
                    seg.flags.remove("DEPARTURE_CELL_EMPTY")
                seg.flags.append("DATE_INFERRED_FROM_SIBLING")
                continue
            if not next_arr_empty and own_arr is not None:
                seg.departure_date = own_arr
                seg.arrival_date = own_arr
                if "DEPARTURE_CELL_EMPTY" in seg.flags:
                    seg.flags.remove("DEPARTURE_CELL_EMPTY")
                seg.flags.append("DATE_INFERRED_SAME_DAY")
