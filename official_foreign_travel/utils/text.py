"""Text processing utilities."""

import re
import unicodedata
from typing import Optional

_TAG_RE = re.compile(r"<[^>]{1,40}>|&lt;[^&]{1,40}&gt;")


def strip_html_tags(text: str) -> str:
    """
    Remove leaked HTML/SGML tags (e.g. <strong>, <SUP>, <html>) from report text.

    Some Congressional Record source files embed markup around emphasized words
    or footnote superscripts. The tags occupy character positions with no visual
    width in the original rendering, so deleting them (rather than blanking them)
    restores the fixed-width column grid used for parsing.

    Args:
        text: Raw file contents (or a single line)

    Returns:
        Text with all `<...>` tags removed
    """
    return _TAG_RE.sub("", text)


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
        charset_lower = {c.lower() for c in charset}
        if "-" in charset_lower:
            charset_lower.remove("-")
            charset_lower.add(r"\-")
        pattern = r"[^{}]".format("".join(c for c in charset_lower))
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


def split_countries(country_raw: str) -> list:
    """
    Best-effort split of a country cell into individual country names.

    Handles comma-separated lists and a trailing "X, Y & Z" / "X, Y and Z"
    conjunction. Not authoritative for multi-word country names containing
    commas -- callers should also keep country_raw for the unsplit original.

    Args:
        country_raw: Cleaned country cell text, e.g. "Germany, Rwanda, & Portugal"

    Returns:
        List of individual country name strings
    """
    text = country_raw.strip().rstrip(".")
    if not text:
        return []
    text = re.sub(r"\s*&\s*", ", ", text)
    text = re.sub(r"\s+and\s+", ", ", text, flags=re.IGNORECASE)
    return [part.strip() for part in text.split(",") if part.strip()]
