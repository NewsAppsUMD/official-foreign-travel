"""Tests for locating a report's raw source lines for the review UI."""

from pathlib import Path

from official_foreign_travel.parsing.assemble import assemble_file
from official_foreign_travel.review.source_lookup import get_raw_lines

FIXTURES = Path(__file__).parent / "fixtures"


class TestGetRawLines:
    def test_returns_lines_for_a_real_report(self):
        reports = assemble_file(FIXTURES / "2019q1jan29.txt")
        report = reports[0]
        lines = get_raw_lines(report, FIXTURES)
        assert lines is not None
        assert any("REPORT OF EXPENDITURES" in line for line in lines)

    def test_missing_source_file_returns_none(self, tmp_path):
        reports = assemble_file(FIXTURES / "2019q1jan29.txt")
        report = reports[0]
        report.source_file = "does-not-exist.txt"
        assert get_raw_lines(report, tmp_path) is None
