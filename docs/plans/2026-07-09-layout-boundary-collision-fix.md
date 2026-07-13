# Layout Boundary Collision Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (or
> superpowers:executing-plans in a separate session) to implement this plan
> task-by-task.

**Goal:** Fix `_refine_boundary` in `official_foreign_travel/parsing/layout.py` so that
right-justified numeric columns are never truncated and column boundaries never collide
onto a neighboring column — and make this failure class visible to validation so it can
never hide again.

**Architecture:** Change the boundary-refinement criterion from "snap to a position
where ≥60% of data rows *start a token*" (wrong for right-justified numbers, whose
token starts shift with digit count) to "snap to the nearest position that *cuts
through no row's token*" (correct: with dot-filled empty cells, the only positions that
split nothing are the true inter-column gutters). Add a post-refinement collision guard
and a `ROW_TOTAL_MISSING` validation flag as defense in depth.

**Tech Stack:** Pure-Python changes to `parsing/layout.py` and `parsing/validate.py`;
pytest; the existing corpus-regression harness.

---

## The bug, precisely

`_refine_boundary` searches outward (offset 0..20) from each label-derived column
position for a column where ≥60% of data rows satisfy `_is_token_start` (non-space
preceded by 2+ spaces). Two failure modes, both measured on the real corpus:

1. **Truncation.** Right-justified amounts start at different columns per row
   (`"467.00"` starts 2 chars later than `"2,079.00"`). The 60%-consensus position is
   where the *most common width* starts — slicing there cuts leading digits off wider
   values. Real example: `1994q1feb10` table 1, Mr. David Finnegan's per diem parses
   as **`79.00`** (true value `2,079.00`). This is silent data corruption, not a null.
2. **Boundary theft / collision.** When no position near the guess reaches 60%
   consensus (mixed widths + dot-fills split the vote), the search widens until it
   lands on a *neighboring column's* boundary. Same table: `transportation.us_dollar`'s
   boundary snapped 12 chars away onto `other.foreign_currency`'s position, and
   `total.us_dollar`'s snapped 14 chars onto `transportation.foreign_currency`'s. Two
   boundaries at the same position = one zero-width column (always null) and one
   double-width column (unparseable mixed text).

**Measured blast radius** (full corpus, 2026-07-09, commit `6943327`):

- 571 of 2,700 tables (21.1%) have at least one collided cost-column boundary.
- Cost-population rates across 55,992 segments: per_diem 74.3%, **transportation
  18.0%**, other 6.2%, total 60.2% — the transportation/total gaps are dominated by
  this bug.
- Invisibility: collided tables still report `layout_confidence: 1.0` (the confidence
  formula counts "refinement found *a* position," not "positions stayed distinct"),
  and `TABLE_SUM_MISMATCH` never fires because it requires a non-null `total` — which
  is exactly what the bug nulls. The failure class is invisible to the review queue.

**Why the fix criterion is sound:** empty cells in these tables are dot-filled to full
cell width, and populated cells are dot-filled (country) or right-justified numbers.
So in every row, cell regions are occupied by tokens and the true gutters are the only
column ranges that are whitespace-adjacent in *all* rows. A position that splits no
row's token is, with overwhelming probability, inside a true gutter. Slicing at any
in-gutter position is correct: leading/trailing spaces are stripped by `clean_cell`,
and the no-cut test guarantees the previous column's content ends before the boundary
in every row. (Landing *early* — inside an all-blank run — is harmless for the same
reason; landing *late* is impossible without cutting the widest row's token, which the
test forbids.)

---

## Task 1: Fixture + failing golden tests that pin the bug

**Files:**
- Create: `tests/fixtures/1994q1feb10_energy.txt`
- Test: `tests/test_layout_goldens.py`

**Step 1: Extract the failing table into a fixture**

Find the exact line range (don't trust hardcoded numbers — the file may differ):

```bash
grep -n "COMMITTEE ON ENERGY AND COMMERCE\|JOHN D. DINGELL" report_text/1994q1feb10.txt
```

Expected: the `REPORT OF EXPENDITURES ... ENERGY AND COMMERCE` title around line 36 and
`JOHN D. DINGELL, Chairman.` a few lines before the next table's title. Extract from
the title line through the Dingell signature line (inclusive), e.g.:

```bash
sed -n '36,64p' report_text/1994q1feb10.txt > tests/fixtures/1994q1feb10_energy.txt
```

Verify the fixture is one complete table:

```bash
uv run python3 -c "
from pathlib import Path
from official_foreign_travel.parsing.segmenter import segment_tables
text = Path('tests/fixtures/1994q1feb10_energy.txt').read_text(errors='replace')
blocks = segment_tables(text, '1994q1feb10_energy.txt')
print(len(blocks), 'block(s)')
assert len(blocks) == 1
assert any('Committee total' in l for l in blocks[0].lines)
assert any('DINGELL' in l for l in blocks[0].lines)
print('fixture ok')
"
```

**Step 2: Write the failing golden tests**

```python
# tests/test_layout_goldens.py
"""Golden values for tables that exposed layout-boundary bugs.

The 1994q1feb10 Energy & Commerce table has right-justified amounts of mixed
widths plus dot-filled empties -- the exact shape that made the old
token-start refinement truncate digits (per diem parsed as 79.00 instead of
2,079.00) and collide boundaries (transportation/total swallowed entirely).
Values below are read directly from the raw fixture text.
"""

from decimal import Decimal
from pathlib import Path

from official_foreign_travel.parsing.assemble import assemble_file

FIXTURES = Path(__file__).parent / "fixtures"


class TestEnergyCommerce1994Goldens:
    def _report(self):
        reports = assemble_file(FIXTURES / "1994q1feb10_energy.txt")
        assert len(reports) == 1
        return reports[0]

    def test_wide_amounts_are_not_truncated(self):
        report = self._report()
        finnegan = report.travelers[0]
        assert finnegan.name == "Mr. David Finnegan"
        seg = finnegan.segments[0]
        assert seg.costs.per_diem.us_dollar.amount == Decimal("2079.00")

    def test_transportation_and_total_are_not_swallowed(self):
        report = self._report()
        seg = report.travelers[0].segments[0]
        assert seg.costs.transportation.us_dollar.amount == Decimal("3049.45")
        assert seg.costs.total.us_dollar.amount == Decimal("5128.45")

    def test_multi_segment_traveler_amounts(self):
        report = self._report()
        endres = next(t for t in report.travelers if "Endres" in t.name)
        amounts = [s.costs.per_diem.us_dollar.amount for s in endres.segments]
        assert amounts == [
            Decimal("467.00"),
            Decimal("398.00"),
            Decimal("592.00"),
            Decimal("621.00"),
        ]
        # The Spain leg carries the transportation charge.
        assert endres.segments[-1].costs.transportation.us_dollar.amount == Decimal(
            "3461.45"
        )

    def test_committee_total_row(self):
        report = self._report()
        total = report.committee_total
        assert total is not None
        assert total.per_diem.us_dollar.amount == Decimal("9826.00")
        assert total.transportation.us_dollar.amount == Decimal("16868.50")
        assert total.total.us_dollar.amount == Decimal("26694.50")
```

**Step 3: Run the tests to verify they fail — and fail the right way**

Run: `uv run pytest tests/test_layout_goldens.py -v`

Expected: all four FAIL against current code, with the truncation test showing the
observed corruption (`Decimal('79.00') != Decimal('2079.00')`) and the others showing
`None`. If any *passes*, stop — the bug reproduction is wrong and the plan needs
re-scoping before proceeding.

**Step 4: Commit the fixture and failing tests (marked xfail temporarily)**

Add `@pytest.mark.xfail(reason="layout boundary collision bug -- fixed in next commit", strict=True)`
to the class (import pytest), so the suite stays green until the fix lands:

```bash
uv run pytest tests/test_layout_goldens.py -v   # 4 xfailed
git add tests/fixtures/1994q1feb10_energy.txt tests/test_layout_goldens.py
git commit -m "Add golden tests pinning the layout boundary truncation/collision bug"
```

---

## Task 2: Replace the refinement criterion in `_refine_boundary`

**Files:**
- Modify: `official_foreign_travel/parsing/layout.py`
- Test: `tests/test_layout.py`

**Step 1: Write failing unit tests for the new criterion**

Append to `tests/test_layout.py`:

```python
from official_foreign_travel.parsing.layout import _cuts_token, _refine_boundary


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
```

**Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_layout.py -v -k "CutsToken or RefineBoundaryRightJustified"`
Expected: FAIL with `ImportError: cannot import name '_cuts_token'`.

**Step 3: Implement**

In `official_foreign_travel/parsing/layout.py`, replace `_is_token_start` and
`_refine_boundary` with:

```python
def _cuts_token(line: str, col: int) -> bool:
    """Whether slicing at `col` would split a token in this row in two."""
    if col <= 0 or col >= len(line):
        return False
    return line[col] != " " and line[col - 1] != " "


def _refine_boundary(guess: int, data_lines: Sequence[str]) -> tuple[int, bool]:
    """
    Snap a label-derived boundary guess to the nearest position that cuts
    through no data row's token.

    Right-justified numeric columns make "where tokens start" the wrong
    criterion: starts shift with digit count, so a majority-vote position
    truncates the wider values, and when no position wins a majority the
    search used to wander onto a neighboring column's boundary entirely.
    Because empty cells are dot-filled to full width, the only positions
    that split nothing in any row are the true inter-column gutters --
    slicing anywhere inside a gutter is correct (leading whitespace is
    stripped downstream, and the no-cut guarantee means the previous
    column's content always ends before the boundary).

    A strict zero-cuts pass runs first; if nothing within the window
    qualifies (e.g. a rare over-wide value bleeds through every gutter), a
    second pass tolerates cuts in up to 10% of rows rather than giving up.
    """
    if not data_lines:
        return guess, False

    for max_cuts in (0, len(data_lines) // 10):
        for offset in range(0, REFINE_WINDOW + 1):
            candidates = [guess - offset, guess + offset] if offset else [guess]
            for candidate in candidates:
                if candidate < 1:
                    continue
                cuts = sum(1 for line in data_lines if _cuts_token(line, candidate))
                if cuts <= max_cuts:
                    return candidate, True

    return guess, False
```

Notes for the implementer:

- Candidate order `[guess - offset, guess + offset]` is deliberate and must be a list,
  not a set: ties at equal offset prefer the *left* candidate (slicing early is safe,
  slicing late truncates), and set iteration order would make layouts
  nondeterministic between runs.
- Check whether `_is_token_start` has any remaining callers
  (`grep -rn "_is_token_start" official_foreign_travel/ tests/`). Tests reference it
  (`tests/test_layout.py` may import it); update those tests to exercise `_cuts_token`
  instead and delete `_is_token_start` — do not leave a dead function with a
  now-misleading docstring.

**Step 4: Run the new unit tests**

Run: `uv run pytest tests/test_layout.py -v -k "CutsToken or RefineBoundaryRightJustified"`
Expected: PASS.

**Step 5: Remove the xfail from Task 1's goldens and run them**

Run: `uv run pytest tests/test_layout_goldens.py -v`
Expected: PASS (4 passed). If the collision cases still fail here, Task 3's guard may
be doing the remaining work — investigate before proceeding, don't reorder tasks
blindly.

**Step 6: Reconcile existing layout/row tests**

Run: `uv run pytest tests/test_layout.py tests/test_rows.py tests/test_assemble.py tests/test_costs.py -v`

Some tests assert exact expected spans per era; refined positions may legitimately
shift by 1-2 columns (into the gutter instead of at a token start). For each failure:
confirm via the golden tests and a spot-parse that the *extracted values* are correct,
then update the expected span. The goldens are the arbiter — never adjust a golden to
match the code.

**Step 7: Commit**

```bash
git add official_foreign_travel/parsing/layout.py tests/test_layout.py tests/test_layout_goldens.py
git commit -m "Refine layout boundaries by token-cut avoidance, not token-start voting"
```

---

## Task 3: Collision guard + honest confidence

**Files:**
- Modify: `official_foreign_travel/parsing/layout.py` (`detect_layout`)
- Test: `tests/test_layout.py`

Even with the new criterion, degenerate inputs (tables with blank rather than
dot-filled empties, OCR noise) could still collide two boundaries. A collision must
never again score `confidence: 1.0`.

**Step 1: Write the failing test**

Append to `tests/test_layout.py`:

```python
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
```

(If the synthetic header doesn't survive `_find_header_window`/`_label_positions`,
adjust the header lines until `detect_layout` returns a layout — the test's point is
the confidence cap, not the header parsing. Confirm the test fails against current
code before implementing.)

**Step 2: Implement the guard in `detect_layout`**

After the `refined.sort(...)` / `positions = ...` lines:

```python
    collided = len(set(positions)) < len(positions)
```

And in the confidence computation, after the existing terms:

```python
    if collided:
        # Two boundaries on the same column means a zero-width column and a
        # doubled neighbor -- extraction from this layout is not trustworthy,
        # so force it under the review/LLM-fallback threshold.
        confidence = min(confidence, 0.5)
```

(0.5 < `LOW_CONFIDENCE_THRESHOLD` (0.8) in `assemble.py`, which appends
`LAYOUT_LOW_CONFIDENCE` — routing the table to the review queue and making it eligible
for `--llm-fallback`. Verify the threshold import/constant rather than trusting this
parenthetical.)

**Step 3: Run tests**

Run: `uv run pytest tests/test_layout.py -v`
Expected: PASS.

**Step 4: Commit**

```bash
git add official_foreign_travel/parsing/layout.py tests/test_layout.py
git commit -m "Cap layout confidence when refined boundaries collide"
```

---

## Task 4: Make missing totals visible — `ROW_TOTAL_MISSING`

**Files:**
- Modify: `official_foreign_travel/parsing/validate.py`
- Test: `tests/test_validate.py`

The sum check silently skips segments whose `total` is null — which let this bug hide.
A segment that has component amounts but no total is itself a reviewable anomaly.

**Step 1: Write the failing tests**

Append to `tests/test_validate.py` (reuse the file's existing report/segment builder
helpers — read them first and follow local conventions):

```python
class TestRowTotalMissing:
    def test_components_without_total_are_flagged(self):
        report = make_report_with_segment(per_diem="100.00", total=None)  # adapt to helpers
        validate_report(report)
        segment = report.travelers[0].segments[0]
        assert "ROW_TOTAL_MISSING" in segment.flags

    def test_fully_empty_cost_row_is_not_flagged(self):
        report = make_report_with_segment(per_diem=None, total=None)
        validate_report(report)
        segment = report.travelers[0].segments[0]
        assert "ROW_TOTAL_MISSING" not in segment.flags

    def test_flag_is_idempotent_across_revalidation(self):
        report = make_report_with_segment(per_diem="100.00", total=None)
        validate_report(report)
        validate_report(report)
        segment = report.travelers[0].segments[0]
        assert segment.flags.count("ROW_TOTAL_MISSING") == 1
```

**Step 2: Implement**

In `validate_report`:

- Add `"ROW_TOTAL_MISSING"` to the segment flags cleared at the top (the idempotency
  block that already clears `ROW_SUM_MISMATCH`).
- In the per-segment loop, where `declared_total is None` currently `continue`s:

```python
        if declared_total is None:
            if _group_total(segment.costs) > 0:
                segment.flags.append("ROW_TOTAL_MISSING")
            continue
```

**Step 3: Run tests, then the full suite**

Run: `uv run pytest tests/test_validate.py -v` then `uv run pytest tests/ -q`
Expected: validate tests pass. **The corpus-regression and fixture tests may now show
new flags** — that is the point of the flag; update fixture-based flag expectations
only where the new flag is legitimately present.

**Step 4: Commit**

```bash
git add official_foreign_travel/parsing/validate.py tests/test_validate.py
git commit -m "Flag segments that have cost components but no total (ROW_TOTAL_MISSING)"
```

---

## Task 5: Full-corpus verification and honest bookkeeping

**Files:**
- Modify: `CHANGELOG.md`, `about_the_data.md`
- No source changes expected (fixes go back to the responsible task)

**Step 1: Full suite + corpus regression**

```bash
uv run pytest tests/ -q
```

Expected: all pass, including `test_corpus_regression.py` (per-year segment counts must
not drop below `tests/baseline_counts.json` — the layout change alters the country
boundary that row extraction searches within, so watch this closely; a drop is a
regression in Task 2, not a baseline to update).

**Step 2: Re-parse the corpus and measure the recovery**

```bash
uv run oft-parse report_text/ /tmp/corpus_after_fix.json --fuzzy-name-matching
```

Then compare against the recorded 2026-07-09 baseline with this script:

```bash
uv run python3 - <<'EOF'
import json
d = json.load(open('/tmp/corpus_after_fix.json'))
segs = [s for r in d['reports'] for t in r['travelers'] for s in t['segments']]
print('segments:', len(segs), '(baseline 55,992 -- must not drop)')
baseline = {'per_diem': 74.3, 'transportation': 18.0, 'other': 6.2, 'total': 60.2}
for cat, before in baseline.items():
    pct = 100 * sum(1 for s in segs if s['costs'][cat]['us_dollar']['amount'] is not None) / len(segs)
    print(f'{cat:15s} {before:5.1f}% -> {pct:5.1f}%')
EOF
```

Acceptance: transportation and total population rates rise substantially (the 571
collided tables all contribute); segment count does not fall; nothing decreases.

**Step 3: Confirm the degenerate-boundary count is ~0**

```bash
uv run python3 - <<'EOF'
from pathlib import Path
from official_foreign_travel.parsing.segmenter import segment_tables
from official_foreign_travel.parsing.layout import detect_layout
import re
CAND = re.compile(r'\d{1,2}/\d{1,2}\s+\d{1,2}/\d{1,2}')
bad = total = 0
for f in sorted(Path('report_text').glob('*.txt')):
    for b in segment_tables(f.read_text(errors='replace'), f.name):
        rows = [l for l in b.lines if CAND.search(l[:80])]
        if not rows:
            continue
        layout = detect_layout(b.lines, rows)
        if layout is None:
            continue
        total += 1
        starts = [s.start for s in layout.cost_columns]
        if len(set(starts)) < len(starts):
            bad += 1
            print(f.name, b.table_index, layout.confidence)
print(f'{bad}/{total} collided (baseline 571/2700)')
EOF
```

Acceptance: 0 collided, or every residual collision prints `confidence <= 0.5` (Task
3's guard working). Investigate any exception.

**Step 4: Diff the flag census and sanity-check it**

The fix will *increase* some flag counts (`TABLE_SUM_MISMATCH` can now actually fire
on tables whose totals were previously null; `ROW_TOTAL_MISSING` is new). That is
honest, not a regression. Record before/after counts:

```bash
python3 -c "
import json, collections
d = json.load(open('/tmp/corpus_after_fix.json'))
c = collections.Counter(f for r in d['reports'] for f in r['flags'])
sc = collections.Counter(f for r in d['reports'] for t in r['travelers'] for s in t['segments'] for f in s['flags'])
print('report-level:', dict(c)); print('segment-level:', dict(sc))
"
```

Spot-check 3 newly-`TABLE_SUM_MISMATCH` tables against their raw text to confirm the
flag is correct rather than a new arithmetic bug.

**Step 5: Update the docs that state corpus numbers**

- `CHANGELOG.md`: new entry describing the bug (truncated digits and swallowed
  columns on ~21% of tables), the fix, and the measured before/after cost-population
  rates. State plainly that **cost figures parsed by earlier v3 versions were
  affected and downstream outputs should be regenerated** — this previously shipped
  wrong numbers, and the changelog is where that gets said out loud.
- `about_the_data.md`: if it cites cost-coverage percentages, refresh them.

**Step 6: Final commit**

```bash
uv run pytest tests/ -q
git add CHANGELOG.md about_the_data.md
git commit -m "Document the layout boundary fix and refreshed cost-coverage numbers"
```

---

## Out of scope (deliberately)

- Re-reviewing existing `corrections.json` entries whose underlying reports now parse
  differently (report_ids are stable; corrections still apply; but a reviewer who
  "confirmed OK" a table with silently-null transportation may want to revisit —
  worth a note in the changelog, not code).
- Adding `ROW_TOTAL_MISSING` to the LLM fallback's `needs_repair` triggers. Plausible
  follow-up once the deterministic fix's residue is known; don't widen the LLM's job
  in the same change that fixes the parser.
- Rewriting layout detection as joint gutter-segmentation (find persistent whitespace
  runs across all rows, assign labels to the regions between them). Strictly sounder
  than per-boundary refinement, but a bigger rewrite; only justified if this fix's
  corpus verification shows a meaningful residue.
