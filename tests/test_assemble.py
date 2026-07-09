"""Tests for the end-to-end table assembly pipeline."""

from pathlib import Path

from official_foreign_travel.parsing.assemble import (
    _match_member,
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


class TestValidateIntegration:
    def test_validated_reports_have_no_unexpected_crashes(self):
        reports = assemble_file(FIXTURES / "2019q1jan29.txt")
        validate_reports(reports)
        assert all(isinstance(r.flags, list) for r in reports)
