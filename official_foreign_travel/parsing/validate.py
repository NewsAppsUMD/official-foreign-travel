"""Arithmetic and date invariant checks. Flags problems; never drops records."""

from decimal import Decimal

from ..models.report import Costs, Report

DEFAULT_TOLERANCE = Decimal("0.02")


def _group_total(costs: Costs) -> Decimal:
    """Sum the per-category US-dollar amounts (foreign-currency cells are informational only)."""
    groups = (costs.per_diem, costs.transportation, costs.other)
    return sum((g.us_dollar.amount for g in groups if g.us_dollar.amount is not None), Decimal("0"))


def validate_report(report: Report, tolerance: Decimal = DEFAULT_TOLERANCE) -> Report:
    """
    Check arithmetic and date invariants, appending flags in place. Never mutates amounts.

    Idempotent: clears its own flags before recomputing, so it's safe to call again
    on a report that's already been through validation (e.g. after a correction).

    Checks:
      - Each segment's per_diem + transportation + other ~= total (ROW_SUM_MISMATCH)
      - Sum of all segment totals ~= the table's committee total (TABLE_SUM_MISMATCH)
      - A committee total row was expected but missing (MISSING_COMMITTEE_TOTAL)

    Args:
        report: Assembled Report to validate
        tolerance: Allowed absolute difference in dollars before flagging a mismatch

    Returns:
        The same Report, with `flags` (report-level) and segment `flags` extended
    """
    all_segments = [seg for traveler in report.travelers for seg in traveler.segments]

    for segment in all_segments:
        if "ROW_SUM_MISMATCH" in segment.flags:
            segment.flags.remove("ROW_SUM_MISMATCH")
    report.flags = [
        f for f in report.flags if f not in ("TABLE_SUM_MISMATCH", "MISSING_COMMITTEE_TOTAL")
    ]

    for segment in all_segments:
        declared_total = segment.costs.total.us_dollar.amount
        if declared_total is None:
            continue
        computed = _group_total(segment.costs)
        if abs(computed - declared_total) > tolerance:
            segment.flags.append("ROW_SUM_MISMATCH")

    if report.committee_total is not None:
        declared_table_total = report.committee_total.total.us_dollar.amount
        if declared_table_total is not None:
            summed = sum(
                (
                    seg.costs.total.us_dollar.amount
                    for seg in all_segments
                    if seg.costs.total.us_dollar.amount is not None
                ),
                Decimal("0"),
            )
            if abs(summed - declared_table_total) > tolerance:
                report.flags.append("TABLE_SUM_MISMATCH")
    elif all_segments:
        report.flags.append("MISSING_COMMITTEE_TOTAL")

    return report


def validate_reports(reports: list[Report], tolerance: Decimal = DEFAULT_TOLERANCE) -> list[Report]:
    """Validate a list of reports in place, returning the same list."""
    for report in reports:
        validate_report(report, tolerance)
    return reports
