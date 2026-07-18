"""Tests for the end-to-end table assembly pipeline."""

from pathlib import Path

from official_foreign_travel.parsing.assemble import (
    _match_member,
    _member_lookup_variants,
    assemble_file,
    load_disambiguation_index,
    load_name_index,
)
from official_foreign_travel.parsing.validate import validate_reports

FIXTURES = Path(__file__).parent / "fixtures"
MEMBERS_CSV = Path(__file__).parent.parent / "members.csv"
COMMITTEES_CSV = Path(__file__).parent.parent / "committees.csv"
DISAMBIGUATION_CSV = Path(__file__).parent.parent / "member_disambiguation.csv"


class TestAssembleFile:
    def test_produces_one_report_per_table(self):
        reports = assemble_file(FIXTURES / "2019q1jan29.txt")
        assert len(reports) == 4

    def test_report_ids_are_unique_and_stable(self):
        reports = assemble_file(FIXTURES / "2018q4nov16.txt")
        ids = [r.report_id for r in reports]
        assert len(ids) == len(set(ids))
        assert ids[0] == "2018q4nov16-000"

    def test_amended_flag_propagates(self):
        reports = assemble_file(FIXTURES / "1996q1jan30.txt")
        amended = [r for r in reports if r.amended]
        assert len(amended) == 1

    def test_committee_code_looked_up(self):
        committee_index = load_name_index(COMMITTEES_CSV)
        reports = assemble_file(FIXTURES / "2018q4nov16.txt", committee_index=committee_index)
        armed_services = next(r for r in reports if "ARMED SERVICES" in r.sponsor.name.upper())
        assert armed_services.sponsor.code == "HSAS"

    def test_exact_member_match(self):
        member_index = load_name_index(MEMBERS_CSV)
        reports = assemble_file(FIXTURES / "2019q1jan29.txt", member_index=member_index)
        matched = [t for r in reports for t in r.travelers if t.bioguide_id is not None]
        # At least the "Hon." members should exact-match the members.csv roster.
        assert len(matched) > 0
        assert all(t.match_confidence == 1.0 for t in matched)

    def test_unmatched_member_flagged_not_guessed(self):
        reports = assemble_file(FIXTURES / "2018q4nov16.txt")  # no member_index passed
        unmatched = [t for r in reports for t in r.travelers if t.bioguide_id is None]
        assert len(unmatched) > 0

    def test_never_drops_a_table_even_with_low_confidence_layout(self):
        """Every segmented table produces a Report, regardless of parse quality."""
        from official_foreign_travel.parsing.segmenter import segment_tables

        text = (FIXTURES / "2007q4nov13.txt").read_text(errors="replace")
        expected_count = len(segment_tables(text, "2007q4nov13.txt"))
        reports = assemble_file(FIXTURES / "2007q4nov13.txt")
        assert len(reports) == expected_count

    def test_costs_are_decimal_and_json_serializable(self):
        reports = assemble_file(FIXTURES / "2018q4nov16.txt")
        armed_services = next(r for r in reports if "ARMED SERVICES" in r.sponsor.name.upper())
        payload = armed_services.model_dump(mode="json")
        assert isinstance(
            payload["travelers"][0]["segments"][0]["costs"]["total"]["us_dollar"], dict
        )


class TestNameFootnoteMarkers:
    """Trailing footnote markers on a name must not break bioguide matching."""

    def test_trailing_asterisk_still_exact_matches(self):
        member_index = {"HON. ELIOT ENGEL": "E000179"}
        bioguide, confidence, flags = _match_member("Hon. Eliot Engel *", [], member_index, None)
        assert bioguide == "E000179"
        assert confidence == 1.0
        assert flags == []

    def test_trailing_backslash_marker_still_exact_matches(self):
        member_index = {"HON. AL GREEN": "G000553"}
        bioguide, _, _ = _match_member("Hon. Al Green \\4\\", [], member_index, None)
        assert bioguide == "G000553"

    def test_marker_without_space_still_exact_matches(self):
        member_index = {"HON. TONY P. HALL": "H000034"}
        bioguide, _, _ = _match_member("Hon. Tony P. Hall\\4\\", [], member_index, None)
        assert bioguide == "H000034"

    def test_double_asterisk_still_exact_matches(self):
        member_index = {"HON. VICENTE GONZALEZ": "G000581"}
        bioguide, _, _ = _match_member("Hon. Vicente Gonzalez **", [], member_index, None)
        assert bioguide == "G000581"

    def test_name_that_is_only_a_marker_stays_unmatched(self):
        bioguide, confidence, flags = _match_member("**", [], {"HON. A": "X"}, None)
        assert bioguide is None
        assert flags == []


class TestMemberDisambiguation:
    """(name, sponsor committee) resolution for names ambiguous even with dates."""

    def test_load_disambiguation_index_reads_real_file(self):
        index = load_disambiguation_index(DISAMBIGUATION_CSV)
        assert index[("HON. MIKE ROGERS", "HLIG")] == "R000572"  # Michigan, Intelligence
        assert index[("HON. MIKE ROGERS", "HSAS")] == "R000575"  # Alabama, Armed Services

    def test_load_disambiguation_index_missing_file_is_empty(self, tmp_path):
        assert load_disambiguation_index(tmp_path / "nope.csv") == {}

    def test_ambiguous_name_resolved_by_sponsor_committee(self):
        index = {("HON. MIKE ROGERS", "HLIG"): "R000572"}
        bioguide, confidence, flags = _match_member(
            "Hon. Mike Rogers", [], {}, None, sponsor_code="HLIG", disambiguation_index=index
        )
        assert bioguide == "R000572"
        assert confidence == 1.0
        assert "MEMBER_DISAMBIGUATED_BY_COMMITTEE" in flags

    def test_no_sponsor_code_stays_unmatched(self):
        index = {("HON. MIKE ROGERS", "HLIG"): "R000572"}
        bioguide, _, flags = _match_member(
            "Hon. Mike Rogers", [], {}, None, sponsor_code=None, disambiguation_index=index
        )
        assert bioguide is None
        assert "MEMBER_UNMATCHED" in flags

    def test_unlisted_sponsor_code_stays_unmatched(self):
        index = {("HON. MIKE ROGERS", "HLIG"): "R000572"}
        bioguide, _, flags = _match_member(
            "Hon. Mike Rogers", [], {}, None, sponsor_code="HSWM", disambiguation_index=index
        )
        assert bioguide is None
        assert "MEMBER_UNMATCHED" in flags

    def test_exact_match_takes_precedence_over_disambiguation(self):
        member_index = {"HON. JANE DOE": "D000001"}
        index = {("HON. JANE DOE", "HLIG"): "WRONG"}
        bioguide, confidence, flags = _match_member(
            "Hon. Jane Doe",
            [],
            member_index,
            None,
            sponsor_code="HLIG",
            disambiguation_index=index,
        )
        assert bioguide == "D000001"
        assert flags == []


class TestMemberLookupVariants:
    """Unit tests for `_member_lookup_variants` -- the multi-key strategy that
    recovers Hon./Rep./Sen. members whose source form doesn't exactly match
    members.csv's `HON. <NAME>` keys."""

    def test_full_body_is_first_variant(self):
        keys = _member_lookup_variants("Hon. Eliot Engel", "Hon.")
        assert keys[0] == "HON. ELIOT ENGEL"

    def test_surname_only_variant(self):
        keys = _member_lookup_variants("Hon. Glen Browder", "Hon.")
        assert "HON. BROWDER" in keys

    def test_middle_initial_stripped(self):
        keys = _member_lookup_variants("Hon. William D. Lipinski", "Hon.")
        assert "HON. WILLIAM LIPINSKI" in keys

    def test_leading_single_letter_initial_stripped(self):
        keys = _member_lookup_variants("Hon. Y. Tim Hutchinson", "Hon.")
        assert "HON. TIM HUTCHINSON" in keys

    def test_period_after_first_initial(self):
        keys = _member_lookup_variants("Hon. E de la Garza", "Hon.")
        assert "HON. E. DE LA GARZA" in keys

    def test_multi_token_surname_with_particles(self):
        keys = _member_lookup_variants("Hon. E de la Garza", "Hon.")
        assert "HON. DE LA GARZA" in keys

    def test_suffix_appended_variants(self):
        keys = _member_lookup_variants("Hon. Donald Payne", "Hon.")
        assert "HON. DONALD PAYNE, JR" in keys
        assert "HON. DONALD PAYNE JR" in keys

    def test_trailing_gunk_stripped(self):
        keys = _member_lookup_variants("Hon. Eliot Engel  \\\\  ", "Hon.")
        assert keys[0] == "HON. ELIOT ENGEL"
        assert all("\\\\" not in k for k in keys)

    def test_rep_honorific_triggers_variants(self):
        keys = _member_lookup_variants("Rep. William Lipinski", "Rep.")
        assert keys[0] == "HON. WILLIAM LIPINSKI"

    def test_sen_honorific_triggers_variants(self):
        keys = _member_lookup_variants("Sen. Tim Hutchinson", "Sen.")
        assert keys[0] == "HON. TIM HUTCHINSON"

    def test_bare_name_does_not_get_hon_prefix(self):
        """Safety gate: bare names fall back to source-form lookup only."""
        keys = _member_lookup_variants("Bart Gordon", None)
        assert keys == ["BART GORDON"]

    def test_mr_honorific_does_not_get_hon_prefix(self):
        """Safety gate: Mr./Ms./Dr. are staff honorifics, not congressional."""
        keys = _member_lookup_variants("Mr. Jennifer Burton", "Mr.")
        assert keys == ["MR. JENNIFER BURTON"]

    def test_dr_honorific_does_not_get_hon_prefix(self):
        keys = _member_lookup_variants("Dr. John Smith", "Dr.")
        assert keys == ["DR. JOHN SMITH"]

    def test_empty_name_returns_empty_list(self):
        assert _member_lookup_variants("", "Hon.") == []
        assert _member_lookup_variants("   ", "Hon.") == []

    def test_honorific_only_returns_empty_list(self):
        assert _member_lookup_variants("Hon.", "Hon.") == []
        assert _member_lookup_variants("Hon. ", "Hon.") == []


class TestMemberLookupVariantMatching:
    """End-to-end: the variant strategy recovers members via `_match_member`."""

    def test_surname_only_match(self):
        member_index = {"HON. BROWDER": "B000897"}
        bioguide, confidence, flags = _match_member(
            "Hon. Glen Browder", [], member_index, None
        )
        assert bioguide == "B000897"
        assert confidence == 1.0
        assert flags == []

    def test_middle_initial_stripped_match(self):
        member_index = {"HON. WILLIAM LIPINSKI": "L000342"}
        bioguide, confidence, flags = _match_member(
            "Hon. William D. Lipinski", [], member_index, None
        )
        assert bioguide == "L000342"
        assert confidence == 1.0

    def test_leading_initial_stripped_match(self):
        member_index = {"HON. TIM HUTCHINSON": "H001015"}
        bioguide, confidence, flags = _match_member(
            "Hon. Y. Tim Hutchinson", [], member_index, None
        )
        assert bioguide == "H001015"
        assert confidence == 1.0

    def test_period_after_first_initial_match(self):
        member_index = {"HON. E. DE LA GARZA": "D000203"}
        bioguide, confidence, flags = _match_member(
            "Hon. E de la Garza", [], member_index, None
        )
        assert bioguide == "D000203"
        assert confidence == 1.0

    def test_multi_token_surname_match(self):
        member_index = {"HON. DE LA GARZA": "D000203"}
        bioguide, confidence, flags = _match_member(
            "Hon. E de la Garza", [], member_index, None
        )
        assert bioguide == "D000203"
        assert confidence == 1.0

    def test_suffix_appended_match(self):
        member_index = {"HON. DONALD PAYNE, JR": "P000604"}
        bioguide, confidence, flags = _match_member(
            "Hon. Donald Payne", [], member_index, None
        )
        assert bioguide == "P000604"
        assert confidence == 1.0

    def test_bare_name_does_not_match_hon_surname_only(self):
        """Safety gate: bare 'Browder' must not match 'HON. BROWDER'."""
        member_index = {"HON. BROWDER": "B000897"}
        bioguide, confidence, flags = _match_member("Browder", [], member_index, None)
        assert bioguide is None
        assert "MEMBER_UNMATCHED" in flags

    def test_mr_honorific_does_not_match_hon_surname_only(self):
        """Safety gate: 'Mr. Browder' must not match 'HON. BROWDER'."""
        member_index = {"HON. BROWDER": "B000897"}
        bioguide, confidence, flags = _match_member(
            "Mr. Glen Browder", [], member_index, None
        )
        assert bioguide is None
        assert "MEMBER_UNMATCHED" in flags


class TestValidateIntegration:
    def test_validated_reports_have_no_unexpected_crashes(self):
        reports = assemble_file(FIXTURES / "2019q1jan29.txt")
        validate_reports(reports)
        assert all(isinstance(r.flags, list) for r in reports)


class TestNoExpendituresForm:
    """A 'no expenditures' checkbox form is a legitimate zero-expenditure quarterly
    filing, not a parse failure. The House Clerk's form says 'Please Note: If there
    were no expenditures during the calendar quarter noted above, please check the
    box at right to so indicate and return. x' -- the 'x' is the checked box. These
    have column headers but no data rows, so the layout detector returns None or
    low confidence. Flag `NO_EXPENDITURES` (informational) instead of
    `LAYOUT_UNDETECTED`/`LAYOUT_LOW_CONFIDENCE` so consumers can distinguish 'filed
    nothing' from 'failed to parse'."""

    def _no_expenditures_block(self):
        from official_foreign_travel.parsing.segmenter import TableBlock

        lines = [
            "REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, COMMITTEE ON BUDGET, HOUSE OF REPRESENTATIVES, EXPENDED BETWEEN OCT. 1 AND DEC. 31, 2001",
            "------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
            "                                                 Date                                                    Foreign         Transportation            Other purposes                 Total",
            "                                        ----------------------                                           currency  -----------------------------------------------------------------------------",
            "       Name of Member or employee                                       Country             Per diem   U.S. dollar               U.S. dollar               U.S. dollar               U.S. dollar",
            "                                                                                      FOR HOUSE COMMITTEES",
            "                         Please Note: If there were no expenditures during the calendar quarter noted above, please check the box at right to so indicate and return. x",
            "------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------",
            "JIM NUSSLE, Chairman, Jan. 30, 2002.",
        ]
        return TableBlock(
            source_file="2002q2apr29.txt",
            table_index=7,
            title_raw=lines[0],
            lines=lines,
            start_line=308,
        )

    def test_no_expenditures_flagged_not_layout_undetected(self):
        from official_foreign_travel.parsing.assemble import assemble_table

        report = assemble_table(self._no_expenditures_block())
        assert "NO_EXPENDITURES" in report.flags
        assert "LAYOUT_UNDETECTED" not in report.flags
        assert "LAYOUT_LOW_CONFIDENCE" not in report.flags

    def test_no_expenditures_report_still_has_sponsor_and_period(self):
        from official_foreign_travel.parsing.assemble import assemble_table

        report = assemble_table(self._no_expenditures_block())
        assert "COMMITTEE ON BUDGET" in report.sponsor.name.upper()
        assert report.period is not None
        assert report.period.year == 2001
        assert report.travelers == []

    def test_real_table_not_misclassified_as_no_expenditures(self):
        """A real data table with travelers must not trip the no-expenditures check.
        The 2019q1jan29 fixture has one no-expenditures form (no travelers) and
        three real tables (with travelers) -- only the empty one should be flagged."""
        reports = assemble_file(FIXTURES / "2019q1jan29.txt")
        for r in reports:
            if r.travelers:
                assert "NO_EXPENDITURES" not in r.flags
        no_exp = [r for r in reports if "NO_EXPENDITURES" in r.flags]
        assert len(no_exp) == 1
        assert no_exp[0].travelers == []


class TestWrapperIntroDropped:
    """A Speaker-Authorized quarterly summary begins with an intro paragraph:
    'REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL -- Reports concerning the
    foreign currencies and U.S. dollars utilized for Speaker-Authorized Official
    Travel during the first quarter of 2008, pursuant to Public Law 95-384 are as
    follows:'. The segmenter splits this as a table (it starts with the header
    phrase), but it's prose with no sponsor, period, or data. Drop it from the
    assembled reports so it doesn't produce a junk LAYOUT_UNDETECTED entry."""

    def test_wrapper_intro_dropped_from_assemble_file(self):
        """2008q2apr23.txt starts with the Speaker-Authorized wrapper intro, then
        has real tables. The intro (which would be table 000) should be dropped,
        and the first real table should still be present with its original id."""
        reports = assemble_file(FIXTURES / "2008q2apr23.txt")
        sponsors = [r.sponsor.name for r in reports]
        # The wrapper intro's 'sponsor' would be the prose about Speaker-Authorized
        # travel -- none of the assembled reports should reference it.
        assert not any("Speaker-Authorized" in s for s in sponsors)
        assert not any("pursuant to Public Law" in s for s in sponsors)
        # The first real table (Janice McKinney) should be present.
        assert any("MCKINNEY" in s.upper() for s in sponsors)

    def test_wrapper_intro_does_not_produce_layout_undetected(self):
        reports = assemble_file(FIXTURES / "2008q2apr23.txt")
        # No report should carry LAYOUT_UNDETECTED from the wrapper intro.
        layout_undetected = [r for r in reports if "LAYOUT_UNDETECTED" in r.flags]
        assert layout_undetected == []


class TestLayoutInferredFromData:
    """Shape 3: tables whose header label block is missing or too garbled to
    parse can still be recovered from data-row gutter detection. The two real
    cases are 2009q1jan08-002 (Brussels -- header merged onto title line) and
    2009q3sep16-000 (Bosnia -- 'Arrival' label missing). Both were previously
    LAYOUT_UNDETECTED with 0 travelers; the fallback recovers their travelers
    and flags them LAYOUT_INFERRED_FROM_DATA so consumers can distinguish."""

    REPORT_TEXT = Path(__file__).parent.parent / "report_text"

    def test_brussels_recovers_travelers_via_data_row_fallback(self):
        reports = assemble_file(self.REPORT_TEXT / "2009q1jan08.txt")
        # Find the Brussels report that was previously LAYOUT_UNDETECTED.
        # It has 8 staffer travelers (Reeves, Morgan, Anderson, etc.).
        brussels = next(
            r for r in reports
            if "BRUSSELS" in r.sponsor.name.upper()
            and any("REEVES" in t.name.upper() for t in r.travelers)
        )
        assert "LAYOUT_INFERRED_FROM_DATA" in brussels.flags
        assert "LAYOUT_UNDETECTED" not in brussels.flags
        assert len(brussels.travelers) == 8
        # Verify costs were extracted correctly from the data-row layout.
        reeves = next(t for t in brussels.travelers if "REEVES" in t.name.upper())
        assert len(reeves.segments) == 1
        seg = reeves.segments[0]
        assert seg.costs.per_diem.us_dollar.amount is not None
        assert str(seg.costs.per_diem.us_dollar.amount) == "514.07"
        assert str(seg.costs.total.us_dollar.amount) == "7740.69"

    def test_bosnia_recovers_travelers_and_matches_members(self):
        member_index = load_name_index(MEMBERS_CSV)
        reports = assemble_file(
            self.REPORT_TEXT / "2009q3sep16.txt", member_index=member_index
        )
        bosnia = next(
            r for r in reports if "BOSNIA" in r.sponsor.name.upper() and r.travelers
        )
        assert "LAYOUT_INFERRED_FROM_DATA" in bosnia.flags
        assert "LAYOUT_UNDETECTED" not in bosnia.flags
        # 19 travelers, several of whom are members with bioguide IDs.
        assert len(bosnia.travelers) == 19
        matched = [t for t in bosnia.travelers if t.bioguide_id is not None]
        assert len(matched) >= 5  # Hastings, Aderholt, Bordallo, Butterfield, etc.
        hastings = next(t for t in bosnia.travelers if "HASTINGS" in t.name.upper())
        assert hastings.bioguide_id == "H000324"

    def test_non_standard_layout_stays_undetected(self):
        """1994-era Slovakia has a 5-column layout (not standard 12) -- the
        11-gutter gate rejects it, so it stays LAYOUT_UNDETECTED rather than
        getting a wrong data-row-derived layout. (This report is
        LAYOUT_LOW_CONFIDENCE in the current parse, not UNDETECTED, but the
        point is it must NOT be LAYOUT_INFERRED_FROM_DATA.)"""
        reports = assemble_file(self.REPORT_TEXT / "1994q2may17.txt")
        slovakia = next(
            (r for r in reports if "SLOVAKIA" in r.sponsor.name.upper()),
            None,
        )
        if slovakia is not None:
            assert "LAYOUT_INFERRED_FROM_DATA" not in slovakia.flags
