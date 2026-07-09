"""Tests for per-table column layout detection."""

import re
from pathlib import Path

import pytest

from official_foreign_travel.parsing.layout import ColumnSpan, detect_layout
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
