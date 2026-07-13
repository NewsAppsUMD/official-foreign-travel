"""Detect per-table column boundaries instead of relying on hardcoded offsets.

Column positions shift across eras (14+ distinct layouts in the corpus), so a
single hardcoded set of slice offsets silently misaligns data for many files.
This module finds the boundaries per table from the column-header block, then
cross-checks each boundary against the actual data rows (which are more
reliable than label text, which is often visually centered rather than
left-aligned to its column).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

# Label text has occasional OCR-style typos in the source ("Hame of Member",
# "Arrive"/"Depart" instead of "Arrival"/"Departure"), so these match on a
# distinctive substring/prefix rather than the exact full word.
NAME_LABEL_RE = re.compile(r"(?:Name|Hame)\s+of\s+(?:Member|Employee)", re.IGNORECASE)
ARRIVAL_LABEL_RE = re.compile(r"\bArriv\w*", re.IGNORECASE)
DEPARTURE_LABEL_RE = re.compile(r"\bDepart\w*", re.IGNORECASE)
COUNTRY_LABEL_RE = re.compile(r"\bCountry\b", re.IGNORECASE)
FOREIGN_LABEL_RE = re.compile(r"\bForeign\b", re.IGNORECASE)
EQUIVALENT_LABEL_RE = re.compile(r"\bequivalent\b", re.IGNORECASE)
RULE_RE = re.compile(r"^-{10,}")

REFINE_WINDOW = 20
NUM_COST_COLUMNS = 8


@dataclass(frozen=True)
class ColumnSpan:
    """A half-open character range [start, end) within a fixed-width line."""

    start: int
    end: Optional[int]  # None means "to end of line"

    def slice(self, line: str) -> str:
        return line[self.start : self.end] if self.end is not None else line[self.start :]


@dataclass
class TableLayout:
    """Column boundaries for one table, plus a confidence score."""

    name: ColumnSpan
    arrival: ColumnSpan
    departure: ColumnSpan
    country: ColumnSpan
    cost_columns: tuple[
        ColumnSpan, ...
    ]  # 8: pd_fc, pd_usd, tr_fc, tr_usd, ot_fc, ot_usd, tot_fc, tot_usd
    confidence: float
    fingerprint: tuple[int, ...]


def _find_header_window(lines: list[str]) -> Optional[list[str]]:
    """Return the lines from the 'Name of Member' label through the rule that follows it."""
    start = None
    for i, line in enumerate(lines):
        if NAME_LABEL_RE.search(line):
            start = i
            break
    if start is None:
        return None

    window = []
    for line in lines[start : start + 8]:
        window.append(line)
        if len(window) > 1 and RULE_RE.match(line.strip()):
            break
    return window


def _label_positions(window: list[str]) -> Optional[dict]:
    """Find raw label column positions within the header window."""
    name_pos = arrival_pos = departure_pos = country_pos = None
    foreign_positions: list[int] = []
    equivalent_positions: list[int] = []

    for line in window:
        name_match = NAME_LABEL_RE.search(line)
        if name_match and name_pos is None:
            name_pos = name_match.start()
            country_match = COUNTRY_LABEL_RE.search(line, name_match.end())
            if country_match:
                country_pos = country_match.start()

        if country_pos is None:
            country_match = COUNTRY_LABEL_RE.search(line)
            if country_match:
                country_pos = country_match.start()

        arrival_match = ARRIVAL_LABEL_RE.search(line)
        if arrival_match and arrival_pos is None:
            arrival_pos = arrival_match.start()
            departure_match = DEPARTURE_LABEL_RE.search(line, arrival_match.end())
            if departure_match:
                departure_pos = departure_match.start()

        foreign_positions.extend(m.start() for m in FOREIGN_LABEL_RE.finditer(line))
        equivalent_positions.extend(m.start() for m in EQUIVALENT_LABEL_RE.finditer(line))

    if name_pos is None or arrival_pos is None or departure_pos is None or country_pos is None:
        return None

    cost_positions = sorted(set(foreign_positions) | set(equivalent_positions))

    return {
        "name": name_pos,
        "arrival": arrival_pos,
        "departure": departure_pos,
        "country": country_pos,
        "cost_positions": cost_positions,
    }


def _cuts_token(line: str, col: int) -> bool:
    """Whether slicing at `col` would split a token in this row in two."""
    if col <= 0 or col >= len(line):
        return False
    return line[col] != " " and line[col - 1] != " "


def _refine_boundary(guess: int, data_lines: Sequence[str]) -> tuple[int, bool]:
    """
    Snap a label-derived boundary guess to the nearest position that cuts
    through no data row's token.

    Right-justified numeric columns make "where tokens start" the wrong
    criterion: starts shift with digit count, so a majority-vote position
    truncates the wider values, and when no position wins a majority the
    search used to wander onto a neighboring column's boundary entirely.
    Because empty cells are dot-filled to full width, the only positions
    that split nothing in any row are the true inter-column gutters --
    slicing anywhere inside a gutter is correct (leading whitespace is
    stripped downstream, and the no-cut guarantee means the previous
    column's content always ends before the boundary).

    A strict zero-cuts pass runs first; if nothing within the window
    qualifies (e.g. a rare over-wide value bleeds through every gutter), a
    second pass tolerates cuts in up to 10% of rows rather than giving up.
    """
    if not data_lines:
        return guess, False

    for max_cuts in (0, len(data_lines) // 10):
        for offset in range(0, REFINE_WINDOW + 1):
            candidates = [guess - offset, guess + offset] if offset else [guess]
            for candidate in candidates:
                if candidate < 1:
                    continue
                cuts = sum(1 for line in data_lines if _cuts_token(line, candidate))
                if cuts <= max_cuts:
                    return candidate, True

    return guess, False


def detect_layout(block_lines: list[str], data_lines: Sequence[str]) -> Optional[TableLayout]:
    """
    Detect column boundaries for a table.

    Args:
        block_lines: All raw lines of the table block (title through footer)
        data_lines: Raw candidate data rows for this table, used to refine boundaries

    Returns:
        TableLayout, or None if the header block couldn't be located at all
    """
    window = _find_header_window(block_lines)
    if window is None:
        return None

    labels = _label_positions(window)
    if labels is None:
        return None

    cost_positions = labels["cost_positions"]
    expected_cost_count = len(cost_positions) == NUM_COST_COLUMNS

    boundaries = [
        ("name", 0),  # names are left-justified at column 0 despite the indented label
        ("arrival", labels["arrival"]),
        ("departure", labels["departure"]),
        ("country", labels["country"]),
    ] + [(f"cost_{i}", pos) for i, pos in enumerate(cost_positions)]

    refined = []
    matched = 0
    for name, guess in boundaries:
        if name == "name":
            refined.append((name, 0))
            continue
        pos, ok = _refine_boundary(guess, data_lines)
        if ok:
            matched += 1
        refined.append((name, pos))

    refined.sort(key=lambda item: item[1])
    positions = [pos for _, pos in refined]
    names = [name for name, _ in refined]

    spans = {}
    for i, name in enumerate(names):
        end = positions[i + 1] if i + 1 < len(positions) else None
        spans[name] = ColumnSpan(start=positions[i], end=end)

    cost_spans = tuple(spans[f"cost_{i}"] for i in range(len(cost_positions)))

    refinable = len(boundaries) - 1  # exclude "name", which is never refined against data
    confidence = 0.5
    if expected_cost_count:
        confidence += 0.3
    if refinable:
        confidence += 0.2 * (matched / refinable)

    collided = len(set(positions)) < len(positions)
    if collided:
        # Two boundaries on the same column means a zero-width column and a
        # doubled neighbor -- extraction from this layout is not trustworthy,
        # so force it under the review/LLM-fallback threshold.
        confidence = min(confidence, 0.5)

    fingerprint = tuple(positions)

    return TableLayout(
        name=spans["name"],
        arrival=spans["arrival"],
        departure=spans["departure"],
        country=spans["country"],
        cost_columns=cost_spans,
        confidence=round(min(confidence, 1.0), 3),
        fingerprint=fingerprint,
    )
