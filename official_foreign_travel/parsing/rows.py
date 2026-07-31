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

from ..utils.text import clean_cell
from .costs import CostGroup, Costs, costs_has_data, merge_costs, parse_cost_cell
from .header import _looks_like_personal_name
from .layout import TableLayout

DATE_TOKEN_RE = re.compile(r"\d{1,2}/\d{1,2}")
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


def _find_date_tokens(zone: str) -> Optional[tuple[re.Match, re.Match]]:
    """Find the first two M/D token matches within a zone, searched from column 0.

    Searching from 0 (not from the layout's arrival boundary) makes this
    robust to names that overflow their nominal column width -- a common
    failure mode where a long name pushes the actual date text to the right
    of where the layout expected it to start.
    """
    tokens = list(DATE_TOKEN_RE.finditer(zone))
    if len(tokens) < 2:
        return None
    return tokens[0], tokens[1]


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
        token_matches = _find_date_tokens(search_zone)
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
                name = clean_cell(search_zone[:token_start])
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
                if name:
                    current = TravelerDraft(name=name, segments=[segment])
                    travelers.append(current)
                elif current is not None:
                    current.segments.append(segment)
                else:
                    current = TravelerDraft(name="", segments=[segment])
                    travelers.append(current)
                    table_flags.append("SEGMENT_WITHOUT_TRAVELER_NAME")
                continue

            name = clean_cell(layout.name.slice(line))
            if current is None or not current.segments:
                # A name row with no usable date tokens can still be the
                # first row of a traveler -- either the dates are written
                # incompletely ("1/" with no day) or the row is a CODEL
                # label-row that names a traveler whose itinerary follows
                # on the subsequent rows. Carry the name forward so the
                # next dated row attaches to it instead of becoming an
                # orphan flagged SEGMENT_WITHOUT_TRAVELER_NAME. The
                # _looks_like_personal_name guard rejects sub-labels like
                # "Commercial airfare" (the second word is lowercase) and
                # multi-line sponsor headings ("Visit to Kuwait, ...").
                if current is None and name and _looks_like_personal_name(name):
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
        name = clean_cell(search_zone[: first_token.start()])
        if not name and pending_name is not None and current is None:
            # Consume a name carried forward from a prior no-dates row
            # (incomplete "1/" dates or a CODEL label-row naming the
            # traveler whose itinerary follows).
            name = pending_name
        pending_name = None
        arrival_raw = first_token.group()
        departure_raw = second_token.group()
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

        if name:
            current = TravelerDraft(name=name, segments=[segment])
            travelers.append(current)
        elif current is not None:
            current.segments.append(segment)
        else:
            current = TravelerDraft(name="", segments=[segment])
            travelers.append(current)
            table_flags.append("SEGMENT_WITHOUT_TRAVELER_NAME")

    return travelers, committee_total, table_flags
