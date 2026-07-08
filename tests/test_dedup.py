"""Tests for amended-report deduplication."""

from datetime import date

from official_foreign_travel.models.report import Period, Report, Sponsor, Traveler
from official_foreign_travel.parsing.dedup import dedup_reports, parse_publication_date


def make_report(
    report_id, source_file, table_index, sponsor_name, start, end, amended=False, travelers=None
):
    return Report(
        report_id=report_id,
        source_file=source_file,
        table_index=table_index,
        amended=amended,
        sponsor=Sponsor(type="committee", name=sponsor_name, raw=sponsor_name),
        period=Period(start=start, end=end, year=end.year, quarter=None) if start else None,
        header_raw="",
        travelers=travelers or [],
    )


def make_traveler(name):
    return Traveler(name=name, segments=[])


class TestParsePublicationDate:
    def test_standard_filename(self):
        assert parse_publication_date("2018q4nov16.txt") == date(2018, 11, 16)

    def test_single_digit_day(self):
        assert parse_publication_date("2009q1jan8.txt") == date(2009, 1, 8)

    def test_unrecognized_filename_returns_none(self):
        assert parse_publication_date("not-a-report.txt") is None


class TestDedupReports:
    def test_no_duplicates_leaves_superseded_by_none(self):
        reports = [
            make_report(
                "a-000",
                "2018q1jan01.txt",
                0,
                "COMMITTEE ON RULES",
                date(2017, 10, 1),
                date(2017, 12, 31),
            ),
            make_report(
                "b-000",
                "2018q1jan01.txt",
                0,
                "COMMITTEE ON RULES",
                date(2018, 1, 1),
                date(2018, 3, 31),
            ),
        ]
        dedup_reports(reports)
        assert all(r.superseded_by is None for r in reports)

    def test_amended_wins_over_earlier_non_amended(self):
        original = make_report(
            "orig-002",
            "1994q1feb10.txt",
            2,
            "COMMITTEE ON ARMED SERVICES",
            date(1993, 10, 1),
            date(1993, 12, 31),
            amended=False,
        )
        amendment = make_report(
            "amend-002",
            "1994q2may17.txt",
            2,
            "COMMITTEE ON ARMED SERVICES",
            date(1993, 10, 1),
            date(1993, 12, 31),
            amended=True,
        )
        dedup_reports([original, amendment])
        assert original.superseded_by == "amend-002"
        assert amendment.superseded_by is None

    def test_amended_wins_even_if_listed_after_winner_in_input_order(self):
        """Winner selection shouldn't depend on which element comes first in the list."""
        amendment = make_report(
            "amend-002",
            "1994q2may17.txt",
            2,
            "COMMITTEE ON ARMED SERVICES",
            date(1993, 10, 1),
            date(1993, 12, 31),
            amended=True,
        )
        original = make_report(
            "orig-002",
            "1994q1feb10.txt",
            2,
            "COMMITTEE ON ARMED SERVICES",
            date(1993, 10, 1),
            date(1993, 12, 31),
            amended=False,
        )
        dedup_reports([amendment, original])
        assert original.superseded_by == "amend-002"
        assert amendment.superseded_by is None

    def test_later_publication_wins_when_neither_is_amended(self):
        shared = [make_traveler("Hon. Ron Wyden"), make_traveler("Marilyn Seiber")]
        earlier = make_report(
            "jan-001",
            "1995q1jan11.txt",
            1,
            "COMMITTEE ON SMALL BUSINESS",
            date(1994, 1, 1),
            date(1994, 3, 31),
            travelers=shared,
        )
        later = make_report(
            "feb-001",
            "1995q1feb09.txt",
            1,
            "COMMITTEE ON SMALL BUSINESS",
            date(1994, 1, 1),
            date(1994, 3, 31),
            travelers=shared,
        )
        dedup_reports([earlier, later])
        assert earlier.superseded_by == "feb-001"
        assert later.superseded_by is None

    def test_different_periods_are_not_duplicates(self):
        q1 = make_report(
            "q1-000",
            "2018q2apr01.txt",
            0,
            "COMMITTEE ON RULES",
            date(2018, 1, 1),
            date(2018, 3, 31),
        )
        q2 = make_report(
            "q2-000",
            "2018q2apr01.txt",
            1,
            "COMMITTEE ON RULES",
            date(2018, 4, 1),
            date(2018, 6, 30),
        )
        dedup_reports([q1, q2])
        assert q1.superseded_by is None
        assert q2.superseded_by is None

    def test_unparseable_period_never_deduped(self):
        a = make_report("a-000", "2018q1jan01.txt", 0, "COMMITTEE ON RULES", None, None)
        b = make_report("b-000", "2018q2apr01.txt", 0, "COMMITTEE ON RULES", None, None)
        dedup_reports([a, b])
        assert a.superseded_by is None
        assert b.superseded_by is None

    def test_same_sponsor_and_period_but_disjoint_rosters_are_not_merged(self):
        """Two Appropriations subcommittees can file separate reports for the same quarter
        under the same generic sponsor label -- they must not supersede each other."""
        report_a = make_report(
            "a-003",
            "2001q3sep17.txt",
            3,
            "COMMITTEE ON APPROPRIATIONS",
            date(2001, 4, 1),
            date(2001, 6, 30),
            travelers=[make_traveler("Hon. Bill Young"), make_traveler("Hon. David Obey")],
        )
        report_b = make_report(
            "a-004",
            "2001q3sep17.txt",
            4,
            "COMMITTEE ON APPROPRIATIONS",
            date(2001, 4, 1),
            date(2001, 6, 30),
            travelers=[make_traveler("Lester C. Farrington"), make_traveler("W.C. Hersman")],
        )
        dedup_reports([report_a, report_b])
        assert report_a.superseded_by is None
        assert report_b.superseded_by is None

    def test_same_sponsor_and_period_with_overlapping_rosters_are_merged(self):
        """A near-identical roster republished later (even without an AMENDED marker) is a dup."""
        shared = [make_traveler("Hon. Ron Wyden"), make_traveler("Marilyn Seiber")]
        earlier = make_report(
            "jan-001",
            "1995q1jan11.txt",
            1,
            "COMMITTEE ON SMALL BUSINESS",
            date(1994, 1, 1),
            date(1994, 3, 31),
            travelers=shared,
        )
        later = make_report(
            "feb-001",
            "1995q1feb09.txt",
            1,
            "COMMITTEE ON SMALL BUSINESS",
            date(1994, 1, 1),
            date(1994, 3, 31),
            travelers=shared,
        )
        dedup_reports([earlier, later])
        assert earlier.superseded_by == "feb-001"

    def test_three_way_duplicate_keeps_single_winner(self):
        reports = [
            make_report(
                "r1-000",
                "1995q1jan11.txt",
                0,
                "COMMITTEE ON RULES",
                date(1994, 1, 1),
                date(1994, 3, 31),
            ),
            make_report(
                "r2-000",
                "1995q1feb09.txt",
                0,
                "COMMITTEE ON RULES",
                date(1994, 1, 1),
                date(1994, 3, 31),
            ),
            make_report(
                "r3-000",
                "1995q1mar06.txt",
                0,
                "COMMITTEE ON RULES",
                date(1994, 1, 1),
                date(1994, 3, 31),
                amended=True,
            ),
        ]
        dedup_reports(reports)
        winners = [r for r in reports if r.superseded_by is None]
        assert len(winners) == 1
        assert winners[0].report_id == "r3-000"
