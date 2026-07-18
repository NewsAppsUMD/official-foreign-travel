"""Tests for report table segmentation."""

from pathlib import Path

import pytest

from official_foreign_travel.parsing.segmenter import segment_tables

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


class TestSegmentTables:
    def test_finds_all_tables_in_modern_file(self):
        blocks = segment_tables(load("2019q1jan29.txt"), "2019q1jan29.txt")
        assert len(blocks) == 4
        assert [b.table_index for b in blocks] == [0, 1, 2, 3]

    def test_file_without_start_delimiter_still_yields_tables(self):
        """2007q4nov13.txt has no legacy start-delimiter but has real tables."""
        blocks = segment_tables(load("2007q4nov13.txt"), "2007q4nov13.txt")
        assert len(blocks) == 13

    def test_file_with_amended_reports(self):
        blocks = segment_tables(load("1996q1jan30.txt"), "1996q1jan30.txt")
        assert len(blocks) == 9
        amended_titles = [b.title_raw for b in blocks if "AMENDED" in b.title_raw.upper()]
        assert len(amended_titles) == 1

    def test_strips_leaked_html_tags_from_title(self):
        text = (
            "REPORT OF EXPENDITURES FOR <strong>OFFICIAL</strong> "
            "<strong>FOREIGN</strong> TRAVEL, COMMITTEE ON AGRICULTURE, "
            "HOUSE OF REPRESENTATIVES, EXPENDED BETWEEN JAN. 1 AND MAR. 31, 2000\n"
            "----------------------------------------------------------------\n"
        )
        blocks = segment_tables(text, "synthetic.txt")
        assert len(blocks) == 1
        assert "<" not in blocks[0].title_raw
        assert "strong" not in blocks[0].title_raw.lower()

    def test_source_file_and_line_numbers_recorded(self):
        blocks = segment_tables(load("2012q2may29.txt"), "2012q2may29.txt")
        assert all(b.source_file == "2012q2may29.txt" for b in blocks)
        assert all(b.start_line >= 1 for b in blocks)
        # start lines strictly increase
        starts = [b.start_line for b in blocks]
        assert starts == sorted(starts)

    def test_no_header_yields_no_tables(self):
        assert segment_tables("just some unrelated text\n", "empty.txt") == []

    def test_continued_block_merges_into_previous_match(self):
        """A `--Continued` header on the next page is one logical table with
        the prior block. segment_tables should merge the two before returning,
        so the merged block carries both the original data rows and the
        Continued block's trailing Committee total row."""
        text = (
            "REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, COMMITTEE ON "
            "AGRICULTURE, EXPENDED BETWEEN JULY 1 AND SEPT. 30, 1994\n"
            "----------------------------------------------------------------\n"
            "  Name of Member or employee     Country      Per diem\n"
            "  Arrival  Departure                          U.S. dollar\n"
            "----------------------------------------------------------------\n"
            "Joan T. Rose........    8/22   8/25   Korea........  785.00\n"
            "[[Page H220]]\n"
            "REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, COMMITTEE ON "
            "AGRICULTURE, EXPENDED BETWEEN JULY 1 AND SEPT. 30, 1994--Continued\n"
            "----------------------------------------------------------------\n"
            "  Name of Member or employee     Country      Per diem\n"
            "  Arrival  Departure                          U.S. dollar\n"
            "----------------------------------------------------------------\n"
            "      Committee total........  ........  ........  785.00\n"
        )
        blocks = segment_tables(text, "synthetic.txt")
        assert len(blocks) == 1
        assert "--Continued" not in blocks[0].title_raw
        assert blocks[0].table_index == 0
        # Merged block has the data row AND the Committee total row
        joined = "\n".join(blocks[0].lines)
        assert "Joan T. Rose" in joined
        assert "Committee total" in joined

    def test_continued_block_without_match_stays_standalone(self):
        """A `--Continued` header with no preceding matching title (e.g. the
        previous block was a different committee) is kept as its own block
        rather than silently dropped."""
        text = (
            "REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, COMMITTEE ON "
            "AGRICULTURE, EXPENDED BETWEEN JULY 1 AND SEPT. 30, 1994\n"
            "----------------------------------------------------------------\n"
            "  Name of Member or employee     Country      Per diem\n"
            "----------------------------------------------------------------\n"
            "Joan T. Rose........    8/22   8/25   Korea........  785.00\n"
            "REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, COMMITTEE ON "
            "FOREIGN AFFAIRS, EXPENDED BETWEEN JULY 1 AND SEPT. 30, 1994--Continued\n"
            "----------------------------------------------------------------\n"
            "      Committee total........  ........  ........  1000.00\n"
        )
        blocks = segment_tables(text, "synthetic.txt")
        assert len(blocks) == 2
        assert any("--Continued" in b.title_raw for b in blocks)

    def test_continued_block_indices_renumbered(self):
        """After a merge, subsequent blocks' table_index values are renumbered
        to be contiguous (no gap where the Continued block used to be)."""
        text = (
            "REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, COMMITTEE ON "
            "AGRICULTURE, EXPENDED BETWEEN JULY 1 AND SEPT. 30, 1994\n"
            "----------------------------------------------------------------\n"
            "  Name of Member or employee     Country      Per diem\n"
            "----------------------------------------------------------------\n"
            "Joan T. Rose........    8/22   8/25   Korea........  785.00\n"
            "REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, COMMITTEE ON "
            "AGRICULTURE, EXPENDED BETWEEN JULY 1 AND SEPT. 30, 1994--Continued\n"
            "----------------------------------------------------------------\n"
            "      Committee total........  ........  ........  785.00\n"
            "REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, COMMITTEE ON "
            "RULES, EXPENDED BETWEEN JULY 1 AND SEPT. 30, 1994\n"
            "----------------------------------------------------------------\n"
            "      Committee total........  ........  ........  100.00\n"
        )
        blocks = segment_tables(text, "synthetic.txt")
        assert [b.table_index for b in blocks] == [0, 1]
        assert "RULES" in blocks[1].title_raw


@pytest.mark.parametrize(
    "filename",
    [
        "1995q1feb09.txt",
        "1996q1jan30.txt",
        "2007q4nov13.txt",
        "2012q2may29.txt",
        "2018q4nov16.txt",
        "2019q1jan29.txt",
    ],
)
def test_every_fixture_yields_at_least_one_table(filename):
    blocks = segment_tables(load(filename), filename)
    assert len(blocks) > 0
