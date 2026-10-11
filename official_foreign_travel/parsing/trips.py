"""Construct canonical per-traveler trip records across filings.

A "trip" is one person's journey, not a filing or a table: the same trip
appears in multiple filings when committees co-sponsor a delegation (each
files its own report listing overlapping travelers) or when a journey spans
a reporting-period boundary and is published twice. This module groups
traveler appearances into trip records by person identity (bioguide when
known, else normalized name tokens) plus overlapping travel dates and
overlapping destination sets, per about_the_data.md.

Pure derivation: ``build_trips`` never mutates its input reports (legs are
deep-copied from the winning filing) and produces the same output for the
same input. Callers pass the visible (post-dedup, post-corrections) report
set -- use ``serialize.visible_reports`` first. Field values come from the
winning (most recently published) filing, with gaps filled from older
filings; cost amounts are never summed across filings.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from ..models.report import Report, Traveler, TravelSegment
from .assemble import _query_name_tokens
from .dedup import parse_publication_date

# Within one filing, a traveler's legs more than this many days apart are
# separate journeys. Corpus-wide, same-day (0) and overnight (1-2) day
# transitions are contiguous legs of one journey; gaps >= 4 (e.g. Peru
# 4/07-4/12 then Ireland 4/16-4/23) are separate trips. Negative gaps
# (overlapping or out-of-order legs) never split.
TRIP_SPLIT_GAP_DAYS = 3

# Minimum length for the substring fallback in destination comparison, so
# short gunk tokens from split_countries ("5/94", "N/A") can't contain-match
# a real country. Deliberately no gazetteer: EUROPE vs DENMARK disagreements
# stay unmerged and flagged (TRIP_DATE_DESTINATION_CONFLICT).
COUNTRY_SUBSTRING_MIN_LEN = 4

TRIP_ID_HASH_LEN = 6

TRIP_MERGED_ACROSS_FILINGS = "TRIP_MERGED_ACROSS_FILINGS"
TRIP_MATCHED_NAME_ONLY = "TRIP_MATCHED_NAME_ONLY"
TRIP_MERGED_MIXED_IDENTITY = "TRIP_MERGED_MIXED_IDENTITY"
TRIP_DATE_DESTINATION_CONFLICT = "TRIP_DATE_DESTINATION_CONFLICT"
TRIP_DATES_MISSING = "TRIP_DATES_MISSING"
TRIP_SPLIT_BY_DATE_GAP = "TRIP_SPLIT_BY_DATE_GAP"
TRIP_GAP_FILLED_FROM_EARLIER_FILING = "TRIP_GAP_FILLED_FROM_EARLIER_FILING"

# Travelers whose every segment carries one of these flags are cost/label
# rows promoted to traveler objects ("Commercial airfare"), not people.
NON_PERSON_SEGMENT_FLAGS = frozenset({"NON_PERSON_LABEL_ROW", "STAFFDEL_GROUP_EXPENSE"})

# The flags that mark a trip as worth a human look in the review app's
# trips queue: matches resting on name alone or on a bare identity whose
# name uniquely resolved to one member, journeys whose dates could not be
# recovered (and which therefore never merged), and unmerged
# date-overlapping destination disagreements. TRIP_SPLIT_BY_DATE_GAP is
# informational (a filing legitimately reporting two separate journeys),
# TRIP_MERGED_ACROSS_FILINGS alone is the merge working as intended, and
# TRIP_GAP_FILLED_FROM_EARLIER_FILING likewise just records where filled-in
# dates and destinations came from -- so none of the three is "attention".
TRIP_ATTENTION_FLAGS = frozenset(
    {
        TRIP_DATE_DESTINATION_CONFLICT,
        TRIP_MATCHED_NAME_ONLY,
        TRIP_MERGED_MIXED_IDENTITY,
        TRIP_DATES_MISSING,
    }
)


@dataclass
class TripLeg:
    """One leg of a trip, deep-copied from its winning filing's segment."""

    segment: TravelSegment
    report_id: str
    source_file: str


@dataclass
class TripFiling:
    """One filing that reported this trip, in rank (newest-first) order."""

    report_id: str
    source_file: str
    sponsor_type: str
    sponsor_name: str
    sponsor_code: Optional[str]
    publication_date: Optional[date]
    amended: bool


@dataclass
class Trip:
    """One person's journey: winning-filing fields, gap-filled from older filings."""

    trip_id: str
    name: str
    honorific: Optional[str]
    bioguide_id: Optional[str]
    sponsor_type: str
    sponsor_name: str
    sponsor_code: Optional[str]
    start_date: Optional[date]
    end_date: Optional[date]
    countries: list[str]
    legs: list[TripLeg]
    filings: list[TripFiling]
    n_filings: int
    source_report_ids: list[str]
    source_files: list[str]
    publication_date: Optional[date]
    amended: bool
    military_air: bool
    per_diem_usd: Optional[Decimal]
    transportation_usd: Optional[Decimal]
    other_usd: Optional[Decimal]
    total_usd: Optional[Decimal]
    flags: list[str]
    notes: list[str]
    # Trip ids of the other side(s) of any TRIP_DATE_DESTINATION_CONFLICT.
    conflict_with: list[str] = field(default_factory=list)

    @property
    def n_legs(self) -> int:
        return len(self.legs)


@dataclass
class _TripCandidate:
    """One (report, person) journey: the unit of cross-filing matching."""

    index: int
    person_key: str
    name_tokens: tuple[str, str]
    name: str
    honorific: Optional[str]
    bioguide_id: Optional[str]
    report: Report
    publication_date: Optional[date]
    amended: bool
    table_index: int
    # Deep copies, chronologically ordered; mutated only by gap-fill.
    segments: list[TravelSegment]
    start_date: Optional[date]
    end_date: Optional[date]
    countries: list[str]
    countries_set: frozenset[str]
    split_from_gap: bool


def _person_key(traveler: Traveler) -> Optional[tuple[str, tuple[str, str]]]:
    """(person_key, (first, last)) or None when the row has no usable identity.

    Token-based rather than full-string so filings that print "Hon. Alcee L.
    Hastings" and "Hon. Alcee Hastings" collide. ``_query_name_tokens``
    already strips honorifics, parentheticals, suffix tokens, and gunk.
    """
    first, last = _query_name_tokens(traveler.name)
    first, last = first.lower(), last.lower()
    if not first and not last:
        return None
    if traveler.bioguide_id:
        return (f"bg:{traveler.bioguide_id}", (first, last))
    return (f"nm:{first}|{last}", (first, last))


def _norm_country(country: str) -> str:
    return re.sub(r"\s+", " ", country.strip().rstrip(".")).upper()


def _destinations_overlap(a: frozenset[str], b: frozenset[str]) -> bool:
    """Whether two destination sets plausibly describe the same place.

    Permissive when either side is empty (missing data shouldn't block a
    date-overlap merge). Exact match, or mutual substring containment for
    strings >= COUNTRY_SUBSTRING_MIN_LEN (handles split_countries gunk like
    "SWITZERLAND 31" vs "SWITZERLAND").
    """
    if not a or not b:
        return True
    if a & b:
        return True
    for x in a:
        if len(x) < COUNTRY_SUBSTRING_MIN_LEN:
            continue
        for y in b:
            if x in y or (len(y) >= COUNTRY_SUBSTRING_MIN_LEN and y in x):
                return True
    return False


def _spans_overlap(a: _TripCandidate, b: _TripCandidate) -> bool:
    if a.start_date is None or a.end_date is None or b.start_date is None or b.end_date is None:
        return False
    return a.start_date <= b.end_date and b.start_date <= a.end_date


def _journeys(
    segments: list[TravelSegment], seg_pos: dict[int, int]
) -> tuple[list[list[TravelSegment]], bool]:
    """Split one person's segments (within one report) into date-contiguous journeys.

    Dated segments are sorted by (arrival, original list position) -- source
    tables are not always chronological -- and chained, splitting when the
    next arrival is more than TRIP_SPLIT_GAP_DAYS after the running
    journey's latest departure (overlapping legs never split). Undated
    segments attach to the journey of their nearest dated neighbor by
    original list position (ties go to the previous journey). Returns
    (journeys, whether the group split into 2+ journeys).

    ``seg_pos`` maps ``id(segment)`` to the position in the traveler's
    original (unsplit) segment list.
    """
    dated = sorted(
        (s for s in segments if s.arrival_date is not None),
        key=lambda s: (s.arrival_date, seg_pos[id(s)]),
    )
    if not dated:
        return [list(segments)], False

    journeys: list[list[TravelSegment]] = [[dated[0]]]
    for seg in dated[1:]:
        departures = [s.departure_date for s in journeys[-1] if s.departure_date is not None]
        arrival = seg.arrival_date
        gap = (arrival - max(departures)).days if departures and arrival is not None else -1
        if gap > TRIP_SPLIT_GAP_DAYS:
            journeys.append([seg])
        else:
            journeys[-1].append(seg)

    journey_of = {seg_pos[id(s)]: j for j, journey in enumerate(journeys) for s in journey}
    for seg in segments:
        if seg.arrival_date is not None:
            continue
        pos = seg_pos[id(seg)]
        for offset in range(1, len(segments) + 1):
            if pos - offset in journey_of:
                journeys[journey_of[pos - offset]].append(seg)
                break
            if pos + offset in journey_of:
                journeys[journey_of[pos + offset]].append(seg)
                break
    return journeys, len(journeys) > 1


def _order_legs(segments: list[TravelSegment], seg_pos: dict[int, int]) -> list[TravelSegment]:
    """Chronological order: by (effective start, position), undated legs last.

    The effective start is the arrival, or the departure when the arrival is
    missing (recovered US legs and gap-fill candidates), so a departure-only
    leg still sorts into its place in the journey.
    """
    dated = sorted(
        (s for s in segments if s.arrival_date is not None or s.departure_date is not None),
        key=lambda s: (s.arrival_date or s.departure_date or date.min, seg_pos[id(s)]),
    )
    undated = sorted(
        (s for s in segments if s.arrival_date is None and s.departure_date is None),
        key=lambda s: seg_pos[id(s)],
    )
    return dated + undated


def _span(segments: list[TravelSegment]) -> tuple[Optional[date], Optional[date]]:
    """(start, end) across a journey's segments, with arrival/departure fallbacks."""
    arrivals = [s.arrival_date for s in segments if s.arrival_date is not None]
    departures = [s.departure_date for s in segments if s.departure_date is not None]
    if not arrivals and not departures:
        return None, None
    starts = arrivals or departures
    ends = departures or arrivals
    return min(starts), max(ends)


def _countries(segments: list[TravelSegment]) -> list[str]:
    """Normalized, deduplicated countries in leg order."""
    out: list[str] = []
    for seg in segments:
        for country in seg.countries:
            norm = _norm_country(country)
            if norm and norm not in out:
                out.append(norm)
    return out


def _collect_candidates(
    reports: list[Report],
) -> tuple[list[_TripCandidate], dict[tuple[str, str], set[str]]]:
    """Phase 0+1: one candidate per (report, person, journey)."""
    report_by_id = {r.report_id: r for r in reports}
    appearances: dict[tuple[str, str], list[Traveler]] = {}
    bioguide_by_tokens: dict[tuple[str, str], set[str]] = {}
    for report in reports:
        for traveler in report.travelers:
            if not traveler.segments:
                continue
            if all(any(f in NON_PERSON_SEGMENT_FLAGS for f in s.flags) for s in traveler.segments):
                continue
            key = _person_key(traveler)
            if key is None:
                continue
            appearances.setdefault((report.report_id, key[0]), []).append(traveler)
            if traveler.bioguide_id:
                bioguide_by_tokens.setdefault(key[1], set()).add(traveler.bioguide_id)

    candidates: list[_TripCandidate] = []
    for (report_id, person_key), travelers in appearances.items():
        report = report_by_id[report_id]
        all_segments = [s for t in travelers for s in t.segments]
        seg_pos = {id(s): i for i, s in enumerate(all_segments)}
        journeys, split = _journeys(all_segments, seg_pos)
        # Prefer the bioguide-bearing traveler's printed name as canonical.
        rep = next((t for t in travelers if t.bioguide_id), travelers[0])
        rep_first, rep_last = _query_name_tokens(rep.name)
        rep_tokens = (rep_first.lower(), rep_last.lower())
        for journey in journeys:
            segments = [s.model_copy(deep=True) for s in _order_legs(journey, seg_pos)]
            start, end = _span(segments)
            countries = _countries(segments)
            candidates.append(
                _TripCandidate(
                    index=len(candidates),
                    person_key=person_key,
                    name_tokens=rep_tokens,
                    name=rep.name,
                    honorific=rep.honorific,
                    bioguide_id=rep.bioguide_id,
                    report=report,
                    publication_date=parse_publication_date(report.source_file),
                    amended=report.amended,
                    table_index=report.table_index,
                    segments=segments,
                    start_date=start,
                    end_date=end,
                    countries=countries,
                    countries_set=frozenset(countries),
                    split_from_gap=split,
                )
            )
    return candidates, bioguide_by_tokens


def _trip_rank(candidate: _TripCandidate) -> tuple[date, bool, int, int]:
    """Higher rank wins: mirrors dedup._rank, with creation order as the
    final deterministic tiebreak (within one file, table_index is unique)."""
    return (
        candidate.publication_date or date.min,
        candidate.amended,
        candidate.table_index,
        -candidate.index,
    )


def _leg_matches(seg: TravelSegment, other: TravelSegment) -> bool:
    """Whether an older filing's leg can gap-fill the winner's leg."""
    if seg.arrival_date is not None and seg.arrival_date == other.arrival_date:
        return True
    if seg.departure_date is not None and seg.departure_date == other.departure_date:
        return True
    if (
        seg.countries
        and other.countries
        and _destinations_overlap(
            frozenset(_norm_country(c) for c in seg.countries),
            frozenset(_norm_country(c) for c in other.countries),
        )
    ):
        return True
    return False


def _sum_usd(segments: list[TravelSegment], group: str) -> Optional[Decimal]:
    """Sum one USD cost category over the winning filing's legs (None-skipping)."""
    amounts = [a for s in segments if (a := getattr(s.costs, group).us_dollar.amount) is not None]
    return sum(amounts) if amounts else None


def _trip_id(
    person_part: str,
    start: Optional[date],
    end: Optional[date],
    person_key: str,
    countries: list[str],
) -> str:
    date_part = start.strftime("%Y%m%d") if start else "undated"
    digest_source = f"{person_key}|{start or ''}|{end or ''}|{'|'.join(countries)}"
    digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:TRIP_ID_HASH_LEN]
    return f"{person_part}-{date_part}-{digest}"


def build_trips(reports: list[Report]) -> list[Trip]:
    """Group traveler appearances into canonical per-person trip records.

    Matching: same person (bioguide, else name tokens) + overlapping date
    spans + overlapping destinations. A bare-name candidate whose name
    tokens map to exactly one bioguide also merges with that member's
    bioguide-keyed candidates (TRIP_MERGED_MIXED_IDENTITY). Candidates whose
    dates overlap but destinations disagree stay separate and both get
    TRIP_DATE_DESTINATION_CONFLICT. Undated candidates never merge.
    """
    candidates, bioguide_by_tokens = _collect_candidates(reports)

    # Phase 2: union-find within each person key, mirroring dedup._cluster.
    parent = list(range(len(candidates)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    by_key: dict[str, list[int]] = {}
    for c in candidates:
        by_key.setdefault(c.person_key, []).append(c.index)

    conflict_candidates: set[int] = set()
    conflict_pairs: list[tuple[int, int]] = []
    for indices in by_key.values():
        for a_pos in range(len(indices)):
            for b_pos in range(a_pos + 1, len(indices)):
                a, b = candidates[indices[a_pos]], candidates[indices[b_pos]]
                if not _spans_overlap(a, b):
                    continue
                if _destinations_overlap(a.countries_set, b.countries_set):
                    union(a.index, b.index)
                elif a.countries_set and b.countries_set:
                    conflict_candidates.add(a.index)
                    conflict_candidates.add(b.index)
                    conflict_pairs.append((a.index, b.index))

    # Secondary mixed-identity pass: a bare-name candidate whose tokens map
    # to exactly one bioguide can merge with that member's bioguide-keyed
    # candidates. Ambiguous name tokens (two members) never merge.
    mixed_participants: set[int] = set()
    bg_candidates: dict[str, list[_TripCandidate]] = {}
    for c in candidates:
        if c.person_key.startswith("bg:") and c.bioguide_id:
            bg_candidates.setdefault(c.bioguide_id, []).append(c)
    for c in candidates:
        if not c.person_key.startswith("nm:"):
            continue
        bioguides = bioguide_by_tokens.get(c.name_tokens, set())
        if len(bioguides) != 1:
            continue
        for g in bg_candidates.get(next(iter(bioguides)), []):
            if g.name_tokens != c.name_tokens:
                continue
            if _spans_overlap(c, g) and _destinations_overlap(c.countries_set, g.countries_set):
                union(c.index, g.index)
                mixed_participants.add(c.index)
                mixed_participants.add(g.index)

    conflict_roots = {find(i) for i in conflict_candidates}
    mixed_roots = {find(i) for i in mixed_participants}

    # Phase 3: assemble each cluster, newest filing first.
    clusters: dict[int, list[_TripCandidate]] = {}
    for c in candidates:
        clusters.setdefault(find(c.index), []).append(c)

    trips: list[Trip] = []
    # (person_key, name_tokens) of each trip's winning candidate, for ids.
    id_meta: list[tuple[str, tuple[str, str]]] = []
    # Cluster root -> index into `trips`, for conflict cross-linking.
    trip_of_root: dict[int, int] = {}
    for members in clusters.values():
        ranked = sorted(members, key=_trip_rank, reverse=True)
        winner = ranked[0]
        legs = [
            TripLeg(
                segment=s, report_id=winner.report.report_id, source_file=winner.report.source_file
            )
            for s in winner.segments
        ]

        gap_filled = False
        for leg in legs:
            seg = leg.segment
            if seg.arrival_date is not None and seg.departure_date is not None and seg.countries:
                continue
            # Fill from the first matching leg of the first older filing.
            for source in ranked[1:]:
                matched = False
                for other in source.segments:
                    if not _leg_matches(seg, other):
                        continue
                    matched = True
                    if seg.arrival_date is None and other.arrival_date is not None:
                        seg.arrival_date = other.arrival_date
                        gap_filled = True
                    if seg.departure_date is None and other.departure_date is not None:
                        seg.departure_date = other.departure_date
                        gap_filled = True
                    if not seg.countries and other.countries:
                        seg.countries = list(other.countries)
                        gap_filled = True
                    break
                if matched:
                    break

        start, end = _span([leg.segment for leg in legs])
        bioguide_id = winner.bioguide_id or next(
            (c.bioguide_id for c in ranked[1:] if c.bioguide_id), None
        )
        countries = winner.countries or next((c.countries for c in ranked[1:] if c.countries), [])
        flags: list[str] = []
        if len(ranked) > 1:
            flags.append(TRIP_MERGED_ACROSS_FILINGS)
        if len(ranked) > 1 and not any(c.bioguide_id for c in ranked):
            flags.append(TRIP_MATCHED_NAME_ONLY)
        if find(winner.index) in mixed_roots:
            flags.append(TRIP_MERGED_MIXED_IDENTITY)
        if find(winner.index) in conflict_roots:
            flags.append(TRIP_DATE_DESTINATION_CONFLICT)
        if start is None:
            flags.append(TRIP_DATES_MISSING)
        if winner.split_from_gap:
            flags.append(TRIP_SPLIT_BY_DATE_GAP)
        if gap_filled:
            flags.append(TRIP_GAP_FILLED_FROM_EARLIER_FILING)

        leg_segments = [leg.segment for leg in legs]
        trip_of_root[find(winner.index)] = len(trips)
        trips.append(
            Trip(
                trip_id="",  # assigned after assembly, once fields are final
                name=winner.name,
                honorific=winner.honorific,
                bioguide_id=bioguide_id,
                sponsor_type=winner.report.sponsor.type,
                sponsor_name=winner.report.sponsor.name,
                sponsor_code=winner.report.sponsor.code,
                start_date=start,
                end_date=end,
                countries=countries,
                legs=legs,
                filings=[
                    TripFiling(
                        report_id=c.report.report_id,
                        source_file=c.report.source_file,
                        sponsor_type=c.report.sponsor.type,
                        sponsor_name=c.report.sponsor.name,
                        sponsor_code=c.report.sponsor.code,
                        publication_date=c.publication_date,
                        amended=c.report.amended,
                    )
                    for c in ranked
                ],
                n_filings=len({c.report.report_id for c in ranked}),
                source_report_ids=sorted({c.report.report_id for c in ranked}),
                source_files=sorted({c.report.source_file for c in ranked}),
                publication_date=winner.publication_date,
                amended=winner.amended,
                military_air=any(
                    cell.military_air
                    for s in leg_segments
                    for group in (
                        s.costs.per_diem,
                        s.costs.transportation,
                        s.costs.other,
                        s.costs.total,
                    )
                    for cell in (group.foreign_currency, group.us_dollar)
                ),
                per_diem_usd=_sum_usd(leg_segments, "per_diem"),
                transportation_usd=_sum_usd(leg_segments, "transportation"),
                other_usd=_sum_usd(leg_segments, "other"),
                total_usd=_sum_usd(leg_segments, "total"),
                flags=flags,
                notes=[n for s in leg_segments for n in s.notes],
            )
        )
        id_meta.append((winner.person_key, winner.name_tokens))

    # Phase 4: deterministic trip ids (no clock, no randomness). Creation
    # order decides which of two identical bases keeps the bare id.
    seen: dict[str, int] = {}
    for trip, (person_key, name_tokens) in zip(trips, id_meta):
        if trip.bioguide_id:
            person_part = trip.bioguide_id
        else:
            person_part = re.sub(r"[^a-z0-9]+", "-", " ".join(name_tokens)).strip("-") or "unknown"
        base = _trip_id(person_part, trip.start_date, trip.end_date, person_key, trip.countries)
        seen[base] = seen.get(base, 0) + 1
        trip.trip_id = base if seen[base] == 1 else f"{base}-{seen[base]}"

    # Cross-link conflict pairs so each side can name the other. A pair whose
    # members ended up in one cluster via a third candidate (e.g. a merged
    # EUROPE+DENMARK filing overlapping both sides) is a transient
    # disagreement -- that single trip keeps the conflict flag but gets no
    # self-link. trip_of_root's values index the pre-sort trips list, so this
    # pass must run before the final sort (the ids it stores survive it).
    partners_by_trip: dict[int, set[str]] = {}
    for a_index, b_index in conflict_pairs:
        root_a, root_b = find(a_index), find(b_index)
        if root_a == root_b:
            continue
        trip_a, trip_b = trip_of_root[root_a], trip_of_root[root_b]
        partners_by_trip.setdefault(trip_a, set()).add(trips[trip_b].trip_id)
        partners_by_trip.setdefault(trip_b, set()).add(trips[trip_a].trip_id)
    for trip_index, partners in partners_by_trip.items():
        trips[trip_index].conflict_with = sorted(partners)

    trips.sort(key=lambda t: (t.start_date or date.max, t.name, t.trip_id))
    return trips
