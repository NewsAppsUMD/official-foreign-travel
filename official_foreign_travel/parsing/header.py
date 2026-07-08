"""Extract sponsor and reporting-period information from a table's title text."""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Tuple

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
# "BTWEEN" in the source.
PERIOD_RE = re.compile(
    r"(?:EXPENDED\s+)?BE?TWEEN,?\s+"
    r"(?P<start_mon>[A-Z]+)\.?\s*(?P<start_day>\d{1,2})?[.,]?\s*(?:(?P<start_year>\d{4})[.,]?\s+)?"
    r"AND\s+"
    r"(?P<end_mon>[A-Z]+)\.?\s*(?P<end_day>\d{1,2})[.,]?\s*(?P<end_year>\d{4})",
    re.IGNORECASE,
)

INDIVIDUAL_PREFIX_RE = re.compile(r"^(HON|MR|MRS|MS|DR)\.\s", re.IGNORECASE)
TRAILING_CHAMBER_RE = re.compile(
    r",?\s*(?:U\.?S\.?\s+)?HOUSE OF REPRESENTATIVES\s*$", re.IGNORECASE
)

QUARTER_BY_MONTHS = {
    (1, 3): 1,
    (4, 6): 2,
    (7, 9): 3,
    (10, 12): 4,
}


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
    flags: List[str] = field(default_factory=list)


def parse_period(text: str) -> Tuple[Optional[Period], List[str]]:
    """
    Parse the "EXPENDED BETWEEN <mon> <day> AND <mon> <day>, <year>" clause.

    Args:
        text: Title text containing the period clause

    Returns:
        Tuple of (Period or None, list of flags)
    """
    flags: List[str] = []
    match = PERIOD_RE.search(text)
    if not match:
        return None, ["PERIOD_UNPARSEABLE"]

    end_mon = month_num(match.group("end_mon"))
    end_day = int(match.group("end_day"))
    end_year = int(match.group("end_year"))

    start_mon = month_num(match.group("start_mon"))
    if start_mon is None:
        flags.append("PERIOD_START_MONTH_UNPARSEABLE")
        start_mon = end_mon

    if match.group("start_day"):
        start_day = int(match.group("start_day"))
    else:
        start_day = 1
        flags.append("PERIOD_START_DAY_ASSUMED")

    if match.group("start_year"):
        start_year = int(match.group("start_year"))
    elif start_mon and end_mon and start_mon > end_mon:
        # Period crosses a calendar year boundary (e.g. "DEC. 1 AND JAN. 10").
        start_year = end_year - 1
    else:
        start_year = end_year

    start_date: Optional[date]
    try:
        start_date = date(start_year, start_mon, start_day) if start_mon else None
        if start_date is None:
            flags.append("PERIOD_START_DATE_INVALID")
    except ValueError:
        start_date = None
        flags.append("PERIOD_START_DATE_INVALID")

    end_date: Optional[date]
    try:
        end_date = date(end_year, end_mon, end_day) if end_mon else None
        if end_date is None:
            flags.append("PERIOD_END_DATE_INVALID")
    except ValueError:
        end_date = None
        flags.append("PERIOD_END_DATE_INVALID")

    quarter = None
    if start_mon and end_mon:
        quarter = QUARTER_BY_MONTHS.get((start_mon, end_mon))

    period = Period(
        start=start_date, end=end_date, year=end_year, quarter=quarter, raw=match.group(0).strip()
    )
    return period, flags


def classify_sponsor(sponsor_raw: str) -> Tuple[SponsorType, List[str]]:
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
        )
    ):
        return "interparliamentary", []
    if upper.startswith("SPEAKER"):
        return "speaker", []
    if INDIVIDUAL_PREFIX_RE.match(text):
        return "individual", []

    return "other", ["SPONSOR_UNCLASSIFIED"]


def parse_header(title_raw: str) -> HeaderInfo:
    """
    Parse a table's joined title text into sponsor + period info.

    Args:
        title_raw: Whitespace-collapsed title text from TableBlock.title_raw

    Returns:
        HeaderInfo with sponsor, period, and any parsing flags
    """
    flags: List[str] = []

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

    period, period_flags = parse_period(rest)
    flags.extend(period_flags)

    sponsor = Sponsor(type=sponsor_type, name=sponsor_raw, raw=sponsor_source.strip())

    return HeaderInfo(
        amended=amended,
        sponsor=sponsor,
        period=period,
        header_raw=title_raw,
        flags=flags,
    )
