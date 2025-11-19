"""Text processing utilities."""

import re
import unicodedata
from typing import Optional


def clean_cell(value: str, default: str = "") -> str:
    """
    Remove repeated periods, trailing periods, and strip whitespace.

    Args:
        value: Input string
        default: Default value if result is empty

    Returns:
        Cleaned string
    """
    value = re.sub(r"\.+$", "", value.strip()).strip()
    return value if value else default


def lower_name(s: str) -> str:
    """
    Convert name to lowercase, remove accents and special characters.

    Args:
        s: Input string

    Returns:
        Normalized lowercase string
    """
    t = str.lower(s)
    t = re.sub(r"[\-(),.`']", " ", t)
    t = re.sub(r"  +", " ", t)
    return unicodedata.normalize("NFKD", t.strip()).encode("ascii", "ignore").decode()


def normalize_name(name: str, charset: Optional[set] = None) -> str:
    """
    Normalize name for matching: lowercase, remove accents, filter characters.

    Args:
        name: Input name
        charset: Optional set of allowed characters

    Returns:
        Normalized name
    """
    name = unicodedata.normalize("NFKD", name.lower()).encode("ascii", "ignore").decode()
    name = re.sub(r" +", " ", name)

    if charset is not None:
        charset_lower = set(c.lower() for c in charset)
        if "-" in charset_lower:
            charset_lower.remove("-")
            charset_lower.add(r"\-")
        pattern = r"[^{0}]".format("".join(c for c in charset_lower))
        name = re.sub(pattern, " ", name)
    else:
        name = re.sub(r"[^ a-zA-Z]", " ", name)

    name = re.sub(r"  +", " ", name)
    return name.strip()


def get_honorific(name_value: str) -> str:
    """
    Extract honorific from name (e.g., 'Hon.', 'Speaker').

    Args:
        name_value: Full name string

    Returns:
        Honorific prefix or empty string
    """
    match = re.match(r"^[a-zA-Z]{2,}\.", name_value)
    return match.group(0) if match else ""
