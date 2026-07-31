"""Tests for traveler/segment row extraction."""

import re
from decimal import Decimal
from pathlib import Path

from official_foreign_travel.parsing.costs import costs_has_data, parse_footnote_map
from official_foreign_travel.parsing.layout import detect_layout
from official_foreign_travel.parsing.rows import extract_rows
from official_foreign_travel.parsing.segmenter import segment_tables

FIXTURES = Path(__file__).parent / "fixtures"
CANDIDATE_RE = re.compile(r"\d{1,2}/\d{1,2}\s+\d{1,2}/\d{1,2}")
FOOTNOTE_LINE_RE = re.compile(r"^\s*\\(\d+)\\")


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


def rows_for(block):
    """Reproduce what assemble.py will do: run the whole block through layout+rows."""
    data_lines = [line for line in block.lines if CANDIDATE_RE.search(line[:80])]
    layout = detect_layout(block.lines, data_lines)
    assert layout is not None, f"no layout for table {block.table_index}"
    footnote_lines = [line for line in block.lines if FOOTNOTE_LINE_RE.match(line)]
    footnote_map = parse_footnote_map(footnote_lines)
    numbered_lines = list(enumerate(block.lines, start=1))
    return extract_rows(numbered_lines, layout, footnote_map)


def find_block(filename, needle):
    blocks = segment_tables(load(filename), filename)
    return next(b for b in blocks if needle in b.title_raw.upper())


class TestExtractRows:
    def test_simple_table_extracts_all_travelers(self):
        block = find_block("2018q4nov16.txt", "ARMED SERVICES")
        travelers, total, flags = rows_for(block)
        assert len(travelers) > 0
        assert all(t.name for t in travelers)
        assert all(len(t.segments) > 0 for t in travelers)

    def test_committee_total_extracted(self):
        block = find_block("2018q4nov16.txt", "ARMED SERVICES")
        travelers, total, flags = rows_for(block)
        assert total is not None
        assert total.total.us_dollar.amount is not None

    def test_repeated_traveler_with_multiple_segments(self):
        """A traveler with multiple rows (repeated leg) is one traveler, many segments."""
        block = find_block("2019q1jan29.txt", "HOMELAND SECURITY")
        travelers, total, flags = rows_for(block)
        by_name = {t.name.strip(): t for t in travelers}
        kirsten = next(t for name, t in by_name.items() if "Kirsten Duncan" in name)
        assert len(kirsten.segments) == 3
        countries = [s.country_raw.rstrip(".") for s in kirsten.segments]
        assert countries == ["Finland", "Norway", "France"]

    def test_wrapped_country_list_merged_via_continuation(self):
        """Bob Goodlatte's multi-country trip wraps across several lines."""
        block = find_block("2019q1jan29.txt", "JUDICIARY")
        travelers, total, flags = rows_for(block)
        goodlatte = next(t for t in travelers if "Goodlatte" in t.name)
        assert len(goodlatte.segments) == 1
        country = goodlatte.segments[0].country_raw
        assert "Germany" in country
        assert "Rwanda" in country
        assert "Portugal" in country
        assert "CONTINUATION_MERGED" in goodlatte.segments[0].flags

    def test_commercial_airfare_supplement_merged_into_cost_not_dropped(self):
        block = find_block("1996q1jan30.txt", "NATIONAL SECURITY")
        travelers, total, flags = rows_for(block)
        assert any("COST_SUPPLEMENT_MERGED" in seg.flags for t in travelers for seg in t.segments)
        # the supplemental transportation cost is not silently dropped
        merged_segment = next(
            seg for t in travelers for seg in t.segments if "COST_SUPPLEMENT_MERGED" in seg.flags
        )
        assert merged_segment.costs.transportation.us_dollar.amount > Decimal("0")

    def test_military_air_footnote_marker_detected(self):
        """Some tables mark a leg with a '(\\3\\)' footnote inside the transportation cell."""
        blocks = segment_tables(load("1995q1feb09.txt"), "1995q1feb09.txt")
        block = next(b for b in blocks if "JUDICIARY" in b.title_raw.upper())
        travelers, total, flags = rows_for(block)
        assert any(
            seg.costs.transportation.us_dollar.military_air for t in travelers for seg in t.segments
        )

    def test_military_air_label_row_detected(self):
        """Other tables mark it with a standalone 'Military air transportation' label row."""
        blocks = segment_tables(load("1995q1feb09.txt"), "1995q1feb09.txt")
        block = next(
            b
            for b in blocks
            if "COMMITTEE ON AGRICULTURE" in b.title_raw.upper()
            and "JULY 1 AND SEPT. 30" in b.title_raw.upper()
        )
        travelers, total, flags = rows_for(block)
        assert any(
            "MILITARY_AIR_LABEL_ROW" in seg.flags
            and seg.costs.transportation.us_dollar.military_air
            for t in travelers
            for seg in t.segments
        )

    def test_footnote_definition_not_read_as_label_row(self):
        """A footnote *definition* below the committee total ("\\3\\ Military air
        transportation.") must not be misread as a labeled sub-row and
        attributed to whichever traveler happened to be last -- it isn't
        data for any traveler. Regression for a report where this footnote
        text falsely tagged the second (and last) traveler in the table with
        MILITARY_AIR_LABEL_ROW, even though the actual "(\\3\\)" footnote
        marker for that table was on the first traveler's own cost cell.
        """
        blocks = segment_tables(load("2025q1feb18.txt"), "2025q1feb18.txt")
        block = next(b for b in blocks if "COMMITTEE ON THE BUDGET" in b.title_raw.upper())
        travelers, total, flags = rows_for(block)
        by_name = {t.name.strip(): t for t in travelers}
        omar = by_name["Hon. Ilhan Omar"]
        assert all("MILITARY_AIR_LABEL_ROW" not in seg.flags for seg in omar.segments)
        lopez = by_name["Hon. Greg Lopez"]
        assert any(seg.costs.transportation.us_dollar.military_air for seg in lopez.segments)

    def test_staffdel_expense_row_flagged_not_treated_as_traveler(self):
        """'STAFFDEL Expense' rows carry real dates/country matching the
        delegation's leg, but the cost is shared across the whole group, not
        any one traveler -- kept as its own record (nothing dropped) but
        flagged so it isn't mistaken for a person.
        """
        block = find_block("2024q4oct22.txt", "COMMITTEE ON HOUSE ADMINISTRATION")
        travelers, total, flags = rows_for(block)
        staffdel = [t for t in travelers if t.name.upper().startswith("STAFFDEL")]
        assert len(staffdel) == 2
        for traveler in staffdel:
            assert len(traveler.segments) == 1
            assert "STAFFDEL_GROUP_EXPENSE" in traveler.segments[0].flags
            assert costs_has_data(traveler.segments[0].costs)

    def test_no_traveler_rows_dropped_across_all_fixtures(self):
        for filename in [
            "1995q1feb09.txt",
            "1996q1jan30.txt",
            "2007q4nov13.txt",
            "2012q2may29.txt",
            "2018q4nov16.txt",
            "2019q1jan29.txt",
        ]:
            blocks = segment_tables(load(filename), filename)
            total_segments = 0
            for block in blocks:
                data_lines = [line for line in block.lines if CANDIDATE_RE.search(line[:80])]
                if not data_lines:
                    continue
                layout = detect_layout(block.lines, data_lines)
                if layout is None:
                    continue
                footnote_lines = [line for line in block.lines if FOOTNOTE_LINE_RE.match(line)]
                footnote_map = parse_footnote_map(footnote_lines)
                travelers, _, _ = extract_rows(
                    list(enumerate(block.lines, start=1)), layout, footnote_map
                )
                total_segments += sum(len(t.segments) for t in travelers)
            assert (
                total_segments
                >= len([line for b in blocks for line in b.lines if CANDIDATE_RE.search(line[:80])])
                - 5
            )  # small slack for genuinely-unparseable rows within low-confidence tables

    def test_us_departure_leg_creates_partial_segment_with_name(self):
        """First row of a trip often shows only the departure date (no foreign
        arrival, since the trip started from the U.S.). The name on that row
        must be captured so the following foreign legs attach to the right
        traveler instead of becoming an orphan flagged
        SEGMENT_WITHOUT_TRAVELER_NAME."""
        block = find_block("1995q1feb09.txt", "COMMISSION ON SECURITY AND COOPERATION")
        travelers, total, flags = rows_for(block)
        assert "SEGMENT_WITHOUT_TRAVELER_NAME" not in flags
        mccloskey = next(t for t in travelers if "McCloskey" in t.name)
        assert len(mccloskey.segments) == 2
        # First segment: US departure -- arrival cell empty, only departure date.
        us_leg = mccloskey.segments[0]
        assert us_leg.arrival_raw == ""
        assert us_leg.departure_raw == "7/6"
        assert "United States" in us_leg.country_raw
        # Second segment: the foreign leg.
        assert mccloskey.segments[1].arrival_raw == "7/7"
        assert mccloskey.segments[1].departure_raw == "7/11"

    def test_code_label_row_with_empty_dates_carries_name_forward(self):
        """A CODEL label-row names the traveler with empty arrival/departure
        cells; the itinerary follows on subsequent rows. The name must be
        carried forward so the next dated row attaches to it."""
        block = find_block("2009q3sep16_education_labor.txt", "EDUCATION AND LABOR")
        travelers, total, flags = rows_for(block)
        # No orphan traveler in this table.
        assert "SEGMENT_WITHOUT_TRAVELER_NAME" not in flags
        # The first traveler with a Kuwait segment should have a real name,
        # not be empty.
        assert any("Sablan" in t.name for t in travelers)

    def test_incomplete_date_row_carries_name_forward(self):
        """A name row with incomplete date tokens ('1/' with no day) must
        still capture the name so the next row with full dates attaches to
        it. Without this, the traveler would be orphaned and flagged
        SEGMENT_WITHOUT_TRAVELER_NAME."""
        block = find_block("1998q1mar31_france_vietnam.txt", "TRAVEL TO FRANCE, VIETNAM")
        travelers, total, flags = rows_for(block)
        assert "SEGMENT_WITHOUT_TRAVELER_NAME" not in flags
        dinh = next(t for t in travelers if "Dinh" in t.name)
        assert len(dinh.segments) >= 1
        assert dinh.segments[0].country_raw == "Vietnam"


class TestTotalRowRecognition:
    """TOTAL_ROW_RE tolerates the source-typo variants of `Committee total`
    found across the corpus. Without this, every table with a typo'd total
    row was flagged MISSING_COMMITTEE_TOTAL even though the row was visibly
    present in source."""

    def test_commitee_typo_total_recognized(self):
        """`Commitee total` (missing one 't') -- the most common prefix typo."""
        block = find_block("1994q1feb10_foreign_affairs.txt", "COMMITTEE ON FOREIGN AFFAIRS")
        _, total, _ = rows_for(block)
        assert total is not None
        assert total.total.us_dollar.amount is not None

    def test_committee_totals_plural_recognized(self):
        """`Committee totals` (plural 's') -- appears in dozens of 1994-1997 reports."""
        block = find_block("1994q1feb10_mexico.txt", "DELEGATION TO MEXICO")
        _, total, _ = rows_for(block)
        assert total is not None
        assert total.total.us_dollar.amount is not None

    def test_committee_tota_semicolon_recognized(self):
        """`Committee tota;` (semicolon for 'l') -- OCR-style source typo."""
        block = find_block("1994q1feb10_wessel.txt", "MICHAEL R. WESSEL")
        _, total, _ = rows_for(block)
        assert total is not None
        assert total.total.us_dollar.amount is not None

    def test_grand_total_for_pages_recognized(self):
        """`Grand total for pages 1 thru 3` -- multi-page committee total."""
        block = find_block("2011q4nov01_transportation.txt", "TRANSPORTATION AND INFRASTRUCTURE")
        _, total, _ = rows_for(block)
        assert total is not None
        assert total.total.us_dollar.amount is not None

    def test_committee_total_with_footnote_marker_recognized(self):
        """`Committee Total\\3\\........` -- footnote marker between token and dot-fill."""
        block = find_block("1994q2may17_natural_resources.txt", "NATURAL RESOURCES")
        _, total, _ = rows_for(block)
        assert total is not None
        assert total.total.us_dollar.amount is not None

    def test_codel_total_recognized(self):
        """`CODEL Total` -- a CODEL-level summary row at the bottom of a table."""
        block = find_block("2009q1feb11_agriculture.txt", "COMMITTEE ON AGRICULTURE")
        _, total, _ = rows_for(block)
        assert total is not None
        assert total.total.us_dollar.amount is not None

    def test_committee_total_with_two_footnote_markers_recognized(self):
        """`Committee total \\1\\ \\2\\..........` -- two footnote markers
        between the token and the dot-fill. 2008q4dec10 Science and Technology.
        The prior (?:\\\\d+\\\\)? matched at most one marker, so this row was
        missed and the report was flagged MISSING_COMMITTEE_TOTAL."""
        block = find_block("2008q4dec10_science.txt", "SCIENCE AND TECHNOLOGY")
        _, total, _ = rows_for(block)
        assert total is not None
        assert total.total.us_dollar.amount == Decimal("16354.60")


class TestCrossBlockContinuedMerge:
    """When a committee's report spans a page break, the next page begins
    with `REPORT OF EXPENDITURES...--Continued`. segment_tables merges the
    two into one block so the original data rows and the Continued block's
    trailing Committee total / supplemental rows are parsed together."""

    def test_continued_table_yields_committee_total(self):
        """The Agriculture report in 1995q1feb09.txt is split across a page
        break: data rows on page H219, Committee total on H220 under a
        `--Continued` header. The merge lets extract_rows see both."""
        block = find_block(
            "1995q1feb09_agriculture_with_continued.txt", "COMMITTEE ON AGRICULTURE"
        )
        travelers, total, flags = rows_for(block)
        assert total is not None
        assert total.total.us_dollar.amount is not None
        # The traveler's data is in the original block; their Commercial
        # airfare supplement is in the Continued block. Both should attach.
        rose = next(t for t in travelers if "Rose" in t.name)
        assert len(rose.segments) >= 1
        assert "SEGMENT_WITHOUT_TRAVELER_NAME" not in flags
