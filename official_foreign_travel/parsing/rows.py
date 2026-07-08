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
from typing import Dict, List, Optional, Tuple

from ..utils.text import clean_cell
from .costs import CostGroup, Costs, costs_has_data, merge_costs, parse_cost_cell
from .layout import TableLayout

DATE_TOKEN_RE = re.compile(r"\d{1,2}/\d{1,2}")
RULE_RE = re.compile(r"^\s*-{10,}")
TOTAL_ROW_RE = re.compile(r"^\s*(committee\s+|grand\s+)?total\b", re.IGNORECASE)


@dataclass
class SegmentDraft:
    """One arrival/departure/country/cost row, before date resolution."""

    arrival_raw: str
    departure_raw: str
    country_raw: str
    costs: Costs
    flags: List[str] = field(default_factory=list)
    source_lines: List[int] = field(default_factory=list)


@dataclass
class TravelerDraft:
    """A named traveler and their travel segments within one table."""

    name: str
    segments: List[SegmentDraft] = field(default_factory=list)


def _find_date_tokens(zone: str) -> Optional[Tuple[re.Match, re.Match]]:
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


def _parse_cost_cells(
    line: str, layout: TableLayout, footnote_map: Dict[str, str]
) -> Tuple[Costs, List[str]]:
    flags: List[str] = []
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
    data_lines: List[Tuple[int, str]],
    layout: TableLayout,
    footnote_map: Dict[str, str],
) -> Tuple[List[TravelerDraft], Optional[Costs], List[str]]:
    """
    Extract travelers and their segments from a table's raw data lines.

    Args:
        data_lines: (line_number, line_text) pairs for the table's data region
        layout: Column layout detected for this table
        footnote_map: Footnote number -> definition text

    Returns:
        Tuple of (travelers, committee_total or None, table-level flags)
    """
    travelers: List[TravelerDraft] = []
    committee_total: Optional[Costs] = None
    table_flags: List[str] = []
    current: Optional[TravelerDraft] = None

    for line_no, line in data_lines:
        if not line.strip() or RULE_RE.match(line):
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
            name = clean_cell(layout.name.slice(line))
            if current is None or not current.segments:
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
