"""Shared month-name resolution used by header and date parsing."""

from typing import Optional

MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUNE": 6,
    "JUN": 6,
    "JULY": 7,
    "JUL": 7,
    "AUG": 8,
    "SEPT": 9,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def month_num(name: str) -> Optional[int]:
    """Resolve a month name/abbreviation (with or without trailing period) to 1-12.

    A leading 'P' is tolerated as a source-typo fallback (one 2019 file has
    "PSEPT." for "SEPT.") -- only tried when the bare name doesn't resolve.
    """
    key = name.upper().rstrip(".")
    n = MONTHS.get(key) or MONTHS.get(key[:4]) or MONTHS.get(key[:3])
    if n is not None:
        return n
    if key.startswith("P"):
        stripped = key[1:]
        return MONTHS.get(stripped) or MONTHS.get(stripped[:4]) or MONTHS.get(stripped[:3])
    return None
