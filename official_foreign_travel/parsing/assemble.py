"""Assemble a canonical Report from a raw table block.

Wires together segmenter -> header -> layout -> rows -> dates -> name
matching into a single validated Report per table.
"""

import csv
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Optional

from ..matchers.name_matcher import NameMatcher
from ..models.report import (
    CostCell,
    CostGroup,
    Costs,
    Period,
    Report,
    Sponsor,
    Traveler,
    TravelSegment,
)
from ..utils.logging import get_logger
from ..utils.text import get_honorific, split_countries
from . import costs as costs_module
from .costs import parse_footnote_map
from .dates import resolve_segment_dates
from .header import parse_header
from .layout import detect_layout
from .rows import extract_rows
from .segmenter import TableBlock, segment_tables

logger = get_logger(__name__)

CANDIDATE_DATE_RE = re.compile(r"\d{1,2}/\d{1,2}\s+\d{1,2}/\d{1,2}")
FOOTNOTE_LINE_RE = re.compile(r"^\s*\\(\d+)\\")
RULE_RE = re.compile(r"^\s*-{10,}")
# Trailing footnote markers on a traveler name ("Hon. Eliot Engel *",
# "Hon. Al Green \4\", "William Patry\4\") -- part of the source text, so
# they stay in the stored name, but they must not reach the match keys.
NAME_FOOTNOTE_TAIL_RE = re.compile(r"(?:\s*(?:\*+|\\\d+\\|\(\d+\)))+\s*$")
# Trailing fixed-width padding residue (dots, whitespace) plus the lone
# backslash that's the leading `\` of a `\1/6\` date token (DATE_TOKEN_RE
# matches `1/6` without the backslashes, so the leading `\` bleeds into the
# name slice). Stripped before generating lookup keys.
NAME_TRAILING_GUNK_RE = re.compile(r"[\s.\\]+$")
# Source honorifics that prefix a Member's name. members.csv uses only
# "HON." for all entries, so the source's honorific is informational, not
# part of the match key.
NAME_HONORIFIC_RE = re.compile(
    r"^(?:Hon|Mr|Ms|Mrs|Dr|Rep|Rev|Sen|Adm|Fr|Amb|Comm|Cong|Maj|Nov|Sgt|Min)\b\.?\s*",
    re.IGNORECASE,
)
# Surname particles in Romance/Teutonic languages; used to recognize
# multi-word surnames like "de la Garza" or "van der Waals".
SURNAME_PARTICLES = {
    "de", "la", "las", "los", "el", "del", "della", "di", "da",
    "du", "le", "van", "von", "der", "den", "ten", "ter", "te", "y",
    "al", "ibn",
}
# Suffix tokens that members.csv appends to disambiguate a son from a father
# (or a third-generation honoree). The source may omit them, so we try both
# with and without.
NAME_SUFFIX_TOKENS = ["JR", "JR.", "SR", "SR.", "II", "III", "IV"]
LOW_CONFIDENCE_THRESHOLD = 0.8

NO_EXPENDITURES_RE = re.compile(
    r"no\s+expenditures\s+during\s+the\s+calendar\s+quarter.*?check\s+the\s+box",
    re.IGNORECASE | re.DOTALL,
)
WRAPPER_INTRO_RE = re.compile(
    r"Reports\s+concerning\s+the\s+foreign\s+currencies|pursuant\s+to\s+Public\s+Law",
    re.IGNORECASE,
)


def _is_no_expenditures_form(block_lines: list[str]) -> bool:
    """True when the block is a 'no expenditures' checkbox form, not a data table.

    The House Clerk's form includes a 'Please Note: If there were no
    expenditures during the calendar quarter noted above, please check
    the box at right to so indicate and return. x' line with the checkbox
    marked. These are legitimate zero-expenditure quarterly filings --
    the committee reported nothing, not a parse failure. Flag them
    `NO_EXPENDITURES` rather than `LAYOUT_UNDETECTED`/`LAYOUT_LOW_CONFIDENCE`.
    """
    text = " ".join(block_lines)
    return bool(NO_EXPENDITURES_RE.search(text))


def _is_wrapper_intro(title_raw: str) -> bool:
    """True when the block is a Speaker-Authorized quarterly summary intro paragraph.

    These begin with 'REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL'
    (so the segmenter splits them as a table) but are followed by 'Reports
    concerning the foreign currencies... pursuant to Public Law 95-384'
    prose, not a column-header block or data rows. The real tables follow.
    Dropping these avoids a junk report with no sponsor, period, or travelers.
    """
    return bool(WRAPPER_INTRO_RE.search(title_raw))


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# Parenthetical annotations the source appends to traveler names ("(AL)" state
# tag, "(Gilman Codel)" delegation note). Not part of the name proper.
NAME_PARENTHETICAL_RE = re.compile(r"\s*\([^)]*\)\s*$")


def _query_name_tokens(name: str) -> tuple[str, str]:
    """Split an honorific-prefixed source name into (first_token, last_token).

    Strips the honorific, trailing parentheticals, suffix tokens, and
    fixed-width gunk so the tokens reflect the actual name body. Used by
    `_disambiguate_ambiguous_match` to verify a fuzzy candidate's first
    and last name against what the source actually wrote.
    """
    cleaned = NAME_FOOTNOTE_TAIL_RE.sub("", name).strip()
    cleaned = NAME_TRAILING_GUNK_RE.sub("", cleaned).strip().rstrip(",").strip()
    cleaned = NAME_PARENTHETICAL_RE.sub("", cleaned).strip()
    m = NAME_HONORIFIC_RE.match(cleaned)
    body = cleaned[m.end():] if m else cleaned
    body = body.strip().rstrip(",").strip()
    tokens = body.split()
    if len(tokens) > 1 and tokens[-1].upper().rstrip(",.") in NAME_SUFFIX_TOKENS:
        tokens = tokens[:-1]
    if not tokens:
        return "", ""
    return tokens[0], tokens[-1]


def _first_name_match(query_first: str, candidate_first: str) -> bool:
    """True if the source's first-name token plausibly refers to the candidate's first name.

    Handles single initials ("D." -> "Donald"), exact matches, 1-character
    typos ("Corinne" -> "Corrine", "Partrick" -> "Patrick"), and prefix
    abbreviations ("Al" -> "Alcee", "Pat" -> "Patrick"). The 1-edit path is
    gated by a relative-ratio cap (0.3) so a single edit on a very short
    name ("Jim" -> "Tim") doesn't count -- that's a staffer coincidence,
    not a typo of a member's name.
    """
    q = query_first.rstrip(".").lower()
    c = candidate_first.lower()
    if not q or not c:
        return False
    if len(q) == 1:
        return c.startswith(q)
    if q == c:
        return True
    dist = _levenshtein(q, c)
    if dist <= 1 and dist / max(len(q), len(c)) <= 0.3:
        return True
    if len(q) >= 2 and (c.startswith(q) or q.startswith(c)):
        return True
    return False


def _surname_match(query_last: str, candidate_last: str) -> bool:
    """True if the source's surname plausibly matches the candidate's surname.

    Handles exact matches, hyphenated compounds ("Chenoweth" ->
    "Chenoweth-Hage"), 1-2 character typos ("Gilmor" -> "Gillmor",
    "LoBionbo" -> "LoBiondo"), and prefix abbreviations. The edit-distance
    path is gated by a relative-ratio cap (0.3) so a 2-edit difference on a
    short surname ("Ennis" -> "Enzi", 2/5 = 0.4) doesn't count -- that's a
    staffer with a coincidentally similar surname, not a typo.
    """
    q = query_last.lower().rstrip(",.")
    c = candidate_last.lower()
    if not q or not c:
        return False
    if q == c:
        return True
    parts = c.split("-")
    if q in parts:
        return True
    dist = _levenshtein(q, c)
    if dist <= 2 and dist / max(len(q), len(c)) <= 0.3:
        return True
    if len(q) >= 4 and (c.startswith(q) or q.startswith(c)):
        return True
    return False


def _disambiguate_ambiguous_match(name: str, matches: list) -> Optional[str]:
    """Pick a single bioguide from an ambiguous fuzzy result by name verification.

    When `NameMatcher.search_by_name` returns `is_inconclusive` (two or more
    candidates scored within the ambiguity threshold), the fuzzy score alone
    can't pick. This tiebreaker checks each candidate's first and last name
    against the source's actual name tokens: if exactly one candidate's
    first name AND surname both plausibly match, that's the member the
    source meant. If both or neither match, the ambiguity is real (a
    staffer whose name coincidentally resembles two members, or a true
    same-name collision needing committee context) and we return None so
    the caller leaves it inconclusive.

    The surname gate is what makes this safe: "Hon. Tim Clancy" fuzzy-matches
    "Tim Holden" on first name, but "Clancy" != "Holden" so the gate rejects
    it -- Clancy is a staffer, not a typo of Holden. Without the gate, every
    staffer whose first name matches a member's would get a wrong bioguide.
    """
    qf, ql = _query_name_tokens(name)
    if not qf or not ql:
        return None
    chosen: list[str] = []
    for m in matches:
        if _first_name_match(qf, m.first_name) and _surname_match(ql, m.last_name):
            chosen.append(m.bioguide_id)
    if len(chosen) == 1:
        return chosen[0]
    return None


def _member_lookup_variants(name: str, honorific: Optional[str]) -> list[str]:
    """Generate `member_index` lookup keys for an honorific-prefixed name.

    `members.csv` keys every entry as `HON. <name>` -- "HON." is the only
    honorific form in the index, regardless of whether the source said
    "Rep.", "Sen.", "Dr.", or "Hon.". The source's honorific is informational
    (used to gate fuzzy matching), not part of the match key.

    Safety gate: only congressional honorifics ("Hon.", "Rep.", "Sen.")
    trigger the full variant set. Bare names (no honorific) and other
    honorifics ("Mr.", "Ms.", "Dr.", "Rev.", etc., which in this corpus
    overwhelmingly prefix committee staff) fall back to the original
    `name.upper()` lookup -- which won't match `HON. ...` entries, so they
    fail through to the honorific-gated fuzzy matcher. This preserves the
    safety guarantee documented in `_match_member`: bare names are not
    fuzzy-matched to members by surname, because that produces confident-
    looking but wrong bioguide IDs (e.g. multiple different staffers all
    matched to the same member by surname).

    Variants are generated in order of decreasing specificity, so the first
    match is the most precise form available:

    1. Full body with `HON.` prefix.
    2. Period after a single-letter first initial: "E de la Garza" -> "E. de la Garza".
    3. First + last (strip middle initials): "William D. Lipinski" -> "William Lipinski".
    4. Strip leading single-letter initial: "Y. Tim Hutchinson" -> "Tim Hutchinson".
    5. Surname-only (single token): last token.
    6. Multi-token surname (with particles): "de la Garza" -> "DE LA GARZA".
    7. With appended suffix: "Donald Payne" -> "Donald Payne, JR" / "Donald Payne JR".
       (members.csv stores members with the same name as their father with a
       suffix; the source may omit it.)

    Trailing fixed-width padding residue (dots, whitespace, lone backslash)
    is stripped first so it doesn't pollute any of the variants. Trailing
    parenthetical annotations ("(AL)" state tag, "(Codel)" delegation note)
    are also stripped -- they aren't part of the name proper (NAME_PARENTHETICAL_RE
    is the same regex _query_name_tokens uses for the inconclusive-path
    tiebreaker), and leaving them in the key blocks committee-based
    disambiguation: "Hon. Mike Rogers (AL)" would otherwise generate
    "HON. MIKE ROGERS (AL)" which never matches the disambiguation_index's
    "HON. MIKE ROGERS" key, even when the sponsor_code resolves correctly.
    """
    cleaned = NAME_TRAILING_GUNK_RE.sub("", name).strip().rstrip(",").strip()
    cleaned = NAME_PARENTHETICAL_RE.sub("", cleaned).strip()
    if not cleaned:
        return []
    # Only generate HON.-prefixed variants for congressional honorifics.
    # Other names (bare names, "Mr.", "Dr.", etc.) fall through with just
    # the source-form lookup, which doesn't match members.csv's "HON. ..." keys.
    hkey = (honorific or "").rstrip(".").upper()
    if hkey not in ("HON", "REP", "SEN"):
        return [cleaned.upper()]

    m = NAME_HONORIFIC_RE.match(cleaned)
    body = cleaned[m.end():] if m else cleaned
    body = body.strip()
    if not body:
        return []

    # Strip a trailing suffix token ("JR", ", JR", "JR.", etc.) from the body;
    # we'll re-append standard forms below.
    body_tokens = body.split()
    if len(body_tokens) > 1 and body_tokens[-1].upper().rstrip(",.") in NAME_SUFFIX_TOKENS:
        body_tokens = body_tokens[:-1]
    if not body_tokens:
        return []

    prefix = "HON."
    upper = " ".join(t.upper() for t in body_tokens)

    keys: list[str] = [f"{prefix} {upper}"]

    # (2) Period after single-letter first initial: "E de la Garza" -> "E. de la Garza"
    if len(body_tokens[0]) == 1 and not body_tokens[0].endswith("."):
        spaced = [body_tokens[0] + "."] + body_tokens[1:]
        keys.append(f"{prefix} {' '.join(t.upper() for t in spaced)}")

    # (3) First + last (drop middle initials): "William D. Lipinski" -> "William Lipinski"
    if len(body_tokens) > 2:
        keys.append(f"{prefix} {body_tokens[0].upper()} {body_tokens[-1].upper()}")

    # (4) Strip leading single-letter initial: "Y. Tim Hutchinson" -> "Tim Hutchinson"
    if len(body_tokens) > 2 and len(body_tokens[0].rstrip(".")) == 1:
        rest = body_tokens[1:]
        keys.append(f"{prefix} {' '.join(t.upper() for t in rest)}")

    # (5) Surname-only (single token)
    keys.append(f"{prefix} {body_tokens[-1].upper()}")

    # (6) Multi-token surname with particles: "de la Garza" -> "DE LA GARZA"
    for n in (2, 3):
        if len(body_tokens) >= n + 1:
            tail = body_tokens[-n:]
            if any(t.lower() in SURNAME_PARTICLES for t in tail[:-1]):
                keys.append(f"{prefix} {' '.join(t.upper() for t in tail)}")

    # (7) Appended suffix variants -- members.csv stores "Donald Payne, JR";
    # source may have just "Donald Payne".
    base = f"{prefix} {upper}"
    for suffix in NAME_SUFFIX_TOKENS:
        keys.append(f"{base}, {suffix}")
        keys.append(f"{base} {suffix}")

    # Deduplicate while preserving order.
    seen = set()
    unique: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            unique.append(k)
    return unique


def _bare_name_date_verified_match(
    name: str,
    member_index: dict[str, str],
    name_matcher: NameMatcher,
    period: Optional[Period],
) -> Optional[tuple[str, float]]:
    """Try HON.-prefixed exact lookups for a bare name, gated by date-of-service.

    The source sometimes omits the "Hon." prefix from a member's row, leaving
    a bare "First Last" (or "First M. Last") name that ``_member_lookup_variants``
    correctly doesn't generate ``HON.`` variants for (the safety gate blocks
    fuzzy matching of bare names to members). But some of those bare names are
    actually members -- the prefix was just dropped. This helper tries the
    HON.-prefixed exact lookup forms and accepts the match only if the
    matched bioguide was serving during the report's period (±1 year for the
    House's filed-next-quarter lag). Without the date gate, a staffer named
    "Mark Walker" traveling in 2011 would match "HON. MARK WALKER" -> W000819
    (who served 2015-2019).

    Returns:
        (bioguide_id, confidence) tuple on a verified match, else None.
    """
    if period is None:
        return None
    cleaned = NAME_TRAILING_GUNK_RE.sub("", name).strip().rstrip(",").strip()
    if not cleaned:
        return None
    body_tokens = cleaned.split()
    # Strip a trailing suffix token (JR, III, etc.); members.csv stores those
    # on the indexed name, and we want the base form for this check.
    if len(body_tokens) > 1 and body_tokens[-1].upper().rstrip(",.") in NAME_SUFFIX_TOKENS:
        body_tokens = body_tokens[:-1]
    if not body_tokens or len(body_tokens) < 2:
        # Single-token bare names are too ambiguous to date-verify reliably.
        return None

    upper = " ".join(t.upper() for t in body_tokens)
    keys: list[str] = [f"HON. {upper}"]
    # For 3+ token names, also try first + last (drop middle initials).
    if len(body_tokens) > 2:
        keys.append(f"HON. {body_tokens[0].upper()} {body_tokens[-1].upper()}")

    for key in keys:
        bioguide = member_index.get(key)
        if bioguide and name_matcher.was_serving(bioguide, period.year):
            return bioguide, 1.0
    return None


def _maiden_name_prefix_match(
    name: str,
    matches: list,
    name_matcher: NameMatcher,
    period: Optional[Period],
) -> Optional[tuple[str, float]]:
    """Recover the maiden-name -> married-name case from a below-threshold fuzzy result.

    A member who marries after an earlier-career source report was filed
    appears in the source under their maiden surname ("Hon. Stephanie
    Herseth") while `members.csv` carries the married compound surname
    ("HON. STEPHANIE HERSETH SANDLIN"). The fuzzy matcher scores the
    partial-name match below its `min_match_score` (the source surname is
    only half of the member's indexed surname), so `is_confident` and
    `is_inconclusive` are both False and the traveler would otherwise fall
    through to `MEMBER_UNMATCHED`. The top match is nonetheless the right
    person under a narrow maiden-name gate.

    Gate (all must hold):
    - A top match exists with `first_name` and `last_name` both populated,
      and a `period` is available for the date check.
    - The source's first-name token EXACTLY equals the top match's first
      name (case-insensitive -- no fuzzy, no initials, no nicknames). The
      maiden-name case is about surname change, not first-name variation;
      accepting first-name slack here would let staffers whose first name
      resembles a member's ride this path.
    - The source's surname is a strict prefix of the top match's surname:
      the member surname is longer, the source surname is its start, and
      the character right after the prefix is a separator (space or
      hyphen). The separator requirement blocks "Hon. Bob Smith" staffer
      matching "Bob Smithers" member -- "Smith" is a prefix of "Smithers"
      but the boundary has no separator, so it's a different name, not a
      marriage extension. The strict-prefix requirement blocks same-surname
      staffers: "Hon. Bob Smith" staffer vs "Bob Smith" member has equal
      surnames, not a prefix relationship.
    - The matched bioguide was serving during the report's period
      (+/-1 year for filing lag). Same date gate as the bare-name recovery
      path: a staffer named "Stephanie Herseth" traveling in 1990 (before
      the member's term started) must not match H001037.

    Returns:
        (bioguide_id, score) tuple on a verified match, else None.
    """
    if not matches or period is None:
        return None
    top = matches[0]
    if not top.first_name or not top.last_name:
        return None
    qf, ql = _query_name_tokens(name)
    if not qf or not ql:
        return None
    if qf.lower() != top.first_name.lower():
        return None
    mem_last = top.last_name.lower().rstrip(",.")
    q_last = ql.lower().rstrip(",.")
    if len(mem_last) <= len(q_last):
        return None
    if not mem_last.startswith(q_last):
        return None
    if mem_last[len(q_last)] not in (" ", "-"):
        return None
    if not name_matcher.was_serving(top.bioguide_id, period.year):
        return None
    return top.bioguide_id, top.score


def load_name_index(csv_path: Path) -> dict[str, str]:
    """Load an uppercase-name -> code lookup from a two-column CSV (name,code)."""
    index: dict[str, str] = {}
    try:
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return index
            key_field, value_field = reader.fieldnames[0], reader.fieldnames[1]
            for row in reader:
                index[row[key_field].strip().upper()] = row[value_field].strip()
    except FileNotFoundError:
        logger.warning(f"Lookup CSV not found: {csv_path}")
    return index


def load_disambiguation_index(csv_path: Path) -> dict[tuple[str, str], str]:
    """
    Load a hand-curated (uppercase name, sponsor code) -> bioguide ID lookup.

    Resolves the names members.csv must drop as ambiguous because two
    different people share them *simultaneously* (e.g. Mike Rogers of
    Michigan and Mike Rogers of Alabama, both serving 2003-2015, where
    neither exact matching nor date-aware fuzzy matching can choose): the
    report's sponsoring committee still separates them, since each sat on
    different committees. Optional -- an absent file just disables this.
    """
    index: dict[tuple[str, str], str] = {}
    try:
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = (row["name"].strip().upper(), row["sponsor_code"].strip().upper())
                index[key] = row["bioguide_id"].strip()
    except FileNotFoundError:
        logger.debug(f"No member disambiguation CSV at {csv_path}")
    return index


def _to_pydantic_cell(cell: costs_module.CostCell) -> CostCell:
    return CostCell(
        amount=cell.amount,
        raw=cell.raw,
        footnotes=list(cell.footnotes),
        military_air=cell.military_air,
    )


def _to_pydantic_costs(draft: costs_module.Costs) -> Costs:
    def group(g: costs_module.CostGroup) -> CostGroup:
        return CostGroup(
            foreign_currency=_to_pydantic_cell(g.foreign_currency),
            us_dollar=_to_pydantic_cell(g.us_dollar),
        )

    return Costs(
        per_diem=group(draft.per_diem),
        transportation=group(draft.transportation),
        other=group(draft.other),
        total=group(draft.total),
    )


def _extract_signature(block_lines: list[str]) -> Optional[str]:
    """Best-effort grab of the chairman signature block after the closing table rule."""
    rule_indices = [i for i, line in enumerate(block_lines) if RULE_RE.match(line)]
    if len(rule_indices) < 2:
        return None
    tail_lines = block_lines[rule_indices[-1] + 1 :]
    candidate_lines = [
        line.strip() for line in tail_lines if line.strip() and not FOOTNOTE_LINE_RE.match(line)
    ]
    if not candidate_lines:
        return None
    return " ".join(candidate_lines)


def _match_member(
    name: str,
    segments: list[TravelSegment],
    member_index: dict[str, str],
    name_matcher: Optional[NameMatcher],
    honorific: Optional[str] = None,
    sponsor_code: Optional[str] = None,
    disambiguation_index: Optional[dict[tuple[str, str], str]] = None,
    period: Optional[Period] = None,
) -> tuple[Optional[str], Optional[float], list[str]]:
    """
    Resolve a traveler's bioguide ID: exact match first, fuzzy fallback, else flagged blank.

    `honorific` is accepted separately from `name` because not every source
    keeps them combined: the deterministic pipeline always embeds the prefix
    in `name` itself (e.g. "Hon. Lois Capps"), matching members.csv's key
    format directly, but an LLM repair pass may instead split them into two
    fields (name="Lois Capps", honorific="Hon."). Both must resolve to the
    same match.

    `sponsor_code`/`disambiguation_index` resolve names that are ambiguous
    even with dates (two people with the same name serving simultaneously)
    via the report's sponsoring committee -- see load_disambiguation_index.

    `period` (the report's filing quarter) stands in for the fuzzy matcher's
    date window when none of the traveler's own segment dates parsed --
    garbled date cells otherwise silently disable fuzzy matching for names
    it would resolve confidently.
    """
    if not name:
        return None, None, []

    name = NAME_FOOTNOTE_TAIL_RE.sub("", name).strip()
    if not name:
        return None, None, []

    effective_honorific = honorific or get_honorific(name)

    lookup_keys = _member_lookup_variants(name, effective_honorific)

    for key in lookup_keys:
        exact = member_index.get(key)
        if exact:
            return exact, 1.0, []

    if sponsor_code and disambiguation_index:
        for key in lookup_keys:
            curated = disambiguation_index.get((key, sponsor_code.upper()))
            if curated:
                return curated, 1.0, ["MEMBER_DISAMBIGUATED_BY_COMMITTEE"]

    if name_matcher is None:
        return None, None, ["MEMBER_UNMATCHED"]

    if not effective_honorific:
        # Bare-name HON.-prefix exact match with date verification.
        # _member_lookup_variants returned only [cleaned.upper()] for this bare
        # name, which doesn't match members.csv's "HON. ..." keys. Some of
        # those bare names are actually members whose source row just omitted
        # the "Hon." prefix -- try HON.-prefixed forms here, but only accept
        # if the matched bioguide was actually serving during the report's
        # period (±1 year for filing lag). A staffer named "Mark Walker"
        # traveling in 2011 would otherwise match "HON. MARK WALKER" -> W000819
        # (who served 2015-2019); the date gate rejects that.
        verified = _bare_name_date_verified_match(
            name, member_index, name_matcher, period
        )
        if verified is not None:
            bioguide, confidence = verified
            return bioguide, confidence, ["MEMBER_MATCHED_BY_NAME_DATE"]
        # No verified bare-name match: fall through to the existing gate.
        # NameMatcher's data is Members of Congress only; it has no way to say
        # "this is staff, not a member" -- it always returns its best-scoring
        # candidate even for a name that isn't a member at all. Bare names
        # (no "Hon."/"Dr."/etc. prefix, in `name` or `honorific`) are
        # overwhelmingly staff in this corpus, and fuzzy-matching them
        # produces confident-looking but wrong bioguide IDs (e.g. multiple
        # different staffers all matched to the same member by surname).
        # Only names the source itself flagged with an honorific are attempted.
        # This is STAFF_UNMATCHED, not MEMBER_UNMATCHED: no real match attempt
        # was made (the source gave us no reason to try), so it's the expected
        # outcome for staff, not a match failure worth flagging for review.
        return None, None, ["STAFF_UNMATCHED"]

    first_dated = next(
        (s for s in segments if s.arrival_date is not None and s.departure_date is not None), None
    )
    if first_dated is not None and first_dated.arrival_date and first_dated.departure_date:
        window_start, window_end = first_dated.arrival_date, first_dated.departure_date
    elif period is not None and period.start and period.end:
        window_start, window_end = period.start, period.end
    else:
        return None, None, ["MEMBER_UNMATCHED"]

    try:
        result = name_matcher.search_by_name(
            name,
            window_start.strftime("%m/%d/%Y"),
            window_end.strftime("%m/%d/%Y"),
        )
    except Exception as e:
        logger.debug(f"Name matcher failed for {name!r}: {e}")
        return None, None, ["MEMBER_UNMATCHED"]

    if result.is_confident and result.best_bioguide_id and result.top_match is not None:
        return result.best_bioguide_id, result.top_match.score, ["MEMBER_FUZZY_MATCHED"]
    if result.is_inconclusive:
        # Two or more candidates scored within the ambiguity threshold. Try
        # to pick one by verifying first name + surname against the source --
        # e.g. "Hon. D. Payne" is ambiguous between Donald Payne (P000149)
        # and Lewis Payne (P000152), but "D." -> Donald and the surname
        # Payne matches both, so only Donald qualifies. If exactly one
        # candidate's first and last name both match, that's the member;
        # otherwise the ambiguity is real and stays inconclusive.
        chosen = _disambiguate_ambiguous_match(name, result.matches)
        if chosen is not None:
            top_match = next((m for m in result.matches if m.bioguide_id == chosen), None)
            score = top_match.score if top_match is not None else None
            return chosen, score, ["MEMBER_DISAMBIGUATED_BY_NAME"]
        return None, None, ["MEMBER_MATCH_INCONCLUSIVE"]
    # Below-threshold single top match. Try the maiden-name-prefix recovery:
    # a member who married after the source report was filed appears under
    # their maiden surname in the source ("Hon. Stephanie Herseth") but
    # under the married compound surname in members.csv ("HON. STEPHANIE
    # HERSETH SANDLIN"). The fuzzy matcher scores the partial-name match
    # below min_match_score, but the top result is unambiguously the right
    # person under a strict maiden-name gate (exact first-name match,
    # source surname is a strict prefix of the member's compound surname
    # with a separator at the boundary, member was serving during the
    # report's period).
    maiden = _maiden_name_prefix_match(name, result.matches, name_matcher, period)
    if maiden is not None:
        bioguide, score = maiden
        return bioguide, score, ["MEMBER_MATCHED_BY_MAIDEN_NAME"]
    return None, None, ["MEMBER_UNMATCHED"]


def assemble_table(
    block: TableBlock,
    member_index: Optional[dict[str, str]] = None,
    committee_index: Optional[dict[str, str]] = None,
    name_matcher: Optional[NameMatcher] = None,
    disambiguation_index: Optional[dict[tuple[str, str], str]] = None,
) -> Report:
    """
    Build a Report from one raw TableBlock.

    Args:
        block: Segmented table block
        member_index: Uppercase traveler name -> bioguide ID, for exact matching
        committee_index: Uppercase committee name -> committee code
        name_matcher: Optional NameMatcher for fuzzy fallback matching
        disambiguation_index: Optional (uppercase name, sponsor code) -> bioguide ID,
            for names ambiguous even with dates -- see load_disambiguation_index

    Returns:
        A fully assembled Report. Nothing is dropped for looking wrong;
        problems are recorded in `flags` on the report or its segments.
    """
    member_index = member_index or {}
    committee_index = committee_index or {}

    header_info = parse_header(block.title_raw, source_file=block.source_file)
    flags: list[str] = list(header_info.flags)

    sponsor_code = None
    if header_info.sponsor.type in ("committee", "commission"):
        sponsor_code = committee_index.get(header_info.sponsor.name.upper())

    period = None
    if header_info.period is not None:
        period = Period(
            start=header_info.period.start,
            end=header_info.period.end,
            year=header_info.period.year,
            quarter=header_info.period.quarter,
        )

    numbered_lines = list(enumerate(block.lines, start=1))
    candidate_lines = [line for line in block.lines if CANDIDATE_DATE_RE.search(line[:80])]
    layout = detect_layout(block.lines, candidate_lines)

    footnote_lines = [line for line in block.lines if FOOTNOTE_LINE_RE.match(line)]
    footnote_map = parse_footnote_map(footnote_lines)

    travelers_out: list[Traveler] = []
    committee_total_out: Optional[Costs] = None

    if _is_no_expenditures_form(block.lines):
        flags.append("NO_EXPENDITURES")
    elif layout is None:
        flags.append("LAYOUT_UNDETECTED")
    else:
        if layout.data_row_derived:
            flags.append("LAYOUT_INFERRED_FROM_DATA")
        if layout.confidence < LOW_CONFIDENCE_THRESHOLD:
            flags.append("LAYOUT_LOW_CONFIDENCE")

        traveler_drafts, total_draft, row_flags = extract_rows(numbered_lines, layout, footnote_map)
        flags.extend(row_flags)

        if not traveler_drafts and candidate_lines:
            flags.append("NO_TRAVELERS_EXTRACTED")

        if total_draft is not None:
            committee_total_out = _to_pydantic_costs(total_draft)

        for draft in traveler_drafts:
            segments_out = []
            for seg in draft.segments:
                resolved = resolve_segment_dates(
                    seg.arrival_raw, seg.departure_raw, header_info.period
                )
                segments_out.append(
                    TravelSegment(
                        arrival_date=resolved.arrival,
                        departure_date=resolved.departure,
                        arrival_raw=seg.arrival_raw,
                        departure_raw=seg.departure_raw,
                        country_raw=seg.country_raw,
                        countries=split_countries(seg.country_raw),
                        costs=_to_pydantic_costs(seg.costs),
                        flags=list(seg.flags) + list(resolved.flags),
                        source_lines=seg.source_lines,
                    )
                )

            bioguide_id, match_confidence, name_flags = _match_member(
                draft.name,
                segments_out,
                member_index,
                name_matcher,
                sponsor_code=sponsor_code,
                disambiguation_index=disambiguation_index,
                period=period,
            )
            flags.extend(name_flags)

            travelers_out.append(
                Traveler(
                    name=draft.name,
                    honorific=get_honorific(draft.name) or None,
                    bioguide_id=bioguide_id,
                    match_confidence=match_confidence,
                    segments=segments_out,
                )
            )

    sponsor = Sponsor(
        type=header_info.sponsor.type,
        name=header_info.sponsor.name,
        code=sponsor_code,
        raw=header_info.sponsor.raw,
    )

    report_id = f"{Path(block.source_file).stem}-{block.table_index:03d}"

    return Report(
        report_id=report_id,
        source_file=block.source_file,
        table_index=block.table_index,
        amended=header_info.amended,
        parse_method="deterministic",
        sponsor=sponsor,
        period=period,
        header_raw=header_info.header_raw,
        travelers=travelers_out,
        committee_total=committee_total_out,
        footnotes=footnote_map,
        signature_raw=_extract_signature(block.lines),
        flags=flags,
        layout_fingerprint=list(layout.fingerprint) if layout else [],
        layout_confidence=layout.confidence if layout else None,
    )


def assemble_file(
    file_path: Path,
    member_index: Optional[dict[str, str]] = None,
    committee_index: Optional[dict[str, str]] = None,
    name_matcher: Optional[NameMatcher] = None,
    disambiguation_index: Optional[dict[tuple[str, str], str]] = None,
) -> list[Report]:
    """Parse one report text file into a list of Report objects, one per table."""
    text = file_path.read_text(encoding="utf-8", errors="replace")
    blocks = segment_tables(text, file_path.name)
    return [
        assemble_table(b, member_index, committee_index, name_matcher, disambiguation_index)
        for b in blocks
        if not _is_wrapper_intro(b.title_raw)
    ]


def assemble_directory(
    directory: Path,
    member_index: Optional[dict[str, str]] = None,
    committee_index: Optional[dict[str, str]] = None,
    name_matcher: Optional[NameMatcher] = None,
    disambiguation_index: Optional[dict[tuple[str, str], str]] = None,
) -> Iterator[Report]:
    """Parse every *.txt report file in a directory into Report objects, in filename order."""
    for file_path in sorted(directory.glob("*.txt")):
        yield from assemble_file(
            file_path, member_index, committee_index, name_matcher, disambiguation_index
        )
