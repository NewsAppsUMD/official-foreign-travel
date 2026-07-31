"""Extract sponsor and reporting-period information from a table's title text."""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from ..models.report import SponsorType
from .months import month_num

TITLE_PREFIX_RE = re.compile(
    r"^\s*(?P<amended>\(?AMENDED\)?\s+)?REPORTS?\s+OF\s+EXPENDITURES\s+FOR\s+OFFICIAL\s+"
    r"(?:FOREIGN\s+)?TRAVEL,?\s*",
    re.IGNORECASE,
)

# "EXPENDED" is frequently present but sometimes missing; start day/year are
# sometimes omitted or duplicated on both ends; separators before the year are
# either a comma or (source typo) a period; "BETWEEN" is occasionally misspelled
# "BTWEEN" (one E) or "BETWEEEN" (three E's) in the source.
PERIOD_RE = re.compile(
    r"(?:EXPENDED\s+)?BE?TWE+ENP?,?\s+"
    r"(?P<start_mon>[A-Z]+)\.?\s*(?P<start_day>\d{1,2})?[.,]?\s*(?:(?P<start_year>\d{4})[.,]?\s+)?"
    r"AND\s+"
    r"(?P<end_mon>[A-Z]+)[.,]?\s*(?P<end_day>\d{1,2})[.,]?\s*(?P<end_year>\d{4})",
    re.IGNORECASE,
)

# Partial: handles BETWEEN ... [AND ...] where the AND clause (or part of it)
# is missing because the title line was truncated. Captures whatever is present
# and lets parse_period infer the rest from the reporting-period quarter and
# the source filename. Strict PERIOD_RE is tried first; this is the fallback.
# `(?!\d)` on day groups prevents "19" from being captured as the end_day of
# "1997" -- the 4-digit year must win when no separator splits them.
PERIOD_PARTIAL_RE = re.compile(
    r"(?:EXPENDED\s+)?BE?TWE+ENP?,?\s+"
    r"(?P<start_mon>[A-Z]+)[.,]?\s*"
    r"(?P<start_day>\d{1,2}(?!\d))?[.,]?\s*"
    r"(?:(?P<start_year>\d{4})[.,]?\s+)?"
    r"(?:AND\s+"
    r"(?P<end_mon>[A-Z]+)[.,]?\s*"
    r"(?P<end_day>\d{1,2}(?!\d))?[.,]?\s*"
    r"(?P<end_year>\d{4})?"
    r")?",
    re.IGNORECASE,
)

# "EXPENDED ON <mon> <day>, <year>" — single-date trip (no range).
PERIOD_ON_RE = re.compile(
    r"EXPENDED\s+ON\s+"
    r"(?P<start_mon>[A-Z]+)\.?\s*(?P<start_day>\d{1,2})[.,]?\s+(?P<start_year>\d{4})",
    re.IGNORECASE,
)

# "EXPENDED <mon> <day> AND <mon> <day>, <year>" — missing the BETWEEN word.
PERIOD_NO_BETWEEN_RE = re.compile(
    r"EXPENDED\s+"
    r"(?P<start_mon>[A-Z]+)\.?\s*(?P<start_day>\d{1,2})[.,]?\s+"
    r"AND\s+"
    r"(?P<end_mon>[A-Z]+)\.?\s*(?P<end_day>\d{1,2})[.,]?\s+(?P<end_year>\d{4})",
    re.IGNORECASE,
)

# "EXPENDED BETWEEN FEB. 3 AND 6, 2000" — end has a bare day with no end
# month (source typo / truncation). Treat end_mon as start_mon.
PERIOD_NO_END_MON_RE = re.compile(
    r"(?:EXPENDED\s+)?BE?TWE+ENP?,?\s+"
    r"(?P<start_mon>[A-Z]+)\.?\s*(?P<start_day>\d{1,2})[.,]?\s+"
    r"(?:(?P<start_year>\d{4})[.,]?\s+)?"
    r"AND\s+"
    r"(?P<end_day>\d{1,2})[.,]?\s+(?P<end_year>\d{4})",
    re.IGNORECASE,
)

# "EXPENDED BETWEEN FEB. 21-26, 2002" — dash-separated day range with a single
# month and year. Treat end_mon = start_mon, end_day = the second day.
PERIOD_DASH_RANGE_RE = re.compile(
    r"(?:EXPENDED\s+)?BE?TWE+ENP?,?\s+"
    r"(?P<start_mon>[A-Z]+)\.?\s*(?P<start_day>\d{1,2})-(?P<end_day>\d{1,2}),?\s+(?P<end_year>\d{4})",
    re.IGNORECASE,
)

# Tail fallback for titles with duplicated/garbage AND clauses, e.g.
# "BETWEEN ARMED SERVICES AND JAN. 1 AND MAR. 31, 2008" (a "COMMITTEE ON,"
# typo let "ARMED SERVICES" leak into the BETWEEN clause). Find the LAST
# "AND <mon> <day>, <year>" as the end, then look backwards for a
# "<mon> <day>" at the end of the prefix as the start. Only tried when
# every other regex fails, so a normal title's first-and-only AND clause
# is used as-is.
PERIOD_TAIL_END_RE = re.compile(
    r"AND\s+(?P<end_mon>[A-Z]+)\.?\s*(?P<end_day>\d{1,2})[.,]?\s+(?P<end_year>\d{4})",
    re.IGNORECASE,
)
PERIOD_TAIL_START_RE = re.compile(
    r"(?P<start_mon>[A-Z]+)\.?\s*(?P<start_day>\d{1,2})[.,]?\s*$",
    re.IGNORECASE,
)

# "EXPENDED BETWEEN 7/1 AND 9/30, 2009" — numeric M/D format instead of month
# names (Speaker report from 2009). Year applies to end; start year is
# inferred for cross-year periods.
PERIOD_MD_RE = re.compile(
    r"(?:EXPENDED\s+)?BE?TWE+ENP?,?\s+"
    r"(?P<start_mon>\d{1,2})/(?P<start_day>\d{1,2})\s+AND\s+"
    r"(?P<end_mon>\d{1,2})/(?P<end_day>\d{1,2})[.,]?\s+(?P<end_year>\d{4})",
    re.IGNORECASE,
)

# Speaker / annual-summary wrappers: "... during the first quarter of 2008 ..."
# or "... during the first, second, third, and fourth quarters of 2018 ...".
# These wrappers have no "EXPENDED BETWEEN" clause and no per-traveler rows;
# the period is the covered quarter(s) of the stated year.
PERIOD_DURING_QUARTERS_RE = re.compile(
    r"during the\s+(?P<quarters>(?:first|second|third|fourth)"
    r"(?:\s*,\s*(?:first|second|third|fourth))*"
    r"(?:\s*,?\s+and\s+(?:first|second|third|fourth))?)\s+quarters?\s+of\s+(?P<year>\d{4})",
    re.IGNORECASE,
)

INDIVIDUAL_PREFIX_RE = re.compile(
    r"^(HON(?:ORABLE)?|MR|MRS|MS|DR|REV|FR|FATHER|MSGR|SIR|LADY)\.?\s", re.IGNORECASE
)
TRAILING_CHAMBER_RE = re.compile(
    r",?\s*(?:U\.?S\.?\s+)?HOUSE OF REPRESENTATIVES\s*,?.*$", re.IGNORECASE
)
# Personal-name-shaped sponsor text: 2-5 uppercase words, each starting with a
# letter, containing letters/periods/apostrophes/hyphens, optional trailing
# comma. Used only after all committee/delegation/travel/interparliamentary
# patterns have been exhausted, so the stopword guard inside the helper is a
# defense-in-depth against matching a phrase that "looks name-shaped" but is
# actually trip or chamber text.
NAME_WORD_RE = re.compile(r"^[A-Z][A-Za-z.''\-]*,?$")
NAME_STOPWORDS = frozenset(
    {
        "TRAVEL", "TO", "HOUSE", "REPRESENTATIVES", "COMMITTEE", "COMMISSION",
        "DELEGATION", "SPEAKER", "ASSEMBLY", "EXPENDED", "REPORT", "REPORTS",
        "BETWEEN", "PARLIAMENTARY", "INTERPARLIAMENTARY", "JOINT", "NATO", "OSCE",
        "ORGANIZATION", "CONSOLIDATED", "CONCERNING", "AND", "OF", "FOR", "THE",
        "IN", "AT", "ON", "PURSUANT", "PUBLIC", "LAW", "QUARTERS", "QUARTER",
        "JANUARY", "FEBRUARY", "MARCH", "APRIL", "JULY", "AUGUST",
        "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
        "FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH",
    }
)


def _looks_like_personal_name(text: str) -> bool:
    """Heuristic: does this sponsor text look like a bare personal name?

    2-5 whitespace-separated words, each starting with an uppercase letter and
    containing only letters/periods/apostrophes/hyphens (plus an optional
    trailing comma). A small stopword set rejects phrases like "TRAVEL TO
    RUSSIA" that happen to match the shape.
    """
    # Strip trailing ", PH.D." / ", PHD" credential suffixes.
    text = re.sub(r",\s*PH\.?D\.?", "", text, flags=re.IGNORECASE).strip()
    words = text.split()
    if not (2 <= len(words) <= 5):
        return False
    for w in words:
        if not NAME_WORD_RE.match(w):
            return False
        if w.rstrip(".,").upper() in NAME_STOPWORDS:
            return False
    return True

QUARTER_BY_MONTHS = {
    (1, 3): 1,
    (4, 6): 2,
    (7, 9): 3,
    (10, 12): 4,
}

# End month/day for a standard quarter, keyed by its start month.
# Used to infer the end date when the AND clause was truncated out of the
# title line -- only standard quarter starts qualify, since other start
# months (e.g. "FEB. 16 ...") don't pin down a quarter end unambiguously.
QUARTER_END_BY_START_MONTH = {
    1: (3, 31),
    4: (6, 30),
    7: (9, 30),
    10: (12, 31),
}

# Ordinal quarter name -> (start_month, start_day, end_month, end_day).
# Used by the "during the <quarter> of <year>" wrapper-summary parser.
QUARTER_BY_ORDINAL_NAME = {
    "first": (1, 1, 3, 31),
    "second": (4, 1, 6, 30),
    "third": (7, 1, 9, 30),
    "fourth": (10, 1, 12, 31),
}

# Filename pattern YYYYqQmmmdd.txt encodes filing year and quarter. Used to
# infer the period year when the title line was truncated before any 4-digit
# year. The House files reports the quarter AFTER the travel, so a period
# ending in the same quarter as filing means the period is from the prior
# year; otherwise the period is from the filing year.
FILENAME_PERIOD_RE = re.compile(r"^(\d{4})q(\d)[a-z]+\d+\.txt$", re.IGNORECASE)


def _days_in_month(year: int, month: int) -> int:
    """Last day of `month` in `year` (handles leap-year February)."""
    if month == 2:
        return 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28
    if month in (4, 6, 9, 11):
        return 30
    return 31


def _quarter_for_month(month: int) -> Optional[int]:
    """Map any month number to its calendar quarter (1-4), or None if out of range.

    Used to infer the period year from the source filename when no 4-digit year
    survives in the title -- the month alone is enough to identify the quarter,
    even if the period doesn't start on a quarter boundary.
    """
    if 1 <= month <= 3:
        return 1
    if 4 <= month <= 6:
        return 2
    if 7 <= month <= 9:
        return 3
    if 10 <= month <= 12:
        return 4
    return None


@dataclass
class Sponsor:
    """A report's sponsoring entity, classified from its free-text title segment."""

    type: SponsorType
    name: str
    raw: str


@dataclass
class Period:
    """The reporting period a table covers."""

    start: Optional[date]
    end: Optional[date]
    year: int
    quarter: Optional[int]
    raw: str


@dataclass
class HeaderInfo:
    """Everything extracted from a table's title line."""

    amended: bool
    sponsor: Sponsor
    period: Optional[Period]
    header_raw: str
    flags: list[str] = field(default_factory=list)


def _infer_year_from_filename(
    source_file: Optional[str], period_quarter: Optional[int]
) -> Optional[int]:
    """Infer the period year from a YYYYqQmmmdd.txt filename.

    The House files reports the quarter AFTER the travel ended, so a period
    that ended in the same quarter as the filing (period_quarter >=
    filing_quarter, e.g. an Oct-Dec period filed in Q1) belongs to the prior
    year; otherwise it belongs to the filing year.
    """
    if not source_file or period_quarter is None:
        return None
    match = FILENAME_PERIOD_RE.match(source_file)
    if not match:
        return None
    filing_year, filing_quarter = int(match.group(1)), int(match.group(2))
    if period_quarter >= filing_quarter:
        return filing_year - 1
    return filing_year


def parse_period(
    text: str, source_file: Optional[str] = None
) -> tuple[Optional[Period], list[str]]:
    """
    Parse the "EXPENDED BETWEEN <mon> <day> AND <mon> <day>, <year>" clause.

    Title lines are sometimes truncated by the source's fixed-width limit
    (193 chars), losing the AND clause, the end year, or both. When the
    strict regex fails, fall back to a partial regex that captures whatever
    is present and infers the rest from the reporting-period quarter (a
    standard quarter start month pins down its quarter end) and the source
    filename's filing year/quarter (which pins down the period year when no
    4-digit year survives in the title).

    Args:
        text: Title text containing the period clause
        source_file: Filename of the source report, used for year inference
            when no year is present in the title text

    Returns:
        Tuple of (Period or None, list of flags)
    """
    flags: list[str] = []
    match = PERIOD_RE.search(text)
    if match:
        return _build_period_from_full_match(match, flags)

    md_match = PERIOD_MD_RE.search(text)
    if md_match:
        return _build_period_from_md_match(md_match, flags)

    on_match = PERIOD_ON_RE.search(text)
    if on_match:
        return _build_period_from_on_match(on_match, flags)

    no_between_match = PERIOD_NO_BETWEEN_RE.search(text)
    if no_between_match:
        return _build_period_from_full_match(no_between_match, flags)

    no_end_mon_match = PERIOD_NO_END_MON_RE.search(text)
    if no_end_mon_match:
        return _build_period_from_no_end_mon_match(no_end_mon_match, flags)

    dash_match = PERIOD_DASH_RANGE_RE.search(text)
    if dash_match:
        return _build_period_from_dash_range_match(dash_match, flags)

    partial_match = PERIOD_PARTIAL_RE.search(text)
    if partial_match and month_num(partial_match.group("start_mon")) is not None:
        partial_period, partial_flags = _build_period_from_partial_match(
            partial_match, source_file, flags
        )
        if partial_period is not None:
            return partial_period, partial_flags
        # _build_period_from_partial_match returned None -- fall through to
        # the during/filename fallbacks instead of declaring unparseable.

    during_match = PERIOD_DURING_QUARTERS_RE.search(text)
    if during_match:
        return _build_period_from_during_match(during_match, flags)

    tail_period, tail_flags = _build_period_from_tail_match(text, flags)
    if tail_period is not None:
        return tail_period, tail_flags

    filename_period, filename_flags = _build_period_from_filename(source_file, flags)
    if filename_period is not None:
        return filename_period, filename_flags

    return None, ["PERIOD_UNPARSEABLE"]


def _build_period_from_tail_match(
    text: str, flags: list[str]
) -> tuple[Optional[Period], list[str]]:
    """Build a Period from the LAST "AND <mon> <day>, <year>" in the text,
    with the start taken from the nearest "<mon> <day> AND" before it.

    Used for titles with duplicated/garbage AND clauses where the first AND
    is part of a typo'd sponsor name (e.g. "COMMITTEE ON," → "ARMED SERVICES"
    leaks in). The last AND clause is the real period end. Returns
    (None, ["PERIOD_UNPARSEABLE"]) when no usable tail is found, so the caller
    can fall back to declaring the period unparseable.
    """
    end_matches = list(PERIOD_TAIL_END_RE.finditer(text))
    if not end_matches:
        return None, ["PERIOD_UNPARSEABLE"]
    last_end = end_matches[-1]
    end_mon = month_num(last_end.group("end_mon"))
    end_day = int(last_end.group("end_day"))
    end_year = int(last_end.group("end_year"))
    if end_mon is None:
        return None, ["PERIOD_UNPARSEABLE"]

    prefix = text[: last_end.start()].rstrip()
    start_match = PERIOD_TAIL_START_RE.search(prefix)
    if start_match is None:
        return None, ["PERIOD_UNPARSEABLE"]
    start_mon = month_num(start_match.group("start_mon"))
    if start_mon is None:
        # The captured "start_mon" is something like "REPRE" (truncated
        # "REPRESENTATIVES" — the chamber line was truncated and the real
        # "JAN." before "14" was lost). The start_day is still meaningful,
        # so infer start_mon = end_mon rather than dropping the period.
        if not start_match.group("start_day"):
            return None, ["PERIOD_UNPARSEABLE"]
        start_mon = end_mon
        flags.append("PERIOD_START_MONTH_INFERRED")
    start_day = int(start_match.group("start_day"))
    start_year = end_year - 1 if start_mon > end_mon else end_year

    start_date = _build_date(start_year, start_mon, start_day, flags, "PERIOD_START")
    end_date = _build_date(end_year, end_mon, end_day, flags, "PERIOD_END")

    quarter = QUARTER_BY_MONTHS.get((start_mon, end_mon))
    raw = f"{start_match.group(0).strip()} {last_end.group(0).strip()}".strip()
    period = Period(
        start=start_date, end=end_date, year=end_year, quarter=quarter, raw=raw
    )
    return period, flags


def _build_period_from_during_match(
    match: "re.Match[str]", flags: list[str]
) -> tuple[Optional[Period], list[str]]:
    """Build a Period from a "during the <quarter(s)> of <year>" clause.

    Used for Speaker / annual-summary wrapper titles that have no
    "EXPENDED BETWEEN" clause: "... during the first quarter of 2008 ..."
    (single quarter) or "... during the first, second, third, and fourth
    quarters of 2018 ..." (annual). The period spans the listed quarters
    of the stated year. `quarter` is set only when a single quarter is
    listed; multi-quarter summaries get `quarter=None`.
    """
    quarters_str = match.group("quarters")
    year = int(match.group("year"))
    # Find each ordinal name in the captured list (handles "first, second,
    # third, and fourth" and "first and second" forms -- the regex splits
    # them with commas and/or "and", so we just need the names themselves).
    names = re.findall(r"first|second|third|fourth", quarters_str, re.IGNORECASE)
    if not names:
        return None, ["PERIOD_UNPARSEABLE"]
    spans = [QUARTER_BY_ORDINAL_NAME[n.lower()] for n in names]
    start_mon, start_day = min(s[0] for s in spans), 1
    end_mon, end_day = max(s[2] for s in spans), max(s[3] for s in spans)
    start_date = _build_date(year, start_mon, start_day, flags, "PERIOD_START")
    end_date = _build_date(year, end_mon, end_day, flags, "PERIOD_END")
    quarter = QUARTER_BY_MONTHS.get((start_mon, end_mon))
    period = Period(
        start=start_date, end=end_date, year=year, quarter=quarter, raw=match.group(0).strip()
    )
    return period, flags


def _build_period_from_filename(
    source_file: Optional[str], flags: list[str]
) -> tuple[Optional[Period], list[str]]:
    """Infer a quarter-wide period from the source filename when no period
    clause survived in the title text.

    Last-resort fallback before declaring a period unparseable. The
    House Clerk files reports the quarter AFTER the travel ended (with
    some same-quarter exceptions), so a report filed in Q2 typically
    covers Q1 travel; filed Q1 typically covers Q4 of the prior year; etc.

    The period is the full prior quarter (start of quarter to last day of
    quarter) of the inferred year. Cross-quarter trips are handled by
    `dates.resolve_dates`'s year-rollover logic at the segment level --
    e.g. a Mar 28 - Apr 2 trip with an inferred Q1 period will resolve
    the Apr 2 segment as Apr 2 of the next year via year-rollover.

    Returns (None, ["PERIOD_UNPARSEABLE"]) when the filename doesn't match
    the YYYYqQmmmdd pattern.
    """
    if not source_file:
        return None, ["PERIOD_UNPARSEABLE"]
    match = FILENAME_PERIOD_RE.match(source_file)
    if not match:
        return None, ["PERIOD_UNPARSEABLE"]
    filing_year, filing_quarter = int(match.group(1)), int(match.group(2))
    # Prior quarter: filed Q2 -> period Q1, filed Q1 -> period Q4 (prior year).
    if filing_quarter == 1:
        period_quarter = 4
        period_year = filing_year - 1
    else:
        period_quarter = filing_quarter - 1
        period_year = filing_year
    start_mon, start_day, end_mon, end_day = QUARTER_BY_ORDINAL_NAME[
        {1: "first", 2: "second", 3: "third", 4: "fourth"}[period_quarter]
    ]
    start_date = _build_date(period_year, start_mon, start_day, flags, "PERIOD_START")
    end_date = _build_date(period_year, end_mon, end_day, flags, "PERIOD_END")
    flags.append("PERIOD_INFERRED_FROM_FILENAME")
    period = Period(
        start=start_date,
        end=end_date,
        year=period_year,
        quarter=period_quarter,
        raw="",
    )
    return period, flags


def _build_period_from_full_match(
    match: "re.Match[str]", flags: list[str]
) -> tuple[Optional[Period], list[str]]:
    """Build a Period from a strict PERIOD_RE / PERIOD_NO_BETWEEN_RE match.

    The two regexes share end_mon/end_day/end_year but PERIOD_NO_BETWEEN_RE
    has no start_year group, so we use groupdict().get() for the optional
    start fields.
    """
    groups = match.groupdict()
    end_mon = month_num(match.group("end_mon"))
    end_day = int(match.group("end_day"))
    end_year = int(match.group("end_year"))

    start_mon = month_num(match.group("start_mon"))
    if start_mon is None:
        flags.append("PERIOD_START_MONTH_UNPARSEABLE")
        start_mon = end_mon

    if groups.get("start_day"):
        start_day = int(groups["start_day"])
    else:
        start_day = 1
        flags.append("PERIOD_START_DAY_ASSUMED")

    if groups.get("start_year"):
        start_year = int(groups["start_year"])
    elif start_mon and end_mon and start_mon > end_mon:
        start_year = end_year - 1
    else:
        start_year = end_year

    start_date = _build_date(start_year, start_mon, start_day, flags, "PERIOD_START")
    end_date = _build_date(end_year, end_mon, end_day, flags, "PERIOD_END")

    quarter = None
    if start_mon and end_mon:
        quarter = QUARTER_BY_MONTHS.get((start_mon, end_mon))

    period = Period(
        start=start_date, end=end_date, year=end_year, quarter=quarter, raw=match.group(0).strip()
    )
    return period, flags


def _build_date(
    year: int, month: Optional[int], day: int, flags: list[str], flag_prefix: str
) -> Optional[date]:
    """Construct a date, clamping an out-of-range day to the month's last day
    rather than dropping the date entirely.

    Source documents have typos like "SEPT. 31" (Sep has 30 days) and "JUNE 31"
    (June has 30 days). Treating these as fatal loses the entire period --
    downstream every segment in the table loses its year inference. Instead we
    clamp to the month's last valid day and flag it, so the period is still
    useful for year inference and the typo is visible to reviewers.
    """
    if month is None:
        flags.append(f"{flag_prefix}_DATE_INVALID")
        return None
    try:
        return date(year, month, day)
    except ValueError:
        clamped = _days_in_month(year, month)
        try:
            d = date(year, month, clamped)
            flags.append(f"{flag_prefix}_DAY_CLAMPED")
            return d
        except ValueError:
            flags.append(f"{flag_prefix}_DATE_INVALID")
            return None


def _build_period_from_on_match(
    match: "re.Match[str]", flags: list[str]
) -> tuple[Optional[Period], list[str]]:
    """Build a Period from an "EXPENDED ON <mon> <day>, <year>" match (single-day trip)."""
    start_mon = month_num(match.group("start_mon"))
    if start_mon is None:
        return None, ["PERIOD_UNPARSEABLE"]
    start_day = int(match.group("start_day"))
    start_year = int(match.group("start_year"))

    try:
        d = date(start_year, start_mon, start_day)
    except ValueError:
        return None, ["PERIOD_END_DATE_INVALID"]

    period = Period(start=d, end=d, year=start_year, quarter=None, raw=match.group(0).strip())
    return period, flags


def _build_period_from_md_match(
    match: "re.Match[str]", flags: list[str]
) -> tuple[Optional[Period], list[str]]:
    """Build a Period from a "BETWEEN 7/1 AND 9/30, 2009" M/D format match."""
    start_mon = int(match.group("start_mon"))
    start_day = int(match.group("start_day"))
    end_mon = int(match.group("end_mon"))
    end_day = int(match.group("end_day"))
    end_year = int(match.group("end_year"))

    if not (1 <= start_mon <= 12 and 1 <= end_mon <= 12):
        return None, ["PERIOD_UNPARSEABLE"]

    start_year = end_year - 1 if start_mon > end_mon else end_year

    start_date = _build_date(start_year, start_mon, start_day, flags, "PERIOD_START")
    end_date = _build_date(end_year, end_mon, end_day, flags, "PERIOD_END")

    quarter = QUARTER_BY_MONTHS.get((start_mon, end_mon))
    period = Period(
        start=start_date, end=end_date, year=end_year, quarter=quarter, raw=match.group(0).strip()
    )
    return period, flags


def _build_period_from_no_end_mon_match(
    match: "re.Match[str]", flags: list[str]
) -> tuple[Optional[Period], list[str]]:
    """Build a Period when the end month is missing (e.g. "FEB. 3 AND 6, 2000").

    The end month is taken to be the same as the start month -- the source
    omitted it, and the bare day after AND only makes sense as a same-month
    end date.
    """
    start_mon = month_num(match.group("start_mon"))
    if start_mon is None:
        return None, ["PERIOD_UNPARSEABLE"]
    end_mon = start_mon

    start_day = int(match.group("start_day"))
    end_day = int(match.group("end_day"))
    end_year = int(match.group("end_year"))

    start_year = int(match.group("start_year")) if match.group("start_year") else end_year

    start_date = _build_date(start_year, start_mon, start_day, flags, "PERIOD_START")
    end_date = _build_date(end_year, end_mon, end_day, flags, "PERIOD_END")

    quarter = QUARTER_BY_MONTHS.get((start_mon, end_mon))
    period = Period(
        start=start_date, end=end_date, year=end_year, quarter=quarter, raw=match.group(0).strip()
    )
    return period, flags


def _build_period_from_dash_range_match(
    match: "re.Match[str]", flags: list[str]
) -> tuple[Optional[Period], list[str]]:
    """Build a Period from a "BETWEEN FEB. 21-26, 2002" dash-range match."""
    start_mon = month_num(match.group("start_mon"))
    if start_mon is None:
        return None, ["PERIOD_UNPARSEABLE"]
    end_mon = start_mon

    start_day = int(match.group("start_day"))
    end_day = int(match.group("end_day"))
    end_year = int(match.group("end_year"))

    start_year = end_year

    start_date = _build_date(start_year, start_mon, start_day, flags, "PERIOD_START")
    end_date = _build_date(end_year, end_mon, end_day, flags, "PERIOD_END")

    quarter = QUARTER_BY_MONTHS.get((start_mon, end_mon))
    period = Period(
        start=start_date, end=end_date, year=end_year, quarter=quarter, raw=match.group(0).strip()
    )
    return period, flags


def _build_period_from_partial_match(
    match: "re.Match[str]", source_file: Optional[str], flags: list[str]
) -> tuple[Optional[Period], list[str]]:
    """Build a Period from a partial (truncated) BETWEEN match with inference.

    The strict regex failed, so we accept the partial match and infer the
    missing pieces. We only proceed when the start month resolves to a real
    month name and either (a) the end month is also present, or (b) the
    start month is a standard quarter start so its quarter-end is unambiguous.
    Anything else (e.g. a typo like "BETWEENP" capturing "ARMED SERVICES" as
    the start month, or "FEB. 16 ..." with no end month) is left unparseable
    rather than guessed.
    """
    start_mon = month_num(match.group("start_mon"))
    if start_mon is None:
        return None, ["PERIOD_UNPARSEABLE"]

    end_mon_str = match.group("end_mon")
    end_mon = month_num(end_mon_str) if end_mon_str else None

    if end_mon is None:
        quarter_end = QUARTER_END_BY_START_MONTH.get(start_mon)
        if quarter_end is None:
            return None, ["PERIOD_UNPARSEABLE"]
        end_mon, end_day_inferred = quarter_end
        flags.append("PERIOD_END_INFERRED")
    else:
        end_day_inferred = None

    if match.group("start_day"):
        start_day = int(match.group("start_day"))
    else:
        start_day = 1
        flags.append("PERIOD_START_DAY_ASSUMED")

    if match.group("end_day"):
        end_day = int(match.group("end_day"))
    elif end_day_inferred is not None:
        end_day = end_day_inferred
    else:
        # end_mon present but no end_day: use the last day of end_mon. Feb's
        # day count depends on the year, so resolve the year first and fill
        # this in below.
        end_day = None
        flags.append("PERIOD_END_INFERRED")

    # Year resolution: prefer explicit years in the title; fall back to the
    # filename's filing year/quarter. The cross-year case (start month later
    # than end month, e.g. "DEC. 1 AND JAN. 10") means start_year = end_year - 1.
    start_year_str = match.group("start_year")
    end_year_str = match.group("end_year")
    if end_year_str:
        end_year = int(end_year_str)
        start_year = end_year - 1 if start_mon > end_mon else end_year
    elif start_year_str:
        start_year = int(start_year_str)
        end_year = start_year + 1 if start_mon > end_mon else start_year
    else:
        period_quarter = QUARTER_BY_MONTHS.get((start_mon, end_mon))
        if period_quarter is None:
            period_quarter = _quarter_for_month(start_mon)
        if period_quarter is None:
            return None, ["PERIOD_UNPARSEABLE"]
        inferred = _infer_year_from_filename(source_file, period_quarter)
        if inferred is None:
            return None, ["PERIOD_UNPARSEABLE"]
        flags.append("PERIOD_YEAR_INFERRED_FROM_FILENAME")
        end_year = inferred
        start_year = end_year - 1 if start_mon > end_mon else end_year

    period_year = end_year

    # Resolve end_day now that the year is known (Feb has 28 or 29).
    if end_day is None:
        end_day = _days_in_month(end_year, end_mon)

    start_date = _build_date(start_year, start_mon, start_day, flags, "PERIOD_START")
    end_date = _build_date(end_year, end_mon, end_day, flags, "PERIOD_END")

    quarter = QUARTER_BY_MONTHS.get((start_mon, end_mon))

    period = Period(
        start=start_date, end=end_date, year=period_year, quarter=quarter, raw=match.group(0).strip()
    )
    return period, flags


def classify_sponsor(sponsor_raw: str) -> tuple[SponsorType, list[str]]:
    """
    Classify a sponsor's type from its free-text name.

    Args:
        sponsor_raw: Sponsor text with trailing chamber boilerplate already stripped

    Returns:
        Tuple of (sponsor type, list of flags). Unrecognized text is classified
        as "other" with a SPONSOR_UNCLASSIFIED flag rather than guessed.
    """
    text = sponsor_raw.strip()
    upper = text.upper()

    if not text:
        return "other", ["SPONSOR_EMPTY"]
    if re.search(r"PERMANENT SELECT COMMITTEE", upper):
        return "committee", []
    if re.search(r"\bCOMMITTEE ON\b", upper):
        return "committee", []
    # "COMMITTEE ONSTANDARDS" typo (no space after ON): drop the \b after ON
    # so the missing space doesn't defeat the committee classification.
    if re.search(r"\bCOMMITTEE ON[A-Z]", upper):
        return "committee", []
    # "JOINT ECONOMIC COMMITTEE" etc. — committee without "COMMITTEE ON".
    if re.search(r"\bJOINT\b.*\bCOMMITTEE\b", upper):
        return "committee", []
    # House task forces (e.g. "Task Force on the Attempted Assassination of
    # Donald J. Trump") are standing bodies formed by resolution, structurally
    # committee-like -- classify alongside committees rather than "other".
    if re.search(r"\bTASK FORCE\b", upper):
        return "committee", []
    if re.search(r"\bDELEGATION\b", upper):
        return "delegation", []
    if re.search(r"\bCOMMISSION ON\b", upper):
        return "commission", []
    if any(
        marker in upper
        for marker in (
            "MEXICO-UNITED STATES",
            "INTERPARLIAMENTARY",
            "ATLANTIC ASSEMBLY",
            "PARLIAMENTARY GROUP",
            # NATO Parliamentary Assembly, OSCE PA, and other parliamentary
            # assemblies that aren't covered by the older keyword set.
            "PARLIAMENTARY ASSEMBLY",
            "NORTH ATLANTIC",  # truncated "NORTH ATLANTIC ASSEMBLY" (pre-NATO PA name)
            "OSCE",
            "TRANSATLANTIC LEGISLATORS",
            "NATO PARLIAMENTARY",
        )
    ):
        return "interparliamentary", []
    if "SPEAKER" in upper:
        return "speaker", []
    # "TRAVEL TO <place>" and the truncated "TO <place>" form (where "TRAVEL"
    # was stripped along with the leading "FOREIGN TRAVEL" boilerplate) are
    # delegation-style sponsor descriptions: the sponsor IS the trip.
    if re.search(r"\bTRAVEL TO\b", upper) or re.match(r"^TO\s+[A-Z]", upper):
        return "delegation", []
    if INDIVIDUAL_PREFIX_RE.match(text):
        return "individual", []
    # Bare personal names ("DANIEL SILVERBERG", "KAY A. KING, PH.D.",
    # "MARIO DIAZ-BALART") — staff or members travelling under their own
    # name, no honorific in the source. Only attempted after every other
    # pattern above has been ruled out.
    if _looks_like_personal_name(text):
        return "individual", []

    return "other", ["SPONSOR_UNCLASSIFIED"]


def parse_header(title_raw: str, source_file: Optional[str] = None) -> HeaderInfo:
    """
    Parse a table's joined title text into sponsor + period info.

    Args:
        title_raw: Whitespace-collapsed title text from TableBlock.title_raw
        source_file: Filename of the source report, used by parse_period to
            infer the period year from the filing year/quarter when the title
            line was truncated before any 4-digit year survived

    Returns:
        HeaderInfo with sponsor, period, and any parsing flags
    """
    flags: list[str] = []

    prefix_match = TITLE_PREFIX_RE.match(title_raw)
    amended = bool(prefix_match and prefix_match.group("amended"))
    rest = title_raw[prefix_match.end() :] if prefix_match else title_raw
    if not prefix_match:
        flags.append("TITLE_PREFIX_UNPARSEABLE")

    period_match = PERIOD_RE.search(rest)
    sponsor_source = rest[: period_match.start()] if period_match else rest
    sponsor_raw = TRAILING_CHAMBER_RE.sub("", sponsor_source.strip().rstrip(",").strip()).strip()

    sponsor_type, sponsor_flags = classify_sponsor(sponsor_raw)
    flags.extend(sponsor_flags)

    period, period_flags = parse_period(rest, source_file=source_file)
    flags.extend(period_flags)

    sponsor = Sponsor(type=sponsor_type, name=sponsor_raw, raw=sponsor_source.strip())

    return HeaderInfo(
        amended=amended,
        sponsor=sponsor,
        period=period,
        header_raw=title_raw,
        flags=flags,
    )
