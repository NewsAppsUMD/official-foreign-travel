"""Extract traveler/segment rows from a table's data lines.

Arrival/departure dates are extracted by regex within the date zone rather
than trusted to exact column boundaries: row-to-row width variation (a
2-digit vs 1-digit day, a short vs long country name) means a rigid slice
can bleed a few characters into the next column even when the layout's
boundaries are correct on average. Extracting the date tokens directly and
treating anything left over as country-cell overflow is robust to that.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from ..utils.text import clean_cell, get_honorific
from .costs import CostGroup, Costs, costs_has_data, merge_costs, parse_cost_cell
from .header import NAME_WORD_RE, _looks_like_personal_name
from .layout import TableLayout

DATE_TOKEN_RE = re.compile(r"\d{1,2}/\d{1,2}")
# Some delegation rosters print the literal text "N/A" in both date cells
# instead of leaving them dot-filled/blank (e.g. a member whose travel dates
# weren't tracked the same way as the rest of the group). "N/A" doesn't match
# DATE_TOKEN_RE, so without this a row like "Hon. Ann Wagner... N/A  N/A
# Luxembourg..." finds zero date tokens, falls through to the "no usable
# date tokens" branch, and -- since `current` already has segments -- gets
# silently read as a labeled cost-supplement row for the PRIOR traveler,
# discarding this traveler's name and merging their cost into someone else's
# segment. Matching "N/A" as an equally valid date-zone token lets the normal
# two-token branch build a real (dateless) segment for them instead.
NA_TOKEN_RE = re.compile(r"N/A", re.IGNORECASE)
DATE_OR_NA_TOKEN_RE = re.compile(r"\d{1,2}/\d{1,2}|N/A", re.IGNORECASE)
# A CODEL that gets cancelled before departure is sometimes recorded with the
# literal words "CODEL"/"cancelled" filling both date cells instead of dates
# -- same shape as "N/A", different wording, so it needs the same treatment
# for the same reason (else every traveler on the roster after the first
# gets silently merged into whoever came before them). This is a narrow,
# evidence-based match for the one wording seen in the corpus, not a general
# "any non-date text" rule -- a wrapped variant that splits across two
# printed lines ("Didn't"/"Depart", "Trip"/"Cancelled") isn't caught here,
# since a single line's text is all this function ever sees. Word-bounded on
# both ends: an unbounded "cancel(?:led)?" would partial-match the "cancel"
# prefix inside "canceled" (single-L American spelling, e.g. "5/31
# (CANCELED)"), a *different* word that appears alongside real dates
# elsewhere in the corpus and must not be mistaken for this placeholder.
#
# Unlike DATE_OR_NA_TOKEN_RE, this is deliberately NOT searched from column
# 0 -- "CODEL" and "Codel" are common substrings of real travelers' own
# printed names ("Hon. Mac Collins (Rogers CODEL)", "Codel Solomon"), which
# must not be mistaken for this row's own date placeholder. It's only
# checked once real dates are already ruled out, and only from where the
# date columns actually start (see `_find_date_tokens`).
PLACEHOLDER_TOKEN_RE = re.compile(r"\bCODEL\b|\bcancel(?:led)?\b", re.IGNORECASE)
RULE_RE = re.compile(r"^\s*-{10,}")
# Footnote *definition* lines ("\3\ Military air transportation.") follow the
# committee total and closing rule, outside the traveler data region -- but
# nothing bounds `data_lines` to stop there, so without this guard they reach
# the same row-classification logic as real data rows. A definition whose text
# happens to match a row's phrasing (e.g. this one matches the "MILITARY AIR"
# label-row check below) would otherwise be misread as a labeled sub-row of
# whichever traveler was last `current`, tagging the wrong traveler's segment.
FOOTNOTE_DEF_RE = re.compile(r"^\s*\\\d+\\")
# "STAFFDEL Expense(s)" rows carry real dates and a country matching the
# delegation's leg, but the cost is a shared expense for the whole group, not
# any one traveler -- unlike "(STAFFDEL)"/"Staffdel Costs" label rows (no date
# tokens, already merged via COST_SUPPLEMENT_MERGED), this shape has valid
# dates and would otherwise be indistinguishable from a real new traveler.
STAFFDEL_EXPENSE_RE = re.compile(r"^STAFFDEL\s+EXPENSES?\b", re.IGNORECASE)
# A bare "--" (or "-"/"---") in the name column is some tables' "ditto" mark
# for "same traveler as the row above," used instead of leaving continuation
# rows blank. Non-empty, so without this it reads as a truthy name -- not a
# person (fails _looks_like_traveler_row_name) and not blank (skips the
# existing continuation path) -- so every continuation row for every
# traveler in the table was misread as one shared "non-person" traveler.
DASH_CONTINUATION_RE = re.compile(r"^-{1,3}$")
# Tolerates source typos in both the prefix word (Commitee, Committe, Committeee,
# Committeel, Committtee, Commmittee, Committed, Grant, Commercial) and the
# "total" token (totals, tota;, tota:, Totals:). The trailing
# (?:\s+for\s+|\s*\.) anchor requires either dot-fill or a "for ..." continuation
# after the token, which excludes footnote lines like "\3\ Total cost of all
# commercial flights." and "* Total air." that begin with non-alphabetic chars.
# The (?:\s*\\\d+\\)* allows one or more footnote markers between the token and
# the trailing dot-fill, e.g. "Committee total \1\ \2\.........." in
# 2008q4dec10 Science and Technology (the prior (?:\\\d+\\)? matched at most one).
TOTAL_ROW_RE = re.compile(
    r"^\s*"
    r"(?:"
    r"(?:committee|commitee|committe|committeee|committeel|committtee|commmittee|committed|grand|grant|codel|commercial)\s+"
    r")?"
    r"tota[a-z;:}]{0,3}"
    r"(?:\s*\\\d+\\)*"
    r"(?:\s+for\s+|\s*\.)",
    re.IGNORECASE,
)


@dataclass
class SegmentDraft:
    """One arrival/departure/country/cost row, before date resolution."""

    arrival_raw: str
    departure_raw: str
    country_raw: str
    costs: Costs
    flags: list[str] = field(default_factory=list)
    source_lines: list[int] = field(default_factory=list)


@dataclass
class TravelerDraft:
    """A named traveler and their travel segments within one table."""

    name: str
    segments: list[SegmentDraft] = field(default_factory=list)


def _looks_like_traveler_row_name(name: str) -> bool:
    """True when a printed row name looks like it names a traveler (a
    person), not a cost/expense/note label row ("Delegation expenses",
    "Luncheon", "Travel day", "(CODEL McCaul)").

    A name carrying a recognized honorific ("Hon.", "Mr.", "Speaker", etc.)
    is always a person, even when only a bare surname follows -- a common
    CODEL-list shorthand ("Hon. Hastert"). Otherwise, real traveler names in
    this corpus are consistently capitalized on every word ("Bart Reising",
    "Speaker Hastert"); label rows are typically capitalized only on their
    first word ("Delegation expenses", "Ground transportation") or are a
    single generic word ("Luncheon", "Interpreters"). A name wrapped in
    parentheses ("(CODEL McCaul)") is a sponsor/trip annotation that bled
    into the name column, never a person.
    """
    stripped = name.strip().rstrip(",")
    if not stripped or stripped.startswith("("):
        return False
    if get_honorific(stripped):
        return True
    words = stripped.split()
    if len(words) < 2:
        return False
    return all(NAME_WORD_RE.match(w) for w in words)


def _strip_dash_continuation(name: str) -> str:
    """Normalize a bare "--" ditto mark to empty, so it flows through the
    existing blank-name continuation logic and attaches to the traveler
    above, instead of being read as a distinct (non-person) traveler.
    """
    return "" if DASH_CONTINUATION_RE.match(name) else name


def _attach_named_segment(
    name: str,
    segment: SegmentDraft,
    travelers: list[TravelerDraft],
    travelers_by_name: dict[str, TravelerDraft],
) -> TravelerDraft:
    """Attach `segment` to the traveler named `name`, merging into an
    existing draft with the exact same name earlier in this table instead
    of creating a duplicate.

    Tables organized leg-by-leg (all travelers for leg 1, then leg 2, ...)
    reprint every traveler's name on every leg, rather than the more common
    convention of naming a traveler once and leaving subsequent rows blank.
    Treating every printed name as a new traveler regardless produced one
    fake traveler per leg per person -- inflating traveler counts and
    match-flag counts (e.g. STAFF_UNMATCHED) by a factor of the leg count.
    """
    existing = travelers_by_name.get(name)
    if existing is not None:
        # Already-flagged non-person rows (STAFFDEL_GROUP_EXPENSE,
        # NON_PERSON_LABEL_ROW -- set by the caller before this call) don't
        # need a second "same identity?" caveat -- they're not a person's
        # identity to begin with. Checking the flags the caller already set,
        # rather than re-deriving the classification here, keeps this in
        # sync with whichever specific pattern (STAFFDEL_EXPENSE_RE, the
        # general non-person check) actually matched: "STAFFDEL Expense"
        # itself passes the general capitalized-words check, so re-deriving
        # would wrongly add both flags.
        already_non_person = (
            "STAFFDEL_GROUP_EXPENSE" in segment.flags or "NON_PERSON_LABEL_ROW" in segment.flags
        )
        if not already_non_person:
            segment.flags.append("REPEATED_NAME_SEGMENTS_MERGED")
        existing.segments.append(segment)
        return existing
    draft = TravelerDraft(name=name, segments=[segment])
    travelers.append(draft)
    travelers_by_name[name] = draft
    return draft


def _find_date_tokens(
    zone: str, placeholder_min_start: int = 0
) -> Optional[tuple[re.Match, re.Match]]:
    """Find the first two M/D-or-"N/A" token matches within a zone, searched
    from column 0.

    Searching from 0 (not from the layout's arrival boundary) makes this
    robust to names that overflow their nominal column width -- a common
    failure mode where a long name pushes the actual date text to the right
    of where the layout expected it to start.

    Falls back to the CODEL/cancelled placeholder only once real dates are
    ruled out, and only at or after `placeholder_min_start` (normally the
    layout's arrival-column start) -- unlike real dates, this text is NOT
    searched from column 0, since "CODEL" is also a substring of real
    travelers' own printed names (see PLACEHOLDER_TOKEN_RE).
    """
    tokens = list(DATE_OR_NA_TOKEN_RE.finditer(zone))
    if len(tokens) >= 2:
        return tokens[0], tokens[1]
    placeholder_tokens = [
        m for m in PLACEHOLDER_TOKEN_RE.finditer(zone) if m.start() >= placeholder_min_start
    ]
    if len(placeholder_tokens) < 2:
        return None
    return placeholder_tokens[0], placeholder_tokens[1]


def _date_token_raw(match: re.Match) -> str:
    """The raw date text for a token match, normalizing "N/A" and the
    CODEL/cancelled placeholder text to empty.

    An explicit "N/A" (or "CODEL"/"cancelled") means the source is asserting
    there's no date here -- the same thing an empty/dot-filled cell means --
    so it should resolve to ARRIVAL_CELL_EMPTY/DEPARTURE_CELL_EMPTY
    downstream, not ARRIVAL_DATE_UNPARSEABLE/DEPARTURE_DATE_UNPARSEABLE
    (reserved for non-blank text that doesn't parse as a date).
    """
    text = match.group()
    if NA_TOKEN_RE.fullmatch(text) or PLACEHOLDER_TOKEN_RE.fullmatch(text):
        return ""
    return text


def _find_single_date_token(zone: str) -> Optional[re.Match]:
    """Return the lone M/D token when a row carries exactly one date.

    Used for "US departure" / "US return" legs in older reports, where one
    of the two date cells is left dot-filled because that end of the trip
    was domestic (no foreign arrival/departure to record). The two-date
    `_find_date_tokens` path skips these rows entirely, which loses the
    traveler's name when this is also the first row of a trip.
    """
    tokens = list(DATE_TOKEN_RE.finditer(zone))
    if len(tokens) != 1:
        return None
    return tokens[0]


def _parse_cost_cells(
    line: str, layout: TableLayout, footnote_map: dict[str, str]
) -> tuple[Costs, list[str]]:
    flags: list[str] = []
    cells = []
    for span in layout.cost_columns:
        cell, flag = parse_cost_cell(span.slice(line), footnote_map)
        cells.append(cell)
        if flag:
            flags.append(flag)

    if len(cells) < 8:
        cells.extend(cells[-1:] * (8 - len(cells)) if cells else [])

    costs = Costs(
        per_diem=CostGroup(foreign_currency=cells[0], us_dollar=cells[1]),
        transportation=CostGroup(foreign_currency=cells[2], us_dollar=cells[3]),
        other=CostGroup(foreign_currency=cells[4], us_dollar=cells[5]),
        total=CostGroup(foreign_currency=cells[6], us_dollar=cells[7]),
    )
    return costs, flags


def extract_rows(
    data_lines: list[tuple[int, str]],
    layout: TableLayout,
    footnote_map: dict[str, str],
) -> tuple[list[TravelerDraft], Optional[Costs], list[str]]:
    """
    Extract travelers and their segments from a table's raw data lines.

    Args:
        data_lines: (line_number, line_text) pairs for the table's data region
        layout: Column layout detected for this table
        footnote_map: Footnote number -> definition text

    Returns:
        Tuple of (travelers, committee_total or None, table-level flags)
    """
    travelers: list[TravelerDraft] = []
    travelers_by_name: dict[str, TravelerDraft] = {}
    committee_total: Optional[Costs] = None
    table_flags: list[str] = []
    current: Optional[TravelerDraft] = None
    pending_name: Optional[str] = None

    for line_no, line in data_lines:
        if not line.strip() or RULE_RE.match(line) or FOOTNOTE_DEF_RE.match(line):
            continue

        stripped = line.strip()
        if TOTAL_ROW_RE.match(stripped):
            costs, _ = _parse_cost_cells(line, layout, footnote_map)
            committee_total = costs
            continue

        search_zone = line[: layout.country.start]
        token_matches = _find_date_tokens(search_zone, placeholder_min_start=layout.arrival.start)
        costs, cost_flags = _parse_cost_cells(line, layout, footnote_map)

        if token_matches is None:
            single_token = _find_single_date_token(search_zone)
            if single_token is not None:
                # A "US departure" / "US return" leg: one of the two date
                # cells is dot-filled because that end of the trip was
                # domestic. Treat it as a partial segment so the traveler's
                # name (when present) is captured, and the following foreign
                # leg attaches to the right traveler instead of becoming an
                # orphan flagged SEGMENT_WITHOUT_TRAVELER_NAME.
                token_start = single_token.start()
                token_text = single_token.group()
                in_arrival = (
                    layout.arrival.end is not None
                    and token_start >= layout.arrival.start
                    and token_start < layout.arrival.end
                )
                if in_arrival:
                    arrival_raw = token_text
                    departure_raw = ""
                else:
                    arrival_raw = ""
                    departure_raw = token_text
                name = _strip_dash_continuation(clean_cell(search_zone[:token_start]))
                if not name and pending_name is not None and current is None:
                    name = pending_name
                pending_name = None
                country_raw = clean_cell(layout.country.slice(line))
                segment = SegmentDraft(
                    arrival_raw=arrival_raw,
                    departure_raw=departure_raw,
                    country_raw=country_raw,
                    costs=costs,
                    flags=cost_flags,
                    source_lines=[line_no],
                )
                if name and not _looks_like_traveler_row_name(name):
                    segment.flags.append("NON_PERSON_LABEL_ROW")
                if name:
                    current = _attach_named_segment(name, segment, travelers, travelers_by_name)
                elif current is not None:
                    current.segments.append(segment)
                else:
                    current = TravelerDraft(name="", segments=[segment])
                    travelers.append(current)
                    table_flags.append("SEGMENT_WITHOUT_TRAVELER_NAME")
                continue

            name = clean_cell(layout.name.slice(line))
            if current is None or not current.segments:
                if current is None and name and _looks_like_personal_name(name):
                    # Blank (dot-filled) date cells mean the source is
                    # asserting "no date here" -- but non-blank, merely
                    # unparseable text ("1/" with no day) means a real date
                    # was attempted and likely got OCR-damaged, with the
                    # intended full date on the very next row (see
                    # test_incomplete_date_row_carries_name_forward). Only
                    # the former is safe to treat as a complete record;
                    # the latter must still defer via pending_name so it
                    # doesn't steal a country/cost-less row over the real
                    # segment that follows.
                    dates_blank = not clean_cell(layout.arrival.slice(line)) and not clean_cell(
                        layout.departure.slice(line)
                    )
                    country_here = clean_cell(layout.country.slice(line))
                    if dates_blank and (country_here or costs_has_data(costs)):
                        # This name row already carries its own country
                        # and/or cost data -- a complete (if dateless)
                        # record on its own, not a bare CODEL-style
                        # introduction whose itinerary follows on later
                        # rows. `pending_name` is a single slot: if the
                        # table lists several such people in a row (e.g. a
                        # delegation where every leg is fully dot-filled,
                        # no dates ever appear), each subsequent name would
                        # silently overwrite and discard the previous one
                        # -- and if the table ends without a dated row
                        # ever arriving to consume it, even the last name
                        # is discarded, having never been flushed to a
                        # real traveler. Building the segment now avoids
                        # both failure modes.
                        segment = SegmentDraft(
                            arrival_raw="",
                            departure_raw="",
                            country_raw=country_here,
                            costs=costs,
                            flags=cost_flags,
                            source_lines=[line_no],
                        )
                        current = _attach_named_segment(
                            name, segment, travelers, travelers_by_name
                        )
                        continue
                    # A bare name with no usable date tokens can still be
                    # the first row of a traveler -- either the dates are
                    # written incompletely ("1/" with no day) or the row is
                    # a CODEL label-row that names a traveler whose
                    # itinerary follows on the subsequent rows. Carry the
                    # name forward so the next dated row attaches to it
                    # instead of becoming an orphan flagged
                    # SEGMENT_WITHOUT_TRAVELER_NAME. The
                    # _looks_like_personal_name guard rejects sub-labels
                    # like "Commercial airfare" (the second word is
                    # lowercase) and multi-line sponsor headings ("Visit to
                    # Kuwait, ...").
                    pending_name = name
                continue
            if name:
                # A labeled sub-row ("Commercial airfare", "Delegation
                # Expenses", etc.) describing an additional cost for the
                # current traveler's most recent segment, not a new
                # traveler or a country continuation -- any country text it
                # carries duplicates what's already on the individual rows.
                if costs_has_data(costs):
                    current.segments[-1].costs = merge_costs(current.segments[-1].costs, costs)
                    current.segments[-1].flags.append("COST_SUPPLEMENT_MERGED")
                    current.segments[-1].source_lines.append(line_no)
                if "MILITARY AIR" in name.upper():
                    # Some tables mark a leg as military-air-transported with
                    # its own all-dot-filled label row instead of a footnote
                    # marker inside the transportation cell.
                    current.segments[-1].costs.transportation.us_dollar.military_air = True
                    current.segments[-1].costs.transportation.foreign_currency.military_air = True
                    current.segments[-1].flags.append("MILITARY_AIR_LABEL_ROW")
                    current.segments[-1].source_lines.append(line_no)
                continue

            country_overflow = clean_cell(layout.country.slice(line))
            if country_overflow:
                current.segments[-1].country_raw = (
                    current.segments[-1].country_raw + " " + country_overflow
                ).strip()
                current.segments[-1].flags.append("CONTINUATION_MERGED")
                current.segments[-1].source_lines.append(line_no)
            elif costs_has_data(costs):
                current.segments[-1].costs = merge_costs(current.segments[-1].costs, costs)
                current.segments[-1].flags.append("COST_SUPPLEMENT_MERGED")
                current.segments[-1].source_lines.append(line_no)
            continue

        first_token, second_token = token_matches
        name = _strip_dash_continuation(clean_cell(search_zone[: first_token.start()]))
        if not name and pending_name is not None and current is None:
            # Consume a name carried forward from a prior no-dates row
            # (incomplete "1/" dates or a CODEL label-row naming the
            # traveler whose itinerary follows).
            name = pending_name
        pending_name = None
        arrival_raw = _date_token_raw(first_token)
        departure_raw = _date_token_raw(second_token)
        leftover = search_zone[second_token.end() :].strip()
        country_raw = clean_cell(layout.country.slice(line))
        if leftover:
            country_raw = " ".join(part for part in (leftover, country_raw) if part).strip()

        segment = SegmentDraft(
            arrival_raw=arrival_raw,
            departure_raw=departure_raw,
            country_raw=country_raw,
            costs=costs,
            flags=cost_flags,
            source_lines=[line_no],
        )
        if name and STAFFDEL_EXPENSE_RE.match(name):
            segment.flags.append("STAFFDEL_GROUP_EXPENSE")
        elif name and not _looks_like_traveler_row_name(name):
            segment.flags.append("NON_PERSON_LABEL_ROW")

        if name:
            current = _attach_named_segment(name, segment, travelers, travelers_by_name)
        elif current is not None:
            current.segments.append(segment)
        else:
            current = TravelerDraft(name="", segments=[segment])
            travelers.append(current)
            table_flags.append("SEGMENT_WITHOUT_TRAVELER_NAME")

    return travelers, committee_total, table_flags
