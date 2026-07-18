"""Tests for per-table column layout detection."""

import re
from pathlib import Path

import pytest

from official_foreign_travel.parsing.layout import (
    ColumnSpan,
    _cuts_token,
    _detect_gutter_starts,
    _merge_nearby,
    _refine_boundary,
    detect_layout,
)
from official_foreign_travel.parsing.segmenter import segment_tables

FIXTURES = Path(__file__).parent / "fixtures"
CANDIDATE_RE = re.compile(r"\d{1,2}/\d{1,2}\s+\d{1,2}/\d{1,2}")


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


def data_lines_for(block):
    return [line for line in block.lines if CANDIDATE_RE.search(line[:80])]


class TestColumnSpan:
    def test_slice_with_end(self):
        assert ColumnSpan(start=2, end=5).slice("abcdefgh") == "cde"

    def test_slice_without_end_goes_to_end_of_line(self):
        assert ColumnSpan(start=5, end=None).slice("abcdefgh") == "fgh"


class TestDetectLayout:
    def test_modern_layout_high_confidence(self):
        blocks = segment_tables(load("2019q1jan29.txt"), "2019q1jan29.txt")
        block = blocks[1]  # Committee on Homeland Security table
        layout = detect_layout(block.lines, data_lines_for(block))
        assert layout is not None
        assert layout.confidence >= 0.9
        assert len(layout.cost_columns) == 8
        assert layout.name.start == 0

    def test_name_column_always_starts_at_zero(self):
        """Names are left-justified at column 0 even though the label is indented."""
        for filename in ["1995q1feb09.txt", "2007q4nov13.txt", "2018q4nov16.txt"]:
            blocks = segment_tables(load(filename), filename)
            for block in blocks:
                layout = detect_layout(block.lines, data_lines_for(block))
                if layout is not None:
                    assert layout.name.start == 0

    def test_file_without_start_delimiter_still_gets_a_layout(self):
        """2012q2may29.txt has no legacy dashed start delimiter."""
        blocks = segment_tables(load("2012q2may29.txt"), "2012q2may29.txt")
        for block in blocks:
            layout = detect_layout(block.lines, data_lines_for(block))
            assert layout is not None
            assert layout.confidence > 0.5

    def test_arrival_departure_slices_extract_real_dates(self):
        blocks = segment_tables(load("2018q4nov16.txt"), "2018q4nov16.txt")
        block = next(b for b in blocks if "ARMED SERVICES" in b.title_raw.upper())
        data_lines = data_lines_for(block)
        layout = detect_layout(block.lines, data_lines)
        assert layout is not None
        date_re = re.compile(r"^\d{1,2}/\d{1,2}$")
        matches = sum(
            1
            for line in data_lines
            if date_re.match(layout.arrival.slice(line).strip().rstrip("."))
            and date_re.match(layout.departure.slice(line).strip())
        )
        assert matches / len(data_lines) > 0.8

    def test_no_header_window_returns_none(self):
        assert detect_layout(["not a real table", "no headers here"], []) is None


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
def test_most_tables_in_each_fixture_get_a_confident_layout(filename):
    blocks = segment_tables(load(filename), filename)
    layouts = [detect_layout(b.lines, data_lines_for(b)) for b in blocks]
    confident = [layout for layout in layouts if layout is not None and layout.confidence >= 0.7]
    assert len(confident) / len(blocks) > 0.85


@pytest.mark.parametrize(
    "filename",
    [
        "1997q3sep23_transportation.txt",
        "2003q2apr30_intelligence.txt",
    ],
)
def test_escaped_html_markup_tables_have_no_collided_boundaries(filename):
    """Two tables had HTML-escaped &lt;SUP&gt; markup in cost cells that shifted
    column positions and collided boundaries. After strip_html_tags learned to
    strip escaped entities, these tables parse with distinct boundaries."""
    blocks = segment_tables(load(filename), filename)
    block = blocks[0]
    layout = detect_layout(block.lines, data_lines_for(block))
    assert layout is not None
    starts = [span.start for span in layout.cost_columns]
    assert len(set(starts)) == len(starts), f"collided boundaries: {starts}"
    assert layout.confidence >= 0.8


def test_1998_truncated_header_finds_all_cost_columns():
    """1998-era files have a header label line truncated before the last
    "equivalent" label, so the primary Foreign/equivalent matches fall short
    of the 8 cost columns. The fallback to the "currency"/"or U.S." labels
    on subsequent header lines (which sit at the same columns) recovers all 8
    boundaries and reaches full confidence."""
    blocks = segment_tables(load("1998q2may05_government_reform.txt"), "1998q2may05_government_reform.txt")
    assert len(blocks) == 1
    layout = detect_layout(blocks[0].lines, data_lines_for(blocks[0]))
    assert layout is not None
    starts = [span.start for span in layout.cost_columns]
    assert len(starts) == 8
    assert len(set(starts)) == len(starts), f"collided boundaries: {starts}"
    assert layout.confidence >= 0.8


def test_1994_concatenated_labels_recovered_via_gutter_fallback():
    """1994-era files concatenate "Foreigncurrency" (no word boundary) and
    word-wrap the 4th label pair to a continuation line at bogus positions.
    The FOREIGN regex change + country_pos filter recover 7 of 8 columns from
    labels; the data-driven gutter fallback recovers the 8th (the 4th FC
    column, whose label was lost to word-wrap)."""
    blocks = segment_tables(load("1994q2may17_science.txt"), "1994q2may17_science.txt")
    assert len(blocks) == 1
    block = blocks[0]
    data = data_lines_for(block)
    assert len(data) >= 6  # enough rows for the gutter fallback
    layout = detect_layout(block.lines, data)
    assert layout is not None
    starts = [span.start for span in layout.cost_columns]
    assert len(starts) == 8
    assert len(set(starts)) == len(starts), f"collided boundaries: {starts}"
    assert layout.confidence >= 0.8


class TestCutsToken:
    def test_inside_a_token_cuts(self):
        assert _cuts_token("  2,079.00", 5) is True

    def test_at_token_start_does_not_cut(self):
        # slicing exactly at a token's first character keeps it whole
        assert _cuts_token("  2,079.00", 2) is False

    def test_inside_whitespace_does_not_cut(self):
        assert _cuts_token("ab    cd", 4) is False

    def test_line_edges_do_not_cut(self):
        assert _cuts_token("abc", 0) is False
        assert _cuts_token("abc", 3) is False  # past end of a short row


class TestMergeNearby:
    def test_dedupes_positions_within_tolerance(self):
        # "equivalent" and the "or U.S." label beneath it start 1 char apart
        # in some 1998-era headers; merging keeps them as one boundary.
        assert _merge_nearby([100, 101, 110, 120]) == [100, 110, 120]

    def test_keeps_positions_outside_tolerance(self):
        assert _merge_nearby([10, 13, 20], tolerance=2) == [10, 13, 20]

    def test_unsorted_input_is_sorted(self):
        assert _merge_nearby([120, 100, 101, 110]) == [100, 110, 120]

    def test_empty_input(self):
        assert _merge_nearby([]) == []


class TestDetectGutterStarts:
    # Right-justified amounts with dot-filled empties -- the shape where
    # labels are insufficient (1994 layout) and gutters must come from data.
    ROWS = [
        "country1     ...........  960.00    ...........  960.00  ",
        "country2     FF4,733.91   801.00    ...........  ...........  ",
        "country3     ...........  240.00    ...........  240.00  ",
        "country4     ...........  354.75    3,709.85    4,089.59  ",
        "country5     ...........  960.00    ...........  960.00  ",
        "country6     ...........  240.00    ...........  240.00  ",
    ]

    def test_finds_gutter_starts_after_country(self):
        # 4 cost columns (2 FC + 2 USD pairs): gutters before each column.
        # The trailing spaces after the last column are excluded.
        starts = _detect_gutter_starts(self.ROWS, country_pos=10)
        assert starts == [10, 24, 32, 47]

    def test_too_few_rows_returns_empty(self):
        assert _detect_gutter_starts(self.ROWS[:3], country_pos=10) == []

    def test_no_data_returns_empty(self):
        assert _detect_gutter_starts([], country_pos=10) == []


class TestRefineBoundaryRightJustified:
    # Two cost columns, right-justified amounts of mixed width, dot-filled
    # empties -- the shape that broke the old token-start criterion.
    #          0         1         2         3
    #          0123456789012345678901234567890123456
    ROWS = [
        "..........    2,079.00  ..........",
        "..........      467.00  ..........",
        "..........      398.00  ..........",
        "..........    3,049.45  ..........",
    ]

    def test_boundary_lands_in_the_gutter_not_at_majority_token_start(self):
        # Label guess 13 sits over the amount column. The widest amount
        # starts at col 14; the narrow ones at col 16. The old criterion
        # snapped to 16 (majority) and truncated "2,079.00" to "79.00".
        pos, ok = _refine_boundary(13, self.ROWS)
        assert ok
        assert pos <= 14, f"boundary {pos} would truncate the widest amount"
        assert pos >= 11, f"boundary {pos} cuts into the previous column"

    def test_boundary_never_steals_a_neighboring_column(self):
        # Guess 20 is late (right of every amount's start). The nearest
        # non-cutting position is the gutter at 24-26, NOT a distant column.
        pos, ok = _refine_boundary(20, self.ROWS)
        assert ok
        assert 22 <= pos <= 26, f"boundary {pos} wandered out of the adjacent gutter"

    def test_no_data_rows_returns_guess_unrefined(self):
        assert _refine_boundary(13, []) == (13, False)


class TestBoundaryCollisions:
    def test_collided_boundaries_cap_confidence_below_threshold(self):
        # Force a collision by monkeypatching refinement to a constant.
        import official_foreign_travel.parsing.layout as layout_module

        original = layout_module._refine_boundary
        layout_module._refine_boundary = lambda guess, rows: (100, True)
        try:
            header = [
                "   Name of Member or employee              Country     "
                "Foreign  equivalent  Foreign  equivalent  Foreign  equivalent  Foreign  equivalent",
                "                            Arrival  Departure",
                "-----------------------------------------------------------------",
            ]
            rows = ["Mr. A....     1/1   1/2  France...  ..  1.00  ..  2.00  ..  3.00  ..  6.00"]
            result = layout_module.detect_layout(header + rows, rows)
            assert result is not None
            from official_foreign_travel.parsing.assemble import LOW_CONFIDENCE_THRESHOLD

            assert result.confidence < LOW_CONFIDENCE_THRESHOLD
        finally:
            layout_module._refine_boundary = original


class TestLayoutFromDataRows:
    """Shape 3: when the header label block is missing or too garbled to parse,
    the layout can still be recovered from data-row gutter detection alone.

    Two real cases trigger this:
    - 2009q1jan08-002 (Brussels): the header was dropped entirely; data rows
      start immediately after the title with no "Name of Member" label.
    - 2009q3sep16-000 (Bosnia): the header window is found but "Arrival" is
      missing/garbled ("2Arrival2"), so _label_positions returns None.

    Both have clean data rows in the standard 12-column layout (11 gutters).
    The fallback requires exactly 11 gutters -- fewer means a non-standard
    layout (1994-era 5-column) or a garbled PDF artifact, both of which stay
    LAYOUT_UNDETECTED rather than risk a wrong layout.
    """

    # Standard 12-column data rows (name, arrival, departure, country, 8 cost).
    # Built to produce exactly 11 all-space gutters between columns.
    DATA_ROWS = [
        "Robert F. Reeves.......................    11/24       11/27   Belgium..................  ...........       514.07  ...........      7011.62  ...........       215.00  ...........      7740.69",
        "Teri Morgan............................    11/24       11/27   Belgium..................  ...........       514.07  ...........      7011.62  ...........       215.00  ...........      7740.69",
        "Kyle Anderson..........................    11/24       11/27   Belgium..................  ...........       514.07  ...........      7011.62  ...........       215.00  ...........      7740.69",
        "Karina Newton..........................    11/24       11/27   Belgium..................  ...........       514.07  ...........      7011.62  ...........       215.00  ...........      7740.69",
        "Catherine Cooke........................    11/24       11/30   Belgium..................  ...........       514.07  ...........      7011.62  ...........       215.00  ...........      7740.69",
        "Jeff Gold..............................    11/24       11/27   Belgium..................  ...........       514.07  ...........      7011.62  ...........       215.00  ...........      7740.69",
        "Kirsten Gullickson.....................    11/24       11/30   Belgium..................  ...........       514.07  ...........      7011.62  ...........       215.00  ...........      7740.69",
        "John Clocker...........................    11/24       11/30   Belgium..................  ...........       514.07  ...........      7011.62  ...........       215.00  ...........      7740.69",
    ]

    def test_recovers_layout_from_data_rows_only(self):
        from official_foreign_travel.parsing.layout import _layout_from_data_rows

        layout = _layout_from_data_rows(self.DATA_ROWS)
        assert layout is not None
        assert layout.data_row_derived is True
        assert len(layout.cost_columns) == 8
        assert layout.name.start == 0
        # Confidence above LOW_CONFIDENCE_THRESHOLD so it's not flagged low-confidence
        assert layout.confidence >= 0.8

    def test_layout_from_data_rows_extracts_correct_costs(self):
        from official_foreign_travel.parsing.layout import _layout_from_data_rows

        layout = _layout_from_data_rows(self.DATA_ROWS)
        row = self.DATA_ROWS[0]
        # cost_1 = per_diem USD = 514.07
        assert layout.cost_columns[1].slice(row).strip() == "514.07"
        # cost_3 = transport USD = 7011.62
        assert layout.cost_columns[3].slice(row).strip() == "7011.62"
        # cost_5 = other USD = 215.00
        assert layout.cost_columns[5].slice(row).strip() == "215.00"
        # cost_7 = total USD = 7740.69
        assert layout.cost_columns[7].slice(row).strip() == "7740.69"
        # cost_0 = per_diem foreign = dot-filled empty
        assert layout.cost_columns[0].slice(row).strip() == "..........."

    def test_layout_from_data_rows_extracts_dates_and_country(self):
        from official_foreign_travel.parsing.layout import _layout_from_data_rows

        layout = _layout_from_data_rows(self.DATA_ROWS)
        row = self.DATA_ROWS[0]
        assert layout.arrival.slice(row).strip() == "11/24"
        assert layout.departure.slice(row).strip() == "11/27"
        assert layout.country.slice(row).strip().rstrip(".") == "Belgium"
        assert layout.name.slice(row).strip().rstrip(".") == "Robert F. Reeves"

    def test_too_few_data_rows_returns_none(self):
        from official_foreign_travel.parsing.layout import _layout_from_data_rows

        assert _layout_from_data_rows(self.DATA_ROWS[:3]) is None

    def test_wrong_gutter_count_returns_none(self):
        """A non-standard layout (e.g. 1994-era 5-column with 4 gutters) must
        not produce a data-row-derived layout -- it stays LAYOUT_UNDETECTED."""
        from official_foreign_travel.parsing.layout import _layout_from_data_rows

        # 5-column layout: name, arrival, departure, country, 1 cost column
        # = 4 gutters. Not the standard 11.
        five_col_rows = [
            "Cathy Brickman........................     1/18        1/23   Slovakia..............................         800.00    ",
            "Cathy Brickman........................     1/23        1/28   Czech Republic........................       1,150.00    ",
            "William Freeman.......................     1/18        1/23   Slovakia..............................         800.00    ",
            "William Freeman.......................     1/23        1/28   Czech Republic........................       1,150.00    ",
            "Other Person..........................     1/18        1/23   Slovakia..............................         800.00    ",
            "Other Person..........................     1/23        1/28   Czech Republic........................       1,150.00    ",
        ]
        assert _layout_from_data_rows(five_col_rows) is None

    def test_detect_layout_falls_back_when_header_missing(self):
        """Brussels shape: no 'Name of Member' label at all. detect_layout
        should fall back to _layout_from_data_rows rather than returning None."""
        block_lines = [
            "REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, DELEGATION TO BRUSSELS",
            "",
            "",
            "",
        ] + self.DATA_ROWS
        layout = detect_layout(block_lines, self.DATA_ROWS)
        assert layout is not None
        assert layout.data_row_derived is True
        assert "LAYOUT_UNDETECTED" not in "check"  # layout recovered, not None

    def test_detect_layout_falls_back_when_label_positions_none(self):
        """Bosnia shape: 'Name of Member' label is present but 'Arrival' is
        missing/garbled, so _label_positions returns None. detect_layout
        should fall back to _layout_from_data_rows."""
        # Header has "Name of Member" but NO "Arrival" label (Bosnia shape).
        block_lines = [
            "REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, DELEGATION TO BOSNIA",
            "-----------------------------------------------------------------------------",
            "                                                 Date                                           Per diem             Transportation",
            "                                        ----------------------                           ---------------------------------------------------",
            "                                                                                                        U.S. dollar               U.S. dollar",
            "        Name of Member or employee                                       Country             Foreign     equivalent    Foreign     equivalent",
            "                                                    Departure                               currency      or U.S.     currency      or U.S.",
            "                                                                                                          currency                  currency",
            "-----------------------------------------------------------------------------------------------------------",
        ] + self.DATA_ROWS
        layout = detect_layout(block_lines, self.DATA_ROWS)
        assert layout is not None
        assert layout.data_row_derived is True

    def test_no_header_and_no_data_returns_none(self):
        """Safety: no header and no data rows -> None (LAYOUT_UNDETECTED)."""
        assert detect_layout(["not a real table", "no headers here"], []) is None

    def test_real_brussels_block_recovers_layout(self):
        """End-to-end: the real 2009q1jan08.txt Brussels block whose header
        was merged onto the title line (so _label_positions returns None)
        recovers a data-row-derived layout. There are two Brussels blocks in
        this file -- block 0 has a proper header, block 2 has the merged
        header. Pick the one where label extraction fails."""
        from official_foreign_travel.parsing.segmenter import segment_tables
        from official_foreign_travel.parsing.layout import _find_header_window, _label_positions

        text = (FIXTURES.parent.parent / "report_text" / "2009q1jan08.txt").read_text(
            errors="replace"
        )
        blocks = segment_tables(text, "2009q1jan08.txt")
        brussels = next(
            b for b in blocks
            if "BRUSSELS" in b.title_raw.upper()
            and _find_header_window(b.lines) is not None
            and _label_positions(_find_header_window(b.lines)) is None
        )
        layout = detect_layout(brussels.lines, data_lines_for(brussels))
        assert layout is not None
        assert layout.data_row_derived is True
        assert len(layout.cost_columns) == 8

    def test_real_bosnia_block_recovers_layout(self):
        """End-to-end: the real 2009q3sep16.txt Bosnia block (whose header is
        found but 'Arrival' is missing) recovers a data-row-derived layout."""
        from official_foreign_travel.parsing.segmenter import segment_tables

        text = (FIXTURES.parent.parent / "report_text" / "2009q3sep16.txt").read_text(
            errors="replace"
        )
        blocks = segment_tables(text, "2009q3sep16.txt")
        bosnia = next(b for b in blocks if "BOSNIA" in b.title_raw.upper())
        layout = detect_layout(bosnia.lines, data_lines_for(bosnia))
        assert layout is not None
        assert layout.data_row_derived is True
        assert len(layout.cost_columns) == 8
