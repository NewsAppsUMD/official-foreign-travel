"""Deduplicate amended reports: keep the latest publication per sponsor+period.

Per about_the_data.md, amended reports substantially duplicate an earlier
report for the same sponsor and period; the most recent publication should
be treated as authoritative. Nothing is deleted -- superseded reports are
marked with `superseded_by` and excluded from flat exports by default.
"""

import re
from datetime import date
from typing import Optional

from ..models.report import Report
from .months import month_num

FILENAME_RE = re.compile(r"^(\d{4})q\d([a-z]+)(\d{1,2})", re.IGNORECASE)

# Two same-sponsor-same-period reports are only true duplicates if one is
# explicitly marked amended, or their traveler rosters substantially overlap.
# Some committees (e.g. Appropriations subcommittees) file multiple genuinely
# distinct reports under the same generic sponsor label for the same quarter;
# without this check they'd wrongly supersede each other and lose real data.
ROSTER_OVERLAP_THRESHOLD = 0.5


def parse_publication_date(source_file: str) -> Optional[date]:
    """Parse a report filename like '2018q4nov16.txt' into its publication date."""
    match = FILENAME_RE.match(source_file)
    if not match:
        return None
    year, month_name, day = match.groups()
    month = month_num(month_name)
    if month is None:
        return None
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def _dedup_key(report: Report) -> Optional[tuple[str, str, date, date]]:
    if report.period is None or report.period.start is None or report.period.end is None:
        return None
    return (
        report.sponsor.type,
        report.sponsor.name.strip().upper(),
        report.period.start,
        report.period.end,
    )


def _rank(report: Report) -> tuple[date, bool, int]:
    """Higher rank wins: later publication date, then amended, then later file/table order."""
    pub_date = parse_publication_date(report.source_file) or date.min
    return (pub_date, report.amended, report.table_index)


def _traveler_name_set(report: Report) -> set[str]:
    return {t.name.strip().upper() for t in report.travelers if t.name.strip()}


def _is_duplicate_pair(a: Report, b: Report) -> bool:
    """Whether two same-sponsor-same-period reports are actually the same content."""
    if a.amended or b.amended:
        return True
    names_a, names_b = _traveler_name_set(a), _traveler_name_set(b)
    if not names_a or not names_b:
        return False
    overlap = len(names_a & names_b)
    smaller = min(len(names_a), len(names_b))
    return (overlap / smaller) >= ROSTER_OVERLAP_THRESHOLD


def _cluster(indices: list[int], reports: list[Report]) -> list[list[int]]:
    """Group indices into clusters of mutually-duplicate reports (union-find)."""
    parent: dict[int, int] = {i: i for i in indices}

    def find(i: int) -> int:
        while parent[i] != i:
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for a_idx in range(len(indices)):
        for b_idx in range(a_idx + 1, len(indices)):
            i, j = indices[a_idx], indices[b_idx]
            if _is_duplicate_pair(reports[i], reports[j]):
                union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in indices:
        clusters.setdefault(find(i), []).append(i)
    return list(clusters.values())


def dedup_reports(reports: list[Report]) -> list[Report]:
    """
    Mark superseded reports among true duplicates sharing a sponsor+period.

    Args:
        reports: All assembled reports, in file order

    Returns:
        The same list, with `superseded_by` set on losing duplicates.
        Reports with no resolvable period, or that share a sponsor+period
        but have disjoint traveler rosters and no amended flag (distinct
        reports filed under the same generic sponsor label), are left alone.
    """
    candidate_groups: dict[tuple, list[int]] = {}
    for index, report in enumerate(reports):
        key = _dedup_key(report)
        if key is None:
            continue
        candidate_groups.setdefault(key, []).append(index)

    for indices in candidate_groups.values():
        if len(indices) < 2:
            continue
        for cluster_indices in _cluster(indices, reports):
            if len(cluster_indices) < 2:
                continue
            winner_index = max(cluster_indices, key=lambda i: _rank(reports[i]))
            winner_id = reports[winner_index].report_id
            for i in cluster_indices:
                if i != winner_index:
                    reports[i].superseded_by = winner_id

    return reports
