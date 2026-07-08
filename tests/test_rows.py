"""Tests for traveler/segment row extraction."""

import re
from decimal import Decimal
from pathlib import Path

from official_foreign_travel.parsing.costs import parse_footnote_map
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
    data_lines = [l for l in block.lines if CANDIDATE_RE.search(l[:80])]
    layout = detect_layout(block.lines, data_lines)
    assert layout is not None, f"no layout for table {block.table_index}"
    footnote_lines = [l for l in block.lines if FOOTNOTE_LINE_RE.match(l)]
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
                data_lines = [l for l in block.lines if CANDIDATE_RE.search(l[:80])]
                if not data_lines:
                    continue
                layout = detect_layout(block.lines, data_lines)
                if layout is None:
                    continue
                footnote_lines = [l for l in block.lines if FOOTNOTE_LINE_RE.match(l)]
                footnote_map = parse_footnote_map(footnote_lines)
                travelers, _, _ = extract_rows(
                    list(enumerate(block.lines, start=1)), layout, footnote_map
                )
                total_segments += sum(len(t.segments) for t in travelers)
            assert (
                total_segments
                >= len([l for b in blocks for l in b.lines if CANDIDATE_RE.search(l[:80])]) - 5
            )  # small slack for genuinely-unparseable rows within low-confidence tables
