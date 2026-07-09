"""Tests for arithmetic/date invariant validation."""

from decimal import Decimal

from official_foreign_travel.models.report import (
    CostCell,
    CostGroup,
    Costs,
    Report,
    Sponsor,
    Traveler,
    TravelSegment,
)
from official_foreign_travel.parsing.validate import validate_report


def cell(amount=None):
    return CostCell(amount=Decimal(amount) if amount is not None else None, raw=str(amount))


def costs(per_diem=None, transportation=None, other=None, total=None):
    empty = cell()
    return Costs(
        per_diem=CostGroup(foreign_currency=empty, us_dollar=cell(per_diem)),
        transportation=CostGroup(foreign_currency=empty, us_dollar=cell(transportation)),
        other=CostGroup(foreign_currency=empty, us_dollar=cell(other)),
        total=CostGroup(foreign_currency=empty, us_dollar=cell(total)),
    )


def segment(total_costs):
    return TravelSegment(
        arrival_raw="1/1", departure_raw="1/2", country_raw="Country", costs=total_costs
    )


def report(travelers, committee_total=None):
    return Report(
        report_id="test-000",
        source_file="test.txt",
        table_index=0,
        sponsor=Sponsor(type="committee", name="COMMITTEE ON TEST", raw="COMMITTEE ON TEST"),
        header_raw="",
        travelers=travelers,
        committee_total=committee_total,
    )


class TestValidateReport:
    def test_matching_row_sum_no_flag(self):
        seg = segment(costs(per_diem="100.00", transportation="50.00", total="150.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_mismatched_row_sum_flagged(self):
        seg = segment(costs(per_diem="100.00", transportation="50.00", total="999.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_SUM_MISMATCH" in seg.flags

    def test_missing_total_cell_not_flagged(self):
        """A segment with no declared total (all dots) can't be checked -- not an error."""
        seg = segment(costs(per_diem="100.00", transportation="50.00", total=None))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_table_sum_matches_committee_total(self):
        seg1 = segment(costs(per_diem="100.00", total="100.00"))
        seg2 = segment(costs(per_diem="50.00", total="50.00"))
        total = costs(per_diem="150.00", total="150.00")
        r = report([Traveler(name="A", segments=[seg1, seg2])], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_MISMATCH" not in r.flags

    def test_table_sum_mismatch_flagged(self):
        """Mirrors a real 1996 source document where the total column doesn't match its own rows."""
        seg = segment(costs(per_diem="100.00", total="100.00"))
        total = costs(per_diem="100.00", total="999.00")
        r = report([Traveler(name="A", segments=[seg])], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_MISMATCH" in r.flags

    def test_missing_committee_total_flagged(self):
        seg = segment(costs(per_diem="100.00", total="100.00"))
        r = report([Traveler(name="A", segments=[seg])], committee_total=None)
        validate_report(r)
        assert "MISSING_COMMITTEE_TOTAL" in r.flags

    def test_no_segments_no_missing_total_flag(self):
        """An empty report (e.g. zero-expenditure quarter) isn't flagged for lacking a total."""
        r = report([], committee_total=None)
        validate_report(r)
        assert "MISSING_COMMITTEE_TOTAL" not in r.flags
