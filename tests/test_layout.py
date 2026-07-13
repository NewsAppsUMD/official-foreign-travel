"""Tests for per-table column layout detection."""

import re
from pathlib import Path

import pytest

from official_foreign_travel.parsing.layout import (
    ColumnSpan,
    _cuts_token,
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
