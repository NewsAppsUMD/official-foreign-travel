"""Parse individual cost cells and footnote references."""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

FOOTNOTE_DEF_RE = re.compile(r"^\\(\d+)\\\s*(.*)$")
FOOTNOTE_MARKER_RE = re.compile(r"\\(\d+)\\")
# Symbolic footnote marker with backslashes, e.g. "\*\18,340.2" -- a "*"
# wrapped in backslashes before an amount. Source uses this for classified
# travel omissions (title 22 USC 1754(b)(2)).
SYMBOLIC_FOOTNOTE_MARKER_RE = re.compile(r"^\\\*\\(?=\d)")
# A cell that is ONLY a footnote reference, e.g. "(\3\)" or (after HTML-tag
# stripping upstream) the bare "(3)" -- backslashes around the digit are optional.
WHOLE_CELL_FOOTNOTE_RE = re.compile(r"^\(\s*\\?(\d+)\\?\s*\)$")
# A cell that is ONLY a symbolic footnote marker: "*", "**", "***", "(*)".
# Source-defined: "*" = Delegation costs, "**" = Cancelled mission.
SYMBOLIC_FOOTNOTE_RE = re.compile(r"^(?P<marker>\*{1,3}|\(\*+\))$")
# Bare "(3)" footnote form (no backslashes), optionally followed by more text
# (e.g. "(3) 620.00"). Used when HTML stripping already removed the backslashes.
BARE_PAREN_FOOTNOTE_RE = re.compile(r"^\(\s*(\d+)\s*\)")
DOTFILL_RE = re.compile(r"^\.{2,}$")
DASHFILL_RE = re.compile(r"^-{2,}$")
# Lenient dot-fill: cells that are only dots, whitespace, supplement-merge
# '+' separators, and trailing footnote-marker residue (backslash, asterisk).
# These are empty cells with layout residue (e.g. "...........  \\", a dot-fill
# cell with a trailing backslash from a footnote marker that lost its digit;
# or "........... + ...........", a merged cell whose constituents were all
# dot-fill). Requires at least 2 dots so a single "." (a decimal point) is
# not misclassified.
DOTFILL_LENIENT_RE = re.compile(r"^[\s.+\*\\]*\.{2,}[\s.+\*\\]*$")
# Leading currency code (FF, DM, SEK, L, HK, LE, D, etc.) before a digit.
CURRENCY_PREFIX_RE = re.compile(r"^[A-Z]{1,3}(?=[\d,])")
# Longer currency names that prefix an amount, e.g. "Euro237.80".
LONG_CURRENCY_PREFIX_RE = re.compile(
    r"^(?:Euro|RMB|CFA|Franc|Pound|Dinar|Krone|Krona|Lira|Yen|Won|Baht|Rand|"
    r"Rupee|Shekel|Ruble|Hryvnia|Lari|Manat|Kuna|Koruna|Forint|Litas|Leu|Lev|"
    r"Zloty|Som|Tenge|Dong|Naira|Cedis|Shilling)(?=[\d,])",
    re.IGNORECASE,
)
# A cell whose entire (stripped) content is a known currency name or a small
# set of currency-name phrases, e.g. "Euro", "Zloty", "Irish pound",
# "English". When only the U.S. dollar equivalent is reported, the
# foreign-currency cell carries just the currency label -- that is a
# labeling convention, not a parse error or a value to recover.
BARE_CURRENCY_NAME_RE = re.compile(
    r"^(?:"
    r"euros?|zlotys?|pounds?|francs?|dinars?|kron(?:e|er|a|or)|liras?|lire|yen|won|"
    r"baht|rand|rupees?|shekels?|rubles?|roubles?|hryvnias?|lar(?:i|is)|manats?|"
    r"kunas?|korunas?|forints?|litas|leu|lei|leva?|soms?|tenge|dong|nairas?|cedis?|"
    r"shillings?|dollar|dollars|cfa|fcfa|rmb|english|irish\s+pound|english\s+pound|"
    r"lek|denar|marka|mark|dram|somoni|togrog|birr|kyat|riels?|kip|guaranis?|"
    r"colons?|cordobas?|lempias?|quetzales?|bolivares?|sucres?|sol|soles|pesos?|"
    # OCR-typo variants observed in the corpus.
    r"zolty|rubble|"
    # 3-letter ISO-style currency codes that appear bare in the
    # foreign-currency column as a labeling convention.
    r"dkk|etb|kes|sek|nok|isk|czk|huf|pln|ron|bgn|hrk|rsd"
    r")$",
    re.IGNORECASE,
)
# Trailing currency code/name after an amount, e.g. "191,590 CFA", "722.55 euro".
TRAILING_CURRENCY_RE = re.compile(r"(.*[\d,])\s*[A-Za-z]{2,4}$")
MILITARY_AIR_RE = re.compile(r"military\s+air", re.IGNORECASE)
# "Milair" is a source shorthand for military air, used as a standalone cell marker.
MILAIR_TEXT_RE = re.compile(r"^milair$", re.IGNORECASE)
# Standalone "Military" / "Military air" label in a transportation cell,
# typically paired with a "(3)" footnote marker on the same row. Marks the
# leg as military-air-transported with no commercial cost.
MILITARY_LABEL_RE = re.compile(r"^(?:military(?:\s+air)?|litary\s+air\s+t?)$", re.IGNORECASE)
# Explicit-empty markers: N/A, n/a, NA, None, -0-.
EMPTY_MARKER_RE = re.compile(r"^(?:N/?A|None|-0-)$", re.IGNORECASE)
# Leading symbolic asterisk marker before an amount, e.g. "* 2,443.46",
# "** 1,001.67", "**** 1,606.10" (4-asterisk variant of the cancelled-mission
# "**" marker).
LEADING_ASTERISK_RE = re.compile(r"^(?P<marker>\*{1,4})\s?")
# Leading single-digit footnote marker without backslashes, e.g. "4 6,912.00".
LEADING_DIGIT_FOOTNOTE_RE = re.compile(r"^(\d)\s+(?=\d)")
# Leading single-digit footnote marker with a trailing backslash (incomplete
# "\d\" marker), e.g. "3\ -700.00" -> footnote 3, amount -700.00.
LEADING_DIGIT_BACKSLASH_FOOTNOTE_RE = re.compile(r"^(\d)\\\s+(?=[\d-])")
# Residue "1A" that appears after a footnote marker in the 2015q3sep08 report,
# e.g. "\4\1A184.00" -> footnote 4, amount 184.00. The "1A" is a layout
# extraction artifact where two characters leaked into the cost cell.
RESIDUE_1A_RE = re.compile(r"^1A(?=\d)")
# Slash instead of period in decimal, e.g. "1,484/00" -> "1,484.00".
SLASH_DECIMAL_RE = re.compile(r"(\d)/(\d{2})$")
# Stray space inside decimal, e.g. "27,368. 74" -> "27,368.74".
SPACE_DECIMAL_RE = re.compile(r"(\d)\.\s+(\d{2})$")
# Parenthesized amount, e.g. "(7.48)" -> -7.48 (accounting-style negative).
PAREN_AMOUNT_RE = re.compile(r"^\(([\d,]+\.\d{1,2})\)$")
# Trailing symbolic asterisk marker after an amount, e.g. "12,597.90*".
TRAILING_ASTERISK_RE = re.compile(r"^(?P<amount>.*\d)\s*(?P<marker>\*{1,3})$")
# Lowercase-o decimal typo, e.g. "394.oo" -> "394.00".
LOWERCASE_O_DECIMAL_RE = re.compile(r"(\d)\.oo$")
# Trailing brace typo, e.g. "5,133.00}" -> "5,133.00".
TRAILING_BRACE_RE = re.compile(r"(.*\d)\}$")
# Trailing bracket typo, e.g. "41.00]" -> "41.00".
TRAILING_BRACKET_RE = re.compile(r"(.*\d)\]$")
# Trailing minus sign, e.g. "1,060.00-" -> -1060.00 (accounting-style negative
# written with a trailing dash instead of parens).
TRAILING_MINUS_RE = re.compile(r"^(?P<amount>[\d,]+\.\d{1,2})-$")
# Leading exclamation typo, e.g. "!1,288.28" -> "1,288.28".
LEADING_BANG_RE = re.compile(r"^!(?=\d)")
# Leading equals sign (formula-style prefix), e.g. "=129.00" -> "129.00".
LEADING_EQUALS_RE = re.compile(r"^=(?=\d)")
# Leading dots/whitespace before content, e.g. "..       287." -> "287." or
# "...........  DKK" -> "DKK". Requires a letter-or-digit lookahead so an
# all-dots cell (which DOTFILL_RE handles earlier) is not affected.
LEADING_DOTS_RE = re.compile(r"^[\s.]+(?=[A-Za-z\d])")


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

    # Whole-cell symbolic footnote marker ("*", "**", "***", "(*)").
    # Source-defined meanings: "*" = Delegation costs, "**" = Cancelled mission.
    symbolic_match = SYMBOLIC_FOOTNOTE_RE.match(text)
    if symbolic_match:
        marker = symbolic_match.group("marker").strip("()")
        footnotes.append(marker)
        return CostCell(amount=None, raw=raw, footnotes=footnotes, military_air=False), None

    # Explicit-empty markers: N/A, n/a, NA, None, -0-. Not a value, not a flag.
    if EMPTY_MARKER_RE.match(text) or text == "-":
        return CostCell(amount=None, raw=raw, footnotes=[], military_air=False), None

    def _collect_marker(match: "re.Match[str]") -> str:
        footnotes.append(match.group(1))
        return ""

    # Symbolic footnote marker "\*\N" -- star between backslashes, used as
    # a footnote for classified travel omissions. Strip and record as "*".
    sym_match = SYMBOLIC_FOOTNOTE_MARKER_RE.match(text)
    if sym_match:
        footnotes.append("*")
        stripped = text[sym_match.end():].strip()
    else:
        stripped = FOOTNOTE_MARKER_RE.sub(_collect_marker, text).strip()

    # After stripping "\\d+\\", residual parens that wrapped the marker remain,
    # e.g. "(\\3\\) 496.1" -> "() 496.1". Drop the empty parens.
    if footnotes and stripped.startswith("()"):
        stripped = stripped[2:].strip()

    # Bare "(3) 620.00" form (no backslashes at all): treat "(3)" as a footnote.
    if not footnotes:
        bare_paren_match = BARE_PAREN_FOOTNOTE_RE.match(stripped)
        if bare_paren_match:
            footnotes.append(bare_paren_match.group(1))
            stripped = stripped[bare_paren_match.end():].strip()

    # Strip "1A" residue that follows a footnote marker, e.g. "\4\1A184.00"
    # -> "184.00". Only applies when a footnote was already collected, so a
    # legitimate cell starting with "1A" (none observed, but defensive) is
    # untouched.
    if footnotes:
        stripped = RESIDUE_1A_RE.sub("", stripped).strip()

    military_air = any(MILITARY_AIR_RE.search(footnote_map.get(fn, "")) for fn in footnotes)

    if stripped == "" or DOTFILL_RE.match(stripped) or DASHFILL_RE.match(stripped):
        return CostCell(amount=None, raw=raw, footnotes=footnotes, military_air=military_air), None

    # Lenient dot-fill: cells that are dots + whitespace + trailing footnote
    # residue (backslash, asterisk) + supplement-merge '+' chains. A trailing
    # asterisk is a symbolic footnote marker ("*" = Delegation costs, etc.) --
    # record it before returning the empty cell. A trailing backslash is residue
    # from a footnote marker that lost its digit; we can't recover the number,
    # so we drop it.
    if DOTFILL_LENIENT_RE.match(stripped):
        if "*" in stripped:
            footnotes.append("*")
        return CostCell(amount=None, raw=raw, footnotes=footnotes, military_air=military_air), None

    # "Milair" as a standalone marker means military air transport, no cost.
    if MILAIR_TEXT_RE.match(stripped):
        return CostCell(amount=None, raw=raw, footnotes=footnotes, military_air=True), None

    # Standalone "Military" / "Military air" label in a transportation cell,
    # paired with a "(3)" footnote on the same row. Marks military-air
    # transport with no commercial cost.
    if MILITARY_LABEL_RE.match(stripped):
        return CostCell(amount=None, raw=raw, footnotes=footnotes, military_air=True), None

    # A cell whose entire content is a bare currency name is the source's
    # labeling convention for "this leg was paid in <currency>" -- no amount
    # to parse, not a column-misalignment artifact.
    if BARE_CURRENCY_NAME_RE.match(stripped):
        return CostCell(amount=None, raw=raw, footnotes=footnotes, military_air=military_air), None

    # Leading symbolic asterisk marker before an amount, e.g. "* 2,443.46".
    # Some cells carry multiple space-separated asterisk markers, e.g.
    # "* * * 234.22" (2005q3jul26-013 Freeman Thailand: each asterisk is a
    # separate footnote reference, the amount is 234.22). Strip iteratively
    # so each marker is recorded separately.
    while True:
        asterisk_match = LEADING_ASTERISK_RE.match(stripped)
        if not asterisk_match:
            break
        footnotes.append(asterisk_match.group("marker"))
        stripped = stripped[asterisk_match.end():].strip()

    # Leading single-digit footnote marker without backslashes, e.g. "4 6,912.00".
    # Only try this when no other footnote has been collected yet.
    if not footnotes:
        leading_digit_match = LEADING_DIGIT_FOOTNOTE_RE.match(stripped)
        if leading_digit_match:
            footnotes.append(leading_digit_match.group(1))
            stripped = stripped[leading_digit_match.end():].strip()

    # Leading single-digit footnote marker with a trailing backslash (incomplete
    # "\d\" marker), e.g. "3\ -700.00" -> footnote 3, amount -700.00.
    if not footnotes:
        leading_digit_bs_match = LEADING_DIGIT_BACKSLASH_FOOTNOTE_RE.match(stripped)
        if leading_digit_bs_match:
            footnotes.append(leading_digit_bs_match.group(1))
            stripped = stripped[leading_digit_bs_match.end():].strip()

    # Strip a leading currency code (FF4,733.91 → 4,733.91), a longer currency
    # name (Euro237.80 → 237.80), or a dollar sign ($315.00 → 315.00).
    currency_match = LONG_CURRENCY_PREFIX_RE.match(stripped) or CURRENCY_PREFIX_RE.match(stripped)
    if currency_match:
        stripped = stripped[currency_match.end():]
    elif stripped.startswith("$"):
        stripped = stripped[1:].strip()

    # Strip a trailing currency code/name (191,590 CFA → 191,590; 722.55 euro → 722.55).
    trailing_match = TRAILING_CURRENCY_RE.match(stripped)
    if trailing_match:
        stripped = trailing_match.group(1).strip()

    # Normalize source typos in the decimal part: slash and stray space.
    stripped = SLASH_DECIMAL_RE.sub(r"\1.\2", stripped)
    stripped = SPACE_DECIMAL_RE.sub(r"\1.\2", stripped)
    stripped = LOWERCASE_O_DECIMAL_RE.sub(r"\1.00", stripped)
    brace_match = TRAILING_BRACE_RE.match(stripped)
    if brace_match:
        stripped = brace_match.group(1)
    bracket_match = TRAILING_BRACKET_RE.match(stripped)
    if bracket_match:
        stripped = bracket_match.group(1)
    # Trailing dash as a negative-amount marker, e.g. "1,060.00-" -> -1,060.00.
    trailing_minus_match = TRAILING_MINUS_RE.match(stripped)
    if trailing_minus_match:
        stripped = "-" + trailing_minus_match.group("amount")
    # Leading exclamation typo before a digit, e.g. "!1,288.28" -> "1,288.28".
    stripped = LEADING_BANG_RE.sub("", stripped)
    # Leading equals sign (formula-style prefix), e.g. "=129.00" -> "129.00".
    stripped = LEADING_EQUALS_RE.sub("", stripped)
    # Leading dots/whitespace before a digit, e.g. "..       287." -> "287.".
    stripped = LEADING_DOTS_RE.sub("", stripped)

    # Strip trailing dots that are fixed-width padding residue, not part of
    # the value (e.g. "462.00  .." → "462.00", or "732.00*  .." → "732.00*"
    # so the trailing-asterisk check below can see the marker). The dots follow
    # the amount because the cell wasn't fully filled by the right-justified
    # number.
    stripped = re.sub(r"\s*\.+$", "", stripped).strip()
    if not stripped or DOTFILL_RE.match(stripped):
        return CostCell(amount=None, raw=raw, footnotes=footnotes, military_air=military_air), None

    # Trailing symbolic asterisk marker after an amount, e.g. "12,597.90*".
    # Runs after the trailing-dots strip so a cell like "732.00*  .." is
    # reduced to "732.00*" first, then the asterisk is extracted here.
    trailing_asterisk_match = TRAILING_ASTERISK_RE.match(stripped)
    if trailing_asterisk_match:
        footnotes.append(trailing_asterisk_match.group("marker"))
        stripped = trailing_asterisk_match.group("amount").strip()

    # Re-check for a bare currency name after all the strip/normalize steps.
    # Catches cells like "...........  DKK" where leading-dot residue was
    # stripped to reveal a bare currency code (a labeling convention, not a
    # value).
    if BARE_CURRENCY_NAME_RE.match(stripped):
        return CostCell(amount=None, raw=raw, footnotes=footnotes, military_air=military_air), None

    # European thousands convention: 5.723.37 → 5723.37 (periods as thousands
    # separators, last period is the decimal point). Only applies when there
    # are 2+ periods and the last group is exactly 2 digits (the decimal part).
    if stripped.count(".") >= 2:
        head, tail = stripped.rsplit(".", 1)
        if len(tail) == 2:
            stripped = head.replace(".", "") + "." + tail

    # Parenthesized amount is accounting-style negative, e.g. "(7.48)" → -7.48.
    is_negative = False
    paren_amount_match = PAREN_AMOUNT_RE.match(stripped)
    if paren_amount_match:
        is_negative = True
        stripped = paren_amount_match.group(1)

    numeric_text = stripped.replace(",", "")
    if is_negative:
        numeric_text = "-" + numeric_text
    try:
        amount = Decimal(numeric_text)
    except InvalidOperation:
        return (
            CostCell(amount=None, raw=raw, footnotes=footnotes, military_air=military_air),
            "UNPARSEABLE_COST_CELL",
        )

    return CostCell(amount=amount, raw=raw, footnotes=footnotes, military_air=military_air), None


def _decimal_part_len(raw: str) -> int:
    """Number of digits after the last '.' in raw, or -1 if no '.' or non-digit decimal.

    Returns 0 when raw ends with '.' (period, no decimal digits) -- this is
    the source convention for a wrapped 2-digit decimal amount whose integer
    part ended with a period on the prior line.
    """
    idx = raw.rfind(".")
    if idx == -1:
        return -1
    decimal = raw[idx + 1 :].strip()
    if decimal == "":
        return 0
    if decimal.isdigit():
        return len(decimal)
    return -1


def _wrap_digit_len(b_raw: str) -> Optional[int]:
    """Number of decimal digits represented by b_raw when it's a wrapped
    decimal fragment (1-2 digits, no decimal point, no other chars), else None.
    """
    b = b_raw.strip()
    if re.match(r"^\d$", b):
        return 1
    if re.match(r"^\d{2}$", b):
        return 2
    return None


def _is_wrap_digit(a_raw: str, b_raw: str) -> bool:
    """True when b_raw is a wrapped decimal fragment of a_raw.

    Source line breaks sometimes split an amount's decimal part onto the
    next line, in the same cost column. The prior cell carries the integer
    part (with 0 or 1 decimal digits), and the wrapped cell carries the
    remaining decimal digit(s):
    - 1-digit wrap: a ends with `.N` (1 decimal digit), b is 1 digit -- the
      new 2nd decimal digit. E.g. `* * * 234.2` + `2` -> `234.22`.
    - 2-digit wrap: a ends with `.` (period, no decimal), b is 2 digits --
      the new 2-digit decimal. E.g. `\\3\\ 12,785.` + `48` -> `12,785.48`.
    """
    a_dec = _decimal_part_len(a_raw)
    b_dec = _wrap_digit_len(b_raw)
    if b_dec is None:
        return False
    return (a_dec == 1 and b_dec == 1) or (a_dec == 0 and b_dec == 2)


def merge_cost_cell(a: CostCell, b: CostCell) -> CostCell:
    """Combine two cost cells for the same subcolumn (e.g. a row plus a supplemental cost line)."""
    if (
        a.amount is not None
        and b.amount is not None
        and _is_wrap_digit(a.raw, b.raw)
    ):
        # Source line break split the decimal part onto the next line in the
        # same cost column. Concatenate b as the next decimal digit(s) of a,
        # not add as a separate dollar amount. E.g. `12,785.` + `48` ->
        # `12,785.48` (not `12,833.00`); `234.2` + `2` -> `234.22` (not `236.20`).
        amount = a.amount + b.amount / Decimal("100")
        return CostCell(
            amount=amount,
            raw=a.raw.strip() + b.raw.strip(),
            footnotes=sorted(set(a.footnotes) | set(b.footnotes)),
            military_air=a.military_air or b.military_air,
        )
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
