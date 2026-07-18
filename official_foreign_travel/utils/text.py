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
    value = re.sub(r"[\s.]+$", "", value.strip()).strip()
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


# Congressional honorifics (Hon/Rep/Sen) may appear in the source without a
# trailing period -- a meaningful minority of 1990s reports write "Hon
# Charles Wilson" rather than "Hon. Charles Wilson". Detecting the
# period-less form lets `_member_lookup_variants` generate HON.-prefixed
# keys for them. Non-congressional honorifics (Mr/Ms/Dr/etc.) overwhelmingly
# prefix committee staff, and period-less forms of those ("Mr Ben McMakin")
# are rare; we require the period for those so a bare-looking staffer name
# doesn't get promoted into the non-congressional fuzzy path.
_CONGRESSIONAL_HONORIFIC_NO_PERIOD_RE = re.compile(
    r"^(?:Hon|Rep|Sen)\b\s*",
    re.IGNORECASE,
)
# All other honorifics (and the congressional ones when written with a period)
# follow the original rule: 2+ letters followed by a dot.
_HONORIFIC_WITH_PERIOD_RE = re.compile(r"^[a-zA-Z]{2,}\.")


def get_honorific(name_value: str) -> str:
    """
    Extract honorific from name (e.g., 'Hon.', 'Hon', 'Speaker').

    Congressional honorifics (Hon/Rep/Sen) are recognized with or without a
    trailing period; all others require a period (the original behavior).

    Args:
        name_value: Full name string

    Returns:
        Honorific prefix (with a trailing period normalized in) or empty string.
    """
    # Try the congressional no-period form first -- "Hon Charles Wilson"
    # should be detected as "Hon." even though there's no period.
    match = _CONGRESSIONAL_HONORIFIC_NO_PERIOD_RE.match(name_value)
    if match:
        token = match.group(0).rstrip(". \t").rstrip()
        if token:
            return token + "." if not token.endswith(".") else token
    # Fall back to the period-required form for all other honorifics
    # (Mr./Ms./Dr./Rev./Adm./etc.).
    match = _HONORIFIC_WITH_PERIOD_RE.match(name_value)
    if match:
        return match.group(0)
    return ""


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
