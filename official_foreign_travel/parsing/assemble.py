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
LOW_CONFIDENCE_THRESHOLD = 0.8


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

    lookup_keys = [name.upper()]
    if effective_honorific and not name.upper().startswith(effective_honorific.upper()):
        lookup_keys.append(f"{effective_honorific} {name}".strip().upper())

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
        # NameMatcher's data is Members of Congress only; it has no way to say
        # "this is staff, not a member" -- it always returns its best-scoring
        # candidate even for a name that isn't a member at all. Bare names
        # (no "Hon."/"Dr."/etc. prefix, in `name` or `honorific`) are
        # overwhelmingly staff in this corpus, and fuzzy-matching them
        # produces confident-looking but wrong bioguide IDs (e.g. multiple
        # different staffers all matched to the same member by surname).
        # Only names the source itself flagged with an honorific are attempted.
        return None, None, ["MEMBER_UNMATCHED"]

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
        return None, None, ["MEMBER_MATCH_INCONCLUSIVE"]
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

    header_info = parse_header(block.title_raw)
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

    if layout is None:
        flags.append("LAYOUT_UNDETECTED")
    else:
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
