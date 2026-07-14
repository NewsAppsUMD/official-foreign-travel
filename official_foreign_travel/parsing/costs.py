"""Parse individual cost cells and footnote references."""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

FOOTNOTE_DEF_RE = re.compile(r"^\\(\d+)\\\s*(.*)$")
FOOTNOTE_MARKER_RE = re.compile(r"\\(\d+)\\")
# A cell that is ONLY a footnote reference, e.g. "(\3\)" or (after HTML-tag
# stripping upstream) the bare "(3)" -- backslashes around the digit are optional.
WHOLE_CELL_FOOTNOTE_RE = re.compile(r"^\(\s*\\?(\d+)\\?\s*\)$")
DOTFILL_RE = re.compile(r"^\.{2,}$")
DASHFILL_RE = re.compile(r"^-{2,}$")
# Leading currency code (FF, DM, SEK, L, HK, LE, D, etc.) before a digit.
CURRENCY_PREFIX_RE = re.compile(r"^[A-Z]{1,3}(?=[\d,])")
MILITARY_AIR_RE = re.compile(r"military\s+air", re.IGNORECASE)


@dataclass
class CostCell:
    """A single dollar-amount cell, possibly empty or footnote-only."""

    amount: Optional[Decimal]
    raw: str
    footnotes: list[str] = field(default_factory=list)
    military_air: bool = False


@dataclass
class CostGroup:
    """Foreign-currency / US-dollar-equivalent pair for one cost category."""

    foreign_currency: CostCell
    us_dollar: CostCell


@dataclass
class Costs:
    """The four cost categories reported for a travel segment or table total."""

    per_diem: CostGroup
    transportation: CostGroup
    other: CostGroup
    total: CostGroup


def parse_footnote_map(footnote_lines: list[str]) -> dict[str, str]:
    """
    Parse footnote definition lines (e.g. "\\1\\ Per diem constitutes lodging and meals.").

    Args:
        footnote_lines: Candidate lines from a table's footer

    Returns:
        Dict mapping footnote number (as string) to its definition text
    """
    footnotes = {}
    for line in footnote_lines:
        match = FOOTNOTE_DEF_RE.match(line.strip())
        if match:
            footnotes[match.group(1)] = match.group(2).strip()
    return footnotes


def parse_cost_cell(
    raw: str, footnote_map: Optional[dict[str, str]] = None
) -> tuple[CostCell, Optional[str]]:
    """
    Parse a single fixed-width cost cell.

    Handles dot-filled empty cells, footnote markers (both "\\3\\138.00" and
    bare "(3)" forms), thousands separators, and unparseable residue.

    Args:
        raw: Raw cell text (not yet stripped)
        footnote_map: Footnote number -> definition text, for military-air detection

    Returns:
        Tuple of (CostCell, flag or None). Flag is set only for genuinely
        unparseable non-empty text, never for a legitimately empty cell.
    """
    footnote_map = footnote_map or {}
    text = raw.strip()
    footnotes: list[str] = []

    whole_cell_match = WHOLE_CELL_FOOTNOTE_RE.match(text)
    if whole_cell_match:
        footnotes.append(whole_cell_match.group(1))
        military_air = any(MILITARY_AIR_RE.search(footnote_map.get(fn, "")) for fn in footnotes)
        return CostCell(amount=None, raw=raw, footnotes=footnotes, military_air=military_air), None

    def _collect_marker(match: "re.Match[str]") -> str:
        footnotes.append(match.group(1))
        return ""

    stripped = FOOTNOTE_MARKER_RE.sub(_collect_marker, text).strip()

    military_air = any(MILITARY_AIR_RE.search(footnote_map.get(fn, "")) for fn in footnotes)

    if stripped == "" or DOTFILL_RE.match(stripped) or DASHFILL_RE.match(stripped):
        return CostCell(amount=None, raw=raw, footnotes=footnotes, military_air=military_air), None

    # Strip a leading currency code (FF4,733.91 → 4,733.91) or dollar sign
    # ($315.00 → 315.00). The prefix is preserved in `raw` for reference.
    currency_match = CURRENCY_PREFIX_RE.match(stripped)
    if currency_match:
        stripped = stripped[currency_match.end():]
    elif stripped.startswith("$"):
        stripped = stripped[1:].strip()

    # Strip trailing dots that are fixed-width padding residue, not part of
    # the value (e.g. "462.00  .." → "462.00"). The dots follow the amount
    # because the cell wasn't fully filled by the right-justified number.
    stripped = re.sub(r"\s*\.+$", "", stripped).strip()
    if not stripped or DOTFILL_RE.match(stripped):
        return CostCell(amount=None, raw=raw, footnotes=footnotes, military_air=military_air), None

    # European thousands convention: 5.723.37 → 5723.37 (periods as thousands
    # separators, last period is the decimal point). Only applies when there
    # are 2+ periods and the last group is exactly 2 digits (the decimal part).
    if stripped.count(".") >= 2:
        head, tail = stripped.rsplit(".", 1)
        if len(tail) == 2:
            stripped = head.replace(".", "") + "." + tail

    numeric_text = stripped.replace(",", "")
    try:
        amount = Decimal(numeric_text)
    except InvalidOperation:
        return (
            CostCell(amount=None, raw=raw, footnotes=footnotes, military_air=military_air),
            "UNPARSEABLE_COST_CELL",
        )

    return CostCell(amount=amount, raw=raw, footnotes=footnotes, military_air=military_air), None


def merge_cost_cell(a: CostCell, b: CostCell) -> CostCell:
    """Combine two cost cells for the same subcolumn (e.g. a row plus a supplemental cost line)."""
    if a.amount is None and b.amount is None:
        amount = None
    else:
        amount = (a.amount or Decimal("0")) + (b.amount or Decimal("0"))
    return CostCell(
        amount=amount,
        raw=" + ".join(r for r in (a.raw.strip(), b.raw.strip()) if r),
        footnotes=sorted(set(a.footnotes) | set(b.footnotes)),
        military_air=a.military_air or b.military_air,
    )


def merge_costs(a: Costs, b: Costs) -> Costs:
    """Combine two Costs objects category-by-category and currency-by-currency."""
    return Costs(
        per_diem=CostGroup(
            foreign_currency=merge_cost_cell(
                a.per_diem.foreign_currency, b.per_diem.foreign_currency
            ),
            us_dollar=merge_cost_cell(a.per_diem.us_dollar, b.per_diem.us_dollar),
        ),
        transportation=CostGroup(
            foreign_currency=merge_cost_cell(
                a.transportation.foreign_currency, b.transportation.foreign_currency
            ),
            us_dollar=merge_cost_cell(a.transportation.us_dollar, b.transportation.us_dollar),
        ),
        other=CostGroup(
            foreign_currency=merge_cost_cell(a.other.foreign_currency, b.other.foreign_currency),
            us_dollar=merge_cost_cell(a.other.us_dollar, b.other.us_dollar),
        ),
        total=CostGroup(
            foreign_currency=merge_cost_cell(a.total.foreign_currency, b.total.foreign_currency),
            us_dollar=merge_cost_cell(a.total.us_dollar, b.total.us_dollar),
        ),
    )


def costs_has_data(costs: Costs) -> bool:
    """Whether any cell in a Costs object has a non-None amount."""
    cells = (
        costs.per_diem.foreign_currency,
        costs.per_diem.us_dollar,
        costs.transportation.foreign_currency,
        costs.transportation.us_dollar,
        costs.other.foreign_currency,
        costs.other.us_dollar,
        costs.total.foreign_currency,
        costs.total.us_dollar,
    )
    return any(cell.amount is not None for cell in cells)
