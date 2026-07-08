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
    """Resolve a month name/abbreviation (with or without trailing period) to 1-12."""
    key = name.upper().rstrip(".")
    return MONTHS.get(key) or MONTHS.get(key[:4]) or MONTHS.get(key[:3])
