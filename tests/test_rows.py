"""Tests for traveler/segment row extraction."""

import re
from decimal import Decimal
from pathlib import Path

from official_foreign_travel.parsing.costs import costs_has_data, parse_footnote_map
from official_foreign_travel.parsing.layout import ColumnSpan, TableLayout, detect_layout
from official_foreign_travel.parsing.rows import (
    _is_person_named_row,
    _looks_like_traveler_row_name,
    extract_rows,
)
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
        flagged so it isn't mistaken for a person. The two occurrences
        (Estonia leg, Latvia leg) share the exact same printed name, so
        they merge into one traveler with two segments rather than two
        separate fake travelers -- but without the merge-caveat flag,
        since a non-person label doesn't need an "is this the same
        identity?" warning.
        """
        block = find_block("2024q4oct22.txt", "COMMITTEE ON HOUSE ADMINISTRATION")
        travelers, total, flags = rows_for(block)
        staffdel = [t for t in travelers if t.name.upper().startswith("STAFFDEL")]
        assert len(staffdel) == 1
        assert len(staffdel[0].segments) == 2
        for seg in staffdel[0].segments:
            assert "STAFFDEL_GROUP_EXPENSE" in seg.flags
            assert "REPEATED_NAME_SEGMENTS_MERGED" not in seg.flags
            assert costs_has_data(seg.costs)

    def test_repeated_name_across_legs_merged_into_one_traveler(self):
        """A table organized leg-by-leg (all travelers for leg 1, then leg
        2, ...) reprints every traveler's name on every leg, instead of
        naming them once with blank continuation rows. Each of the three
        staffers here (Bart Reising, Brian Cress, Derek Luyten) appears on
        three separate rows with the exact same printed name -- they must
        merge into one traveler with three segments each, not nine
        separate fake travelers.
        """
        blocks = segment_tables(load("2024q4oct22.txt"), "2024q4oct22.txt")
        block = next(
            b
            for b in blocks
            if "DELEGATION TO LITHUANIA" in b.title_raw.upper() and "AMENDED" in b.title_raw.upper()
        )
        travelers, total, flags = rows_for(block)
        assert len(travelers) == 3
        names = {t.name for t in travelers}
        assert names == {"Bart Reising", "Brian Cress", "Derek Luyten"}
        for traveler in travelers:
            assert len(traveler.segments) == 3
            countries = [s.country_raw.rstrip(".") for s in traveler.segments]
            assert countries == ["Lithuania", "Latvia", "Estonia"]
            # First occurrence starts the traveler; the later two are merges.
            assert "REPEATED_NAME_SEGMENTS_MERGED" not in traveler.segments[0].flags
            assert "REPEATED_NAME_SEGMENTS_MERGED" in traveler.segments[1].flags
            assert "REPEATED_NAME_SEGMENTS_MERGED" in traveler.segments[2].flags

    def test_non_person_label_row_with_dates_flagged_not_merged_as_person(self):
        """'Delegation expenses' rows carry real dates/country like a
        traveler row, but the name doesn't look like a person (title-case
        only on the first word) -- flagged NON_PERSON_LABEL_ROW. Repeated
        occurrences still merge into one record (nothing dropped, no
        duplicate fake entries), but without the person-merge caveat.
        """
        blocks = segment_tables(load("1994q2may17.txt"), "1994q2may17.txt")
        # This specific table has three separate "Delegation expenses" rows
        # (one per unrelated sub-trip); an earlier table in the same file has
        # only one, which wouldn't exercise the merge path.
        block = next(
            b
            for b in blocks
            if "\n".join(b.lines).count("Delegation expenses") > 1
        )
        travelers, total, flags = rows_for(block)
        expenses = [t for t in travelers if t.name == "Delegation expenses"]
        assert len(expenses) == 1
        assert len(expenses[0].segments) >= 3
        # Every row that explicitly prints "Delegation expenses" is flagged;
        # a blank-name continuation row (e.g. Czech Republic/UK following the
        # Italy row) merges via the pre-existing continuation mechanism and
        # isn't itself a repeat of the name, so it carries no flag here.
        named_rows = [s for s in expenses[0].segments if s.flags]
        assert len(named_rows) >= 3
        for seg in named_rows:
            assert "NON_PERSON_LABEL_ROW" in seg.flags
            assert "REPEATED_NAME_SEGMENTS_MERGED" not in seg.flags

    def test_looks_like_traveler_row_name(self):
        assert _looks_like_traveler_row_name("Bart Reising") is True
        assert _looks_like_traveler_row_name("Hon. Hastert") is True
        assert _looks_like_traveler_row_name("Speaker Hastert") is True
        assert _looks_like_traveler_row_name("Delegation expenses") is False
        assert _looks_like_traveler_row_name("Luncheon") is False
        assert _looks_like_traveler_row_name("Interpreters") is False
        assert _looks_like_traveler_row_name("(CODEL McCaul)") is False

    def test_na_date_cells_do_not_swallow_the_traveler(self):
        """Some delegation rosters print the literal text 'N/A' in both date
        cells instead of leaving them dot-filled -- 'N/A' doesn't match the
        M/D date pattern, so without recognizing it as an equally valid
        date-zone token, this row finds zero date tokens and (since the
        prior traveler already has segments) gets silently read as a
        labeled cost-supplement row for THAT traveler -- discarding this
        traveler's name and merging their cost into someone else's segment.
        Three members (Wagner, Babin, Ellzey) on this Luxembourg delegation
        roster are affected; all three must survive as their own travelers.
        """
        block = find_block("2025q1feb18.txt", "DELEGATION TO LUXEMBOURG")
        travelers, total, flags = rows_for(block)
        names = {t.name for t in travelers}
        for name in ("Hon. Ann Wagner", "Hon. Brian Babin", "Hon. Jake Ellzey"):
            assert name in names, f"{name} was silently merged into another traveler"
            traveler = next(t for t in travelers if t.name == name)
            assert len(traveler.segments) == 1
            seg = traveler.segments[0]
            assert seg.arrival_raw == ""
            assert seg.departure_raw == ""
            assert seg.country_raw.rstrip(".") == "Luxembourg"
            assert costs_has_data(seg.costs)
        # Nobody's cost was inflated by an unwanted merge.
        mccaul = next(t for t in travelers if "McCaul" in t.name)
        assert costs_has_data(mccaul.segments[0].costs)
        assert len(mccaul.segments) == 1

    def test_codel_cancelled_placeholder_does_not_swallow_the_roster(self):
        """A cancelled CODEL is sometimes recorded with the literal words
        'CODEL'/'cancelled' filling both date cells instead of dates --
        same failure mode as 'N/A', different wording. Without recognizing
        this as an equally valid date-zone token, the entire table (9
        people) collapsed to zero travelers: the first name had no dated
        row to attach to (deferred via pending_name, then discarded), and
        every subsequent name was read as a labeled cost-supplement row
        for whoever came before it, which also never existed."""
        block = find_block("2026q2apr29.txt", "COMMITTEE ON NATURAL RESOURCES")
        travelers, total, flags = rows_for(block)
        expected_names = {
            "Hon. Bruce Westerman", "Vivian Moeglein", "Christopher Marklund",
            "Madeline Kelley", "Aneila Butler", "Robert MacGregor",
            "Michelle Lane", "Richard O'Connell", "Matthew Muirraugi",
        }
        names = {t.name for t in travelers}
        assert expected_names <= names
        for name in expected_names:
            traveler = next(t for t in travelers if t.name == name)
            assert len(traveler.segments) == 1
            seg = traveler.segments[0]
            assert seg.arrival_raw == ""
            assert seg.departure_raw == ""
            assert costs_has_data(seg.costs)
        # Each person's own cost, not merged with a neighbor's.
        kelley = next(t for t in travelers if t.name == "Madeline Kelley")
        assert kelley.segments[0].costs.total.us_dollar.amount == Decimal("7046.98")
        westerman = next(t for t in travelers if t.name == "Hon. Bruce Westerman")
        assert westerman.segments[0].costs.total.us_dollar.amount == Decimal("7650.49")

    def test_cancel_fees_placeholder_straddling_column_boundary_not_garbled(self):
        """A second wording of the cancelled-trip placeholder -- the literal
        words 'Cancel Fees' (not 'CODEL'/'cancelled') filling both date
        cells -- happens to fall a few characters to the left of this
        table's arrival-column boundary, splitting the first 'Cancel' into
        'Cance' (left of the boundary, in the name zone) and 'l' (right of
        it). Since only the second, undivided 'Cancel' then satisfied
        PLACEHOLDER_TOKEN_RE within the search zone, `_find_date_tokens`
        found just one placeholder token (needs two) and fell through to
        the dateless-row branch, where the name slice picked up the
        stray 'Cance' fragment: 'Susan Adams......................  Cance'.
        A token that straddles the boundary -- starts before it but ends
        at or after it -- must still count."""
        block = find_block("2025q1feb18.txt", "COMMITTEE ON APPROPRIATIONS")
        travelers, total, flags = rows_for(block)
        adams = next(t for t in travelers if "Adams" in t.name)
        assert adams.name == "Susan Adams"
        # Adams also has a separate, fully-dated trip elsewhere in this same
        # table -- both correctly attach to one traveler record by name; the
        # cancelled England leg is the dateless one among her segments.
        seg = next(s for s in adams.segments if s.country_raw == "England")
        assert seg.arrival_raw == ""
        assert seg.departure_raw == ""
        assert seg.costs.total.us_dollar.amount == Decimal("595.00")

        laturner = next(t for t in travelers if "LaTurner" in t.name)
        assert laturner.name == "Hon. Jake LaTurner"
        assert len(laturner.segments) == 1
        assert laturner.segments[0].costs.total.us_dollar.amount == Decimal("2496.75")

    def test_wrapped_didnt_depart_placeholder_not_promoted_as_garbled_name(self):
        """A third placeholder wording -- 'Didn't'/'Depart' split across two
        printed lines -- isn't recognized by PLACEHOLDER_TOKEN_RE at all (a
        single line's text is all `_find_date_tokens` ever sees; the
        continuation line is a separate row entirely). The row falls to the
        dateless branch, where this table's column boundary lands mid-word
        in the dot-fill after 'Ogles', so `layout.name.slice` captures
        'Hon. Andy Ogles.......................  Di' -- and NAME_WORD_RE
        (which allows dots mid-word for abbreviations like 'St.') would
        happily accept the dot-fill-contaminated fragment as a name-shaped
        word, promoting a garbled phantom traveler. Fully supporting this
        wrapped wording is out of scope (same as the CODEL/cancelled fix);
        the fallback must at least not fabricate a bad name."""
        block = find_block("2026q2apr16.txt", "(AMENDED) REPORT OF EXPENDITURES")
        travelers, total, flags = rows_for(block)
        assert not any(".." in t.name for t in travelers)
        assert not any(t.name.startswith("Hon. Andy Ogles.") for t in travelers)

    def test_canceled_single_l_not_confused_with_cancelled_placeholder(self):
        """Regression: 'canceled' (single-L, American spelling, appearing as
        a trailing annotation right after a REAL date -- '5/31 (CANCELED)')
        is a different word from the 'cancelled' (double-L) CODEL
        placeholder. An earlier, unanchored version of the placeholder
        pattern partial-matched the 'cancel' prefix inside 'CANCELED',
        which stole the second real date token and corrupted the segment
        (empty departure date, garbled country). Word boundaries on the
        placeholder pattern must keep these apart."""
        block = find_block("2023q3sep08.txt", "COMMITTEE ON WAYS AND MEANS")
        travelers, total, flags = rows_for(block)
        carey = next(t for t in travelers if "Carey" in t.name)
        assert carey.segments[0].arrival_raw == "5/31"
        assert carey.segments[0].departure_raw == "6/4"

    def test_dash_ditto_mark_attaches_to_traveler_above_not_treated_as_new(self):
        """Some tables print a bare '--' in the name column on continuation
        rows instead of leaving it blank. '--' is non-empty (so it doesn't
        take the existing blank-name continuation path) and isn't
        person-shaped (so it isn't a normal new traveler either) -- every
        continuation row for every traveler in the table was being merged
        together into one shared fake '--' traveler. Each '--' row must
        instead attach to the specific traveler named on the row above it.
        """
        block = find_block("2024q4nov14.txt", "COMMITTEE ON APPROPRIATIONS")
        travelers, total, flags = rows_for(block)
        assert not any(t.name == "--" for t in travelers)

        ellzey = next(t for t in travelers if "Ellzey" in t.name)
        countries = [s.country_raw.rstrip(".") for s in ellzey.segments]
        assert countries == ["Estonia", "Latvia", "Lithuania", "Poland"]

        grogis = next(t for t in travelers if t.name == "Joshua Grogis")
        assert [s.country_raw.rstrip(".") for s in grogis.segments] == [
            "Estonia",
            "Latvia",
            "Lithuania",
            "Poland",
        ]

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


def _synthetic_layout():
    """A simple fixed-width layout for constructing rows directly, bypassing
    layout auto-detection so these tests isolate extract_rows's own logic."""
    bounds = [0, 40, 52, 64, 90, 102, 114, 126, 138, 150, 162, 174]
    return TableLayout(
        name=ColumnSpan(bounds[0], bounds[1]),
        arrival=ColumnSpan(bounds[1], bounds[2]),
        departure=ColumnSpan(bounds[2], bounds[3]),
        country=ColumnSpan(bounds[3], bounds[4]),
        cost_columns=tuple(
            ColumnSpan(bounds[i], bounds[i + 1] if i + 1 < len(bounds) else None)
            for i in range(4, len(bounds))
        ),
        confidence=0.8,
        fingerprint=tuple(bounds),
        data_row_derived=False,
    )


def _row(name="", arrival="", departure="", country="", cost="", cost_col=1):
    layout = _synthetic_layout()
    parts = [""] * 12
    parts[0] = name
    parts[1] = arrival
    parts[2] = departure
    parts[3] = country
    parts[4 + cost_col] = cost
    bounds = [0, 40, 52, 64, 90, 102, 114, 126, 138, 150, 162, 174, 186]
    line = ""
    for i, part in enumerate(parts):
        width = bounds[i + 1] - bounds[i]
        line += part.ljust(width)[:width]
    return line


class TestDatelessNameRowWithOwnData:
    """A name row with no date tokens at all, but its own country and/or
    cost data, is a complete (if dateless) record on its own -- not a bare
    CODEL-style introduction whose itinerary follows on later rows. The old
    single-slot `pending_name` mechanism would defer such a name and then
    either silently overwrite it with the next such name, or discard it
    entirely if the table ends without a dated row ever arriving to
    consume it.
    """

    def test_lone_dateless_person_not_discarded_when_table_has_no_dates_at_all(self):
        """Regression for a real case (Delegation to Egypt,
        2007q3sep06-003): a table where every row is fully dot-filled for
        dates. The old code deferred each name into pending_name and
        discarded it unflushed once the loop ended with no dated row ever
        appearing -- the whole table produced zero travelers."""
        layout = _synthetic_layout()
        line = _row(name="Hon. Betty McCollum", country="Egypt", cost="500.00", cost_col=3)
        travelers, total, flags = extract_rows([(1, line)], layout, {})
        assert len(travelers) == 1
        mccollum = travelers[0]
        assert mccollum.name.strip() == "Hon. Betty McCollum"
        assert len(mccollum.segments) == 1
        seg = mccollum.segments[0]
        assert seg.arrival_raw == ""
        assert seg.departure_raw == ""
        assert seg.country_raw.rstrip(".") == "Egypt"
        assert costs_has_data(seg.costs)

    def test_bare_codel_label_row_still_defers_to_next_dated_row(self):
        """A genuine CODEL-style label row (name only, no country, no cost
        of its own) must still carry forward via pending_name to the next
        dated row -- this behavior must not regress."""
        layout = _synthetic_layout()
        label_line = _row(name="Hon. Gregorio Sablan")
        dated_line = _row(arrival="9/2", departure="9/5", country="Kuwait", cost="100.00", cost_col=3)
        travelers, total, flags = extract_rows(
            [(1, label_line), (2, dated_line)], layout, {}
        )
        assert len(travelers) == 1
        assert travelers[0].name.strip() == "Hon. Gregorio Sablan"
        assert len(travelers[0].segments) == 1
        assert travelers[0].segments[0].arrival_raw == "9/2"
        assert "SEGMENT_WITHOUT_TRAVELER_NAME" not in flags

    def test_incomplete_date_fragment_defers_rather_than_creating_dateless_record(self):
        """Regression: a name row with a country but INCOMPLETE date
        fragments ('1/' with no day -- not truly blank) must still defer
        via pending_name, not be treated as a complete dateless record.
        The real, fully-dated segment is on the very next row; treating
        the fragment row as complete would give the traveler a country-only
        phantom segment instead of attaching to their real itinerary."""
        layout = _synthetic_layout()
        fragment_line = _row(name="Uyen T. Dinh", arrival="1/", departure="1/", country="France")
        dated_line = _row(arrival="1/6", departure="1/12", country="Vietnam", cost="1300.00", cost_col=1)
        travelers, total, flags = extract_rows(
            [(1, fragment_line), (2, dated_line)], layout, {}
        )
        assert len(travelers) == 1
        dinh = travelers[0]
        assert dinh.name.strip() == "Uyen T. Dinh"
        assert len(dinh.segments) == 1
        assert dinh.segments[0].country_raw.rstrip(".") == "Vietnam"


class TestIsPersonNamedRow:
    """Direct tests for the classifier that decides whether a dateless
    row's printed name is a specific person (promote to their own record)
    or a cost/label line (stays a supplement merge)."""

    def test_honorific_with_annotation(self):
        assert _is_person_named_row("Hon. Neal Dunn (Did not travel)") == (True, True)

    def test_bare_name_with_annotation(self):
        assert _is_person_named_row("Sean Brady (Did not travel)") == (True, True)

    def test_bare_name_no_annotation(self):
        assert _is_person_named_row("Kevin Roper") == (True, False)

    def test_honorific_with_footnote_tail(self):
        assert _is_person_named_row("Hon. Steny Hoyer \\3\\") == (True, False)

    def test_bare_surname_shorthand_after_honorific(self):
        assert _is_person_named_row("Hon. Hastert") == (True, False)
        assert _is_person_named_row("Speaker Hastert") == (True, False)

    def test_cost_labels_rejected_even_in_title_case(self):
        assert _is_person_named_row("Commercial Airfare") == (False, False)
        assert _is_person_named_row("Commercial airfare") == (False, False)
        assert _is_person_named_row("Delegation expenses") == (False, False)
        assert _is_person_named_row("Misc. delegation expenses") == (False, False)
        assert _is_person_named_row("Military air transportation") == (False, False)

    def test_leading_parenthetical_rejected(self):
        assert _is_person_named_row("(CODEL McCaul)") == (False, False)

    def test_cancel_annotation_with_vocab_word_rejected(self):
        assert _is_person_named_row("Ground transportation (Cancelled)") == (False, True)

    def test_surname_matching_common_word_not_rejected(self):
        """'Day' is a real surname (Corinne Day, Tim Day, Jonathan Day all
        appear in the corpus) that happens to collide with vocabulary once
        considered for label rows like 'Travel day' -- but that phrase only
        ever appears in the *country* column in this corpus, never as a
        name-column label, so it was dropped from LABEL_VOCAB rather than
        risk rejecting real people."""
        assert _is_person_named_row("Corinne Day") == (True, False)

    def test_comma_typo_in_honorific_still_recognized(self):
        assert _is_person_named_row("Hon, Stephen Lynch") == (True, False)

    def test_honorific_name_with_trailing_annotation_not_rejected(self):
        """A curated honorific followed by a real name, plus a trailing
        parenthetical/context note that isn't part of the name, still
        names a real person -- only a *leading* name-shaped run after the
        honorific is required, and a vocab word appearing solely in the
        trailing note (e.g. 'CODEL') doesn't disqualify the row."""
        assert _is_person_named_row("Hon. Mike Rogers (AL)") == (True, False)
        assert _is_person_named_row("Hon. Harold Rogers of Kentucky") == (True, False)
        assert _is_person_named_row("Hon. Bud Cramer (App.)") == (True, False)
        assert _is_person_named_row("Hon. Mac Collins (Rogers CODEL)") == (True, False)


class TestPersonDatelessRowPromotion:
    """A dateless row naming a SPECIFIC person (a booked traveler who
    didn't go, a cancellation fee, or a bare staffer row in an all-dateless
    roster table) must become its own traveler record, not be silently
    read as a cost-supplement label for whoever came before it.
    """

    def test_annotated_person_promoted_not_merged_into_prior_traveler(self):
        """Regression for the real case (2024q4nov14-002): 'Hon. Neal Dunn
        (Did not travel)' following Hon. Michael McCaul's dated row must
        become Dunn's own record, not inflate McCaul's segment."""
        layout = _synthetic_layout()
        mccaul_line = _row(
            name="Hon. Michael McCaul", arrival="9/29", departure="10/1",
            country="Japan", cost="623.00", cost_col=7,
        )
        dunn_line = _row(
            name="Hon. Neal Dunn (Did not travel)", country="Japan",
            cost="200.07", cost_col=7,
        )
        travelers, total, flags = extract_rows([(1, mccaul_line), (2, dunn_line)], layout, {})
        assert len(travelers) == 2
        mccaul = next(t for t in travelers if "McCaul" in t.name)
        dunn = next(t for t in travelers if "Dunn" in t.name)
        assert len(mccaul.segments) == 1
        assert "COST_SUPPLEMENT_MERGED" not in mccaul.segments[0].flags
        assert mccaul.segments[0].costs.total.us_dollar.amount == Decimal("623.00")
        assert len(dunn.segments) == 1
        assert dunn.segments[0].arrival_raw == ""
        assert dunn.segments[0].departure_raw == ""
        assert dunn.segments[0].country_raw.rstrip(".") == "Japan"
        assert dunn.segments[0].costs.total.us_dollar.amount == Decimal("200.07")
        assert "DID_NOT_TRAVEL" in dunn.segments[0].flags

    def test_bare_name_with_and_without_annotation(self):
        layout = _synthetic_layout()
        dated_line = _row(
            name="Hon. Michael McCaul", arrival="9/29", departure="10/1",
            country="Japan", cost="623.00", cost_col=7,
        )
        brady_line = _row(
            name="Sean Brady (Did not travel)", country="Japan", cost="200.07", cost_col=7,
        )
        roper_line = _row(name="Kevin Roper", country="Japan", cost="199.00", cost_col=7)
        travelers, total, flags = extract_rows(
            [(1, dated_line), (2, brady_line), (3, roper_line)], layout, {}
        )
        assert len(travelers) == 3
        brady = next(t for t in travelers if "Brady" in t.name)
        roper = next(t for t in travelers if "Roper" in t.name)
        assert "DID_NOT_TRAVEL" in brady.segments[0].flags
        assert "DID_NOT_TRAVEL" not in roper.segments[0].flags
        assert roper.segments[0].costs.total.us_dollar.amount == Decimal("199.00")

    def test_footnote_tail_person_promoted(self):
        layout = _synthetic_layout()
        dated_line = _row(
            name="Hon. C.W. Bill Young", arrival="1/6", departure="1/7",
            country="United States", cost="199.00", cost_col=7,
        )
        hoyer_line = _row(
            name="Hon. Steny Hoyer \\3\\", country="United States", cost="199.00", cost_col=7,
        )
        travelers, total, flags = extract_rows([(1, dated_line), (2, hoyer_line)], layout, {})
        assert len(travelers) == 2
        hoyer = next(t for t in travelers if "Hoyer" in t.name)
        assert len(hoyer.segments) == 1
        assert hoyer.segments[0].costs.total.us_dollar.amount == Decimal("199.00")

    def test_cost_label_rows_still_merge_as_supplement(self):
        """Regression guard: label rows -- including a TITLE-CASE cost
        label, which is the exact shape that would otherwise pass a pure
        capitalization heuristic -- must still merge into the current
        traveler's segment, not become phantom travelers."""
        layout = _synthetic_layout()
        dated_line = _row(
            name="Hon. Michael McCaul", arrival="9/29", departure="10/1",
            country="Japan", cost="500.00", cost_col=7,
        )
        for label in ("Commercial airfare", "Commercial Airfare", "Misc. delegation expenses"):
            label_line = _row(name=label, cost="50.00", cost_col=7)
            travelers, total, flags = extract_rows([(1, dated_line), (2, label_line)], layout, {})
            assert len(travelers) == 1, f"{label!r} wrongly promoted to its own traveler"
            seg = travelers[0].segments[0]
            assert "COST_SUPPLEMENT_MERGED" in seg.flags
            assert seg.costs.total.us_dollar.amount == Decimal("550.00")

    def test_military_air_label_row_unaffected(self):
        layout = _synthetic_layout()
        dated_line = _row(
            name="Hon. Michael McCaul", arrival="9/29", departure="10/1",
            country="Japan", cost="500.00", cost_col=7,
        )
        military_line = _row(name="Military air transportation")
        travelers, total, flags = extract_rows([(1, dated_line), (2, military_line)], layout, {})
        assert len(travelers) == 1
        seg = travelers[0].segments[0]
        assert "MILITARY_AIR_LABEL_ROW" in seg.flags
        assert seg.costs.transportation.us_dollar.military_air is True

    def test_person_row_with_no_cost_data_not_promoted(self):
        """A person-shaped name with neither country nor cost data isn't a
        complete record -- don't create a phantom empty traveler for it."""
        layout = _synthetic_layout()
        dated_line = _row(
            name="Hon. Michael McCaul", arrival="9/29", departure="10/1",
            country="Japan", cost="500.00", cost_col=7,
        )
        empty_person_line = _row(name="Hon. Someone Else")
        travelers, total, flags = extract_rows(
            [(1, dated_line), (2, empty_person_line)], layout, {}
        )
        assert len(travelers) == 1
        assert not any("Someone Else" in t.name for t in travelers)

    def test_country_overflow_after_promoted_person_attaches_to_their_segment(self):
        """A country-overflow continuation line following a promoted
        person's row must extend THAT person's segment, not the prior
        traveler's -- confirms `current` is correctly repointed."""
        layout = _synthetic_layout()
        dated_line = _row(
            name="Hon. Michael McCaul", arrival="9/29", departure="10/1",
            country="Japan", cost="500.00", cost_col=7,
        )
        dunn_line = _row(
            name="Hon. Neal Dunn (Did not travel)", country="Republic of", cost="200.07", cost_col=7,
        )
        overflow_line = _row(country="Korea")
        travelers, total, flags = extract_rows(
            [(1, dated_line), (2, dunn_line), (3, overflow_line)], layout, {}
        )
        dunn = next(t for t in travelers if "Dunn" in t.name)
        mccaul = next(t for t in travelers if "McCaul" in t.name)
        assert "Korea" in dunn.segments[0].country_raw
        assert "CONTINUATION_MERGED" in dunn.segments[0].flags
        assert "Korea" not in mccaul.segments[0].country_raw

    def test_first_row_annotated_person_promoted(self):
        """A person-with-annotation row as the very first row of a table
        (current is None) must also be promoted, not silently dropped."""
        layout = _synthetic_layout()
        line = _row(
            name="Hon. Neal Dunn (Did not travel)", country="Japan", cost="200.07", cost_col=7,
        )
        travelers, total, flags = extract_rows([(1, line)], layout, {})
        assert len(travelers) == 1
        assert travelers[0].name.strip() == "Hon. Neal Dunn (Did not travel)"
        assert "DID_NOT_TRAVEL" in travelers[0].segments[0].flags


class TestPersonDatelessRowPromotionRealFixture:
    """End-to-end regression for the real 2024q4nov14-002 case (Delegation
    to Japan, the Philippines, Qatar, and Finland): three people who didn't
    travel (or had cancellation fees) were previously swallowed into
    whichever traveler happened to be `current` at that point in the
    table, inflating Hon. Victoria Spartz's and Mitchell Moonier's Japan
    segments by the missing people's fees.
    """

    def test_did_not_travel_rows_promoted_not_merged_into_neighbors(self):
        block = find_block("2024q4nov14.txt", "DELEGATION TO JAPAN")
        travelers, total, flags = rows_for(block)
        by_name = {t.name.strip(): t for t in travelers}

        for name, amount in (
            ("Hon. Neal Dunn (Did not travel)", "200.07"),
            ("Hon. Anna Luna (Did not travel)", "250.09"),
            ("Sean Brady (Did not travel)", "200.07"),
        ):
            assert name in by_name, f"{name} was silently merged into another traveler"
            traveler = by_name[name]
            assert len(traveler.segments) == 1
            seg = traveler.segments[0]
            assert seg.arrival_raw == ""
            assert seg.departure_raw == ""
            assert "DID_NOT_TRAVEL" in seg.flags
            assert seg.costs.total.us_dollar.amount == Decimal(amount)

        # The two travelers whose segments used to absorb these fees are
        # back to their own, un-inflated totals.
        spartz = by_name["Hon. Victoria Spartz"]
        assert spartz.segments[0].country_raw.rstrip(".") == "Japan"
        assert spartz.segments[0].costs.total.us_dollar.amount == Decimal("623.00")
        assert "COST_SUPPLEMENT_MERGED" not in spartz.segments[0].flags

        moonier = by_name["Mitchell Moonier"]
        assert moonier.segments[0].country_raw.rstrip(".") == "Japan"
        assert moonier.segments[0].costs.total.us_dollar.amount == Decimal("623.00")
        assert "COST_SUPPLEMENT_MERGED" not in moonier.segments[0].flags
