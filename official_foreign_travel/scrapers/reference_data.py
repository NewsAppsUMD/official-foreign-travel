"""Build members.csv/committees.csv indices from unitedstates/congress-legislators data.

Pure transforms over already-parsed YAML documents (lists of dicts) -- callers
handle reading the YAML files and writing the resulting CSVs, so this module
is testable without touching disk.
"""

import csv
import re
import unicodedata
from pathlib import Path
from typing import Any, NamedTuple

MIN_TERM_END = "1990-01-01"  # covers report_text/'s 1994-onward corpus with a small buffer

_AMBIGUOUS = object()  # sentinel: two different people/committees share a key -- drop it

_INITIAL_RE = re.compile(r"^[A-Z]\.?$")
# "C. W. Bill" -> "C.W. Bill": reports usually print consecutive initials unspaced.
_INITIALS_GAP_RE = re.compile(r"\b([A-Z])\. (?=[A-Z]\.)")

# Common diminutives the reports use where congress-legislators has the formal
# given name and no nickname field (e.g. "Hon. Gerry Connolly" for Gerald E.
# Connolly). Wrong guesses are impossible by construction: if a diminutive
# form collides with a different person's name, build_members_index drops the
# key as ambiguous rather than matching either.
DIMINUTIVES = {
    "Andrew": ["Andy", "Drew"],
    "Anthony": ["Tony"],
    "Benjamin": ["Ben"],
    "Charles": ["Charlie", "Chuck"],
    "Christopher": ["Chris"],
    "Cynthia": ["Cindy"],
    "Daniel": ["Dan", "Danny"],
    "David": ["Dave"],
    "Deborah": ["Deb", "Debbie"],
    "Donald": ["Don"],
    "Douglas": ["Doug"],
    "Edward": ["Ed"],
    "Elizabeth": ["Liz"],
    "Frederick": ["Fred"],
    "Gerald": ["Gerry", "Jerry"],
    "Gregory": ["Greg"],
    "James": ["Jim", "Jimmy"],
    "Jerrold": ["Jerry"],
    "Jonathan": ["Jon"],
    "Joseph": ["Joe"],
    "Kenneth": ["Ken"],
    "Lawrence": ["Larry"],
    "Matthew": ["Matt"],
    "Michael": ["Mike"],
    "Newton": ["Newt"],
    "Nicholas": ["Nick"],
    "Patrick": ["Pat"],
    "Peter": ["Pete"],
    "Raymond": ["Ray"],
    "Richard": ["Rich", "Rick", "Dick"],
    "Robert": ["Rob", "Bob", "Bobby"],
    "Rodney": ["Rod"],
    "Ronald": ["Ron"],
    "Samuel": ["Sam"],
    "Stephen": ["Steve"],
    "Steven": ["Steve"],
    "Theodore": ["Ted"],
    "Thomas": ["Tom"],
    "Timothy": ["Tim"],
    "William": ["Bill", "Will"],
}

# The other direction too: the YAML sometimes has the informal name as `first`
# ("Tom Lantos") while reports print the formal one ("Thomas Lantos").
FORMAL_NAMES: dict[str, list[str]] = {}
for _formal, _dims in DIMINUTIVES.items():
    for _dim in _dims:
        FORMAL_NAMES.setdefault(_dim, []).append(_formal)


class MembersResult(NamedTuple):
    """Result of `build_members_index`."""

    rows: dict[str, str]  # "HON. NAME" -> bioguide_id
    people_considered: int
    skipped_no_name: int
    dropped_ambiguous: list[str]


class CommitteesResult(NamedTuple):
    """Result of `build_committees_index`."""

    rows: dict[str, str]  # "COMMITTEE NAME" -> code
    committees_considered: int
    collisions: list[tuple[str, str, str]]  # (key, kept_code, rejected_code)


def _fold_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _initialize(middle: str) -> str:
    """Reduce each middle-name word to its initial: 'Lee' -> 'L.', 'F. H.' -> 'F. H.'."""
    return " ".join(f"{word[0]}." for word in middle.split() if word)


def _given_variants(first: str, middle: str, nickname: str) -> set[str]:
    """The given-name forms a report might print for this person."""
    givens = {first}
    if nickname:
        givens.add(nickname)
    givens.update(DIMINUTIVES.get(first, []))
    givens.update(FORMAL_NAMES.get(first, []))

    # When the formal first name is just an initial ("K. Michael Conaway",
    # "C. A. Dutch Ruppersberger"), the go-by name reports actually print
    # ("Mike Conaway", "Dutch Ruppersberger") is the last word of `middle`.
    if middle and _INITIAL_RE.match(first):
        goby = middle.split()[-1]
        if not _INITIAL_RE.match(goby):
            givens.add(goby)
            givens.update(DIMINUTIVES.get(goby, []))
    return givens


def _suffix_forms(body: str, suffix: str) -> set[str]:
    """'... Brown' + 'Jr.' -> the punctuation spellings reports use."""
    bare = suffix.rstrip(".")
    return {f"{body} {suffix}", f"{body}, {suffix}", f"{body} {bare}", f"{body}, {bare}"}


def _spelling_variants(names: set[str]) -> set[str]:
    """Per-name spelling forms: collapsed consecutive initials and accents folded."""
    expanded: set[str] = set()
    for name in names:
        forms = {name, _INITIALS_GAP_RE.sub(r"\1.", name)}
        forms.update({_fold_accents(f) for f in forms})
        expanded.update(f for f in forms if f)
    return expanded


def full_names_for(name_field: dict[str, Any]) -> set[str]:
    """
    Return the name string(s) a report might print for one legislator's `name` field.

    `official_full` is only populated for a minority of legislators (mostly
    those serving since roughly the mid-2010s); requiring it drops most
    people who served earlier, even well within this corpus's 1994-2019
    range. And even when present, reports print many other forms of the same
    person's name, all observed in the actual corpus: middle initial omitted
    ("Charles Rangel"), middle word reduced to an initial ("David L. Hobson"
    for David Lee Hobson), first+middle as initials ("E.B. Johnson" for Eddie
    Bernice Johnson), consecutive initials unspaced ("C.W. Bill Young"),
    accents dropped ("Nydia Velazquez"), multi-word surnames hyphenated
    ("Sheila Jackson-Lee"), suffixes set off with a comma ("George E. Brown,
    Jr"), and diminutives ("Gerry Connolly" for Gerald). Generating every
    plausible form is safe: any form shared by two different people is
    dropped as ambiguous by build_members_index, never guessed.
    """
    names = set()
    official_full = name_field.get("official_full")
    if official_full:
        names.add(official_full)

    first = name_field.get("first")
    last = name_field.get("last")
    if not first or not last:
        return _spelling_variants(names)

    middle = name_field.get("middle") or ""
    suffix = name_field.get("suffix") or ""
    nickname = name_field.get("nickname") or ""

    last_variants = {last}
    if " " in last:
        last_variants.add(last.replace(" ", "-"))
    if "-" in last:
        last_variants.add(last.replace("-", " "))

    bodies = set()
    for given in _given_variants(first, middle, nickname):
        for last_variant in last_variants:
            bodies.add(f"{given} {last_variant}")
            if middle and given == first:
                bodies.add(f"{given} {middle} {last_variant}")
                initials = _initialize(middle)
                if initials != middle:
                    bodies.add(f"{given} {initials} {last_variant}")

    # "E. B. Johnson" for Eddie Bernice Johnson: both names as initials.
    if middle and not _INITIAL_RE.match(first):
        for last_variant in last_variants:
            bodies.add(f"{first[0]}. {_initialize(middle)} {last_variant}")

    names.update(bodies)
    if suffix:
        for body in bodies:
            names.update(_suffix_forms(body, suffix))

    return _spelling_variants(names)


def build_members_index(
    legislator_docs: list[list[dict[str, Any]]], min_term_end: str = MIN_TERM_END
) -> MembersResult:
    """Build a "HON. NAME" -> bioguide_id index from parsed legislators-*.yaml documents."""
    rows: dict[str, Any] = {}
    people_considered = 0
    skipped_no_name = 0

    for people in legislator_docs:
        for person in people:
            terms = person.get("terms", [])
            relevant = any(
                t.get("type") == "rep" and t.get("end", "0000") >= min_term_end for t in terms
            )
            if not relevant:
                continue
            people_considered += 1

            bioguide = person.get("id", {}).get("bioguide")
            names = full_names_for(person.get("name", {}))
            if not bioguide or not names:
                skipped_no_name += 1
                continue

            for name in names:
                key = f"HON. {name.upper()}"
                existing = rows.get(key)
                if existing is _AMBIGUOUS:
                    continue
                if existing is not None and existing != bioguide:
                    # A guessed-wrong exact match is worse than no match at
                    # all (which just flags MEMBER_UNMATCHED) -- void it
                    # rather than arbitrarily keep whichever person came first.
                    rows[key] = _AMBIGUOUS
                    continue
                rows[key] = bioguide

    dropped = sorted(key for key, value in rows.items() if value is _AMBIGUOUS)
    clean_rows = {key: value for key, value in rows.items() if value is not _AMBIGUOUS}
    return MembersResult(
        rows=clean_rows,
        people_considered=people_considered,
        skipped_no_name=skipped_no_name,
        dropped_ambiguous=dropped,
    )


def committee_name_variants(name: str, chamber_prefix: str) -> set[str]:
    """
    Given a full committee name, generate the sponsor-text forms this
    parser's header extraction might actually encounter: the chamber
    prefix stripped, select/permanent-select status reordered from a
    trailing parenthetical to a leading modifier, and "the" both inserted
    and removed before the subject (both forms appear across eras).
    """
    variants = set()
    base = name
    if chamber_prefix and base.startswith(chamber_prefix):
        base = base[len(chamber_prefix) :]

    if base.endswith(")") and "(" in base:
        main, _, suffix = base.rpartition("(")
        suffix = suffix.rstrip(")").strip()
        main = main.strip()
        if suffix and main.startswith("Committee on "):
            # Don't also add the bare (suffix-stripped) form: "(Select)" and
            # "(Permanent Select)" distinguish genuinely different
            # committees that can otherwise share an identical base name
            # (e.g. two unrelated "Committee on Ethics" entries decades apart).
            variants.add(f"{suffix} {main}")
        else:
            variants.add(main)
    else:
        variants.add(base)

    expanded = set()
    for v in variants:
        expanded.add(v)
        if " on the " in v:
            expanded.add(v.replace(" on the ", " on ", 1))
        elif " on " in v:
            expanded.add(v.replace(" on ", " on the ", 1))
    return expanded


def build_committees_index(
    committee_docs: list[list[dict[str, Any]]], chamber_types: tuple[str, ...] = ("house", "joint")
) -> CommitteesResult:
    """
    Build a committee-name -> code index from parsed committees-*.yaml documents.

    `committee_docs` should be passed current-first: on a collision between
    two different codes for the same name variant, whichever doc appears
    first wins (current committees are the authoritative/active mapping).
    """
    rows: dict[str, str] = {}
    committees_considered = 0
    collisions: list[tuple[str, str, str]] = []

    for committees in committee_docs:
        for committee in committees:
            if committee.get("type") not in chamber_types:
                continue
            code = committee.get("thomas_id")
            if not code:
                continue
            committees_considered += 1
            chamber_prefix = "House " if committee.get("type") == "house" else ""

            names = set()
            main_name = committee.get("name")
            if main_name:
                names.add(main_name)
            for historical_name in (committee.get("names") or {}).values():
                # historical `names:` values are short forms, e.g. "Ethics".
                # Reconstruct the full form using the current name's shape;
                # adding the bare short form itself is unsafe when it carries
                # a "(Select)"-style suffix (see committee_name_variants).
                if main_name and " on " in main_name:
                    prefix = main_name.split(" on ", 1)[0]
                    names.add(f"{prefix} on {historical_name}")
                else:
                    names.add(historical_name)

            for name in names:
                for variant in committee_name_variants(name, chamber_prefix):
                    key = variant.upper()
                    if key in rows and rows[key] != code:
                        collisions.append((key, rows[key], code))
                        continue
                    rows[key] = code

    return CommitteesResult(
        rows=rows, committees_considered=committees_considered, collisions=collisions
    )


def write_name_index_csv(rows: dict[str, str], output_path: Path, columns: tuple[str, str]) -> None:
    """Write a name->code/bioguide index to a two-column CSV, sorted by name."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(list(columns))
        for name, value in sorted(rows.items()):
            writer.writerow([name, value])
