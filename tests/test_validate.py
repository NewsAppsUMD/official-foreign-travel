"""Tests for arithmetic/date invariant validation."""

from datetime import date
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
    # When amount is None, raw is the dot-fill convention used by the source
    # for empty cells; otherwise raw mirrors the parsed amount.
    if amount is None:
        return CostCell(amount=None, raw="...........")
    return CostCell(amount=Decimal(amount), raw=str(amount))


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
        # Positive delta (999 - 150 = 849) → ROW_TOTAL_INCLUDES_UNBROKEN_COSTS
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in seg.flags

    def test_missing_total_cell_not_flagged_as_mismatch(self):
        """A segment with no declared total (all dots) can't be checked -- not a mismatch."""
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
        """A genuine mismatch -- ct components don't match segment components
        and ct_total doesn't match either -- is flagged TABLE_SUM_MISMATCH."""
        seg = segment(costs(per_diem="100.00", total="100.00"))
        total = costs(per_diem="150.00", total="999.00")
        r = report([Traveler(name="A", segments=[seg])], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_MISMATCH" in r.flags

    def test_committee_total_computed_when_components_match_segments(self):
        """When the committee total row's TOTAL cell doesn't match its own
        components but its components DO sum to the segment components, the
        total cell is wrong (layout digit-shift, comma-decimal typo, or source
        typo) and is recovered from the components. Mirrors ROW_TOTAL_COMPUTED
        at the table level. Real example: 1997q2jun17-002 has ct_total
        `71,882.24` (layout dropped the leading `1`) but ct components and
        segment components both sum to `171,882.24`."""
        seg = segment(costs(per_diem="100.00", total="100.00"))
        total = costs(per_diem="100.00", total="999.00")
        r = report([Traveler(name="A", segments=[seg])], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_MISMATCH" not in r.flags
        assert "COMMITTEE_TOTAL_COMPUTED" in r.flags
        assert r.committee_total.total.us_dollar.amount == Decimal("100.00")
        assert r.committee_total.total.us_dollar.computed is True
        assert r.committee_total.total.us_dollar.source_amount == Decimal("999.00")

    def test_committee_total_computed_idempotent(self):
        """Re-validating a report with a previously-computed committee total
        re-tags COMMITTEE_TOTAL_COMPUTED from the `computed` marker."""
        seg = segment(costs(per_diem="100.00", total="100.00"))
        total = costs(per_diem="100.00", total="999.00")
        r = report([Traveler(name="A", segments=[seg])], committee_total=total)
        validate_report(r)
        assert "COMMITTEE_TOTAL_COMPUTED" in r.flags
        # Second pass: amount is already the computed sum, so the recovery
        # condition won't fire. The flag must be re-added from `computed`.
        validate_report(r)
        assert "COMMITTEE_TOTAL_COMPUTED" in r.flags
        assert r.flags.count("COMMITTEE_TOTAL_COMPUTED") == 1

    def test_missing_committee_total_flagged(self):
        """A report with segments but no cost cells in any segment can't
        have a ct inferred -- stays MISSING_COMMITTEE_TOTAL. (When segs
        do have costs, the ct is inferred -- see
        TestCommitteeTotalInferredFromSegments.)"""
        seg = segment(costs())
        r = report([Traveler(name="A", segments=[seg])], committee_total=None)
        validate_report(r)
        assert "MISSING_COMMITTEE_TOTAL" in r.flags

    def test_no_segments_no_missing_total_flag(self):
        """An empty report (e.g. zero-expenditure quarter) isn't flagged for lacking a total."""
        r = report([], committee_total=None)
        validate_report(r)
        assert "MISSING_COMMITTEE_TOTAL" not in r.flags


class TestTableSumClassification:
    """TABLE_SUM_MISMATCH is downgraded to a specific informational flag when
    the mismatch matches a known source convention or recovery artifact."""

    def test_rounding_delta_flagged_as_rounding(self):
        """A small delta (within $5 or 1% of the declared total) is rounding
        accumulation across many segments, not a genuine mismatch."""
        segs = [segment(costs(per_diem="100.00", total="100.00")) for _ in range(10)]
        # Sum = 1000.00, declared = 1000.50, delta = 0.50 (rounding)
        total = costs(per_diem="1000.00", total="1000.50")
        r = report([Traveler(name="A", segments=segs)], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_ROUNDING" in r.flags
        assert "TABLE_SUM_MISMATCH" not in r.flags

    def test_large_delta_not_rounding(self):
        """A delta > $5 and > 1% of the declared total is NOT rounding."""
        seg = segment(costs(per_diem="75.00", total="75.00"))
        # Sum = 75, declared = 200, delta = -125 (63% — genuine mismatch,
        # and 125 doesn't match any component amount so it's not COMPONENT_DELTA).
        # ct_per_diem=200 so ct_components (200) != seg_components (75), which
        # means the COMMITTEE_TOTAL_COMPUTED recovery does NOT fire -- this is
        # a genuine mismatch where the ct row and seg row disagree on per_diem.
        total = costs(per_diem="200.00", total="200.00")
        r = report([Traveler(name="A", segments=[seg])], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_MISMATCH" in r.flags
        assert "TABLE_SUM_ROUNDING" not in r.flags

    def test_rounding_threshold_capped_at_1_percent(self):
        """For small totals, the rounding threshold is 1% (not $5), so a $3
        delta on a $103 total (1.5%) is NOT rounding."""
        seg = segment(costs(per_diem="100.00", total="100.00"))
        # Sum = 100, declared = 103, delta = 3. 1% of 103 = 1.03. $3 > $1.03.
        # ct_per_diem=103 so ct_components != seg_components, which means the
        # COMMITTEE_TOTAL_COMPUTED recovery does NOT fire -- genuine mismatch.
        total = costs(per_diem="103.00", total="103.00")
        r = report([Traveler(name="A", segments=[seg])], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_MISMATCH" in r.flags
        assert "TABLE_SUM_ROUNDING" not in r.flags

    def test_transport_excluded_from_total(self):
        """The source excludes transportation from the committee total
        (DoD-provided transport not counted). declared = per_diem + other."""
        seg1 = segment(costs(per_diem="100.00", transportation="500.00", total="600.00"))
        seg2 = segment(costs(per_diem="50.00", transportation="300.00", total="350.00"))
        # Sum of totals = 950, but declared = 150 (= per_diem + other, no transport)
        total = costs(per_diem="150.00", total="150.00")
        r = report([Traveler(name="A", segments=[seg1, seg2])], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_TRANSPORT_EXCLUDED" in r.flags
        assert "TABLE_SUM_MISMATCH" not in r.flags

    def test_component_delta_flagged(self):
        """|delta| exactly matches one segment's per_diem -- a table-level
        double-count or component exclusion."""
        seg1 = segment(costs(per_diem="100.00", total="100.00"))
        seg2 = segment(costs(per_diem="200.00", total="200.00"))
        # Sum = 300, declared = 200, delta = 100 = seg1.per_diem
        total = costs(per_diem="300.00", total="200.00")
        r = report([Traveler(name="A", segments=[seg1, seg2])], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_COMPONENT_DELTA" in r.flags
        assert "TABLE_SUM_MISMATCH" not in r.flags

    def test_supplement_explained_table_mismatch(self):
        """When a supplement merge inflated segment totals, the pre-supplement
        sum (preserved in source_amount) should match the declared committee
        total. The mismatch is an artifact of the recovery, not a source error."""
        seg = segment(costs(per_diem="100.00", transportation="50.00", total="100.00"))
        seg.flags.append("COST_SUPPLEMENT_MERGED")
        # After validation, seg.total will be 150.00 (computed) with
        # source_amount=100.00 (original). Committee total = 100.00
        # (pre-supplement). The pre-supplement sum (100.00) matches.
        total = costs(per_diem="100.00", total="100.00")
        r = report([Traveler(name="A", segments=[seg])], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_EXPLAINED_BY_SUPPLEMENT" in r.flags
        assert "TABLE_SUM_MISMATCH" not in r.flags
        assert seg.costs.total.us_dollar.source_amount == Decimal("100.00")

    def test_row_total_flags_explain_table_mismatch(self):
        """When the table-level delta is fully explained by the sum of
        per-segment `total - component_sum` residuals on segments flagged
        with row-total-explanatory flags (ROW_TOTAL_INCLUDES_UNBROKEN_COSTS,
        ROW_TOTAL_LESS_THAN_COMPONENT, ROW_NO_COMPONENT_BREAKDOWN, etc.),
        the mismatch is downgraded to TABLE_SUM_EXPLAINED_BY_ROW_TOTAL_FLAGS.
        Real example: 1995q1feb09-023 has 4 segments flagged
        ROW_TOTAL_INCLUDES_UNBROKEN_COSTS / ROW_NO_COMPONENT_BREAKDOWN whose
        residuals sum to exactly the table-level delta."""
        # Segment with source-declared total > component sum (source included
        # costs not broken out). Validator keeps the source total and flags
        # ROW_TOTAL_INCLUDES_UNBROKEN_COSTS. Transportation is populated so
        # the transport-excluded classifier doesn't fire.
        seg = segment(costs(per_diem="173.00", transportation="500.00", total="1176.15"))
        seg.flags.append("ROW_TOTAL_INCLUDES_UNBROKEN_COSTS")
        # Committee total = component sum = 673.00. sum_seg_totals = 1176.15.
        # delta = 1176.15 - 673.00 = 503.15 = seg residual. Fully explained.
        total = costs(per_diem="173.00", transportation="500.00", total="673.00")
        r = report([Traveler(name="A", segments=[seg])], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_EXPLAINED_BY_ROW_TOTAL_FLAGS" in r.flags
        assert "TABLE_SUM_MISMATCH" not in r.flags

    def test_row_total_flags_do_not_explain_partial_mismatch(self):
        """When row-total-flag residuals only partially explain the delta,
        the mismatch stays TABLE_SUM_MISMATCH -- the residual is a separate
        unexplained error."""
        seg = segment(costs(per_diem="173.00", transportation="500.00", total="1176.15"))
        seg.flags.append("ROW_TOTAL_INCLUDES_UNBROKEN_COSTS")
        # Committee total = 700.00 with ct_per_diem=200 (not 173). ct_components
        # (700) != seg_components (673), so COMMITTEE_TOTAL_COMPUTED does NOT
        # fire. delta = 1176.15 - 700 = 476.15. seg residual = 503.15.
        # Residual = 27.00 (unexplained) -- not fully explained.
        total = costs(per_diem="200.00", transportation="500.00", total="700.00")
        r = report([Traveler(name="A", segments=[seg])], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_MISMATCH" in r.flags
        assert "TABLE_SUM_EXPLAINED_BY_ROW_TOTAL_FLAGS" not in r.flags

    def test_supplement_does_not_explain_mismatch(self):
        """When a supplement merge is present but the pre-supplement sum
        STILL doesn't match the declared total, it's a genuine mismatch --
        the supplement doesn't fully explain the discrepancy."""
        seg = segment(costs(per_diem="100.00", transportation="50.00", total="100.00"))
        seg.flags.append("COST_SUPPLEMENT_MERGED")
        # After validation, seg.total = 150.00, source_amount = 100.00.
        # Committee total = 999.00 — pre-supplement sum (100) != 999.
        total = costs(per_diem="100.00", total="999.00")
        r = report([Traveler(name="A", segments=[seg])], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_MISMATCH" in r.flags
        assert "TABLE_SUM_EXPLAINED_BY_SUPPLEMENT" not in r.flags

    def test_genuine_mismatch_still_flagged(self):
        """A mismatch that doesn't match any known pattern stays TABLE_SUM_MISMATCH."""
        seg1 = segment(costs(per_diem="100.00", total="100.00"))
        seg2 = segment(costs(per_diem="200.00", total="200.00"))
        # Sum = 300, declared = 550, delta = -250. ct_per_diem=500 (not 300)
        # so ct_components != seg_components and COMMITTEE_TOTAL_COMPUTED
        # does NOT fire. 250 doesn't match any segment component, so
        # COMPONENT_DELTA doesn't fire either. Stays TABLE_SUM_MISMATCH.
        total = costs(per_diem="500.00", total="550.00")
        r = report([Traveler(name="A", segments=[seg1, seg2])], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_MISMATCH" in r.flags

    def test_classification_is_idempotent(self):
        """Revalidation doesn't stack duplicate flags."""
        segs = [segment(costs(per_diem="100.00", total="100.00")) for _ in range(10)]
        total = costs(per_diem="1000.00", total="1000.50")
        r = report([Traveler(name="A", segments=segs)], committee_total=total)
        validate_report(r)
        validate_report(r)
        assert r.flags.count("TABLE_SUM_ROUNDING") == 1


class TestCommitteeTotalInferredFromSegments:
    """When the source table had no committee-total row (committee_total is
    None) but the segments have cost data, the validator synthesizes a
    Costs from the per-segment sums. Each US-dollar cell is the sum of
    the corresponding seg cells with computed=True; the synthesized Costs
    is attached to the report and flagged COMMITTEE_TOTAL_INFERRED_FROM_SEGMENTS
    (replaces MISSING_COMMITTEE_TOTAL). 27 reports in corpus."""

    def test_inferred_from_single_seg(self):
        """One seg with pd=100, total=100. Inferred ct has pd=100, total=100."""
        seg = segment(costs(per_diem="100.00", total="100.00"))
        r = report([Traveler(name="A", segments=[seg])], committee_total=None)
        validate_report(r)
        assert "COMMITTEE_TOTAL_INFERRED_FROM_SEGMENTS" in r.flags
        assert "MISSING_COMMITTEE_TOTAL" not in r.flags
        assert r.committee_total is not None
        assert r.committee_total.per_diem.us_dollar.amount == Decimal("100.00")
        assert r.committee_total.total.us_dollar.amount == Decimal("100.00")
        assert r.committee_total.total.us_dollar.computed is True
        assert r.committee_total.per_diem.us_dollar.computed is True

    def test_inferred_sums_components_across_segs(self):
        """Two segs: pd=100+200=300, tr=50+75=125, ot=0+10=10, total=150+285=435.
        Inferred ct has each component as the seg sum."""
        seg1 = segment(costs(per_diem="100.00", transportation="50.00", total="150.00"))
        seg2 = segment(costs(per_diem="200.00", transportation="75.00", other="10.00",
                             total="285.00"))
        r = report([Traveler(name="A", segments=[seg1, seg2])], committee_total=None)
        validate_report(r)
        assert "COMMITTEE_TOTAL_INFERRED_FROM_SEGMENTS" in r.flags
        assert r.committee_total.per_diem.us_dollar.amount == Decimal("300.00")
        assert r.committee_total.transportation.us_dollar.amount == Decimal("125.00")
        assert r.committee_total.other.us_dollar.amount == Decimal("10.00")
        assert r.committee_total.total.us_dollar.amount == Decimal("435.00")

    def test_inferred_across_travelers(self):
        """Segs from multiple travelers are all summed."""
        seg1 = segment(costs(per_diem="100.00", total="100.00"))
        seg2 = segment(costs(per_diem="200.00", total="200.00"))
        r = report(
            [Traveler(name="A", segments=[seg1]), Traveler(name="B", segments=[seg2])],
            committee_total=None,
        )
        validate_report(r)
        assert "COMMITTEE_TOTAL_INFERRED_FROM_SEGMENTS" in r.flags
        assert r.committee_total.per_diem.us_dollar.amount == Decimal("300.00")
        assert r.committee_total.total.us_dollar.amount == Decimal("300.00")

    def test_no_cost_data_stays_missing(self):
        """A seg with no cost cells at all can't be inferred -- stays
        MISSING_COMMITTEE_TOTAL."""
        seg = segment(costs())
        r = report([Traveler(name="A", segments=[seg])], committee_total=None)
        validate_report(r)
        assert "MISSING_COMMITTEE_TOTAL" in r.flags
        assert "COMMITTEE_TOTAL_INFERRED_FROM_SEGMENTS" not in r.flags
        assert r.committee_total is None

    def test_inferred_total_equals_seg_total_sum(self):
        """The synthesized ct.total is exactly sum(seg.totals) -- by
        construction there's no table-sum mismatch to classify."""
        segs = [
            segment(costs(per_diem="100.00", total="100.00")),
            segment(costs(per_diem="250.50", total="250.50")),
        ]
        r = report([Traveler(name="A", segments=segs)], committee_total=None)
        validate_report(r)
        assert r.committee_total.total.us_dollar.amount == Decimal("350.50")
        assert "TABLE_SUM_MISMATCH" not in r.flags
        assert "TABLE_SUM_ROUNDING" not in r.flags

    def test_idempotent_under_revalidation(self):
        """Re-running validate_report on a synthesized report reproduces
        the same flag set (the all-4-cells-computed marker triggers the
        inference re-tag, not COMMITTEE_TOTAL_COMPUTED)."""
        seg = segment(costs(per_diem="100.00", total="100.00"))
        r = report([Traveler(name="A", segments=[seg])], committee_total=None)
        validate_report(r)
        first_flags = list(r.flags)
        # Reset flags but keep the synthesized committee_total
        r.flags = []
        validate_report(r)
        assert "COMMITTEE_TOTAL_INFERRED_FROM_SEGMENTS" in r.flags
        assert "COMMITTEE_TOTAL_COMPUTED" not in r.flags
        assert first_flags == r.flags


class TestCommitteeTotalCommaDecimalTypo:
    """A comma-as-decimal typo in the committee total cell: the source wrote
    a comma where a decimal point should be (e.g. `3,312,32` parsed as 331232,
    intended `3,312.32` = 3312.32). The segment totals are intact; recovery
    divides the declared total by 100 (`,NN`) or 1000 (`,NNN`) and overwrites
    the cell, preserving the source-declared (inflated) value in
    `source_amount`. Mirrors ROW_TOTAL_COMMA_DECIMAL_TYPO at the table level.
    3 reports in corpus (2012q3sep13-000, 1996q3sep11-005, 2001q2jun25-000).
    """

    def _ct_typo_report(self, ct_total_raw, ct_total_amt, seg_total):
        """Build a report where ct has only a total (no breakdown) with the
        given raw, and one segment with the given total."""
        from official_foreign_travel.models.report import Costs as _C
        from official_foreign_travel.models.report import CostGroup as _CG
        empty = CostCell(amount=None, raw="...........")
        ct_total_cell = CostCell(amount=Decimal(ct_total_amt), raw=ct_total_raw)
        ct = _C(
            per_diem=_CG(foreign_currency=empty, us_dollar=empty),
            transportation=_CG(foreign_currency=empty, us_dollar=empty),
            other=_CG(foreign_currency=empty, us_dollar=empty),
            total=_CG(foreign_currency=empty, us_dollar=ct_total_cell),
        )
        seg = segment(costs(per_diem=seg_total, total=seg_total))
        return report([Traveler(name="A", segments=[seg])], committee_total=ct)

    def test_2digit_typo_divides_by_100(self):
        """2012q3sep13-000: ct total raw `3,312,32` → 331232, intended
        `3,312.32` = 3312.32. seg_total = 3312.32. Recovery: ct_total = 3312.32."""
        r = self._ct_typo_report("3,312,32", "331232", "3312.32")
        validate_report(r)
        assert "COMMITTEE_TOTAL_COMMA_DECIMAL_TYPO" in r.flags
        assert "COMMITTEE_TOTAL_COMPUTED" in r.flags
        assert "TABLE_SUM_MISMATCH" not in r.flags
        ct_total = r.committee_total.total.us_dollar
        assert ct_total.amount == Decimal("3312.32")
        assert ct_total.source_amount == Decimal("331232")
        assert ct_total.computed is True
        assert ct_total.comma_decimal_typo is True

    def test_3digit_typo_divides_by_1000(self):
        """1996q3sep11-005: ct total raw `7,202,000` → 7202000, intended
        `7,202.000` = 7202. seg_total = 7202.00. Recovery: ct_total = 7202."""
        r = self._ct_typo_report("7,202,000", "7202000", "7202.00")
        validate_report(r)
        assert "COMMITTEE_TOTAL_COMMA_DECIMAL_TYPO" in r.flags
        ct_total = r.committee_total.total.us_dollar
        assert ct_total.amount == Decimal("7202")
        assert ct_total.source_amount == Decimal("7202000")

    def test_typo_does_not_fire_when_no_match(self):
        """A ct total with the typo raw shape but where dividing doesn't match
        seg_total falls through to TABLE_SUM_MISMATCH (or another classifier)."""
        # 3,312,32 → 331232, /100 = 3312.32. seg_total = 5000 (no match).
        r = self._ct_typo_report("3,312,32", "331232", "5000.00")
        validate_report(r)
        assert "COMMITTEE_TOTAL_COMMA_DECIMAL_TYPO" not in r.flags
        # ct_components = 0, seg has costs → TABLE_SUM_CT_NO_BREAKDOWN
        assert "TABLE_SUM_CT_NO_BREAKDOWN" in r.flags
        assert r.committee_total.total.us_dollar.amount == Decimal("331232")

    def test_clean_ct_total_not_recovered(self):
        """A ct total with a decimal point (no typo shape) isn't recovered.
        When it matches seg_total, the table balances and no flag fires."""
        r = self._ct_typo_report("3,312.32", "3312.32", "3312.32")
        validate_report(r)
        assert "COMMITTEE_TOTAL_COMMA_DECIMAL_TYPO" not in r.flags
        assert "TABLE_SUM_MISMATCH" not in r.flags

    def test_idempotent_across_revalidation(self):
        """Revalidation re-derives the flag from the comma_decimal_typo marker."""
        r = self._ct_typo_report("3,312,32", "331232", "3312.32")
        validate_report(r)
        validate_report(r)
        assert r.flags.count("COMMITTEE_TOTAL_COMMA_DECIMAL_TYPO") == 1
        assert r.flags.count("COMMITTEE_TOTAL_COMPUTED") == 1
        assert "TABLE_SUM_MISMATCH" not in r.flags
        assert r.committee_total.total.us_dollar.amount == Decimal("3312.32")
        assert r.committee_total.total.us_dollar.source_amount == Decimal("331232")


class TestCommitteeComponentCommaDecimalTypo:
    """A comma-as-decimal typo in one or more committee-total component cells
    (per_diem / transportation / other): the source wrote a comma where a
    decimal point should be (e.g. `37,347,86` parsed as 3734786, intended
    `37,347.86` = 37347.86). The ct total is correct; recovery overwrites
    each typo'd component with `amount/divisor`, preserves the source-
    declared (inflated) value in `source_amount`, sets `computed=True` and
    `comma_decimal_typo=True` on the cell. Mirrors
    ROW_COMPONENT_COMMA_DECIMAL_TYPO at the committee-total level. 2 reports
    in corpus (2003q4nov10-005, 2015q3sep08-005); 0 TSM resolved (data
    quality fix only -- seg totals still don't match ct_total after the
    ct components are corrected).
    """

    def _ct_component_typo_report(
        self,
        ct_total_raw,
        ct_total_amt,
        ct_components,
        seg_total,
        seg_components=("0", "0", "0"),
    ):
        """Build a report with the given ct total cell, ct component cells
        (list of (raw, amount) tuples for pd/tr/ot), and one segment."""
        from official_foreign_travel.models.report import Costs as _C
        from official_foreign_travel.models.report import CostGroup as _CG
        empty = CostCell(amount=None, raw="...........")

        def cell_for(spec):
            if spec is None:
                return empty
            raw, amt = spec
            return CostCell(amount=Decimal(amt), raw=raw)

        pd_spec, tr_spec, ot_spec = ct_components
        ct = _C(
            per_diem=_CG(foreign_currency=empty, us_dollar=cell_for(pd_spec)),
            transportation=_CG(foreign_currency=empty, us_dollar=cell_for(tr_spec)),
            other=_CG(foreign_currency=empty, us_dollar=cell_for(ot_spec)),
            total=_CG(
                foreign_currency=empty,
                us_dollar=CostCell(amount=Decimal(ct_total_amt), raw=ct_total_raw),
            ),
        )
        seg = segment(
            costs(
                per_diem=seg_components[0],
                transportation=seg_components[1],
                other=seg_components[2],
                total=seg_total,
            )
        )
        return report([Traveler(name="A", segments=[seg])], committee_total=ct)

    def test_2component_typo_recovered(self):
        """2003q4nov10-005: ct_pd raw `37,347,86` → 3734786, ct_tr raw
        `52,748,58` → 5274858, ct_ot empty. ct_total=90096.44 (correct).
        Fixing both: 37347.86 + 52748.58 = 90096.44 = ct_total. Recovery
        fires on both component cells."""
        r = self._ct_component_typo_report(
            ct_total_raw="90,096.44",
            ct_total_amt="90096.44",
            ct_components=(
                ("  37,347,86  ", "3734786"),
                ("  52,748,58  ", "5274858"),
                None,
            ),
            seg_total="171185.29",
            seg_components=("37122.86", "134062.43", "0"),
        )
        validate_report(r)
        assert "COMMITTEE_COMPONENT_COMMA_DECIMAL_TYPO" in r.flags
        ct_pd = r.committee_total.per_diem.us_dollar
        ct_tr = r.committee_total.transportation.us_dollar
        assert ct_pd.amount == Decimal("37347.86")
        assert ct_pd.source_amount == Decimal("3734786")
        assert ct_pd.computed is True
        assert ct_pd.comma_decimal_typo is True
        assert ct_tr.amount == Decimal("52748.58")
        assert ct_tr.source_amount == Decimal("5274858")
        assert ct_tr.comma_decimal_typo is True
        # ct_total unchanged (the components were wrong, not the total).
        assert r.committee_total.total.us_dollar.amount == Decimal("90096.44")
        assert r.committee_total.total.us_dollar.computed is False
        # Table delta remains (segs don't match ct_total even after fix).
        assert "TABLE_SUM_MISMATCH" in r.flags

    def test_1component_typo_recovered(self):
        """2015q3sep08-005: ct_pd and ct_tr are clean, ct_ot raw
        `35,753,19` → 3575319. ct_total=234141.63 = 46601.54 + 151786.90 +
        35753.19. Only ct_ot is typo'd."""
        r = self._ct_component_typo_report(
            ct_total_raw="234,141.63",
            ct_total_amt="234141.63",
            ct_components=(
                ("46,601.54", "46601.54"),
                ("151,786.90", "151786.90"),
                ("  35,753,19  ", "3575319"),
            ),
            seg_total="211731.80",
            seg_components=("56601.54", "151710.60", "13419.66"),
        )
        validate_report(r)
        assert "COMMITTEE_COMPONENT_COMMA_DECIMAL_TYPO" in r.flags
        ct_ot = r.committee_total.other.us_dollar
        assert ct_ot.amount == Decimal("35753.19")
        assert ct_ot.source_amount == Decimal("3575319")
        assert ct_ot.comma_decimal_typo is True
        # Clean components unchanged.
        assert r.committee_total.per_diem.us_dollar.amount == Decimal("46601.54")
        assert r.committee_total.per_diem.us_dollar.computed is False
        assert r.committee_total.transportation.us_dollar.amount == Decimal("151786.90")
        assert r.committee_total.per_diem.us_dollar.comma_decimal_typo is False
        # Table delta remains.
        assert "TABLE_SUM_MISMATCH" in r.flags

    def test_3digit_typo_divides_by_1000(self):
        """A 3-digit last group (`,NNN`) divides by 1000. ct_pd raw
        `1,123,000` → 1123000, intended `1,123.000` = 1123. ct_total=1123,
        ct_tr=0, ct_ot=0."""
        r = self._ct_component_typo_report(
            ct_total_raw="1,123.00",
            ct_total_amt="1123.00",
            ct_components=(("1,123,000", "1123000"), None, None),
            seg_total="1123.00",
            seg_components=("1123.00", "0", "0"),
        )
        validate_report(r)
        assert "COMMITTEE_COMPONENT_COMMA_DECIMAL_TYPO" in r.flags
        assert r.committee_total.per_diem.us_dollar.amount == Decimal("1123")
        assert r.committee_total.per_diem.us_dollar.source_amount == Decimal("1123000")

    def test_typo_does_not_fire_when_no_match(self):
        """Typo-shaped raws but fixing them doesn't sum to ct_total. No
        recovery; ct components stay inflated."""
        r = self._ct_component_typo_report(
            ct_total_raw="5000.00",
            ct_total_amt="5000.00",
            ct_components=(("37,347,86", "3734786"), ("52,748,58", "5274858"), None),
            seg_total="5000.00",
            seg_components=("5000.00", "0", "0"),
        )
        validate_report(r)
        assert "COMMITTEE_COMPONENT_COMMA_DECIMAL_TYPO" not in r.flags
        assert r.committee_total.per_diem.us_dollar.amount == Decimal("3734786")
        assert r.committee_total.per_diem.us_dollar.computed is False

    def test_clean_components_not_recovered(self):
        """Clean ct components (decimal points, no typo shape) with a small
        table delta don't trigger the typo recovery."""
        r = self._ct_component_typo_report(
            ct_total_raw="300.00",
            ct_total_amt="300.00",
            ct_components=(("100.00", "100.00"), ("100.00", "100.00"), ("100.00", "100.00")),
            seg_total="300.00",
            seg_components=("100.00", "100.00", "100.00"),
        )
        validate_report(r)
        assert "COMMITTEE_COMPONENT_COMMA_DECIMAL_TYPO" not in r.flags
        # Table balances, no TSM.
        assert "TABLE_SUM_MISMATCH" not in r.flags

    def test_ct_total_typo_wins_over_ct_component_typo(self):
        """When both ct-total and ct-component raws have typo shape, the
        ct-total recovery fires first (it's earlier in the elif chain).
        Construct a case where ct_total raw is typo-shaped and divides to
        match seg_total, AND ct_pd is also typo-shaped. The ct-total
        recovery should win (single flag, ct_total fixed)."""
        # ct_total raw `3,312,32` → 331232, /100 = 3312.32 = seg_total.
        # ct_pd raw `1,000,00` → 100000, /100 = 1000. But the arithmetic
        # gate for ct_total typo (ct_total/divisor == seg_total) passes.
        # For ct_component typo, we'd need ct_pd/div + ct_tr + ct_ot ==
        # ct_total. With ct_total inflated to 331232, that wouldn't match.
        # So only the ct-total recovery fires.
        r = self._ct_component_typo_report(
            ct_total_raw="3,312,32",
            ct_total_amt="331232",
            ct_components=(("1,000,00", "100000"), None, None),
            seg_total="3312.32",
            seg_components=("3312.32", "0", "0"),
        )
        validate_report(r)
        assert "COMMITTEE_TOTAL_COMMA_DECIMAL_TYPO" in r.flags
        assert "COMMITTEE_COMPONENT_COMMA_DECIMAL_TYPO" not in r.flags
        # ct_total was fixed to 3312.32 (the seg_total).
        assert r.committee_total.total.us_dollar.amount == Decimal("3312.32")
        # ct_pd was NOT fixed (ct-component recovery didn't fire).
        assert r.committee_total.per_diem.us_dollar.amount == Decimal("100000")
        assert r.committee_total.per_diem.us_dollar.computed is False

    def test_idempotent_across_revalidation(self):
        """Revalidation re-derives the flag from the comma_decimal_typo
        markers on the ct component cells."""
        r = self._ct_component_typo_report(
            ct_total_raw="90,096.44",
            ct_total_amt="90096.44",
            ct_components=(
                ("  37,347,86  ", "3734786"),
                ("  52,748,58  ", "5274858"),
                None,
            ),
            seg_total="171185.29",
            seg_components=("37122.86", "134062.43", "0"),
        )
        validate_report(r)
        validate_report(r)
        assert r.flags.count("COMMITTEE_COMPONENT_COMMA_DECIMAL_TYPO") == 1
        assert r.committee_total.per_diem.us_dollar.amount == Decimal("37347.86")
        assert r.committee_total.per_diem.us_dollar.source_amount == Decimal("3734786")
        assert r.committee_total.transportation.us_dollar.amount == Decimal("52748.58")


class TestTableSumCtNoBreakdown:
    """A report where the committee total is the only cell populated (all 3
    per-component cells empty) and segments have costs. Source convention:
    the ct total was entered as a single number with no breakdown, so the
    table delta can't be arithmetically verified. Downgraded from
    TABLE_SUM_MISMATCH to TABLE_SUM_CT_NO_BREAKDOWN. 48 reports in corpus.
    """

    def test_ct_no_breakdown_downgrades_tsm(self):
        """ct has only total, segs have per_diem; delta can't be verified."""
        from official_foreign_travel.models.report import Costs as _C
        from official_foreign_travel.models.report import CostGroup as _CG
        empty = CostCell(amount=None, raw="...........")
        ct = _C(
            per_diem=_CG(foreign_currency=empty, us_dollar=empty),
            transportation=_CG(foreign_currency=empty, us_dollar=empty),
            other=_CG(foreign_currency=empty, us_dollar=empty),
            total=_CG(
                foreign_currency=empty,
                us_dollar=CostCell(amount=Decimal("5000.00"), raw="5,000.00"),
            ),
        )
        seg = segment(costs(per_diem="3000.00", total="3000.00"))
        r = report([Traveler(name="A", segments=[seg])], committee_total=ct)
        validate_report(r)
        assert "TABLE_SUM_CT_NO_BREAKDOWN" in r.flags
        assert "TABLE_SUM_MISMATCH" not in r.flags

    def test_arithmetic_explanation_wins_over_ct_no_breakdown(self):
        """When a specific arithmetic pattern fires (e.g. transport-excluded),
        it wins over the structural CT_NO_BREAKDOWN downgrade. The ct has no
        breakdown (only total), but the transport-excluded pattern explains
        the delta — that arithmetic explanation is more specific."""
        from official_foreign_travel.models.report import Costs as _C
        from official_foreign_travel.models.report import CostGroup as _CG
        empty = CostCell(amount=None, raw="...........")
        # ct has only total = 1500.00 (no breakdown). seg has pd=1000, ot=500,
        # tr=300, total=1800. sum_pd + sum_ot = 1500 = ct_total →
        # transport-excluded fires. delta = 1800 - 1500 = 300 (the tr amount).
        ct = _C(
            per_diem=_CG(foreign_currency=empty, us_dollar=empty),
            transportation=_CG(foreign_currency=empty, us_dollar=empty),
            other=_CG(foreign_currency=empty, us_dollar=empty),
            total=_CG(
                foreign_currency=empty,
                us_dollar=CostCell(amount=Decimal("1500.00"), raw="1,500.00"),
            ),
        )
        seg = segment(
            costs(per_diem="1000.00", transportation="300.00", other="500.00", total="1800.00")
        )
        r = report([Traveler(name="A", segments=[seg])], committee_total=ct)
        validate_report(r)
        assert "TABLE_SUM_TRANSPORT_EXCLUDED" in r.flags
        assert "TABLE_SUM_CT_NO_BREAKDOWN" not in r.flags

    def test_ct_with_breakdown_not_no_breakdown(self):
        """When ct has at least one component populated, CT_NO_BREAKDOWN
        doesn't fire — even if the table doesn't balance."""
        seg = segment(costs(per_diem="100.00", total="100.00"))
        # ct has pd=500, total=999 — both populated but mismatch. seg=100.
        # delta = 100 - 999 = -899. No arithmetic classifier fires.
        total = costs(per_diem="500.00", total="999.00")
        r = report([Traveler(name="A", segments=[seg])], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_CT_NO_BREAKDOWN" not in r.flags
        assert "TABLE_SUM_MISMATCH" in r.flags


class TestTableSumNoSegBreakdown:
    """A report where the committee total has full breakdown but no segment
    has any cost cells populated (or there are no segments at all). Source
    convention: the ct breakdown exists but no per-traveler breakdown was
    provided. Downgraded from TABLE_SUM_MISMATCH to TABLE_SUM_NO_SEG_BREAKDOWN.
    11 reports in corpus.
    """

    def test_no_segments_downgrades_tsm(self):
        """ct has full breakdown but no travelers at all."""
        ct = costs(per_diem="100.00", transportation="200.00", total="300.00")
        r = report([], committee_total=ct)
        validate_report(r)
        assert "TABLE_SUM_NO_SEG_BREAKDOWN" in r.flags
        assert "TABLE_SUM_MISMATCH" not in r.flags

    def test_segments_with_no_costs_downgrades_tsm(self):
        """ct has full breakdown; travelers exist but their segments have no
        cost cells populated."""
        from official_foreign_travel.models.report import CostGroup as _CG
        empty = CostCell(amount=None, raw="...........")
        empty_seg = TravelSegment(
            arrival_raw="1/1",
            departure_raw="1/2",
            country_raw="Country",
            costs=Costs(
                per_diem=_CG(foreign_currency=empty, us_dollar=empty),
                transportation=_CG(foreign_currency=empty, us_dollar=empty),
                other=_CG(foreign_currency=empty, us_dollar=empty),
                total=_CG(foreign_currency=empty, us_dollar=empty),
            ),
        )
        ct = costs(per_diem="100.00", transportation="200.00", total="300.00")
        r = report([Traveler(name="A", segments=[empty_seg])], committee_total=ct)
        validate_report(r)
        assert "TABLE_SUM_NO_SEG_BREAKDOWN" in r.flags
        assert "TABLE_SUM_MISMATCH" not in r.flags

    def test_segments_with_costs_not_no_seg_breakdown(self):
        """When segments have costs, NO_SEG_BREAKDOWN doesn't fire — even if
        the table doesn't balance."""
        seg = segment(costs(per_diem="100.00", total="100.00"))
        # ct has pd=500, total=999 — both populated but mismatch.
        total = costs(per_diem="500.00", total="999.00")
        r = report([Traveler(name="A", segments=[seg])], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_NO_SEG_BREAKDOWN" not in r.flags
        assert "TABLE_SUM_MISMATCH" in r.flags


class TestTableSumCtHasUnbrokenComponent:
    """A report where one ct component is populated but the corresponding
    segment component sum is 0, and the rest of the ct components match the
    rest of the segment components. Source convention: the ct broke out a
    component (often transportation) that wasn't broken out per-segment.
    Downgraded from TABLE_SUM_MISMATCH to TABLE_SUM_CT_HAS_UNBROKEN_COMPONENT.
    8 reports in corpus.
    """

    def test_ct_has_transport_segs_dont_downgrades(self):
        """2000q1feb02-007: ct has pd=2400 + tr=2672.78 = 5072.78 total. Segs
        only have pd=2400 (no transport). The ct transport (2672.78) fully
        accounts for the delta; the rest matches."""
        seg = segment(costs(per_diem="2400.00", total="2400.00"))
        total = costs(per_diem="2400.00", transportation="2672.78", total="5072.78")
        r = report([Traveler(name="A", segments=[seg])], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_CT_HAS_UNBROKEN_COMPONENT" in r.flags
        assert "TABLE_SUM_MISMATCH" not in r.flags

    def test_rest_must_match_for_downgrade(self):
        """If the rest of the ct components DON'T match the rest of the seg
        components, the downgrade doesn't fire."""
        # ct has pd=2400 + tr=2672.78 = 5072.78. seg has pd=2000 (not 2400).
        # rest_ct = 2400, rest_seg = 2000. Mismatch — downgrade doesn't fire.
        seg = segment(costs(per_diem="2000.00", total="2000.00"))
        total = costs(per_diem="2400.00", transportation="2672.78", total="5072.78")
        r = report([Traveler(name="A", segments=[seg])], committee_total=total)
        validate_report(r)
        assert "TABLE_SUM_CT_HAS_UNBROKEN_COMPONENT" not in r.flags
        # The delta is 2000 - 5072.78 = -3072.78. ct has tr=2672.78 (seg has 0).
        # rest_ct=2400, rest_seg=2000, diff=400 > tolerance. Falls through.
        assert "TABLE_SUM_MISMATCH" in r.flags


class TestTableSumSegHasUnbrokenComponent:
    """A report where one seg component sum is populated but the corresponding
    ct component is 0, and the rest of the seg components matches the rest of
    the ct components. Source convention: a seg (or per-traveler rollup) broke
    out a component that wasn't broken out at the committee-total level.
    Downgraded from TABLE_SUM_MISMATCH to TABLE_SUM_SEG_HAS_UNBROKEN_COMPONENT.
    Mirror of TABLE_SUM_CT_HAS_UNBROKEN_COMPONENT. 1 report in corpus
    (2005q1mar16-010).
    """

    def test_seg_has_other_ct_doesnt_downgrades(self):
        """2005q1mar16-010: segs have ot summing to 645.74 across multiple
        segments (516.00 + 129.74), ct has ot=0. Rest matches
        (ct_pd+ct_tr == seg_pd+seg_tr). The seg other sum (645.74) fully
        accounts for the delta. Multiple segs are used so the delta doesn't
        match any single seg component amount (otherwise the existing
        TABLE_SUM_COMPONENT_DELTA classifier would fire first)."""
        seg1 = segment(
            costs(per_diem="30000.00", transportation="38136.77", other="516.00",
                  total="68652.77")
        )
        seg2 = segment(
            costs(per_diem="0.00", transportation="0.00", other="129.74",
                  total="129.74")
        )
        ct = costs(per_diem="30000.00", transportation="38136.77", other=None,
                   total="68136.77")
        r = report([Traveler(name="A", segments=[seg1, seg2])], committee_total=ct)
        validate_report(r)
        assert "TABLE_SUM_SEG_HAS_UNBROKEN_COMPONENT" in r.flags
        assert "TABLE_SUM_MISMATCH" not in r.flags

    def test_seg_has_pd_ct_doesnt_downgrades(self):
        """Symmetric case: segs have pd populated, ct has pd=0, rest matches.
        Two segs are used so the pd sum (500) doesn't match any single seg
        component amount (otherwise TABLE_SUM_COMPONENT_DELTA would fire)."""
        seg1 = segment(
            costs(per_diem="300.00", transportation="1000.00",
                  total="1300.00")
        )
        seg2 = segment(
            costs(per_diem="200.00", transportation="0.00", total="200.00")
        )
        ct = costs(per_diem=None, transportation="1000.00", other="0.00",
                   total="1000.00")
        r = report([Traveler(name="A", segments=[seg1, seg2])], committee_total=ct)
        validate_report(r)
        assert "TABLE_SUM_SEG_HAS_UNBROKEN_COMPONENT" in r.flags
        assert "TABLE_SUM_MISMATCH" not in r.flags

    def test_rest_must_match_for_downgrade(self):
        """If the rest of the seg components DON'T match the rest of the ct
        components, the downgrade doesn't fire."""
        # seg has pd=500 + tr=1000 = 1500. ct has pd=0, tr=900 (not 1000).
        # rest_ct = 900, rest_seg = 1000. Mismatch — downgrade doesn't fire.
        seg = segment(
            costs(per_diem="500.00", transportation="1000.00", total="1500.00")
        )
        ct = costs(per_diem=None, transportation="900.00", other="0.00",
                   total="900.00")
        r = report([Traveler(name="A", segments=[seg])], committee_total=ct)
        validate_report(r)
        assert "TABLE_SUM_SEG_HAS_UNBROKEN_COMPONENT" not in r.flags
        assert "TABLE_SUM_MISMATCH" in r.flags

    def test_both_nonzero_not_unbroken(self):
        """When both ct and seg have a component populated (both_nonzero
        disagreement), neither unbroken-component downgrade fires."""
        # ct has pd=500, seg has pd=600 (both nonzero, disagree).
        seg = segment(costs(per_diem="600.00", total="600.00"))
        ct = costs(per_diem="500.00", transportation=None, other=None,
                   total="500.00")
        r = report([Traveler(name="A", segments=[seg])], committee_total=ct)
        validate_report(r)
        assert "TABLE_SUM_SEG_HAS_UNBROKEN_COMPONENT" not in r.flags
        assert "TABLE_SUM_CT_HAS_UNBROKEN_COMPONENT" not in r.flags


class TestRowNoComponentBreakdown:
    """A segment with a declared total but no per-component breakdown (all
    three component cells empty/dot-filled) has nothing to arithmetically
    check. This is a source convention (e.g. 2009q2may13.txt declares only
    USD totals with no breakdown for any row), not an arithmetic error.
    Flag it informationally as ROW_NO_COMPONENT_BREAKDOWN rather than as
    ROW_SUM_MISMATCH, so downstream consumers can distinguish "source
    didn't provide a breakdown" from "source provided a breakdown that
    doesn't add up."""

    def test_declared_total_no_components_flagged_informational(self):
        seg = segment(costs(per_diem=None, transportation=None, other=None, total="100.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_NO_COMPONENT_BREAKDOWN" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_declared_total_with_components_still_sum_checked(self):
        """When components are present, the row is arithmetically checked --
        ROW_NO_COMPONENT_BREAKDOWN does not apply even if the sum matches."""
        seg = segment(costs(per_diem="100.00", total="100.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_NO_COMPONENT_BREAKDOWN" not in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_no_total_no_components_not_flagged(self):
        """Fully empty row (no total, no components) is neither a mismatch
        nor a no-breakdown case -- nothing to flag."""
        seg = segment(costs(per_diem=None, total=None))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_NO_COMPONENT_BREAKDOWN" not in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_zero_declared_total_no_components_not_flagged(self):
        """A declared total of zero with no components is not a meaningful
        breakdown-omission -- skip flagging (mirrors the computed > 0 guard
        used elsewhere to avoid flagging zero-expenditure rows)."""
        seg = segment(costs(per_diem=None, total="0.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_NO_COMPONENT_BREAKDOWN" not in seg.flags

    def test_flag_is_idempotent_across_revalidation(self):
        seg = segment(costs(per_diem=None, total="100.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        validate_report(r)
        assert seg.flags.count("ROW_NO_COMPONENT_BREAKDOWN") == 1
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_total_still_included_in_table_sum(self):
        """A no-breakdown row's declared total still counts toward the
        table's committee-total check -- it's a real dollar amount, just
        one we can't arithmetically verify at the row level."""
        seg = segment(costs(per_diem=None, total="100.00"))
        total = costs(per_diem="100.00", total="100.00")
        r = report([Traveler(name="A", segments=[seg])], committee_total=total)
        validate_report(r)
        assert "ROW_NO_COMPONENT_BREAKDOWN" in seg.flags
        assert "TABLE_SUM_MISMATCH" not in r.flags


class TestRowBreakdownInFcColumn:
    """A segment with a declared US-dollar total but no US-dollar component
    breakdown, where the foreign-currency components sum to the declared
    total, is a source convention: the per-category amounts were entered in
    the foreign-currency column even though they are US-dollar figures.
    Downgrade from ROW_NO_COMPONENT_BREAKDOWN to ROW_BREAKDOWN_IN_FC_COLUMN
    so downstream consumers can distinguish "no breakdown at all" from
    "breakdown in FC column"."""

    def _costs_fc(self, pd_fc=None, tr_fc=None, ot_fc=None, total_us="100.00"):
        empty = cell()
        def fc_group(amt):
            if amt is None:
                return CostGroup(foreign_currency=empty, us_dollar=empty)
            return CostGroup(
                foreign_currency=CostCell(amount=Decimal(amt), raw=str(amt)),
                us_dollar=empty,
            )
        return Costs(
            per_diem=fc_group(pd_fc),
            transportation=fc_group(tr_fc),
            other=fc_group(ot_fc),
            total=CostGroup(foreign_currency=empty, us_dollar=cell(total_us)),
        )

    def test_fc_components_sum_to_us_total_downgrades(self):
        seg = segment(self._costs_fc(pd_fc="60.00", tr_fc="40.00", total_us="100.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_BREAKDOWN_IN_FC_COLUMN" in seg.flags
        assert "ROW_NO_COMPONENT_BREAKDOWN" not in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_single_fc_component_summing_to_total_downgrades(self):
        seg = segment(self._costs_fc(tr_fc="742.00", total_us="742.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_BREAKDOWN_IN_FC_COLUMN" in seg.flags
        assert "ROW_NO_COMPONENT_BREAKDOWN" not in seg.flags

    def test_fc_components_do_not_sum_stays_no_breakdown(self):
        """When FC components don't sum to the US total, the FC amounts are
        genuine foreign currency (not US dollars in the FC column) -- keep
        ROW_NO_COMPONENT_BREAKDOWN."""
        seg = segment(self._costs_fc(pd_fc="60.00", tr_fc="40.00", total_us="150.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_NO_COMPONENT_BREAKDOWN" in seg.flags
        assert "ROW_BREAKDOWN_IN_FC_COLUMN" not in seg.flags

    def test_no_fc_components_stays_no_breakdown(self):
        """No FC components either → genuine total-only row."""
        seg = segment(self._costs_fc(total_us="100.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_NO_COMPONENT_BREAKDOWN" in seg.flags
        assert "ROW_BREAKDOWN_IN_FC_COLUMN" not in seg.flags

    def test_us_components_present_not_downgraded(self):
        """When US-dollar components are present, ROW_NO_COMPONENT_BREAKDOWN
        doesn't fire at all (the row is arithmetically checked)."""
        seg = segment(costs(per_diem="100.00", total="100.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_NO_COMPONENT_BREAKDOWN" not in seg.flags
        assert "ROW_BREAKDOWN_IN_FC_COLUMN" not in seg.flags

    def test_fc_sum_within_tolerance_downgrades(self):
        """FC sum within tolerance of the US total downgrades."""
        seg = segment(self._costs_fc(pd_fc="60.00", tr_fc="40.01", total_us="100.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_BREAKDOWN_IN_FC_COLUMN" in seg.flags

    def test_flag_is_idempotent_across_revalidation(self):
        seg = segment(self._costs_fc(pd_fc="100.00", total_us="100.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        validate_report(r)
        assert seg.flags.count("ROW_BREAKDOWN_IN_FC_COLUMN") == 1
        assert "ROW_NO_COMPONENT_BREAKDOWN" not in seg.flags


class TestRowTotalComputed:
    def test_components_without_total_are_recovered(self):
        """When components exist but total is empty, fill the total with the sum."""
        seg = segment(costs(per_diem="100.00", total=None))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_MISSING" not in seg.flags
        assert "ROW_TOTAL_COMPUTED" in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("100.00")

    def test_multi_component_total_recovered(self):
        """per_diem + transportation + other sum is recovered as the total."""
        seg = segment(costs(per_diem="100.00", transportation="50.00", other="25.00", total=None))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMPUTED" in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("175.00")

    def test_per_diem_only_recovered(self):
        """The dominant case in the corpus: only per_diem declared, total dot-filled."""
        seg = segment(costs(per_diem="462.00", total=None))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMPUTED" in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("462.00")

    def test_fully_empty_cost_row_is_not_flagged(self):
        """No components and no total: nothing to recover, no flag."""
        seg = segment(costs(per_diem=None, total=None))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMPUTED" not in seg.flags
        assert seg.costs.total.us_dollar.amount is None

    def test_source_declared_total_is_not_overwritten(self):
        """A real source total (raw has digits) is never replaced -- even if it equals the sum."""
        seg = segment(costs(per_diem="100.00", total="100.00"))
        # Set raw to a real source value (the `costs` helper uses str(amount) by default,
        # which produces '100.00' -- not dot-filled -- so this matches a real declared total).
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMPUTED" not in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("100.00")

    def test_flag_is_idempotent_across_revalidation(self):
        """Re-validating preserves the flag and doesn't re-mutate the amount."""
        seg = segment(costs(per_diem="100.00", total=None))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        first_amount = seg.costs.total.us_dollar.amount
        validate_report(r)
        assert seg.flags.count("ROW_TOTAL_COMPUTED") == 1
        assert seg.costs.total.us_dollar.amount == first_amount

    def test_computed_total_skips_sum_mismatch_check(self):
        """A computed total is by definition the sum -- never flagged as a mismatch."""
        seg = segment(costs(per_diem="100.00", transportation="50.00", total=None))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert "ROW_TOTAL_COMPUTED" in seg.flags

    def test_dot_filled_raw_treated_as_empty(self):
        """The fixed-width empty-cell convention (dots) is recognized as 'no source value'."""
        seg = segment(costs(per_diem="100.00", total=None))
        seg.costs.total.us_dollar.raw = "..........."
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMPUTED" in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("100.00")

    def test_whitespace_raw_treated_as_empty(self):
        seg = segment(costs(per_diem="100.00", total=None))
        seg.costs.total.us_dollar.raw = "        "
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMPUTED" in seg.flags

    def test_correction_replacing_computed_total_clears_flag(self):
        """If a correction step supplies a real source total (sets `amount`,
        clears `computed`, and matches the components), the
        ROW_TOTAL_COMPUTED flag is cleared on revalidation and no
        ROW_SUM_MISMATCH is raised. `computed` is the explicit idempotency
        marker -- a correction that overwrites a recovered total must
        clear it to signal "this is now a real source value, not a
        computed one."""
        seg = segment(costs(per_diem="100.00", total=None))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMPUTED" in seg.flags
        assert seg.costs.total.us_dollar.computed is True
        # Correction supplies a real total value: set amount, clear
        # `computed`, and set raw to the source digits.
        seg.costs.total.us_dollar.amount = Decimal("100.00")
        seg.costs.total.us_dollar.computed = False
        seg.costs.total.us_dollar.raw = "100.00"
        validate_report(r)
        assert "ROW_TOTAL_COMPUTED" not in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags


class TestSupplementOutdatedTotal:
    """When COST_SUPPLEMENT_MERGED is set and the source total doesn't match
    the (post-supplement) component sum, the source total is stale -- the
    supplement row's cost was added to the components but the source's
    declared total was never updated. Recover by overwriting the total
    with the computed sum."""

    def test_supplement_outdated_total_recovered(self):
        seg = segment(costs(per_diem="100.00", transportation="50.00", total="100.00"))
        seg.flags.append("COST_SUPPLEMENT_MERGED")
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert "ROW_TOTAL_COMPUTED" in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("150.00")
        assert seg.costs.total.us_dollar.computed is True

    def test_supplement_with_matching_total_not_flagged(self):
        """If the source total was correctly updated to reflect the supplement
        (matches the component sum), no flag is raised -- the supplement is
        informational, not a recovery."""
        seg = segment(costs(per_diem="100.00", transportation="50.00", total="150.00"))
        seg.flags.append("COST_SUPPLEMENT_MERGED")
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMPUTED" not in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert seg.costs.total.us_dollar.computed is False

    def test_mismatch_without_supplement_still_flagged(self):
        """A mismatch with no supplement flag is not recovered as a
        supplement merge. Positive delta → ROW_TOTAL_INCLUDES_UNBROKEN_COSTS
        (not ROW_TOTAL_COMPUTED)."""
        seg = segment(costs(per_diem="100.00", transportation="50.00", total="999.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in seg.flags
        assert "ROW_TOTAL_COMPUTED" not in seg.flags
        assert seg.costs.total.us_dollar.computed is False

    def test_supplement_recovery_is_idempotent(self):
        seg = segment(costs(per_diem="100.00", transportation="50.00", total="100.00"))
        seg.flags.append("COST_SUPPLEMENT_MERGED")
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        first_amount = seg.costs.total.us_dollar.amount
        validate_report(r)
        assert seg.flags.count("ROW_TOTAL_COMPUTED") == 1
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert seg.costs.total.us_dollar.amount == first_amount

    def test_supplement_recovery_preserves_supplement_flag(self):
        """Recovering the total doesn't drop the supplement marker -- the
        audit trail that a supplement was merged in is preserved."""
        seg = segment(costs(per_diem="100.00", transportation="50.00", total="100.00"))
        seg.flags.append("COST_SUPPLEMENT_MERGED")
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "COST_SUPPLEMENT_MERGED" in seg.flags
        assert "ROW_TOTAL_COMPUTED" in seg.flags


class TestSourceDoubleCountedComponent:
    """When the source total exceeds the component sum by exactly one component
    amount, the source double-counted that component (e.g. 1997 Korea trips
    where per_diem=305 and total=610). Recovery overwrites the total with the
    component sum and tags ROW_TOTAL_DOUBLE_COUNTED alongside ROW_TOTAL_COMPUTED."""

    def test_per_diem_double_counted_recovered(self):
        """The Korea pattern: only per_diem non-null, total = 2 * per_diem."""
        seg = segment(costs(per_diem="305.00", total="610.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert "ROW_TOTAL_COMPUTED" in seg.flags
        assert "ROW_TOTAL_DOUBLE_COUNTED" in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("305.00")
        assert seg.costs.total.us_dollar.computed is True
        assert seg.costs.total.us_dollar.double_counted is True

    def test_double_counted_with_other_components(self):
        """Source double-counted per_diem in a row that also has transport/other."""
        seg = segment(costs(per_diem="789.00", transportation="6124.23", other="94.09", total="7796.32"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_DOUBLE_COUNTED" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        # computed = 789 + 6124.23 + 94.09 = 7007.32
        assert seg.costs.total.us_dollar.amount == Decimal("7007.32")

    def test_transport_double_counted_recovered(self):
        """If the source double-counted transport (diff = transport), recover."""
        seg = segment(costs(per_diem="100.00", transportation="500.00", total="1100.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_DOUBLE_COUNTED" in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("600.00")

    def test_other_double_counted_recovered(self):
        seg = segment(costs(per_diem="100.00", other="200.00", total="400.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_DOUBLE_COUNTED" in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("300.00")

    def test_source_excluded_component_stays_flagged(self):
        """A total that EXCLUDES a component (diff < 0) is an intentional source
        convention (military airfare, separate reimbursement) -- do NOT recover
        as a double-count. Downgrade to ROW_TOTAL_TRANSPORT_EXCLUDED
        (informational), keeping the source-declared total."""
        seg = segment(costs(per_diem="992.00", transportation="461.20", total="992.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_TRANSPORT_EXCLUDED" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert "ROW_TOTAL_DOUBLE_COUNTED" not in seg.flags
        assert "ROW_TOTAL_COMPUTED" not in seg.flags
        assert seg.costs.total.us_dollar.computed is False
        assert seg.costs.total.us_dollar.amount == Decimal("992.00")

    def test_diff_does_not_match_any_component_stays_flagged(self):
        """A diff that doesn't equal any single component is ambiguous -- not
        recovered as a double-count. Positive delta → ROW_TOTAL_INCLUDES_UNBROKEN_COSTS."""
        seg = segment(costs(per_diem="100.00", transportation="50.00", total="999.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in seg.flags
        assert "ROW_TOTAL_DOUBLE_COUNTED" not in seg.flags

    def test_zero_diff_not_double_counted(self):
        """A matching total is not a double-count -- no flag."""
        seg = segment(costs(per_diem="100.00", transportation="50.00", total="150.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_DOUBLE_COUNTED" not in seg.flags
        assert "ROW_TOTAL_COMPUTED" not in seg.flags

    def test_double_counted_recovery_is_idempotent(self):
        seg = segment(costs(per_diem="305.00", total="610.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        first_amount = seg.costs.total.us_dollar.amount
        validate_report(r)
        assert seg.flags.count("ROW_TOTAL_DOUBLE_COUNTED") == 1
        assert seg.flags.count("ROW_TOTAL_COMPUTED") == 1
        assert seg.costs.total.us_dollar.amount == first_amount
        assert seg.costs.total.us_dollar.double_counted is True

    def test_double_counted_marker_distinguishes_from_source_omitted(self):
        """A source-omitted recovery (no declared total) sets `computed` but
        NOT `double_counted` -- the revalidation path uses the explicit
        `double_counted` marker to re-derive ROW_TOTAL_DOUBLE_COUNTED only
        when the recovery was actually a double-count, not a source omission."""
        # Source-omitted: no total, components present.
        seg = segment(costs(per_diem="100.00", total=None))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert seg.costs.total.us_dollar.computed is True
        assert seg.costs.total.us_dollar.double_counted is False
        assert "ROW_TOTAL_COMPUTED" in seg.flags
        assert "ROW_TOTAL_DOUBLE_COUNTED" not in seg.flags

    def test_correction_clearing_double_counted_marker_clears_flag(self):
        """If a correction step supplies a real source total (clears `computed`
        and `double_counted`), the ROW_TOTAL_DOUBLE_COUNTED flag is cleared on
        revalidation."""
        seg = segment(costs(per_diem="305.00", total="610.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_DOUBLE_COUNTED" in seg.flags
        # Correction supplies a real total: clear markers, set raw.
        seg.costs.total.us_dollar.amount = Decimal("305.00")
        seg.costs.total.us_dollar.computed = False
        seg.costs.total.us_dollar.double_counted = False
        seg.costs.total.us_dollar.raw = "305.00"
        validate_report(r)
        assert "ROW_TOTAL_DOUBLE_COUNTED" not in seg.flags
        assert "ROW_TOTAL_COMPUTED" not in seg.flags


def segment_dated(total_costs, arrival, departure):
    """Like segment() but with real arrival/departure dates for day-count checks."""
    return TravelSegment(
        arrival_date=arrival,
        departure_date=departure,
        arrival_raw=arrival.strftime("%-m/%-d"),
        departure_raw=departure.strftime("%-m/%-d"),
        country_raw="Country",
        costs=total_costs,
    )


def costs_fx(per_diem_usd, per_diem_fx, total_usd, total_fx):
    """Costs with both USD and foreign-currency sides populated."""
    pd = CostGroup(
        foreign_currency=CostCell(amount=Decimal(per_diem_fx), raw=str(per_diem_fx)),
        us_dollar=CostCell(amount=Decimal(per_diem_usd), raw=str(per_diem_usd)),
    )
    tot = CostGroup(
        foreign_currency=CostCell(amount=Decimal(total_fx), raw=str(total_fx)),
        us_dollar=CostCell(amount=Decimal(total_usd), raw=str(total_usd)),
    )
    empty = cell()
    empty_group = CostGroup(foreign_currency=empty, us_dollar=empty)
    return Costs(per_diem=pd, transportation=empty_group, other=empty_group, total=tot)


class TestPerDiemTimesDays:
    """A segment whose declared total equals per_diem × (departure - arrival).days,
    with only per_diem populated (T and O empty), follows a recognizable source
    convention: the per_diem column is per-day and the source multiplies it by
    the day count to get the segment total, without breaking the multiplier into
    a separate component. E.g. Gingrich's 1997 China segment: per_diem=$255,
    3/27→3/30 (3 days), total=$765. Flag informationally as
    ROW_TOTAL_IS_PER_DIEM_X_DAYS; keep the declared total (do NOT overwrite).

    This must be detected BEFORE the double-count check: a 2-day segment of
    this shape (per_diem=305, total=610) is arithmetic-indistinguishable from
    a per_diem double-count (delta = per_diem × (days-1) = per_diem × 1 =
    per_diem) without the day-count check, and would otherwise be misclassified
    as ROW_TOTAL_DOUBLE_COUNTED with the correct total overwritten to per_diem.
    """

    def test_two_day_segment_flagged_not_double_counted(self):
        """The Korea regression: P=305, 3/24→3/26 (2d), tot=610 = 305×2.
        Previously misclassified as a per_diem double-count and overwritten to 305."""
        seg = segment_dated(costs(per_diem="305.00", total="610.00"), date(1997, 3, 24), date(1997, 3, 26))
        r = report([Traveler(name="Gingrich", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_IS_PER_DIEM_X_DAYS" in seg.flags
        assert "ROW_TOTAL_DOUBLE_COUNTED" not in seg.flags
        assert "ROW_TOTAL_COMPUTED" not in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("610.00")
        assert seg.costs.total.us_dollar.computed is False
        assert seg.costs.total.us_dollar.double_counted is False

    def test_three_day_segment_flagged(self):
        """Gingrich China: P=255, 3/27→3/30 (3d), tot=765 = 255×3."""
        seg = segment_dated(costs(per_diem="255.00", total="765.00"), date(1997, 3, 27), date(1997, 3, 30))
        r = report([Traveler(name="Gingrich", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_IS_PER_DIEM_X_DAYS" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("765.00")

    def test_one_day_segment_no_flag(self):
        """Gingrich Hong Kong: P=394, 3/26→3/27 (1d), tot=394 = 394×1.
        total == per_diem == computed, so no mismatch and no Shape A flag."""
        seg = segment_dated(costs(per_diem="394.00", total="394.00"), date(1997, 3, 26), date(1997, 3, 27))
        r = report([Traveler(name="Gingrich", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_IS_PER_DIEM_X_DAYS" not in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_total_not_equal_per_diem_times_days_stays_mismatch(self):
        """When the total doesn't match per_diem × days, the per_diem × days
        flag doesn't fire. A single-component segment with a large positive
        delta falls through to ROW_TOTAL_INCLUDES_UNBROKEN_COSTS (the source
        convention: total includes unbroken-out costs), not the generic
        ROW_SUM_MISMATCH."""
        seg = segment_dated(costs(per_diem="305.00", total="999.00"), date(1997, 3, 24), date(1997, 3, 26))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in seg.flags
        assert "ROW_TOTAL_IS_PER_DIEM_X_DAYS" not in seg.flags
        assert "ROW_TOTAL_DOUBLE_COUNTED" not in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_transportation_populated_not_shape_a(self):
        """Shape A requires T and O both empty. With transportation present, it's
        either a real mismatch or a double-count -- not per_diem × days."""
        seg = segment_dated(
            costs(per_diem="305.00", transportation="100.00", total="710.00"),
            date(1997, 3, 24), date(1997, 3, 26),
        )
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_IS_PER_DIEM_X_DAYS" not in seg.flags

    def test_foreign_currency_side_must_also_match(self):
        """Defense in depth: if both foreign-currency cells are populated, the FX
        side must also follow per_diem × days. A USD-only coincidence (e.g. a
        genuine double-count whose per_diem × days happens to hit the same
        number) won't reproduce on the FX side."""
        seg = segment_dated(
            costs_fx("305.00", "268400", "610.00", "400000"),
            date(1997, 3, 24), date(1997, 3, 26),
        )
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_IS_PER_DIEM_X_DAYS" not in seg.flags
        assert "ROW_TOTAL_DOUBLE_COUNTED" in seg.flags

    def test_foreign_currency_side_matches_shape_a_confirmed(self):
        """Gingrich Korea with both sides matching: USD 305×2=610, won 268400×2=536800."""
        seg = segment_dated(
            costs_fx("305.00", "268400", "610.00", "536800"),
            date(1997, 3, 24), date(1997, 3, 26),
        )
        r = report([Traveler(name="Gingrich", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_IS_PER_DIEM_X_DAYS" in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("610.00")
        assert seg.costs.total.foreign_currency.amount == Decimal("536800")

    def test_no_dates_falls_through_to_double_count(self):
        """Without arrival/departure dates, we can't compute days -- the Shape A
        check declines and the existing double-count fallback handles the
        per_diem=305, total=610 shape (the pre-fix behavior)."""
        seg = segment(costs(per_diem="305.00", total="610.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_IS_PER_DIEM_X_DAYS" not in seg.flags
        assert "ROW_TOTAL_DOUBLE_COUNTED" in seg.flags

    def test_flag_is_idempotent_across_revalidation(self):
        """Re-validating an already-validated segment doesn't duplicate the flag."""
        seg = segment_dated(costs(per_diem="255.00", total="765.00"), date(1997, 3, 27), date(1997, 3, 30))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        validate_report(r)
        assert seg.flags.count("ROW_TOTAL_IS_PER_DIEM_X_DAYS") == 1
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert "ROW_TOTAL_DOUBLE_COUNTED" not in seg.flags

    def test_total_still_included_in_table_sum(self):
        """The preserved source total counts toward the table-level sum check."""
        seg = segment_dated(costs(per_diem="255.00", total="765.00"), date(1997, 3, 27), date(1997, 3, 30))
        committee = Costs(
            per_diem=CostGroup(foreign_currency=cell(), us_dollar=cell()),
            transportation=CostGroup(foreign_currency=cell(), us_dollar=cell()),
            other=CostGroup(foreign_currency=cell(), us_dollar=cell()),
            total=CostGroup(foreign_currency=cell(), us_dollar=cell("765.00")),
        )
        r = report([Traveler(name="A", segments=[seg])], committee_total=committee)
        validate_report(r)
        assert "ROW_TOTAL_IS_PER_DIEM_X_DAYS" in seg.flags
        assert "TABLE_SUM_MISMATCH" not in r.flags


class TestTripTotalInOneSegment:
    """A source convention: the trip total (cumulative per_diem across all
    the traveler's segments) is filled into ONE segment's total cell, rather
    than a per-segment total. The parser already computes the other segments'
    totals (dot-filled → ROW_TOTAL_COMPUTED); the segment carrying the trip
    total would otherwise be flagged ROW_SUM_MISMATCH because its own per_diem
    doesn't sum to the trip total.

    Recovery: overwrite that segment's total with its own per_diem, preserve
    the source trip total in `source_amount`, flag ROW_TOTAL_IS_TRIP_TOTAL +
    ROW_TOTAL_COMPUTED. The committee total (== sum of trip totals == sum of
    per_diems) then matches the post-recovery sum of segment totals, so no
    TABLE_SUM_MISMATCH.

    The shape: traveler has 2+ segments, every segment has per_diem populated
    (USD > 0) with transport/other empty, exactly one segment has a
    source-declared (non-computed) total, and that total equals the sum of
    per_diems within tolerance. Both "last segment" and "first segment"
    conventions exist in the corpus.
    """

    def test_last_segment_carries_trip_total_recovered(self):
        """1995q4dec13-005 shape: France per_diem=834.46 (no total), Belgium
        per_diem=606.00 total=1440.46. 834.46 + 606.00 = 1440.46 → trip total
        in the last segment."""
        seg0 = segment(costs(per_diem="834.46"))
        seg1 = segment(costs(per_diem="606.00", total="1440.46"))
        committee = Costs(
            per_diem=CostGroup(foreign_currency=cell(), us_dollar=cell()),
            transportation=CostGroup(foreign_currency=cell(), us_dollar=cell()),
            other=CostGroup(foreign_currency=cell(), us_dollar=cell()),
            total=CostGroup(foreign_currency=cell(), us_dollar=cell("1440.46")),
        )
        r = report([Traveler(name="Bereuter", segments=[seg0, seg1])], committee_total=committee)
        validate_report(r)
        assert "ROW_TOTAL_COMPUTED" in seg0.flags
        assert "ROW_SUM_MISMATCH" not in seg0.flags
        assert seg0.costs.total.us_dollar.amount == Decimal("834.46")
        assert "ROW_TOTAL_IS_TRIP_TOTAL" in seg1.flags
        assert "ROW_TOTAL_COMPUTED" in seg1.flags
        assert "ROW_SUM_MISMATCH" not in seg1.flags
        assert seg1.costs.total.us_dollar.amount == Decimal("606.00")
        assert seg1.costs.total.us_dollar.computed is True
        assert seg1.costs.total.us_dollar.trip_total is True
        assert seg1.costs.total.us_dollar.source_amount == Decimal("1440.46")
        assert "TABLE_SUM_MISMATCH" not in r.flags

    def test_first_segment_carries_trip_total_recovered(self):
        """The mirror convention: trip total in the FIRST segment. 149 cases
        in the corpus."""
        seg0 = segment(costs(per_diem="606.00", total="1440.46"))
        seg1 = segment(costs(per_diem="834.46"))
        r = report([Traveler(name="A", segments=[seg0, seg1])])
        validate_report(r)
        assert "ROW_TOTAL_IS_TRIP_TOTAL" in seg0.flags
        assert "ROW_SUM_MISMATCH" not in seg0.flags
        assert seg0.costs.total.us_dollar.amount == Decimal("606.00")
        assert seg0.costs.total.us_dollar.source_amount == Decimal("1440.46")
        assert "ROW_TOTAL_COMPUTED" in seg1.flags
        assert seg1.costs.total.us_dollar.amount == Decimal("834.46")

    def test_three_segments_with_trip_total_in_last(self):
        """135 cases in the corpus: 3 segments with trip total in the last."""
        seg0 = segment(costs(per_diem="100.00"))
        seg1 = segment(costs(per_diem="200.00"))
        seg2 = segment(costs(per_diem="300.00", total="600.00"))
        r = report([Traveler(name="A", segments=[seg0, seg1, seg2])])
        validate_report(r)
        assert "ROW_TOTAL_IS_TRIP_TOTAL" in seg2.flags
        assert seg2.costs.total.us_dollar.amount == Decimal("300.00")
        assert seg2.costs.total.us_dollar.source_amount == Decimal("600.00")
        assert seg0.costs.total.us_dollar.amount == Decimal("100.00")
        assert seg1.costs.total.us_dollar.amount == Decimal("200.00")
        assert "ROW_SUM_MISMATCH" not in seg0.flags
        assert "ROW_SUM_MISMATCH" not in seg1.flags
        assert "ROW_SUM_MISMATCH" not in seg2.flags

    def test_sum_mismatch_stays_row_sum_mismatch(self):
        """When the source-declared total doesn't equal the sum of per_diems,
        the trip-total convention doesn't apply. The single-component segment
        with a large positive delta falls through to
        ROW_TOTAL_INCLUDES_UNBROKEN_COSTS (the source convention), not the
        generic ROW_SUM_MISMATCH."""
        seg0 = segment(costs(per_diem="834.46"))
        seg1 = segment(costs(per_diem="606.00", total="9999.00"))
        r = report([Traveler(name="A", segments=[seg0, seg1])])
        validate_report(r)
        assert "ROW_TOTAL_IS_TRIP_TOTAL" not in seg1.flags
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in seg1.flags
        assert "ROW_SUM_MISMATCH" not in seg1.flags
        assert seg1.costs.total.us_dollar.amount == Decimal("9999.00")
        assert seg1.costs.total.us_dollar.source_amount is None

    def test_single_segment_does_not_qualify(self):
        """Trip-total shape requires 2+ segments; a single segment with a
        per_diem that doesn't match its declared total falls through to
        ROW_TOTAL_INCLUDES_UNBROKEN_COSTS (single-component, positive delta)."""
        seg = segment(costs(per_diem="606.00", total="1440.46"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_IS_TRIP_TOTAL" not in seg.flags
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_transport_populated_does_not_qualify(self):
        """The trip-total shape requires transport and other to be empty
        across all the traveler's segments. With transport populated, this
        is not the trip-total convention."""
        seg0 = segment(costs(per_diem="834.46", transportation="100.00"))
        seg1 = segment(costs(per_diem="606.00", total="1440.46"))
        r = report([Traveler(name="A", segments=[seg0, seg1])])
        validate_report(r)
        assert "ROW_TOTAL_IS_TRIP_TOTAL" not in seg1.flags

    def test_one_segment_missing_per_diem_does_not_qualify(self):
        """Every segment must have a per_diem populated; a missing per_diem in
        any segment disqualifies the shape."""
        seg0 = segment(costs())
        seg1 = segment(costs(per_diem="606.00", total="606.00"))
        r = report([Traveler(name="A", segments=[seg0, seg1])])
        validate_report(r)
        assert "ROW_TOTAL_IS_TRIP_TOTAL" not in seg1.flags

    def test_two_declared_totals_does_not_qualify(self):
        """If more than one segment has a source-declared total, the trip-total
        interpretation doesn't apply."""
        seg0 = segment(costs(per_diem="834.46", total="834.46"))
        seg1 = segment(costs(per_diem="606.00", total="606.00"))
        r = report([Traveler(name="A", segments=[seg0, seg1])])
        validate_report(r)
        assert "ROW_TOTAL_IS_TRIP_TOTAL" not in seg0.flags
        assert "ROW_TOTAL_IS_TRIP_TOTAL" not in seg1.flags
        assert "ROW_SUM_MISMATCH" not in seg0.flags
        assert "ROW_SUM_MISMATCH" not in seg1.flags

    def test_preempts_per_diem_x_days_when_ambiguous(self):
        """Edge case: a traveler with 2 segments of equal per_diem P, last
        segment 2 days with total=2P. Both interpretations fit:
        - per_diem × days: P × 2 = 2P (keep 2P)
        - trip total: 2P = P + P (recover to P)

        Trip total wins: it's the more specific shape (multi-segment), and
        matches the committee total (2P per traveler, not 3P which per_diem
        × days would imply)."""
        seg0 = segment_dated(costs(per_diem="305.00"), date(1997, 3, 24), date(1997, 3, 25))
        seg1 = segment_dated(costs(per_diem="305.00", total="610.00"), date(1997, 3, 25), date(1997, 3, 27))
        committee = Costs(
            per_diem=CostGroup(foreign_currency=cell(), us_dollar=cell()),
            transportation=CostGroup(foreign_currency=cell(), us_dollar=cell()),
            other=CostGroup(foreign_currency=cell(), us_dollar=cell()),
            total=CostGroup(foreign_currency=cell(), us_dollar=cell("610.00")),
        )
        r = report([Traveler(name="A", segments=[seg0, seg1])], committee_total=committee)
        validate_report(r)
        assert "ROW_TOTAL_IS_TRIP_TOTAL" in seg1.flags
        assert "ROW_TOTAL_IS_PER_DIEM_X_DAYS" not in seg1.flags
        assert seg1.costs.total.us_dollar.amount == Decimal("305.00")
        assert "TABLE_SUM_MISMATCH" not in r.flags

    def test_idempotent_across_revalidation(self):
        """Re-validating doesn't duplicate the flag and preserves recovery."""
        seg0 = segment(costs(per_diem="834.46"))
        seg1 = segment(costs(per_diem="606.00", total="1440.46"))
        r = report([Traveler(name="A", segments=[seg0, seg1])])
        validate_report(r)
        validate_report(r)
        assert seg1.flags.count("ROW_TOTAL_IS_TRIP_TOTAL") == 1
        assert seg1.flags.count("ROW_TOTAL_COMPUTED") == 1
        assert "ROW_SUM_MISMATCH" not in seg1.flags
        assert seg1.costs.total.us_dollar.amount == Decimal("606.00")
        assert seg1.costs.total.us_dollar.source_amount == Decimal("1440.46")

    def test_multiple_travelers_each_recovered(self):
        """Multiple travelers in the same report each get their own recovery."""
        segs_a = [segment(costs(per_diem="834.46")), segment(costs(per_diem="606.00", total="1440.46"))]
        segs_b = [segment(costs(per_diem="834.46")), segment(costs(per_diem="606.00", total="1440.46"))]
        r = report([
            Traveler(name="Bereuter", segments=segs_a),
            Traveler(name="Solomon", segments=segs_b),
        ])
        validate_report(r)
        for segs in (segs_a, segs_b):
            assert "ROW_TOTAL_IS_TRIP_TOTAL" in segs[1].flags
            assert "ROW_SUM_MISMATCH" not in segs[1].flags
            assert segs[1].costs.total.us_dollar.amount == Decimal("606.00")
            assert segs[1].costs.total.us_dollar.source_amount == Decimal("1440.46")


class TestRowSumRounding:
    """A segment whose component sum is within a small threshold of the
    declared total is a source rounding / small typo, not a genuine
    arithmetic error. Downgrade from ROW_SUM_MISMATCH to ROW_SUM_ROUNDING
    (informational). Keep the source-declared total -- consumers care about
    the source's stated amount.

    Threshold: min($5.00, 1% of declared_total), mirroring the table-level
    TABLE_SUM_ROUNDING rule. Runs after the more specific recoveries
    (supplement-merge, trip-total, per_diem × days, double-count) so those
    patterns stay specific.
    """

    def test_sub_cent_delta_classified_as_rounding(self):
        """14 cases in corpus: sub-cent noise (e.g. 837.06 vs 837.10)."""
        seg = segment(costs(per_diem="837.06", total="837.10"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_SUM_ROUNDING" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("837.10")
        assert seg.costs.total.us_dollar.computed is False

    def test_small_absolute_delta_classified_as_rounding(self):
        """Most common: small sub-dollar delta on a 1-component segment
        (source rounded per_diem to a clean total)."""
        seg = segment(costs(per_diem="423.75", total="423.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_SUM_ROUNDING" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_two_component_rounding(self):
        """Delta on a 2-component breakdown is also rounding."""
        seg = segment(costs(per_diem="950.00", transportation="1736.80", total="2686.30"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_SUM_ROUNDING" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_three_component_rounding(self):
        seg = segment(
            costs(per_diem="408.50", transportation="3417.45", other="93.70", total="3919.90")
        )
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_SUM_ROUNDING" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_percent_based_rounding_on_large_total(self):
        """A $3 delta on a $2000 total is 0.15% -- within 1%, classified as
        rounding. min($5, $20) = $5; $3 < $5 → rounding."""
        seg = segment(costs(per_diem="1000.00", transportation="997.00", total="2000.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_SUM_ROUNDING" in seg.flags

    def test_large_delta_above_absolute_cap_stays_mismatch(self):
        """A $30 delta on a $3600 total is below 1% ($36) but above the $5
        absolute cap. min($5, $36) = $5; $30 > $5 → not rounding. Positive
        delta → ROW_TOTAL_INCLUDES_UNBROKEN_COSTS (not ROW_SUM_ROUNDING)."""
        seg = segment(
            costs(per_diem="1225.00", transportation="1800.25", other="568.00", total="3623.25")
        )
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in seg.flags
        assert "ROW_SUM_ROUNDING" not in seg.flags

    def test_small_total_uses_percent_threshold(self):
        """For a $100 total, 1% = $1. A $3 delta is above $1 but below $5.
        min($5, $1) = $1; $3 > $1 → not rounding. Positive delta →
        ROW_TOTAL_INCLUDES_UNBROKEN_COSTS (not ROW_SUM_ROUNDING)."""
        seg = segment(costs(per_diem="50.00", transportation="47.00", total="100.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in seg.flags
        assert "ROW_SUM_ROUNDING" not in seg.flags

    def test_does_not_preempt_per_diem_x_days(self):
        """A segment that fits per_diem × days keeps that more specific flag
        even if the delta is also within rounding."""
        # per_diem=255, 3/27→3/30 (3 days), total=765 = 255×3. Delta=0.
        # The per_diem_x_days check fires first; rounding never reached.
        seg = segment_dated(costs(per_diem="255.00", total="765.00"), date(1997, 3, 27), date(1997, 3, 30))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_IS_PER_DIEM_X_DAYS" in seg.flags
        assert "ROW_SUM_ROUNDING" not in seg.flags

    def test_does_not_preempt_trip_total(self):
        """A segment in a trip-total shape keeps that more specific flag."""
        seg0 = segment(costs(per_diem="834.46"))
        seg1 = segment(costs(per_diem="606.00", total="1440.46"))
        r = report([Traveler(name="A", segments=[seg0, seg1])])
        validate_report(r)
        assert "ROW_TOTAL_IS_TRIP_TOTAL" in seg1.flags
        assert "ROW_SUM_ROUNDING" not in seg1.flags

    def test_does_not_preempt_double_count(self):
        """A per_diem double-count (per_diem=305, total=610, no other signal)
        keeps the double-count flag even though |delta| = per_diem is
        within rounding."""
        seg = segment(costs(per_diem="305.00", total="610.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_DOUBLE_COUNTED" in seg.flags
        assert "ROW_SUM_ROUNDING" not in seg.flags

    def test_idempotent_across_revalidation(self):
        seg = segment(costs(per_diem="423.75", total="423.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        validate_report(r)
        assert seg.flags.count("ROW_SUM_ROUNDING") == 1
        assert "ROW_SUM_MISMATCH" not in seg.flags


class TestRowTotalCommaDecimalTypo:
    """A recurring source typo: the writer used a comma where a decimal point
    should be (e.g. per_diem=`1,204.00`, total=`1,204,00` which the parser
    reads as 120400). Recovery: overwrite total = single component, preserve
    the source-declared (100×) total in `source_amount`, flag
    `ROW_TOTAL_COMMA_DECIMAL_TYPO` + `ROW_TOTAL_COMPUTED`. 12 cases in corpus.
    """

    def test_comma_decimal_typo_recovered(self):
        """1997q2jun17 Bosnia: pd=1204, total raw `1,204,00` parsed as 120400.
        Recovery: total = 1204 (= per_diem)."""
        seg = segment(costs(per_diem="1204.00", total="120400"))
        r = report([Traveler(name="Herzberg", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMMA_DECIMAL_TYPO" in seg.flags
        assert "ROW_TOTAL_COMPUTED" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" not in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("1204.00")
        assert seg.costs.total.us_dollar.source_amount == Decimal("120400")
        assert seg.costs.total.us_dollar.computed is True
        assert seg.costs.total.us_dollar.comma_decimal_typo is True

    def test_comma_decimal_typo_with_cents(self):
        """1998q4nov12 Clay France: pd=1448.46, total raw `1,448,46` → 144846."""
        seg = segment(costs(per_diem="1448.46", total="144846"))
        r = report([Traveler(name="Clay", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMMA_DECIMAL_TYPO" in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("1448.46")
        assert seg.costs.total.us_dollar.source_amount == Decimal("144846")

    def test_transportation_only_comma_typo(self):
        """A single-component transportation segment with a 100× total also
        gets the recovery (e.g. a 'Commercial airfare' supplement row)."""
        seg = segment(costs(transportation="8623.84", total="862384.00"))
        r = report([Traveler(name="Commercial airfare", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMMA_DECIMAL_TYPO" in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("8623.84")

    def test_multi_component_100x_not_typo(self):
        """A multi-component segment with a 100× total but a clean raw
        (no comma-as-decimal typo shape) is NOT the comma-decimal typo --
        the raw-shape gate on the multi-component recovery excludes it.
        Stays ROW_SUM_MISMATCH or another downstream classification."""
        seg = segment(costs(per_diem="100.00", transportation="200.00", total="30000.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMMA_DECIMAL_TYPO" not in seg.flags

    def test_not_100x_stays_other_classification(self):
        """A single-component segment with a 10× total (not 100×) is not the
        comma-decimal typo -- it falls through to the next check."""
        # 432 × 10 = 4320. Positive delta, single component → INCLUDES_UNBROKEN.
        seg = segment(costs(per_diem="432.00", total="4320.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMMA_DECIMAL_TYPO" not in seg.flags
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in seg.flags

    def test_idempotent_across_revalidation(self):
        seg = segment(costs(per_diem="1204.00", total="120400"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        validate_report(r)
        assert seg.flags.count("ROW_TOTAL_COMMA_DECIMAL_TYPO") == 1
        assert seg.flags.count("ROW_TOTAL_COMPUTED") == 1
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("1204.00")
        assert seg.costs.total.us_dollar.source_amount == Decimal("120400")


class TestRowTotalMultiComponentCommaDecimalTypo:
    """Multi-component segment whose total cell raw has the comma-as-decimal
    typo shape (e.g. `2,345,36` parsed as 234536, intended `2,345.36` =
    1622.76 + 722.60). The single-component `_is_100x_typo` recovery doesn't
    fire (it requires `len(components) == 1`); this recovery extends to
    multi-component segments by also gating on the raw shape. 16 segments
    across 6 reports in corpus; 1 TSM resolved (2010q1mar12-003).
    """

    def _seg_with_typo_total(self, per_diem, transportation, other, total_raw, total_amount):
        """Build a segment whose total cell has a typo-shaped raw and an
        inflated parsed amount, with the given component amounts."""
        empty = cell()
        total_cell = CostCell(amount=Decimal(total_amount), raw=total_raw)
        return segment(
            Costs(
                per_diem=CostGroup(foreign_currency=empty, us_dollar=cell(per_diem)),
                transportation=CostGroup(
                    foreign_currency=empty, us_dollar=cell(transportation)
                ),
                other=CostGroup(foreign_currency=empty, us_dollar=cell(other)),
                total=CostGroup(foreign_currency=empty, us_dollar=total_cell),
            )
        )

    def test_2digit_typo_divides_by_100(self):
        """2010q1mar12-003: pd=482.00 + tr=1250.80 = 1732.80, total raw
        `1,732,80` parsed as 173280. Recovery: total = 1732.80."""
        seg = self._seg_with_typo_total(
            per_diem="482.00", transportation="1250.80", other=None,
            total_raw="  1,732,80  ", total_amount="173280",
        )
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMMA_DECIMAL_TYPO" in seg.flags
        assert "ROW_TOTAL_COMPUTED" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("1732.80")
        assert seg.costs.total.us_dollar.source_amount == Decimal("173280")
        assert seg.costs.total.us_dollar.computed is True
        assert seg.costs.total.us_dollar.comma_decimal_typo is True

    def test_3digit_typo_divides_by_1000(self):
        """A 3-digit last group (`,NNN`) divides by 1000. E.g. pd=5000 +
        tr=2202 = 7202, total raw `7,202,000` parsed as 7202000."""
        seg = self._seg_with_typo_total(
            per_diem="5000.00", transportation="2202.00", other=None,
            total_raw="7,202,000", total_amount="7202000",
        )
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMMA_DECIMAL_TYPO" in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("7202.00")
        assert seg.costs.total.us_dollar.source_amount == Decimal("7202000")

    def test_3component_typo_recovered(self):
        """2015q2apr27-000: tr=458.64 + ot=787.17 = 1245.81, total raw
        `1,245,81` parsed as 124581. Three components (pd is dot-fill)."""
        seg = self._seg_with_typo_total(
            per_diem=None, transportation="458.64", other="787.17",
            total_raw="   1,245,81", total_amount="124581",
        )
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMMA_DECIMAL_TYPO" in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("1245.81")

    def test_clean_raw_not_recovered(self):
        """Multi-component segment with a clean raw (decimal point, no
        comma-as-decimal shape) doesn't trigger the recovery even when
        declared_total == computed * 100. Falls through to downstream
        classification."""
        # raw `30000.00` has a decimal point → not typo-shaped.
        # pd=100 + tr=200 = 300, total=30000 (100×). No raw-shape gate hit.
        seg = segment(costs(per_diem="100.00", transportation="200.00", total="30000.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMMA_DECIMAL_TYPO" not in seg.flags

    def test_arithmetic_mismatch_not_recovered(self):
        """Typo-shaped raw but the divided total doesn't equal the component
        sum. No recovery; falls through."""
        # raw `2,345,36` → /100 = 2345.36, but components sum to 999.00.
        seg = self._seg_with_typo_total(
            per_diem="500.00", transportation="499.00", other=None,
            total_raw="2,345,36", total_amount="234536",
        )
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMMA_DECIMAL_TYPO" not in seg.flags

    def test_single_component_typo_uses_existing_path(self):
        """Single-component typo-shaped raw still recovers via the existing
        `_is_100x_typo` path (which doesn't require raw shape). Verifies
        the new branch doesn't shadow or duplicate the existing one."""
        seg = self._seg_with_typo_total(
            per_diem="1204.00", transportation=None, other=None,
            total_raw="1,204,00", total_amount="120400",
        )
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMMA_DECIMAL_TYPO" in seg.flags
        assert seg.flags.count("ROW_TOTAL_COMMA_DECIMAL_TYPO") == 1
        assert seg.flags.count("ROW_TOTAL_COMPUTED") == 1
        assert seg.costs.total.us_dollar.amount == Decimal("1204.00")

    def test_idempotent_across_revalidation(self):
        seg = self._seg_with_typo_total(
            per_diem="482.00", transportation="1250.80", other=None,
            total_raw="  1,732,80  ", total_amount="173280",
        )
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        validate_report(r)
        assert seg.flags.count("ROW_TOTAL_COMMA_DECIMAL_TYPO") == 1
        assert seg.flags.count("ROW_TOTAL_COMPUTED") == 1
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("1732.80")
        assert seg.costs.total.us_dollar.source_amount == Decimal("173280")


def _typo_cell(raw: str, amount: str) -> CostCell:
    """Build a component cell whose raw carries a comma-decimal typo and
    whose amount is the inflated parse (e.g. raw=`749,00`, amount=74900)."""
    return CostCell(amount=Decimal(amount), raw=raw)


def _typo_costs(
    per_diem=None, transportation=None, other=None, total=None
) -> Costs:
    """Build Costs from (raw, amount) tuples for components and a plain
    decimal for the total. Empty components default to dot-fill."""
    empty = CostCell(amount=None, raw="...........")

    def group(spec):
        if spec is None:
            return CostGroup(foreign_currency=empty, us_dollar=empty)
        raw, amt = spec
        return CostGroup(foreign_currency=empty, us_dollar=_typo_cell(raw, amt))

    total_cell = cell() if total is None else CostCell(amount=Decimal(total), raw=str(total))
    return Costs(
        per_diem=group(per_diem),
        transportation=group(transportation),
        other=group(other),
        total=CostGroup(foreign_currency=empty, us_dollar=total_cell),
    )


class TestRowComponentCommaDecimalTypo:
    """A recurring source typo: a component cell (per_diem / transportation /
    other) used a comma where a decimal point should be (e.g. `749,00` parsed
    as 74900, `1,123,000` parsed as 1123000). The declared total is correct;
    recovery overwrites the component cell with `amount/divisor`, preserves
    the source-declared (inflated) value in `source_amount`, and replaces
    the would-be ROW_TOTAL_LESS_THAN_COMPONENT flag with
    ROW_COMPONENT_COMMA_DECIMAL_TYPO. 35 segments across 24 reports in corpus.
    """

    def test_single_component_typo_recovered(self):
        """1997q3sep23-001 Dagne: pd raw `749,00` parsed as 74900, total 749.00."""
        seg = segment(_typo_costs(per_diem=("749,00", "74900"), total="749.00"))
        r = report([Traveler(name="Dagne", segments=[seg])])
        validate_report(r)
        assert "ROW_COMPONENT_COMMA_DECIMAL_TYPO" in seg.flags
        assert "ROW_TOTAL_LESS_THAN_COMPONENT" not in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        pd = seg.costs.per_diem.us_dollar
        assert pd.amount == Decimal("749.00")
        assert pd.source_amount == Decimal("74900")
        assert pd.computed is True
        assert pd.comma_decimal_typo is True

    def test_two_component_typos_same_segment(self):
        """2015q2may12-006 Morocco: pd raw `749,00`→74900 AND ot raw
        `1,262,07`→126207; total 2011.07. Both typos must be fixed together
        for comp_sum to equal total."""
        seg = segment(
            _typo_costs(
                per_diem=("749,00", "74900"),
                other=("1,262,07", "126207"),
                total="2011.07",
            )
        )
        r = report([Traveler(name="Stewart", segments=[seg])])
        validate_report(r)
        assert "ROW_COMPONENT_COMMA_DECIMAL_TYPO" in seg.flags
        assert "ROW_TOTAL_LESS_THAN_COMPONENT" not in seg.flags
        assert seg.costs.per_diem.us_dollar.amount == Decimal("749.00")
        assert seg.costs.other.us_dollar.amount == Decimal("1262.07")
        assert seg.costs.per_diem.us_dollar.source_amount == Decimal("74900")
        assert seg.costs.other.us_dollar.source_amount == Decimal("126207")

    def test_three_digit_typo_divisor_1000(self):
        """1999q2may14-003 Knollenberg: pd raw `480,000` parsed as 480000;
        total 480.00. The 3-digit last group requires /1000 (not /100)."""
        seg = segment(_typo_costs(per_diem=("480,000", "480000"), total="480.00"))
        r = report([Traveler(name="Knollenberg", segments=[seg])])
        validate_report(r)
        assert "ROW_COMPONENT_COMMA_DECIMAL_TYPO" in seg.flags
        assert seg.costs.per_diem.us_dollar.amount == Decimal("480.00")
        assert seg.costs.per_diem.us_dollar.source_amount == Decimal("480000")

    def test_typo_with_other_normal_component(self):
        """2003q1jan31-012 Turner: pd raw `1,431,25`→143125 (typo), tr 6551.69
        (clean), ot 127.91 (clean); total 8110.85. The typo'd component is
        fixed while the clean components pass through unchanged."""
        seg = segment(
            _typo_costs(
                per_diem=("1,431,25", "143125"),
                transportation=("6,551.69", "6551.69"),
                other=("127.91", "127.91"),
                total="8110.85",
            )
        )
        r = report([Traveler(name="Turner", segments=[seg])])
        validate_report(r)
        assert "ROW_COMPONENT_COMMA_DECIMAL_TYPO" in seg.flags
        assert seg.costs.per_diem.us_dollar.amount == Decimal("1431.25")
        assert seg.costs.transportation.us_dollar.amount == Decimal("6551.69")
        assert seg.costs.other.us_dollar.amount == Decimal("127.91")

    def test_residual_delta_not_recovered(self):
        """2005q4nov17-026 Beutel: ot raw `2,141,76`→214176 (typo), total
        2141.24. Fixing the typo gives 2141.76, off from total by 0.52 — the
        source total itself is wrong, not just the component. The typo
        recovery must NOT fire; the segment falls through to ROW_TOTAL_LESS_THAN_COMPONENT."""
        seg = segment(_typo_costs(other=("2,141,76", "214176"), total="2141.24"))
        r = report([Traveler(name="Beutel", segments=[seg])])
        validate_report(r)
        assert "ROW_COMPONENT_COMMA_DECIMAL_TYPO" not in seg.flags
        # Without recovery, comp_sum (214176) > total (2141.24) →
        # ROW_TOTAL_LESS_THAN_COMPONENT
        assert "ROW_TOTAL_LESS_THAN_COMPONENT" in seg.flags
        assert seg.costs.other.us_dollar.amount == Decimal("214176")

    def test_idempotent_across_revalidation(self):
        """After recovery, comp_sum == total, so the mismatch check no
        longer fires. The marker on the component cells is the only signal
        to re-derive ROW_COMPONENT_COMMA_DECIMAL_TYPO."""
        seg = segment(_typo_costs(per_diem=("749,00", "74900"), total="749.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        validate_report(r)
        assert seg.flags.count("ROW_COMPONENT_COMMA_DECIMAL_TYPO") == 1
        assert "ROW_TOTAL_LESS_THAN_COMPONENT" not in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert seg.costs.per_diem.us_dollar.amount == Decimal("749.00")
        assert seg.costs.per_diem.us_dollar.source_amount == Decimal("74900")
        assert seg.costs.per_diem.us_dollar.comma_decimal_typo is True

    def test_clean_amounts_not_recovered(self):
        """A segment with clean component raws (decimal points present) and
        a comp_sum > total falls through to ROW_TOTAL_LESS_THAN_COMPONENT —
        the typo recovery only fires when a component raw lacks a decimal
        point and ends with a comma group."""
        seg = segment(
            costs(per_diem="1000.00", transportation="500.00", total="1200.00")
        )
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_COMPONENT_COMMA_DECIMAL_TYPO" not in seg.flags
        assert "ROW_TOTAL_LESS_THAN_COMPONENT" in seg.flags

    def test_recovers_to_internally_consistent_segment(self):
        """After recovery, the segment's comp_sum equals its declared total.
        The table-sum check sees a 0 row-flag residual (the recovery eliminated
        the mismatch rather than explaining it)."""
        seg = segment(_typo_costs(per_diem=("749,00", "74900"), total="749.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        # comp_sum == declared_total (no segment-level residual)
        from official_foreign_travel.parsing.validate import _group_total
        assert _group_total(seg.costs) == Decimal("749.00")
        assert seg.costs.total.us_dollar.amount == Decimal("749.00")


class TestSingleComponentUnmatchedDowngrades:
    """A segment with exactly one cost component populated, whose declared
    total doesn't match the component (delta exceeds rounding), is a source
    convention -- not a genuine arithmetic error.

    - Positive delta: source's total includes unbroken-out costs (shared
      airfare, etc.) → ROW_TOTAL_INCLUDES_UNBROKEN_COSTS.
    - Negative delta: source's total is less than the component (per-diem
      deductions/returns) → ROW_TOTAL_LESS_THAN_COMPONENT.

    Both keep the source-declared total as-is.
    """

    def test_positive_delta_classified_as_includes_unbroken_costs(self):
        """1995q1feb09 Pete Peterson: pd=173, tot=1176.15, delta=1003.15.
        Source convention: total includes unbroken-out airfare."""
        seg = segment(costs(per_diem="173.00", total="1176.15"))
        r = report([Traveler(name="Peterson", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert "ROW_TOTAL_LESS_THAN_COMPONENT" not in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("1176.15")
        assert seg.costs.total.us_dollar.computed is False
        assert seg.costs.total.us_dollar.source_amount is None

    def test_negative_delta_classified_as_less_than_component(self):
        """1996q3sep11 Wise: pd=1216, tot=1056, delta=-160. Source convention:
        per_diem column shows full rate, total reflects deductions."""
        seg = segment(costs(per_diem="1216.00", total="1056.00"))
        r = report([Traveler(name="Wise", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_LESS_THAN_COMPONENT" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" not in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("1056.00")

    def test_transportation_only_positive_delta(self):
        seg = segment(costs(transportation="4104.30", total="4104.03"))
        r = report([Traveler(name="Hoyer", segments=[seg])])
        validate_report(r)
        # delta = -0.27 -- within rounding! So ROW_SUM_ROUNDING fires.
        assert "ROW_SUM_ROUNDING" in seg.flags
        assert "ROW_TOTAL_LESS_THAN_COMPONENT" not in seg.flags

    def test_transportation_only_large_negative_delta(self):
        seg = segment(costs(transportation="4104.30", total="3000.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_LESS_THAN_COMPONENT" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_other_only_positive_delta(self):
        seg = segment(costs(other="2100.73", total="5000.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in seg.flags

    def test_multi_component_does_not_qualify(self):
        """Two or more components populated: the generalized delta-sign
        downgrade applies. Positive delta → ROW_TOTAL_INCLUDES_UNBROKEN_COSTS."""
        seg = segment(costs(per_diem="100.00", transportation="200.00", total="9999.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in seg.flags
        assert "ROW_TOTAL_LESS_THAN_COMPONENT" not in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_small_delta_goes_to_rounding_instead(self):
        """A single-component segment whose delta is within the rounding
        threshold gets ROW_SUM_ROUNDING, not the unbroken-costs downgrade."""
        seg = segment(costs(per_diem="423.75", total="423.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_SUM_ROUNDING" in seg.flags
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" not in seg.flags

    def test_100x_typo_preempts_unbroken_costs(self):
        """A 100× typo also has positive delta > rounding, but the typo
        check fires first."""
        seg = segment(costs(per_diem="1204.00", total="120400"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_COMMA_DECIMAL_TYPO" in seg.flags
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" not in seg.flags

    def test_idempotent_across_revalidation(self):
        seg = segment(costs(per_diem="173.00", total="1176.15"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        validate_report(r)
        assert seg.flags.count("ROW_TOTAL_INCLUDES_UNBROKEN_COSTS") == 1
        assert "ROW_SUM_MISMATCH" not in seg.flags


class TestComponentExcludedSubset:
    """A multi-component segment whose declared total equals the sum of a
    subset of its populated components. The source convention excludes
    certain components from the declared total (e.g. DoD-provided transport
    not counted, per_diem reimbursed separately / returned). Mirrors the
    table-level TABLE_SUM_TRANSPORT_EXCLUDED convention.

    Three flags, one per excluded component:
    - ROW_TOTAL_TRANSPORT_EXCLUDED
    - ROW_TOTAL_OTHER_EXCLUDED
    - ROW_TOTAL_PER_DIEM_EXCLUDED

    The source-declared total is kept as-is; this is informational.
    """

    def test_transport_excluded_pd_only_in_total(self):
        """declared = per_diem only (transportation excluded). The exact case
        from `test_source_excluded_component_stays_flagged`: pd=992,
        tr=461.20, tot=992."""
        seg = segment(costs(per_diem="992.00", transportation="461.20", total="992.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_TRANSPORT_EXCLUDED" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("992.00")
        assert seg.costs.total.us_dollar.computed is False

    def test_other_excluded_pd_only_in_total(self):
        """declared = per_diem only (other excluded)."""
        seg = segment(costs(per_diem="500.00", other="300.00", total="500.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_OTHER_EXCLUDED" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("500.00")

    def test_transport_excluded_other_only_in_total(self):
        """declared = other only (transportation excluded)."""
        seg = segment(costs(transportation="800.00", other="400.00", total="400.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_TRANSPORT_EXCLUDED" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_per_diem_excluded_transport_only_in_total(self):
        """declared = transportation only (per_diem excluded)."""
        seg = segment(costs(per_diem="600.00", transportation="700.00", total="700.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_PER_DIEM_EXCLUDED" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_pd_ot_excluded_transport_in_total(self):
        """declared = transportation only (per_diem and other both excluded)."""
        seg = segment(costs(per_diem="200.00", transportation="900.00", other="350.00", total="900.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_PER_DIEM_EXCLUDED" in seg.flags
        assert "ROW_TOTAL_OTHER_EXCLUDED" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_no_subset_match_gets_delta_sign_downgrade(self):
        """declared doesn't match any subset sum → falls through to the
        generalized delta-sign downgrade. Positive delta →
        ROW_TOTAL_INCLUDES_UNBROKEN_COSTS (not a subset-exclusion flag)."""
        seg = segment(costs(per_diem="100.00", transportation="50.00", total="999.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert "ROW_TOTAL_TRANSPORT_EXCLUDED" not in seg.flags
        assert "ROW_TOTAL_OTHER_EXCLUDED" not in seg.flags
        assert "ROW_TOTAL_PER_DIEM_EXCLUDED" not in seg.flags

    def test_ambiguous_equal_components_prefers_transport_excluded(self):
        """When two populated components have equal amounts and the total
        matches either single-component subset, prefer transport-excluded
        (the most common convention per table-level precedent)."""
        seg = segment(costs(per_diem="500.00", transportation="500.00", total="500.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_TRANSPORT_EXCLUDED" in seg.flags
        assert "ROW_TOTAL_PER_DIEM_EXCLUDED" not in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_idempotent_across_revalidation(self):
        seg = segment(costs(per_diem="992.00", transportation="461.20", total="992.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        validate_report(r)
        assert seg.flags.count("ROW_TOTAL_TRANSPORT_EXCLUDED") == 1
        assert "ROW_SUM_MISMATCH" not in seg.flags


class TestMultiComponentUnmatchedDowngrades:
    """A multi-component segment (2+ populated components) whose declared
    total doesn't match any subset sum of its components, and whose delta
    exceeds the rounding threshold, is a recognized source convention --
    not a genuine arithmetic error. Mirrors the single-component downgrades
    but for the multi-component case.

    - Positive delta (declared > component sum): source's total includes
      unbroken-out costs (shared airfare, etc.) → ROW_TOTAL_INCLUDES_UNBROKEN_COSTS.
    - Negative delta (declared < component sum): source's total reflects
      deductions (returned per-diem, host-provided meals) →
      ROW_TOTAL_LESS_THAN_COMPONENT.

    Runs AFTER the subset-exclusion check (more specific). The source-declared
    total is kept as-is in both cases.
    """

    def test_positive_delta_classified_as_includes_unbroken_costs(self):
        """1997q2apr23-024 Stuart Symington: pd=338, tr=1244, tot=2582.
        Delta = 2582 - 1582 = 1000 (unbroken-out airfare)."""
        seg = segment(costs(per_diem="338.00", transportation="1244.00", total="2582.00"))
        r = report([Traveler(name="Symington", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert "ROW_TOTAL_LESS_THAN_COMPONENT" not in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("2582.00")
        assert seg.costs.total.us_dollar.computed is False

    def test_negative_delta_classified_as_less_than_component(self):
        """1994q2may17-007 Richard Weaver: pd=759.75, tr=33036.35, ot=77.09,
        tot=3873.19. Delta = 3873.19 - 33873.19 = -30000 (large deduction)."""
        seg = segment(costs(per_diem="759.75", transportation="33036.35", other="77.09", total="3873.19"))
        r = report([Traveler(name="Weaver", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_LESS_THAN_COMPONENT" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" not in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("3873.19")

    def test_three_component_positive_delta(self):
        seg = segment(costs(per_diem="100.00", transportation="200.00", other="300.00", total="9999.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_three_component_negative_delta(self):
        seg = segment(costs(per_diem="100.00", transportation="200.00", other="300.00", total="50.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_LESS_THAN_COMPONENT" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_subset_match_takes_priority_over_delta_sign(self):
        """A segment where declared = subset sum gets the specific
        subset-exclusion flag, NOT the delta-sign downgrade."""
        seg = segment(costs(per_diem="992.00", transportation="461.20", total="992.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_TRANSPORT_EXCLUDED" in seg.flags
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" not in seg.flags
        assert "ROW_TOTAL_LESS_THAN_COMPONENT" not in seg.flags

    def test_small_delta_goes_to_rounding_instead(self):
        """A multi-component segment whose delta is within the rounding
        threshold gets ROW_SUM_ROUNDING, not the delta-sign downgrade."""
        seg = segment(costs(per_diem="100.00", transportation="50.00", total="150.50"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_SUM_ROUNDING" in seg.flags
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" not in seg.flags
        assert "ROW_TOTAL_LESS_THAN_COMPONENT" not in seg.flags

    def test_no_components_populated_stays_mismatch(self):
        """A segment with no populated components (all empty/negative) but a
        declared total: if per_diem is negative and total = abs(per_diem),
        it's the negative-per_diem refund shape → ROW_TOTAL_NEGATIVE_PER_DIEM."""
        seg = segment(costs(per_diem="-100.00", total="100.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_NEGATIVE_PER_DIEM" in seg.flags
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" not in seg.flags
        assert "ROW_TOTAL_LESS_THAN_COMPONENT" not in seg.flags

    def test_idempotent_across_revalidation(self):
        seg = segment(costs(per_diem="338.00", transportation="1244.00", total="2582.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        validate_report(r)
        assert seg.flags.count("ROW_TOTAL_INCLUDES_UNBROKEN_COSTS") == 1
        assert "ROW_SUM_MISMATCH" not in seg.flags


class TestNegativePerDiemRefund:
    """A segment with a negative per_diem (trailing minus in the source, e.g.
    `1,060.00-` which the parser reads as -1060.00), no other populated
    components, and a declared total equal to the absolute value of per_diem.
    Source convention: the total is the absolute value of the negatively-
    written per_diem. All 3 cases are in `2017q4dec06-000` (Janice Robinson).

    Keep the source-declared total as-is; flag `ROW_TOTAL_NEGATIVE_PER_DIEM`
    (informational).
    """

    def test_negative_per_diem_with_abs_total(self):
        """2017q4dec06-000 Janice Robinson Netherlands: pd=-1060, tot=1060."""
        seg = segment(costs(per_diem="-1060.00", total="1060.00"))
        r = report([Traveler(name="Robinson", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_NEGATIVE_PER_DIEM" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags
        assert seg.costs.total.us_dollar.amount == Decimal("1060.00")
        assert seg.costs.total.us_dollar.computed is False

    def test_negative_per_diem_with_cents(self):
        """2017q4dec06-000 Hemingway Estonia: pd=-259.00, tot=259.00."""
        seg = segment(costs(per_diem="-259.00", total="259.00"))
        r = report([Traveler(name="Hemingway", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_NEGATIVE_PER_DIEM" in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_negative_per_diem_with_other_populated_does_not_qualify(self):
        """If another component is populated, it's not the refund shape."""
        seg = segment(costs(per_diem="-100.00", transportation="50.00", total="100.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_NEGATIVE_PER_DIEM" not in seg.flags

    def test_positive_per_diem_does_not_qualify(self):
        """A positive per_diem matching the total is just a normal match."""
        seg = segment(costs(per_diem="100.00", total="100.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_NEGATIVE_PER_DIEM" not in seg.flags
        assert "ROW_SUM_MISMATCH" not in seg.flags

    def test_negative_per_diem_total_not_abs_does_not_qualify(self):
        """If total != abs(per_diem), it's not the refund shape."""
        seg = segment(costs(per_diem="-100.00", total="200.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_NEGATIVE_PER_DIEM" not in seg.flags

    def test_negative_total_does_not_qualify(self):
        """A negative total is not the refund shape (total must be positive)."""
        seg = segment(costs(per_diem="-100.00", total="-100.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        assert "ROW_TOTAL_NEGATIVE_PER_DIEM" not in seg.flags

    def test_idempotent_across_revalidation(self):
        seg = segment(costs(per_diem="-1060.00", total="1060.00"))
        r = report([Traveler(name="A", segments=[seg])])
        validate_report(r)
        validate_report(r)
        assert seg.flags.count("ROW_TOTAL_NEGATIVE_PER_DIEM") == 1
        assert "ROW_SUM_MISMATCH" not in seg.flags
