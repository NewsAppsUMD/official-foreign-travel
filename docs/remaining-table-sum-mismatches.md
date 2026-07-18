# Remaining `TABLE_SUM_MISMATCH` issues

As of v3.0.26, **218 reports** still carry the `TABLE_SUM_MISMATCH` flag. These
are reports where the committee-total cell cannot be reconciled with the
per-segment cost cells under any of the recovery patterns implemented in
`official_foreign_travel/parsing/validate.py` (supplement merge,
transport-excluded, row-flag residuals, single-component delta, rounding,
committee-total comma-decimal typo, committee-component comma-decimal typo,
`CT_NO_BREAKDOWN`, `NO_SEG_BREAKDOWN`, `CT_HAS_UNBROKEN_COMPONENT`,
`SEG_HAS_UNBROKEN_COMPONENT`).

This document describes the structural shape of the remaining 218 reports so
future work can pick targets deliberately rather than re-scoping from scratch.

## Classification scheme

Each TSM report is classified along three independent axes:

- **A**: ct internal consistency — `ct.total == ct.per_diem + ct.transportation + ct.other`
- **B**: segment internal consistency — `sum(seg.total) == sum(seg.per_diem + seg.transportation + seg.other)`
- **C**: component agreement — `ct.per_diem == sum(seg.per_diem)` (and likewise for transportation and other)

A report can be internally consistent on either side (A=Y, B=Y) but still
have C-level component disagreement, or be internally inconsistent on one side
but not the other. `TABLE_SUM_MISMATCH` only fires when no recovery pattern
explains the delta, so every report below failed all of the existing
classifiers.

## Bucket breakdown

| Bucket | Count | Notes |
|---|---:|---|
| A=Y B=Y C=N | 124 | Both sides internally consistent; per-component sums disagree |
| A=Y B=N C=N | 46  | Segs internally inconsistent; ct consistent |
| A=N B=Y C=N | 28  | ct internally inconsistent; segs consistent |
| A=N B=N C=N | 20  | Both sides internally inconsistent |
| **Total** | **218** | |

The four buckets are described in detail below, with sub-patterns and
candidate recovery ideas where they exist. None of the candidates look like
clean wins — most reports have multiple disagreements that compound, and the
existing arithmetic classifiers already absorb the cases that have a single
explanation.

## A=Y B=Y C=N (124 reports)

Both the committee total and the segment totals are internally consistent,
but the per-component sums differ between the two sides. The table delta
equals the sum of the three per-component diffs.

**Sub-patterns:**

- **One component differs (71 reports)** — Only one of pd/tr/ot disagrees;
  the other two match exactly. All 71 have a per-component delta greater than
  $5 (so the existing `TABLE_SUM_ROUNDING` classifier doesn't fire) and none
  are 100x/1000x typo relationships between the ct and seg amounts. Both sides
  have a value for the component; they just disagree by hundreds or thousands
  of dollars. Examples:
  - `1994q1feb10-011`: ct_pd=10618.50, seg_pd=10418.50 (pd diff +200.00)
  - `1994q3aug20-007`: ct_pd=8102.30, seg_pd=118932.30 (pd diff -110830.00)
  - `1995q4nov18-014`: ct_pd=40854.55, seg_pd=40954.55 (pd diff -100.00)
  - `1997q2apr23-005`: ct_pd=36814.93, seg_pd=40560.93 (pd diff -3746.00)
  - `1997q3sep03-016`: ct_tr=12140.08, seg_tr=13575.80 (tr diff -1435.72)
- **Two components differ (35 reports)** — pd+tr, pd+ot, or tr+ot disagree.
- **Three components differ (18 reports)** — all three components disagree.
- **All three per-component diffs within 1% of ct_total (27 reports)** —
  these look superficially like rounding, but the per-component deltas are
  typically hundreds of dollars (just under 1% of a large ct_total). The
  existing `TABLE_SUM_ROUNDING` classifier caps the threshold at `min($5, 1%
  of ct_total)`, so these don't qualify. There is no obvious source convention
  that explains a per-component diff of, say, $270 on a $48k ct_total —
  calling this "rounding" would be labeling rather than explaining.

**Recovery candidates:** None with a clean source convention. The one-component
cases are the most tempting target, but without a typo relationship (100x /
1000x / comma-decimal) there is no principled rule for picking which side is
correct.

## A=Y B=N C=N (46 reports)

The committee total is internally consistent, but the segments are not. By
number of internally inconsistent segments:

| bad_segs | count |
|---:|---:|
| 1 | 24 |
| 2 | 7  |
| 3 | 4  |
| 4 | 5  |
| 5 | 1  |
| 6 | 1  |
| 7 | 1  |
| 8 | 1  |
| 27 | 1 |
| 44 | 1 |
| 66 | 1 |

The 24 reports with exactly one bad segment are the most tractable. All 24
already carry a row-level explanatory flag on the bad segment
(`ROW_SUM_ROUNDING`, `ROW_TOTAL_LESS_THAN_COMPONENT`,
`ROW_TOTAL_INCLUDES_UNBROKEN_COSTS`, or `ROW_BREAKDOWN_IN_FC_COLUMN`). The
existing `TABLE_SUM_EXPLAINED_BY_ROW_TOTAL_FLAGS` classifier (pattern 2.5)
does not fire because it requires `ct_components == sum(seg_components)` (the
C=Y condition) — these reports also have C-level disagreement.

For the 11 reports where the bad segment carries `ROW_SUM_ROUNDING` (small
residual like 0.25, -1.00), the table delta is the sum of (a) the bad
segment's rounding residual and (b) the C-level disagreement. A new compound
pattern that explains both could downgrade these. Examples:

- `2000q1mar14-005`: bad seg residual 0.73, C-level tr diff -220.00, table
  delta 220.73.
- `2013q3sep11-001`: bad seg residual -1.00, C-level pd diff 255.00, table
  delta -256.00.
- `2016q2jun20-001`: bad seg residual 0.30, C-level pd diff -468.00, table
  delta 468.30.
- `2018q3jul24-004`: bad seg residual -0.63, C-level pd diff 18.00, table
  delta -18.63.

The C-level disagreement in these examples is a single component with no
obvious source convention (not a typo, not an unbroken-component shape). A
downgrade here would be "row-level rounding residual plus unexplained
single-component disagreement" — informational but not fully explanatory.

**Recovery candidates:** A compound `ROW_ROUNDING_PLUS_COMPONENT_DIFF`
downgrade for the 4–6 reports where the C-level disagreement is in a single
component and the bad-seg residual is small. Modest win, compound pattern.

## A=N B=Y C=N (28 reports)

The committee total is internally inconsistent — `ct.total !=
ct.per_diem + ct.transportation + ct.other` — but the segments are
internally consistent. The ct-internal delta varies widely:

- **Small ct-internal delta (≤ $5): 6 reports** — Examples:
  - `1999q1feb08-002`: ct_total=132704.28, comp_sum=132701.28, delta=3.00
  - `2002q2may14-002`: ct_total=150197.00, comp_sum=150197.99, delta=-0.99
  - `2007q1feb16-003`: ct_total=39164.36, comp_sum=39163.36, delta=1.00
  - `2015q1feb20-011`: ct_total=229043.99, comp_sum=229043.19, delta=0.80
  - `2017q2jun02-001`: ct_total=48584.00, comp_sum=48589.00, delta=-5.00
  - `1998q2may05-003`: ct_total=60168.26, comp_sum=60178.26, delta=-10.00

  A new `CT_INTERNAL_ROUNDING` informational flag would surface these as a
  known source-side rounding convention, but the table delta is still
  unexplained (e.g., `2015q1feb20-011` has seg_total=1834845.77 vs
  ct_total=229043.99 — table delta over $1.6M). Adding the flag would not
  remove these from TSM, just label the ct-internal side.

- **Large ct-internal delta (> $5): 22 reports** — Includes a single
  double-count candidate (`2009q3sep16-010`: ct_total=7770.82, comp_sum=9179.82,
  delta=-1409.00 which equals ct_pd). The sign is negative (omission, not
  double-count), and the existing row-level recovery explicitly skips
  `diff <= 0` cases. The remaining 21 have arbitrary deltas (1470.58,
  588.18, 6266.03, 4743.88, etc.) with no clean pattern.

  One report (`1998q1mar11-001`) has ct_total=11164634 (no decimal point in
  the source — likely a parsing artifact, not a recovery target for this
  classifier).

**Recovery candidates:** `CT_INTERNAL_ROUNDING` as an informational downgrade
for the 6 small-delta cases. Does not reduce TSM count, but parallel to
`ROW_SUM_ROUNDING` and would surface the convention in the data.

## A=N B=N C=N (20 reports)

Both sides are internally inconsistent and the components disagree. This is
the hardest bucket — there is no consistent side to anchor a recovery
against. Distribution of bad segments:

| bad_segs | count |
|---:|---:|
| 1 | 9  |
| 2 | 2  |
| 3 | 5  |
| 8 | 1  |
| 12 | 1 |
| 15 | 1 |
| 66 | 1 |

Even the 9 reports with a single bad segment have ct-internal inconsistencies
on top, so a row-level recovery would not suffice. Example:
`1995q3aug04-004`: ct_total=95517.37 vs ct_comp=195517.37 (ct-internal delta
-100000.00), seg_total=862654.60 vs seg_comp=866654.60 (seg-internal delta
-4000.00, one bad seg). Three independent things wrong.

**Recovery candidates:** None systematic. Per-report manual investigation
only.

## Summary

| Bucket | Count | Best candidate | Expected TSM reduction |
|---|---:|---|---|
| A=Y B=Y C=N | 124 | None (no clean source convention) | 0 |
| A=Y B=N C=N | 46  | Compound row-rounding + component-diff downgrade | 4–6 |
| A=N B=Y C=N | 28  | `CT_INTERNAL_ROUNDING` informational flag | 0 (label only) |
| A=N B=N C=N | 20  | None (per-report investigation) | 0 |
| **Total** | **218** | | **4–6** |

The cleanly recoverable patterns are largely extracted. The remaining 218
are either genuine source-side inconsistencies with no single explanation,
compound issues that resist a single-name downgrade, or cases where the
source convention is ambiguous (one component differs between sides, no
typo, no obvious reason). Further reductions would require either
permissive informational downgrades that label rather than explain, or
per-report manual investigation.