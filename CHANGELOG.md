# Changelog

## [3.0.29] - 2026-07-17

### Fixed - Scraper rewritten for disclosures-clerk.house.gov (104 new reports)

The House Clerk moved the foreign-travel disclosures from
`clerk.house.gov/public_disc/foreign/index.aspx` (ASP.NET WebForms with
a per-session `__VIEWSTATE`) to `disclosures-clerk.house.gov/ForeignTravel`
(MVC with an anti-forgery `__RequestVerificationToken`). The old scraper
was hardcoded to a stale `__VIEWSTATE` URL and silently failed; the
corpus stopped at 2019.

The new `ReportDownloader`:
- GETs `/ForeignTravel/ViewReport` to scrape a fresh
  `__RequestVerificationToken` per run.
- POSTs to `/ForeignTravel/ViewSearchResult` with the token + `Year` +
  `Quarter` for each (year, quarter) pair, parses the result page for
  `foreign-reports/<filename>.txt` links.
- Downloads each report from `/foreign-reports/<filename>.txt` via a
  reused `requests.Session` (so the anti-forgery cookie set on the GET
  is sent on the POST).
- Refreshes the token if a 500 mid-loop signals expiry.
- Skips files whose content lacks `OFFICIAL FOREIGN TRAVEL` /
  `EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL` -- the Clerk site
  occasionally misfiles a non-travel Congressional Record page under
  the foreign-travel index (e.g. `2020q4dec02.txt`, an executive-
  communications page); the guard keeps such files out of the corpus
  without manual intervention.

`config.base_url` is now `https://disclosures-clerk.house.gov`,
`config.end_year` bumped from 2020 to 2027 (exclusive) so the default
range covers 1994 through the current quarter. The hardcoded
`__VIEWSTATE` URL is gone from `cli/download.py`; `get_quarterly_urls`
takes no argument (the pairs come entirely from
`start_year`/`end_year`).

Re-running `oft-download --start-year 2020 --end-year 2027` fetched
104 new reports covering 2020 Q1 through 2026 Q2 (2022 Q1 is a
genuine gap on the Clerk site, not a fetch error). `oft-parse` over
the full 1994-2026 corpus yields 3607 reports (up from 3593), and the
new reports surface only flag patterns the parser already knows
(`SPONSOR_UNCLASSIFIED` appears for the first time on 9 Ethics
Committee "no travel this quarter" filings -- the flag was already
in the code, just never previously triggered). Idempotency: 0
non-idempotent reports across the full corpus. All 739 tests pass.

One Clerk misfiling (`2020q4dec02.txt`, an executive-communications
page with zero foreign-travel content) was deleted from `report_text/`
and the new content guard prevents re-introduction on future
downloads.

## [3.0.28] - 2026-07-17

### Fixed - Infer committee total from segment sums (27 reports)

When a report omits the committee total row but every traveler's segments
carry per-component US-dollar amounts, the total is inferred from the sum
of segment costs (per_diem, transportation, other, total). The inferred
`Costs` is built with `computed=True` cells so downstream consumers can
distinguish it from a source-declared total, and the report is flagged
`COMMITTEE_TOTAL_INFERRED_FROM_SEGMENTS` (informational, mirrors
`COMMITTEE_TOTAL_COMPUTED`).

27 reports recovered. 1 remains flagged `MISSING_COMMITTEE_TOTAL`
(`2010q3sep15-016`) because no segment in that report has any US-dollar
cost data to sum.

Added `_infer_committee_total_from_segments` helper in
`official_foreign_travel/parsing/validate.py`, integrated into the
`elif all_segments:` branch where `MISSING_COMMITTEE_TOTAL` was
previously appended unconditionally. The idempotency re-tag path
detects the all-4-cells-computed shape and re-tags the flag on
revalidation; the flag is also added to the report-level flag-clearing
list at the top of `validate_report`. New
`TestCommitteeTotalInferredFromSegments` test class covers single-seg
inference, multi-seg component sums, multi-traveler aggregation, the
no-cost-data fallback, total-equals-seg-total-sum, and idempotency.

## [3.0.27] - 2026-07-17

### Fixed - Day-clamp recovery for invalid dates (3 reports)

A source `M/D` date where the day overshoots month-end by exactly one
(e.g. `9/31`, `11/31`, `2/30` in a leap year) is a typo for the last
day of the month. The day is clamped to `days_in_month` and the segment
is flagged `DATE_DAY_CLAMPED_TO_MONTH_END` instead of
`*_DATE_INVALID`. A second shape also qualifies: `2/30` in a non-leap
year, where the source treated Feb as a 30-day month, clamps to `2/28`.

Feb 29 in a non-leap year is excluded -- the existing leap-year
recovery (`DEPARTURE_DATE_INFERRED_LEAP_YEAR` / `YEAR_ROLLOVER_APPLIED`)
handles that shape, and clamping it to Feb 28 would mask the
year-rollover signal.

3 reports in corpus recovered (4 segments -- `2013q2may06-003` has 2
travelers with the same segment):
- `2006q1mar07-018`: dep `9/31` → `9/30`
- `2019q1feb07-005`: dep `11/31` → `11/30`
- `2013q2may06-003`: dep `2/30` → `2/28` (2013 non-leap)

The day-clamp also fires on the arrival side (same shape). The
departure path tries `arrival.year` first, then `arrival.year + 1` for
the year-rollover case (`dep_month < arr_month`), so a clamped Feb 30
in a cross-year trip rolls forward instead of producing a departure
before the arrival. `DEPARTURE_DATE_INVALID` + `ARRIVAL_DATE_INVALID`
counts: 13 → 10.

The existing `test_invalid_departure_not_feb29_stays_invalid` test
explicitly documented `9/31` and `11/31` as unrecoverable; that
decision is reversed for the off-by-one shape, and the test now uses
`13/13` (both numbers > 12, no clean interpretation) as the
unrecoverable example.

Added `_clamped_day` helper and `_best_fit_year_with_day_clamp` wrapper
in `official_foreign_travel/parsing/dates.py`. New
`TestDateDayClampedToMonthEnd` test class covers strict off-by-one,
Feb 30 in leap and non-leap years, arrival-side recovery,
non-recoverable cases, and idempotency.

## [3.0.26] - 2026-07-17

### Fixed - Seg-has-unbroken-component downgrade (1 report)

Mirror of the existing `TABLE_SUM_CT_HAS_UNBROKEN_COMPONENT` pattern (5c).
When one seg component sum is populated but the corresponding ct component
is 0, the rest of the seg components matches the rest of the ct components,
and both sides are internally consistent (ct_total == ct_components and
seg_total_sum == seg_components), the seg component fully accounts for the
table delta: a seg or per-traveler rollup broke out a component — often
per-segment other costs — that wasn't broken out at the committee-total
level. Downgraded from `TABLE_SUM_MISMATCH` to the new
`TABLE_SUM_SEG_HAS_UNBROKEN_COMPONENT` flag.

The A=Y/B=Y requirement distinguishes a real source convention (where the
unbroken component equals the table delta) from a coincidental rest-match
on a report whose ct_total is otherwise unrelated to its components — those
stay `TABLE_SUM_MISMATCH`. 1 report in corpus (2005q1mar16-010: segs have
other costs summing to 645.74 across multiple segments, ct has other=0, rest
matches, ct and segs both internally consistent). TSM 219 → 218.

## [3.0.25] - 2026-07-17

### Fixed - Committee-component comma-decimal typo recovery (16 reports)

The v3.0.23 recovery handled comma-as-decimal typos in the committee TOTAL
cell. The same typo appears in committee COMPONENT cells (per_diem /
transportation / other):
- 2003q4nov10-005: ct_pd raw `37,347,86` → 3734786, ct_tr raw `52,748,58`
  → 5274858, ct_total=90096.44 (correct). Both components typo'd.
- 2015q3sep08-005: ct_ot raw `35,753,19` → 3575319, ct_total=234141.63
  (correct). Single component typo'd.
- 13 additional reports where the table sum balanced (ct_total matched
  seg_total) but the ct components were silently inflated by the typo.
  These were never TSM; the recovery surfaces and corrects them.
- 1996q3sep11-005: ct_total raw `7,202,000` (typo, v3.0.23 fix) AND ct_pd
  raw `7,202,000` (typo). Both recoveries fire in the same pass — the
  ct-total branch fixes ct_total first, then the ct-component branch
  fixes ct_pd against the corrected ct_total.

Added `_detect_ct_component_typos`: mirrors `_detect_component_comma_decimal_typos`
at the committee-total level. Returns the list of (cell, divisor) pairs
to recover when fixing the typo'd ct components makes their sum exactly
equal the declared ct total. Recovery overwrites each cell with
`amount/divisor`, preserves the source-declared (inflated) value in
`source_amount`, and sets `computed=True` and `comma_decimal_typo=True`
on the cell. New flag `COMMITTEE_COMPONENT_COMMA_DECIMAL_TYPO`. Runs as
a separate `if/elif` block AFTER the ct-total elif chain (not as part of
it) so it can fire in the same pass as the ct-total typo branch when
both apply. Idempotent via the `comma_decimal_typo` markers on the ct
component cells (re-tag flag from markers when ct_total_cell.computed
short-circuits the ct-total elif chain on revalidation).

### Corpus impact
- COMMITTEE_COMPONENT_COMMA_DECIMAL_TYPO: 0 → 16 (new flag, 16 reports
  with ct component typos: 2 in TSM buckets, 13 silent data-quality
  issues, 1 with both ct-total and ct-component typos)
- TABLE_SUM_MISMATCH: 219 → 219 (unchanged — the recovery fixes ct
  components but doesn't change ct_total, so the table delta is
  unchanged. The 2 TSM reports with ct-component typos remain TSM because
  their seg totals don't match ct_total even after the ct components
  are corrected.)

## [3.0.24] - 2026-07-17

### Fixed - Multi-component row-total comma-decimal typo recovery (21 segments / 11 reports)

The v3.0.23 `_is_100x_typo` recovery only fired for single-component
segments where `declared_total == component × 100`. The same
comma-as-decimal typo appears on multi-component segment totals where
the total cell's raw has the comma-decimal shape (e.g. `2,345,36` parsed
as 234536, intended `2,345.36` = 1622.76 + 722.60; `7,202,000` parsed as
7202000, intended `7,202.000` = 7202). 21 segments across 11 reports;
1 TABLE_SUM_MISMATCH resolved (2010q1mar12-003: delta 171547.20 → 0).

Added `_row_total_typo_divisor`: returns 100 (`,NN`) or 1000 (`,NNN`)
when the segment total cell's raw has the comma-as-decimal shape and
`declared_total / divisor` exactly equals the component sum. The
raw-shape gate (in addition to the exact-arithmetic gate) distinguishes
the typo from a genuine large total in the multi-component case, where
arithmetic alone is a weaker signal than for single-component segments.
Recovery overwrites the total with the component sum, preserves the
source-declared (inflated) total in `source_amount`, sets
`computed=True` and `comma_decimal_typo=True`, and emits
`ROW_TOTAL_COMPUTED` + `ROW_TOTAL_COMMA_DECIMAL_TYPO`. Idempotent via
the `comma_decimal_typo` marker on the total cell. Runs AFTER the
existing `_is_100x_typo` branch so single-component cases continue to
use the existing path (which doesn't require the raw shape); only
fires for the multi-component case that `_is_100x_typo` explicitly
excludes.

### Corpus impact
- TABLE_SUM_MISMATCH: 220 → 219 (−1)
- ROW_TOTAL_COMMA_DECIMAL_TYPO: 12 → 33 (+21)
- ROW_TOTAL_COMPUTED: 7521 → 7542 (+21)
- ROW_TOTAL_INCLUDES_UNBROKEN_COSTS: 321 → 300 (−21, shifted to the
  more specific typo flag)
- TABLE_SUM_EXPLAINED_BY_ROW_TOTAL_FLAGS: 83 → 79 (−4, the row-level
  recovery changes seg totals so the table delta is no longer
  explained by row-flag residuals; those reports move to other
  table-level classifications)

## [3.0.23] - 2026-07-17

### Fixed - Committee-total comma-decimal typo recovery (3 reports)

The v3.0.22 recovery handled comma-as-decimal typos in segment component
cells. The same typo appears in the committee total cell:
- 2012q3sep13-000: ct total raw `3,312,32` → 331232, intended `3,312.32`
  (ct has no breakdown, segs sum to 3312.32)
- 1996q3sep11-005: ct total raw `7,202,000` → 7202000, intended `7,202.000`
  = 7202 (ct.pd has the same typo, segs sum to 7202.00)
- 2001q2jun25-000: ct total raw `21,020,26` → 2102026, intended `21,020.26`
  (segs sum to 21020.27, within tolerance)

Added `_ct_total_typo_divisor`: returns 100 (`,NN`) or 1000 (`,NNN`) when
the ct total raw has the comma-as-decimal shape. Recovery divides the
declared total by the divisor and overwrites the cell when
`declared_total / divisor` matches `seg_total_sum` within tolerance;
preserves the source-declared (inflated) value in `source_amount`; sets
`computed=True` and `comma_decimal_typo=True` on the cell. Mirrors
`ROW_TOTAL_COMMA_DECIMAL_TYPO` at the table level with the new report-level
flag `COMMITTEE_TOTAL_COMMA_DECIMAL_TYPO`. Idempotent via the marker on
the ct total cell. Runs AFTER the existing `COMMITTEE_TOTAL_COMPUTED`
branch — when ct_components match seg_components, that recovery wins (it
recovers from the components, which are the source of truth when intact).

### Added - Three informational downgrades for unverifiable table shapes (67 reports)

Three source conventions where the table-level delta can't be arithmetically
verified but the shape is recognizable. Downgraded from TABLE_SUM_MISMATCH
to specific informational flags. Run AFTER the arithmetic classifiers
(supplement, transport-excluded, row-flag residuals, component delta,
rounding) so a specific arithmetic explanation wins when one applies; the
structural downgrades only catch shapes the arithmetic classifiers can't
verify.

1. `TABLE_SUM_CT_NO_BREAKDOWN` (48 reports): the committee total is the
   only cell populated; the three per-component cells are all empty.
   Source convention: the ct total was entered as a single number with no
   per-component breakdown, so the table delta cannot be arithmetically
   verified. Examples: 1995q1feb09-008 (ct_total=155323.79, seg_total=
   164331.79), 2005q3jul26-000 (ct_total=1185, seg_total=2157).

2. `TABLE_SUM_NO_SEG_BREAKDOWN` (11 reports): the committee total has full
   per-component breakdown but no segment has any cost cells populated (or
   there are no travelers at all). Source convention: the ct breakdown
   exists but no per-traveler breakdown was provided. Examples:
   1994q2jun21-003 (ct has only `other=295.69`, no travelers),
   1995q1feb09-000 (ct has full breakdown, 3 travelers but no seg costs).

3. `TABLE_SUM_CT_HAS_UNBROKEN_COMPONENT` (8 reports): one ct component cell
   is populated but the corresponding segment component sum is 0, and the
   rest of the ct components match the rest of the segment components.
   Source convention: the ct broke out a component (often transportation)
   that wasn't broken out per-segment. Examples: 2000q1feb02-007 (ct has
   pd=2400 + tr=2672.78, segs only have pd=2400).

**Corpus-wide totals after this change:** TABLE_SUM_MISMATCH 290 → 220
(−70). COMMITTEE_TOTAL_COMPUTED 55 → 58 (+3 for the typo recoveries).
COMMITTEE_TOTAL_COMMA_DECIMAL_TYPO 0 → 3. The arithmetic classifiers
(TABLE_SUM_EXPLAINED_BY_SUPPLEMENT, _ROW_TOTAL_FLAGS, _COMPONENT_DELTA,
_ROUNDING, _TRANSPORT_EXCLUDED) are unchanged — the new patterns only
catch shapes they don't cover.

## [3.0.22] - 2026-07-17

### Fixed - Component-cell comma-decimal typos recovered (35 segments, 2 reports)

The v3.0.21 `_is_100x_typo` recovery handled the case where the **total
cell** used a comma where a decimal point should be (e.g. `1,204,00`
parsed as 120400, intended `1,204.00`). The mirror image exists in the
corpus: a **component cell** (per_diem / transportation / other) carries
the typo while the declared total is correct. Examples:

- 1997q3sep23-001 Dagne: per_diem raw `749,00` → 74900, total 749.00
- 1999q2may14-003 Knollenberg: per_diem raw `480,000` → 480000, total 480.00
  (the 3-digit last group requires /1000, not /100)
- 2003q1jan31-012 Turner: per_diem raw `1,431,25` → 143125 (typo),
  transportation 6551.69 and other 127.91 clean; total 8110.85
- 2015q2may12-006 Morocco: 9 travelers all with per_diem `749,00` → 74900
  AND other `1,262,07` → 126207, total 2011.07 — both typos must be fixed
  together for the segment to become internally consistent

Added `_detect_component_comma_decimal_typos`: returns a list of
(component cell, divisor) pairs to recover when (a) the declared total is
present, (b) at least one component cell's `raw` ends with a `,NN` (2-digit,
divisor=100) or `,NNN` (3-digit, divisor=1000) group with no decimal point,
and (c) dividing each typo'd component by its divisor makes the component
sum exactly equal the declared total. The 3-digit `,NNN` shape is
ambiguous with a normal thousands separator (e.g. `1,123,000` could be
$1,123,000 or `1,123.000` = 1123); the exact-match arithmetic gate
distinguishes the typo from a genuine large component.

Recovery overwrites each typo'd component cell with `amount/divisor`,
preserves the source-declared (inflated) value in `source_amount`, and
sets `computed=True` and `comma_decimal_typo=True` on the cell. The
segment's `ROW_TOTAL_LESS_THAN_COMPONENT` flag is replaced with the more
specific `ROW_COMPONENT_COMMA_DECIMAL_TYPO` flag. Idempotent via the
marker on the component cells: after recovery, `comp_sum == declared_total`
so no mismatch check fires; the marker is the only signal to re-derive
the flag on revalidation.

Two candidates with residual deltas after the typo fix (0.52 and 0.09 —
the source total itself is slightly wrong) correctly fall through to
`ROW_TOTAL_LESS_THAN_COMPONENT`; the recovery only fires when the fix
fully reconciles the segment.

**Corpus-wide totals after this change:** ROW_TOTAL_LESS_THAN_COMPONENT
189 → 154 (−35), all replaced by ROW_COMPONENT_COMMA_DECIMAL_TYPO (0 →
35). TABLE_SUM_MISMATCH 292 → 290 (−2 — 2010q4nov18-013 now explained by
row-total flags; 2016q4nov14-021 now recovered by COMMITTEE_TOTAL_COMPUTED
once its segments became internally consistent).

## [3.0.21] - 2026-07-17

### Fixed - Negative segment totals now computed; looser tolerance on ct-component match (3 recoveries)

Two small refinements to the v3.0.19 / v3.0.20 recoveries:

1. **Negative segment totals**: the segment-level `ROW_TOTAL_COMPUTED`
   recovery previously gated on `computed > 0`, skipping segments whose
   component sum was negative. But a negative total IS meaningful -- e.g.
   a US_RETURN_LEG with a negative per_diem (traveler returned unused
   per_diem, 2002q3sep10-024 Wolf). Changed to `computed != 0` so
   negative totals are computed and the segment contributes correctly
   to the table-level sum. Zero-sum components (all empty) still skip,
   preserving the "source didn't declare a total" convention.

2. **Looser tolerance on ct-component match**: the `COMMITTEE_TOTAL_COMPUTED`
   recovery required ct components to match segment components within
   the strict $0.02 tolerance. Two reports (2009q1feb11-003,
   2012q2may29-002) had ct components within ~$1 of segment components
   (rounding noise from per-segment FX conversion) but were rejected.
   Loosened to the same rounding threshold ($5 or 1%) used by the
   TABLE_SUM_ROUNDING classifier.

**Corpus-wide totals after this change:** TABLE_SUM_MISMATCH 295 → 292
(−3). ROW_TOTAL_COMPUTED 7520 → 7521 (the Wolf US-return segment).
COMMITTEE_TOTAL_COMPUTED 52 → 54.

## [3.0.20] - 2026-07-17

### Fixed - TABLE_SUM_MISMATCH downgraded when explained by row-total flags (79 recoveries)

Some segments carry an informational row-level flag where the validator
intentionally keeps the source-declared segment total even though it
doesn't equal the segment's own component sum:
`ROW_TOTAL_INCLUDES_UNBROKEN_COSTS` (source included costs not broken
out into per_diem/transportation/other), `ROW_TOTAL_LESS_THAN_COMPONENT`
(source declared less than the components), `ROW_NO_COMPONENT_BREAKDOWN`
(no component breakdown at all), `ROW_TOTAL_TRANSPORT_EXCLUDED` /
`ROW_TOTAL_OTHER_EXCLUDED` / `ROW_TOTAL_PER_DIEM_EXCLUDED` (source
excluded a component), `ROW_TOTAL_NEGATIVE_PER_DIEM`, and
`ROW_BREAKDOWN_IN_FC_COLUMN`.

When the table-level delta exactly equals the sum of these per-segment
`total - component_sum` residuals, the mismatch is fully explained by
those row-level source conventions -- not a separate table-level error.
The classifier now downgrades these to
`TABLE_SUM_EXPLAINED_BY_ROW_TOTAL_FLAGS` (informational), mirroring the
existing `TABLE_SUM_EXPLAINED_BY_SUPPLEMENT` pattern.

Gates:
- At least one segment must carry an explanatory flag (otherwise the
  pattern would shadow `TABLE_SUM_ROUNDING` for small deltas with no
  flagged segments).
- The residual sum must exceed tolerance (otherwise it's noise).
- `|delta - row_flag_residual|` must be within `max(tolerance, $5)`.

**Corpus-wide totals after this change:** TABLE_SUM_MISMATCH 363 → 295
(−68). TABLE_SUM_EXPLAINED_BY_ROW_TOTAL_FLAGS 0 → 79. 11 of the 79
also carry `COMMITTEE_TOTAL_COMPUTED` (the ct_total was a separate typo,
corrected first, then the remaining delta is explained by row flags).

## [3.0.19] - 2026-07-17

### Fixed - Committee total recovered from components when total cell is a typo (50 recoveries)

When a committee total row's TOTAL cell doesn't match its own component
cells (per_diem + transportation + other) but the components DO sum to
the segment components, the TOTAL cell is wrong -- a layout digit-shift
(e.g. `71,882.24` parsed instead of `171,882.24`, the leading `1`
dropped by column extraction), a comma-decimal typo (`40,135,73` parsed
as 4013573 instead of 40135.73), or a small source typo. The components
themselves are intact.

`validate_report` now overwrites `committee_total.total.us_dollar.amount`
with the computed component sum, preserves the source-declared value in
`source_amount`, sets `computed=True`, and flags `COMMITTEE_TOTAL_COMPUTED`
(informational). Mirrors the segment-level `ROW_TOTAL_COMPUTED` recovery.

Gates:
- Diff between declared total and component sum must exceed the
  TABLE_SUM_ROUNDING threshold ($5 or 1% of component sum) -- smaller
  diffs are left to the classifier as TABLE_SUM_ROUNDING.
- Diff must NOT exactly match a segment component amount -- those are
  left to the classifier as TABLE_SUM_COMPONENT_DELTA (source
  intentionally excluded or double-counted that component).
- Component sum must be > 0.

**Corpus-wide totals after this change:** COMMITTEE_TOTAL_COMPUTED 0 → 50
reports. TABLE_SUM_MISMATCH 371 → 363 (−8). 32 of the 50 recoveries are
fully clean (the source total was the only issue); 18 retain
TABLE_SUM_MISMATCH because the segment row totals independently don't
sum to the corrected committee total -- both flags are accurate.

## [3.0.18] - 2026-07-17

### Fixed - Source line-break wraps concat'd as decimals (676 recoveries)

When a source report breaks an amount at the decimal point and continues
the decimal digits on the next line in the same cost column, the prior
parser merged the two cells by *summing* them, producing wrong amounts
across the corpus. Two wrap shapes occur:

- 2-digit wrap: `12,785.` on one source line, `48` on the next (same
  column) → the prior parser stored `12,785 + 48 = 12,833.00`; the
  correct value is `12,785.48` (Gilman, 1995q1feb09).
- 1-digit wrap: `234.2` on one line, `2` on the next → the prior parser
  stored `234.2 + 2 = 236.20`; the correct value is `234.22`.

`merge_cost_cell` now detects the wrap shape (prior cell's decimal part
has 0 or 1 digits, wrapped cell is a bare 1- or 2-digit integer matching
the missing decimal width) and concat's instead of summing:
`a.amount + b.amount / 100`. Footnotes and `military_air` are preserved
from both cells. Normal supplement merges (where `b` carries a full
amount with its own decimal point, or where `a` has no decimal point, or
where `a` already has 2 decimal digits) are unaffected.

A parallel fix in `parse_cost_cell` strips space-separated symbolic
asterisk markers iteratively, so cells like `* * * 234.2` record three
separate `*` footnote references and the amount `234.2` is exposed for
wrap-concat with the following cell. This recovers the Daniel Freeman
Thailand segment (`2005q3jul26-013`) whose `other` cost was previously
flagged `UNPARSEABLE_COST_CELL` -- it now parses as `$234.22`.

**Corpus-wide totals after this change:** 676 cost cells recovered
(366 nonzero 1-digit wraps, 210 zero 1-digit wraps, 100 2-digit wraps).
`UNPARSEABLE_COST_CELL` 9 → 8 segments (−1). 6 wrap-shaped cells remain
unrecovered due to independent layout issues (e.g. prior cell with two
decimal points, or wrapped cell whose column extraction produced a
non-numeric residue).

## [3.0.17] - 2026-07-16

### Fixed - `Committee total` with two footnote markers (1 recovery)

The total-row detector (`TOTAL_ROW_RE`) previously accepted at most one
`\d+\` footnote marker between the "total" token and the trailing
dot-fill (e.g. `Committee Total\3\........`). One corpus report has two
markers: `Committee total \1\ \2\..................` in `2008q4dec10-024`
(Science and Technology). The detector now accepts any number of
`\d+\` markers (`(?:\\\d+\\)?` → `(?:\s*\\\d+\\)*`), so the row is
recognized as the committee total and the report is no longer flagged
`MISSING_COMMITTEE_TOTAL`. Total `$16,354.60` recovered.

**Corpus-wide totals after this change:** MISSING_COMMITTEE_TOTAL 29 → 28
(−1). The 28 remaining flags are genuine source omissions (classified
Intelligence reports, individual trips with no committee-level
aggregation by convention, and committee/delegation reports that simply
didn't print a total row).

## [3.0.16] - 2026-07-16

### Fixed - Feb 29 year-rollover to a leap year (1 recovery)

A departure date of `2/29` (Feb 29) in a non-leap arrival year, where
`dep_month < arr_month` (year-rollover) and `arrival.year + 1` is a leap
year, now rolls to Feb 29 of the next year and is flagged
`YEAR_ROLLOVER_APPLIED`. E.g. `2019q2may20-001` Engel Colombia:
arr=3/28, dep=2/29, 2019 not leap, 2020 leap → 2020-02-29.

The year-rollover check previously required `departure is not None` (the
departure to be a valid date in the arrival year) before trying the roll.
For Feb 29 in a non-leap year, `_try_date(arrival.year, 2, 29)` returns
`None`, so the roll was skipped. The guard is removed: Feb 29 is the only
date that is invalid in one year but valid in the next, so removing the
guard only affects leap-day year-rollover cases. Other invalid dates
(Sep 31, Nov 31, Feb 30, month 13/18) still return `None` from both
`_try_date` calls and stay `DEPARTURE_DATE_INVALID`.

A 2/29 departure with arrival not in February, where the next year is
also not a leap year (e.g. `2009q4nov19-009` Schmidt Ireland: arr=6/28,
dep=2/29, 2010 not leap), stays `DEPARTURE_DATE_INVALID` -- the roll
returns `None` for both years.

**Corpus-wide totals after this change:** DEPARTURE_DATE_INVALID 10 → 9
(−1); YEAR_ROLLOVER_APPLIED 93 → 94 (+1).

## [3.0.15] - 2026-07-16

### Added - ROW_BREAKDOWN_IN_FC_COLUMN flag distinguishes FC-column breakdowns (35 downgrades)

35 segments previously flagged `ROW_NO_COMPONENT_BREAKDOWN` are now
downgraded to `ROW_BREAKDOWN_IN_FC_COLUMN` (informational). These are
segments where the US-dollar component cells are empty but the
foreign-currency component cells sum to the declared US-dollar total --
a source convention where the per-category amounts were entered in the
foreign-currency column even though they are US-dollar figures (e.g.
`1997q3sep23-001` Christopher Kojm Kuwait: `tr.fc=742.00`, `tot.us=742.00`;
`2000q1mar14-006` Hon. Tom Campbell New Zealand: `pd.fc=713.19`,
`tot.us=713.19` -- neither KWD nor NZD is 1:1 with USD, so the FC column
is carrying US-dollar amounts, not foreign currency).

The new flag distinguishes "source provided no breakdown at all" (65
remaining `ROW_NO_COMPONENT_BREAKDOWN` cases -- total-only rows with no
FC breakdown either) from "source provided a breakdown but put it in the
FC column" (35 `ROW_BREAKDOWN_IN_FC_COLUMN` cases). The declared total is
left in place; the FC amounts are not promoted to the US-dollar column
(the flag is a label, not a data move).

Added `_fc_group_total` helper (mirrors `_group_total` for the FC side).
The FC-sum check uses the same `tolerance` as the row-sum check, so
rounding-level differences still downgrade. 8 segments where FC components
don't sum to the US total (genuine foreign-currency amounts with a
different US-dollar equivalent) stay `ROW_NO_COMPONENT_BREAKDOWN`.

**Corpus-wide totals after this change:** ROW_NO_COMPONENT_BREAKDOWN
100 → 65 (−35); ROW_BREAKDOWN_IN_FC_COLUMN 0 → 35.

## [3.0.14] - 2026-07-16

### Fixed - Recover UNPARSEABLE_COST_CELL dot-fill variants and footnote residue (24 segments, 103 cells)

24 segments previously flagged `UNPARSEABLE_COST_CELL` are now recovered.
Three layout-residue patterns are recognized:

1. **Lenient dot-fill** (~100 cells): cells that are dots + whitespace +
   trailing footnote-marker residue (backslash, asterisk) + supplement-merge
   `+` chains are now recognized as empty. The strict `^\.{2,}$` dot-fill
   regex only matched pure dots; cells like `...........  ` (dots + trailing
   whitespace), `...........  \\` (dots + trailing backslash from a footnote
   marker that lost its digit), `...........  *` (dots + symbolic `*`
   marker, recorded as a footnote), `.........  .` (dots + space + dot),
   and `........... + ...........` (merged dot-fill chains from supplement
   lines) all fell through to `UNPARSEABLE_COST_CELL`. The new
   `DOTFILL_LENIENT_RE` matches cells that contain only dots, whitespace,
   `+`, `\`, and `*` with at least 2 dots, so any cell with no digits or
   letters is recognized as empty.

2. **Footnote marker + `1A` residue + amount** (2 cells, Thomas Hill
   `2015q3sep08-009`): `\4\1A184.00` and `\4\1A116.54` -- the `\4\`
   footnote marker ("Indicates delegation costs") is followed by `1A`, a
   layout-extraction artifact, then the amount. After stripping the
   footnote marker, the `1A` is stripped (only when a footnote was already
   collected), recovering `184.00` and `116.54` (confirmed by row-total
   arithmetic: 797.52 + 6100.60 + 184.00 = 7082.12; 663.00 + 116.54 =
   779.54). The `1A` strip is gated on `footnotes` being non-empty so a
   legitimate cell starting with `1A` (none observed) is untouched.

3. **Incomplete footnote marker `3\` + amount** (1 cell, Daniel Scandling
   `2001q1mar15-003`): `3\ -700.00` -- a footnote marker `\3\` that lost its
   leading backslash. The new `LEADING_DIGIT_BACKSLASH_FOOTNOTE_RE` matches
   `^(\d)\\\s+(?=[\d-])` (digit, backslash, whitespace, then digit or minus),
   recording `3` as the footnote and recovering `-700.00`. The segment now
   correctly gets `ROW_TOTAL_NEGATIVE_PER_DIEM` (the negative-per_diem
   refund recovery from v3.0.11).

Also fixed: the trailing-dots strip (`re.sub(r"\s*\.+$", "", ...)`) now
runs *before* the trailing-asterisk check, so a cell like `732.00*  ..`
(Dan Scandling `2008q3jul29-005`) is reduced to `732.00*` first, then
the asterisk is extracted as a footnote, recovering `732.00`.

The 9 remaining `UNPARSEABLE_COST_CELL` segments are genuine
column-misalignment artifacts where a country name or descriptive label
leaked into a cost cell (`Haiti`, `Air`, `(eurostar)`, `Transport`,
`Taxi/Bags`, `Conf/Rental`, `Commercial`, `Misc. exp.`, `Kyrgyzstan +
...........`) -- there is no numeric amount to recover, so the flag stays.

**Corpus-wide totals after this change:** UNPARSEABLE_COST_CELL 33
segments → 9 (−24); UNPARSEABLE_COST_CELL cells 112 → 44 (−68 recovered,
44 remaining are 9 genuine labels + 35 dot-fill cells in the same
segments).

## [3.0.13] - 2026-07-16

### Fixed - Recover Feb 29 departure dates in non-leap years (18 recoveries)

18 segments had a departure date of `2/29` (Feb 29) in a non-leap
year, with the arrival also in February. The source wrote Feb 29 but
the year doesn't have one -- a source data error. The departure is now
inferred as March 1 of the same year (the day after Feb 28, which is
what Feb 29 would map to in a non-leap year) and flagged
`DEPARTURE_DATE_INFERRED_LEAP_YEAR` (informational). E.g.
`2005q2may16-027` Trinidad delegation: arr=2/26, dep=2/29, 2005 is
not a leap year → departure inferred as 2005-03-01. All 18 cases are
from this one report.

The recovery only fires when the arrival is also in February (same
month as the departure) -- a 2/29 departure with arrival in a
different month (e.g. `2019q2may20-001` Colombia: arr=3/28, dep=2/29)
is a different error shape and stays `DEPARTURE_DATE_INVALID`. Other
invalid departure dates (Sep 31, Nov 31, Feb 30, month 13/18) also
stay `DEPARTURE_DATE_INVALID`.

**Corpus-wide totals after this change:** DEPARTURE_DATE_INVALID 28
→ 10 (−18); DEPARTURE_DATE_INFERRED_LEAP_YEAR 0 → 18.

## [3.0.12] - 2026-07-16

### Fixed - Sibling date inference resolves from sibling's raw text (50 recoveries)

The sibling date inference in `recover_empty_dates` was using the
sibling's `arrival_date`/`departure_date` (which is `None` when the
sibling has its own empty date cell, since `resolve_segment_dates`
returns `None` for both dates when either is empty) instead of
resolving the sibling's date from its raw text. This meant a chain of
consecutive segments with empty departures but valid arrivals (e.g.
`1998q1mar11-004` Kevin Long: Jordan arr=11/18, Kuwait arr=11/19,
Bahrain arr=11/20, all departures empty) could not recover any
departure -- each segment's next sibling had `arrival_date=None`
because the sibling itself had an empty departure.

The fix resolves the sibling's date from its raw text via
`_resolve_single_date`, and only skips inference when the sibling's
*relevant* date raw is empty (not when either date raw is empty).
For departure inference, only the next sibling's arrival matters; for
arrival inference, only the previous sibling's departure matters.

**Corpus-wide totals after this change:** DEPARTURE_CELL_EMPTY 52 → 4
(−48); ARRIVAL_CELL_EMPTY 8 → 6 (−2); DATE_INFERRED_FROM_SIBLING 32 →
82 (+50). The 10 remaining empty-cell flags are adjacent pairs where
the sibling's relevant date is also empty (the Walseth pattern).

## [3.0.11] - 2026-07-16

### Fixed - Downgrade 3 negative-per_diem refund segments to ROW_TOTAL_NEGATIVE_PER_DIEM

The last 3 ROW_SUM_MISMATCH segments are all in `2017q4dec06-000`
(Janice Robinson): per_diem is negative (the source wrote the amount
with a trailing minus, e.g. `1,060.00-` which the parser reads as
-1060.00), no other components are populated, and the declared total
is the absolute value of per_diem. This is a source convention: the
total is the absolute value of the negatively-written per_diem.

Flagged `ROW_TOTAL_NEGATIVE_PER_DIEM` (informational); the source-
declared total is kept as-is. With this, no segments remain as
genuine `ROW_SUM_MISMATCH` -- every row-level cost mismatch in the
corpus is now either recovered (total overwritten) or downgraded to
a specific informational flag.

**Corpus-wide totals after this change:** ROW_SUM_MISMATCH 3 → 0;
ROW_TOTAL_NEGATIVE_PER_DIEM 0 → 3. No table-level flag changes.

## [3.0.10] - 2026-07-16

### Fixed - Downgrade 295 multi-component ROW_SUM_MISMATCH segments by delta sign

After the subset-exclusion downgrades, 298 ROW_SUM_MISMATCH segments
remained. 295 of those are multi-component segments (2+ populated
components) where the declared total doesn't match any subset sum of
the components and the delta exceeds the rounding threshold. These
are the same source conventions as the single-component downgrades,
extended to the multi-component case:

- **181 positive-delta segments → `ROW_TOTAL_INCLUDES_UNBROKEN_COSTS`.**
  The source declared a total greater than the component sum (e.g.
  `1997q2apr23-024` Stuart Symington: pd=$338, tr=$1244, tot=$2582;
  $1000 delta is unbroken-out airfare). The source-declared total is
  kept as-is.
- **114 negative-delta segments → `ROW_TOTAL_LESS_THAN_COMPONENT`.**
  The source declared a total less than the component sum (e.g.
  `1994q2may17-007` Richard Weaver: pd=$759.75, tr=$33036.35,
  ot=$77.09, tot=$3873.19; large deduction). The source-declared
  total is kept as-is.

The delta-sign classifier (`_classify_unmatched_by_delta_sign`,
generalized from the single-component helper) runs after the
subset-exclusion check (`_classify_component_excluded_subset`), so
segments that match a specific subset-exclusion shape keep their
more specific flag. The chain order is now: supplement-merge →
trip-total → per_diem × days → double-count → rounding → 100× typo →
subset-exclusion downgrades → delta-sign downgrades → ROW_SUM_MISMATCH.

Only 3 segments remain as genuine `ROW_SUM_MISMATCH` (negative-
per_diem refund shapes that no current recovery covers).

**Corpus-wide totals after this change:** ROW_SUM_MISMATCH 298 → 3
(−295); ROW_TOTAL_INCLUDES_UNBROKEN_COSTS 142 → 323 (+181);
ROW_TOTAL_LESS_THAN_COMPONENT 75 → 189 (+114). No table-level flag
changes (the source total is preserved, so the table-level sum is
unchanged).

## [3.0.9] - 2026-07-16

### Fixed - Downgrade 57 multi-component ROW_SUM_MISMATCH segments where declared = subset sum

After the single-component downgrades, 57 ROW_SUM_MISMATCH segments
remained where two or more cost components were populated and the
declared total equalled the sum of a subset of them -- the source
excluded certain components from the total (e.g. DoD-provided transport
not counted, per_diem reimbursed separately / returned). Mirrors the
table-level `TABLE_SUM_TRANSPORT_EXCLUDED` convention.

Three new informational flags, one per excluded component:
- `ROW_TOTAL_TRANSPORT_EXCLUDED`: declared = per_diem and/or other
  (transportation excluded). 21 cases.
- `ROW_TOTAL_OTHER_EXCLUDED`: declared = per_diem and/or transportation
  (other excluded). 25 cases.
- `ROW_TOTAL_PER_DIEM_EXCLUDED`: declared = transportation and/or other
  (per_diem excluded). 11 cases.

Single-component exclusions are tried first (more common, less
ambiguous). For genuinely ambiguous cases -- two populated components
with equal amounts where the total matches either single-component
subset -- the priority order is transport-excluded, then
other-excluded, then per_diem-excluded, matching the table-level
convention precedent. A segment whose declared total equals exactly
one component while two others are populated gets both of those
components' excluded flags. The source-declared total is kept as-is in
all cases; this is informational, not a recovery.

Runs after the more specific recoveries (supplement-merge → trip-total
→ per_diem × days → double-count → rounding → 100× typo →
single-component downgrades) so each stays specific.

**Corpus-wide totals after this change:** ROW_SUM_MISMATCH 355 → 298
(−57); ROW_TOTAL_TRANSPORT_EXCLUDED 0 → 21; ROW_TOTAL_OTHER_EXCLUDED 0
→ 25; ROW_TOTAL_PER_DIEM_EXCLUDED 0 → 11. No table-level flag changes
(the source total is preserved, so the table-level sum is unchanged).

## [3.0.8] - 2026-07-16

### Fixed - Recover/downgrade 229 single-component ROW_SUM_MISMATCH segments

After trip-total and rounding recoveries, 229 ROW_SUM_MISMATCH
segments remained where exactly one cost component was populated
(per_diem, transportation, or other) but the declared total didn't
match it. Three distinct shapes, each with its own recovery:

**1. ROW_TOTAL_COMMA_DECIMAL_TYPO recovery (12 segments).** A
recurring source typo: the writer used a comma where a decimal point
should be (e.g. per_diem=`1,204.00`, total=`1,204,00` which the parser
reads as 120400). The intended total equals the single component
amount; recovery overwrites with the component value and preserves
the source-declared (100×) total in `source_amount`. Only
single-component segments with `declared_total == component × 100`
exactly qualify (the exact-100× gate is tight enough that
coincidental hits are negligible). New
`CostCell.comma_decimal_typo: bool` field is the idempotency marker,
mirroring `double_counted` and `trip_total`. 12 cases across 8 source
files.

**2. ROW_TOTAL_INCLUDES_UNBROKEN_COSTS downgrade (142 segments).**
Positive delta (declared > component). The source declared a total
that includes costs not broken out into per_diem/transport/other --
often a shared commercial airfare charged to the delegation but not
broken out per-traveler (e.g. `1995q1feb09-023` Pete Peterson:
per_diem=$173, total=$1176.15, $1003.15 delta is the airfare).
Informational flag; the source-declared total is kept as-is.

**3. ROW_TOTAL_LESS_THAN_COMPONENT downgrade (75 segments).**
Negative delta (declared < component). The source declared a total
less than the single component -- often the per_diem column shows
the full rate and the total reflects deductions (returned per-diem,
host-provided meals). E.g. `1996q3sep11-007` Sweden delegation: every
traveler has per_diem=$1216 but totals are $1056, $1171.89, $826.85,
etc. (different deduction per traveler). Informational flag; the
source-declared total is kept as-is.

The two downgrades keep the source total; only the typo recovery
overwrites it. All three run after the more specific recoveries
(supplement-merge → trip-total → per_diem × days → double-count →
rounding) so each of those patterns stays specific.

**Test fixture cleanup:** `tests/test_review_corrections.py`'s
`_costs` helper was sharing a single `empty = _cell()` instance across
all cost groups, so mutating one cell (e.g. setting
`transportation.us_dollar.amount`) silently mutated `other.us_dollar`
and all four `foreign_currency` cells. The fixture now creates
distinct cell instances per group. Pre-existing bug, exposed by the
new downgrades.

**Corpus-wide totals after this change:** ROW_SUM_MISMATCH 584 → 355
(−229); ROW_TOTAL_COMPUTED 7909 → 7921 (+12, from the typo
recoveries); ROW_TOTAL_COMMA_DECIMAL_TYPO 0 → 12; ROW_TOTAL_INCLUDES_UNBROKEN_COSTS
0 → 142; ROW_TOTAL_LESS_THAN_COMPONENT 0 → 75. TABLE_SUM_MISMATCH
392 → 389 (−3, downstream of the 12 typo recoveries); TABLE_SUM_ROUNDING
73 → 74 (+1); TABLE_SUM_TRANSPORT_EXCLUDED 15 → 13 (−2).

## [3.0.7] - 2026-07-16

### Fixed - Downgrade small-delta ROW_SUM_MISMATCH to ROW_SUM_ROUNDING (215 segments)

215 segments had a component sum within a small threshold of the
source-declared total but above the strict 2-cent tolerance, so they
were flagged `ROW_SUM_MISMATCH` even though the delta is overwhelmingly
source rounding or a small typo (median delta $0.25, max $3.09). These
are now downgraded to `ROW_SUM_ROUNDING` (informational). The
source-declared total is kept as-is -- consumers care about the
source's stated amount, and the delta is too small to be a real
arithmetic error.

Threshold: `min($5.00, 1% of declared total)`, mirroring the existing
`TABLE_SUM_ROUNDING` rule. This catches:
- 14 sub-cent noise cases (e.g. 837.06 vs 837.10)
- 117 small sub-dollar deltas (source rounded per_diem to a clean total)
- 65 medium $0.50-$1.00 deltas
- 19 $1.00-$5.00 deltas on small/medium totals

Runs after the more specific recoveries so each stays specific:
supplement-merge → trip-total → per_diem × days → double-count →
rounding → mismatch. A segment that fits one of the more specific
patterns is never reclassified as rounding.

**Corpus-wide totals after this change:** ROW_SUM_MISMATCH 799 → 584
(−215); ROW_SUM_ROUNDING 0 → 215 (new). No table-level flag changes
(the source total is preserved, so the table-level sum is unchanged).

## [3.0.6] - 2026-07-16

### Fixed - Recover "trip total in one segment" convention (499 segments, 307 tables)

A recognizable source convention: a traveler has 2+ segments, every
segment has only per_diem populated (no transport or other), and exactly
one segment has a source-declared total equal to the sum of per_diems
across all the traveler's segments. The source is filling the trip
total — the cumulative per_diem across the whole trip — into one
segment's total cell (either the first or the last, depending on the
report), rather than a per-segment total. E.g. `1995q4dec13-005`
(NORTH ATLANTIC ASSEMBLY France/Belgium delegation): each traveler has
France (per_diem=$834.46, no total) and Belgium (per_diem=$606.00,
total=$1,440.46), and 834.46 + 606.00 = 1,440.46.

Without recovery, the segment carrying the trip total got flagged
`ROW_SUM_MISMATCH` (its own per_diem doesn't sum to the trip total);
the other segments were already `ROW_TOTAL_COMPUTED` via the
source-omitted path. 499 such segments across 51 source files were
recovered. Each recovery overwrites the trip-total segment's total
with its own per_diem, preserves the source trip total in
`CostCell.source_amount`, and flags `ROW_TOTAL_IS_TRIP_TOTAL`
(informational) alongside `ROW_TOTAL_COMPUTED`. A new
`CostCell.trip_total` boolean is the idempotency marker for
revalidation (mirrors `double_counted`).

The recovery runs before the `ROW_TOTAL_IS_PER_DIEM_X_DAYS` check so
a 2-segment traveler with equal per_diems where the last segment is a
2-day stay (per_diem × 2 = 2 × per_diem = trip total) is
arithmetic-indistinguishable between the two conventions but
trip-total is the more specific shape (the committee total equals
the sum of trip totals, not the sum of per_diem × days). It also
preempts 24 false `ROW_TOTAL_DOUBLE_COUNTED` cases: the trip-total
segment's delta (declared − per_diem = sum of OTHER segments'
per_diems) can coincidentally equal this segment's own per_diem,
which the double-count check would have misread as a
self-double-count.

The post-recovery sum of segment totals equals the sum of all
per_diems equals the sum of source trip totals equals the
committee-declared total, so the table-level `TABLE_SUM_MISMATCH`
check now resolves cleanly for 307 reports that previously failed
it (some residual `TABLE_SUM_MISMATCH` cases are unmasked as
`TABLE_SUM_EXPLAINED_BY_SUPPLEMENT` / `TABLE_SUM_ROUNDING` /
`TABLE_SUM_COMPONENT_DELTA` / `TABLE_SUM_TRANSPORT_EXCLUDED` — the
underlying pattern was always there, just hidden behind the louder
segment-level mismatch flag).

**Corpus-wide totals after this change:** ROW_SUM_MISMATCH 1462 → 799
(−663, 45% reduction); ROW_TOTAL_DOUBLE_COUNTED 26 → 2 (−24);
ROW_TOTAL_IS_PER_DIEM_X_DAYS 0 → 71 (newly visible, not a regression
— the per-segment check was always there, but trip-total preempted
the false positives and unmasked the real ones in the same pass);
ROW_TOTAL_IS_TRIP_TOTAL 0 → 499 (new). TABLE_SUM_MISMATCH 699 → 392
(−307, 44% reduction); TABLE_SUM_EXPLAINED_BY_SUPPLEMENT 0 → 81,
TABLE_SUM_ROUNDING 0 → 73, TABLE_SUM_COMPONENT_DELTA 0 → 45,
TABLE_SUM_TRANSPORT_EXCLUDED 0 → 15 (all newly visible downgrades
of previously-generic TABLE_SUM_MISMATCH cases).

## [3.0.5] - 2026-07-16

### Fixed - Recover committee-disambiguable member matches (10 reports)

10 member-shaped `MEMBER_UNMATCHED` / `MEMBER_MATCH_INCONCLUSIVE`
travelers fell through the existing committee-disambiguation path
because of three independent lookup-key issues. Each is a small,
surgical fix; together they recover 10 of 176 member-shaped
unmatched, including 5 of 7 Mike Rogers cases (the live ambiguous
same-name pair: R000572 Michigan and R000575 Alabama, both serving
2003-2015).

**Fix 1: Strip parenthetical annotations before disambiguation-index
lookup (7 reports).** `_member_lookup_variants` kept parentheticals
like "(AL)" state tags or "(Codel)" delegation notes in the lookup
keys, so "Hon. Mike Rogers (AL)" generated "HON. MIKE ROGERS (AL)"
which never matched `member_disambiguation.csv`'s "HON. MIKE ROGERS"
entry, even when `sponsor_code` resolved correctly. The variants now
strip trailing parentheticals before key generation (same
`NAME_PARENTHETICAL_RE` already used by `_query_name_tokens` for the
inconclusive-path tiebreaker). Live recovered cases: 2011q2may23
"Hon. Mike Rogers (AL)" → R000575 via HSHM; 2001q2jun25 "Hon. C. Shaw
(Rogers Codel)" → S000303 and "Hon. J.D. Hayworth (Rogers Codel)" →
H000413 via the cleaner lookup reaching the fuzzy matcher.

**Fix 2: Add missing committee-name aliases to `committees.csv`
(2 reports).** "SELECT COMMITTEE ON INTELLIGENCE" and "COMMITTEE ON
INTELLIGENCE" are informal variants House reports use for the
Permanent Select Committee on Intelligence (HLIG) — the committees.csv
alias list only had the formal name, so sponsor_code stayed None and
committee disambiguation couldn't run. Live recovered cases:
2005q2jun09 and 2005q3sep19, both "Hon. Mike Rogers" / "Hon. Michael
Rogers" → R000572 via HLIG.

**Fix 3: Extend `TRAILING_CHAMBER_RE` to consume trailing clipped text
after "HOUSE OF REPRESENTATIVES" (2 reports).** The title-line 193-char
fixed-width limit sometimes clips the committee name mid-token, leaving
gunk like ",P" (the clipped start of "PERIOD") after "HOUSE OF
REPRESENTATIVES". The regex anchored to `\s*$` so the trailing gunk
blocked the chamber strip and the sponsor.name kept ", HOUSE OF
REPRESENTATIVES,P" — too long for any committees.csv key. The regex now
matches ", HOUSE OF REPRESENTATIVES,..." and consumes everything after.
Live recovered cases: 2015q1feb20 "Rep. Mike Rogers" → R000572 via
HLIG (sponsor.name "PERMANENT SELECT COMMITTEE ON INTELLIGENCE, HOUSE OF
REPRESENTATIVES,P" → "PERMANENT SELECT COMMITTEE ON INTELLIGENCE"); and
2015q2apr27 "Hon. Mike Rogers" → R000575 via HSAS (sponsor.name
"COMMITTEE ON ARMED SERVICES, HOUSE OF REPRESENTATIVES,P" → "COMMITTEE
ON ARMED SERVICES").

**Remaining 2 Mike Rogers cases** (correctly stay unmatched):
- 2003q3jul16 — sponsor is a delegation ("DELEGATION TO ITALY, BAHRAIN,
  KUWAIT, AND IRAQ"), no committee to disambiguate against. Genuinely
  ambiguous without state context.
- 2008q2may19 — header parser produced sponsor.name "COMMITTEE ON"
  (severely garbled, the "ARMED SERVICES" name landed after "EXPENDED
  BETWEEN" in the title). A deeper header-parsing bug, out of scope.

**Corpus-wide totals after this change:** MEMBER_UNMATCHED 15121 →
15118 (3 maiden, previous change) → 15099 (this change); MEMBER_MATCH_INCONCLUSIVE
182 → 175 (7 disambiguated via committee); MEMBER_DISAMBIGUATED_BY_COMMITTEE
43 → 50; MEMBER_MATCHED_BY_MAIDEN_NAME 0 → 3.

## [3.0.4] - 2026-07-16

### Fixed - Recover maiden-name → married-name member matches (3 reports)

A member who marries after an earlier-career source report was filed
appears in the source under their maiden surname ("Hon. Stephanie
Herseth") while `members.csv` carries the married compound surname
("HON. STEPHANIE HERSETH SANDLIN"). The fuzzy `NameMatcher` scores the
partial-name query below its `min_match_score` (the source surname is
only half of the member's indexed surname), so `is_confident` and
`is_inconclusive` are both False and the traveler fell through to
`MEMBER_UNMATCHED`. The top fuzzy result is nonetheless the right
person under a strict maiden-name gate, now recovered as
`MEMBER_MATCHED_BY_MAIDEN_NAME`.

**Gate (all must hold):**
- Top fuzzy match exists with first and last name both populated, and
  a period is available for the date check.
- Source first-name token EXACTLY equals the top match's first name
  (case-insensitive — no fuzzy, no initials, no nicknames). The
  maiden-name case is about surname change, not first-name variation;
  accepting first-name slack here would let staffers whose first name
  resembles a member's ride this path.
- Source surname is a strict prefix of the top match's surname: the
  member surname is longer, the source surname is its start, and the
  character right after the prefix is a separator (space or hyphen).
  The separator requirement blocks "Hon. Bob Smith" staffer matching
  "Bob Smithers" member ("Smith" is a prefix of "Smithers" but the
  boundary has no separator — a different name, not a marriage
  extension). The strict-prefix requirement blocks same-surname
  staffers: "Hon. Bob Smith" staffer vs "Bob Smith" member has equal
  surnames, not a prefix relationship.
- The matched bioguide was serving during the report's period (±1
  year for filing lag), via the same `NameMatcher.was_serving` gate
  used by the bare-name recovery path.

**Result on the corpus:** 3 of 176 member-shaped unmatched
`MEMBER_UNMATCHED` travelers recovered, all "Hon. Stephanie Herseth"
across three reports (2005q2may16, 2006q2jun21, 2007q1feb16) →
H001037. The narrow gate produces no false positives on the remaining
173 member-shaped unmatched (Wilson "Bill" Livingood the House
Sergeant at Arms, committee staff with Hon. prefix, etc. — all
correctly stay unmatched).

## [3.0.3] - 2026-07-16

### Fixed - Classify TABLE_SUM_MISMATCH into specific source-convention flags

625 reports carried `TABLE_SUM_MISMATCH` — the committee total didn't
match the sum of segment totals. Scoping identified four recoverable
sub-patterns where the mismatch is a known source convention or a
recovery artifact, not a genuine arithmetic error. The table-level
check now classifies these into specific informational flags rather
than leaving them as generic mismatches.

**Pattern 1: Rounding (74 reports)** — `|delta|` is within a small
threshold (flat `$5` or `1%` of the declared total, whichever is
smaller). Genuine rounding accumulation across many segments. Flagged
`TABLE_SUM_ROUNDING`.

**Pattern 2: Transport excluded (27 reports)** — the source excludes
transportation from the committee total (DoD-provided transport not
counted). `declared ≈ sum(per_diem) + sum(other)` within tolerance.
Flagged `TABLE_SUM_TRANSPORT_EXCLUDED`.

**Pattern 3: Component delta (45 reports)** — `|delta|` exactly
matches one segment's per_diem, transportation, other, or total
amount — a table-level double-count or component exclusion. Flagged
`TABLE_SUM_COMPONENT_DELTA`.

**Pattern 4: Supplement-explained (81 reports, strict)** — the
segment-level `COST_SUPPLEMENT_MERGED` recovery recomputed segment
totals to include supplement rows (Commercial airfare, Delegation
Expenses), but the committee total was declared *before* the
supplements were merged. The original source-declared segment total
is now preserved in `CostCell.source_amount` before the supplement
merge overwrites it. The table-level check computes the pre-supplement
sum (using `source_amount` where available) and verifies it matches
the declared committee total *exactly* (within tolerance). Only when
the arithmetic is confirmed is the mismatch downgraded to
`TABLE_SUM_EXPLAINED_BY_SUPPLEMENT`. The 160 supplement-merged reports
where the pre-supplement sum *still* doesn't match stay
`TABLE_SUM_MISMATCH` — the supplement doesn't fully explain the
discrepancy, and the strict check correctly refuses to downgrade them.

**Measured result** (full corpus):

| category                            | before | after |
|-------------------------------------|--------|-------|
| `TABLE_SUM_MISMATCH`                | 625    | 398   |
| `TABLE_SUM_EXPLAINED_BY_SUPPLEMENT` | 0      | 81    |
| `TABLE_SUM_ROUNDING`                | 0      | 74    |
| `TABLE_SUM_COMPONENT_DELTA`         | 0      | 45    |
| `TABLE_SUM_TRANSPORT_EXCLUDED`      | 0      | 27    |

227 of 625 (36%) downgraded from generic mismatch to specific
informational flags. The remaining 398 are genuine unexplained
mismatches (source typos, OCR errors, multi-issue cases).

**Model change**: `CostCell` gains a `source_amount: Optional[Decimal]`
field that stores the original source-declared amount before a
supplement-merge recovery overwrites it. This is serialized to JSON
and persists across save/load, enabling the strict table-level check.

## [3.0.2] - 2026-07-16

### Fixed - Recover layouts from data rows when headers are garbled (LAYOUT_INFERRED_FROM_DATA)

Two tables in the corpus had header label blocks too garbled for the
normal label-based layout detector, leaving them `LAYOUT_UNDETECTED`
with zero travelers extracted -- their data silently dropped:

- **2009q1jan08-002** (Brussels delegation): the PDF extraction merged
  the header labels onto the title line as `...20081Name of Member or
  employee1Date2Arrival2Departure1Country1Per diem...`, so the
  word-boundary `Arriv` regex sees `2Arrival2` (digit-to-letter is not a
  word boundary) and `_label_positions` returns None.
- **2009q3sep16-000** (Bosnia/Lithuania OSCE delegation): the "Arrival"
  label is entirely missing from the header (the source only printed
  "Departure"), so `_label_positions` returns None.

In both cases the data rows themselves are clean and follow the standard
12-column layout. The fix adds a `_layout_from_data_rows` fallback in
`detect_layout` that's invoked when `_find_header_window` returns None
(header missing entirely) or `_label_positions` returns None (header
found but labels unparseable). The fallback calls
`_detect_gutter_starts(data_lines, country_pos=0)` and requires exactly
11 gutters (4 field boundaries + 7 inter-cost = 12 columns, the standard
layout). Fewer gutters means a non-standard layout (e.g. 1994-era
5-column) or a garbled PDF-extraction artifact -- both are left as
`LAYOUT_UNDETECTED` rather than risk a wrong layout.

**Safety gate:** the 11-gutter requirement is what makes this safe. Of
the 7 remaining layout-flagged reports after the Shape 1+2 fixes, only
these 2 have exactly 11 gutters. Slovakia (1994, 5-column layout, 4
gutters) and the four 2012q4dec11 reports (garbled PDF artifacts with
line-wrapped columns, 0-1 gutters) all correctly stay
`LAYOUT_UNDETECTED`.

Recovered layouts are flagged `LAYOUT_INFERRED_FROM_DATA` (informational)
so downstream consumers can distinguish "header parsed cleanly" from
"header was garbled, layout recovered from data rows." Confidence is set
to 0.85 (above `LOW_CONFIDENCE_THRESHOLD=0.8`) -- the column structure
is unambiguous from the data even though the header was garbled, but the
absence of header cross-checking warrants the flag. `LAYOUT_INFERRED_FROM_DATA`
is not in the `--llm-fallback` trigger set, so recovered reports don't
route to the LLM path.

**Measured result** (full corpus, `--include-superseded`):

| category                     | before | after |
|------------------------------|--------|-------|
| `LAYOUT_UNDETECTED`          | 6      | 4     |
| `LAYOUT_INFERRED_FROM_DATA`  | 0      | 2     |
| travelers recovered          | 0      | 27    |
| segments recovered           | 0      | 41    |

The 2 recovered reports: Brussels (8 staffer travelers, 8 segments) and
Bosnia (19 travelers -- 9 members matched to bioguide IDs including
Hastings, Aderholt, Bordallo, Butterfield -- 33 segments).

## [3.0.1] - 2026-07-13

### Fixed - Layout boundary truncation and column collision

The v3.0.0 layout refiner snapped each column boundary to the nearest
position where ≥60% of data rows *started a token*. That criterion is
wrong for right-justified numeric columns, where the column where a
token starts shifts with digit count: a majority-vote position lands at
the *narrowest* width and silently truncates wider values, and when no
position wins a majority (mixed widths + dot-filled empty cells split
the vote) the search widened until it landed on a *neighboring column's*
boundary, producing a zero-width column and a doubled neighbor.

**Measured blast radius** (full corpus, before this fix):

- 571 of 2,700 tables (21.1%) had at least one collided cost-column
  boundary.
- Cost-population rates across 55,992 segments: per_diem 74.3%,
  transportation 18.0%, other 6.2%, total 60.2%. The transportation and
  total gaps were dominated by this bug.
- The failure was invisible to the review queue: collided tables still
  reported `layout_confidence: 1.0`, and `TABLE_SUM_MISMATCH` never
  fired because it requires a non-null `total` -- exactly what the bug
  nulled.

**The fix** replaces the token-start voting with token-cut avoidance:
refine each boundary to the nearest position that cuts through no data
row's token. Because empty cells in these tables are dot-filled to full
width, the only positions that split nothing in any row are the true
inter-column gutters, so a zero-cuts match is overwhelmingly a correct
gutter position. A second pass tolerates cuts in up to 10% of rows for
rare over-wide values that bleed through every gutter, rather than
giving up and returning the unrefined guess.

**Defense in depth:**

- A post-refinement collision guard in `detect_layout` caps
  `layout_confidence` to 0.5 when two refined boundaries land on the
  same column, routing the table to the review queue (and the
  `--llm-fallback` path) instead of reporting `1.0`.
- A new `ROW_TOTAL_MISSING` segment flag fires when a segment has
  component amounts (per_diem / transportation / other) but no declared
  total -- the exact shape that let this bug hide, because the sum
  check used to silently skip null-total segments.

**Measured result after the fix** (same corpus, 62,503 segments -- no
drop):

| category        | before | after |
|-----------------|--------|-------|
| per_diem        | 74.3%  | 91.0% |
| transportation  | 18.0%  | 24.7% |
| other           | 6.2%   | 8.8%  |
| total           | 60.2%  | 84.3% |

Collided cost-column boundaries in the corpus fell from 571/2700 to
0/2700. The 2 residuals that remained after the boundary-criterion fix
were caused by HTML-escaped `&lt;SUP&gt;` markup in cost cells (in
`1997q3sep23.txt` and `2003q2apr30.txt`) that `strip_html_tags` didn't
catch — the escaped entities shifted column positions in affected rows,
destroying the aligned gutters the refiner relies on. `strip_html_tags`
now strips `&lt;...&gt;` escaped entities alongside raw `<...>` tags,
eliminating both residuals.

A second class of low-confidence tables remained: 54 tables (mostly
1998-era files with truncated header label lines, plus 4 tables in
`1994q2may17.txt` with a concatenated "Foreigncurrency" label format
where the 4th label pair word-wrapped to a continuation line at bogus
positions). The truncated-header tables were recovered by falling back
to the "currency" and "or U.S." labels on subsequent header lines when
the primary Foreign/equivalent labels fell short. The 1994 tables were
recovered by matching "Foreign" without requiring a trailing word
boundary (so "Foreigncurrency" matches), filtering word-wrap artifacts
before the country column, and falling back to data-driven gutter
detection when labels still produced fewer than 8 positions. After
both fixes, only 2 tables remain below the confidence threshold: one
4-row 1994 table with too few data rows for the gutter fallback, and
one "no expenditures" table with a genuine 7-column layout.

**Previously shipped numbers were wrong.** Cost figures parsed by
v3.0.0 were affected on roughly 21% of tables: right-justified amounts
were truncated to their trailing digits (e.g. `2,079.00` parsed as
`79.00`), and the transportation and total columns were swallowed
entirely on collided tables. Downstream outputs (`travel_reports.json`,
`travel_report_data.csv`, any derivative analysis) regenerated with
v3.0.0 should be **regenerated with v3.0.1** -- the affected fields are
not safe to use as-is. The `corrections.json` review queue entries
whose underlying reports now parse differently are still valid
(report_ids are stable, and corrections apply on top of the parsed
values), but a reviewer who "confirmed OK" a table with silently-null
transportation may want to revisit that confirmation.

### Added

- `ROW_TOTAL_MISSING` segment flag (set when a segment has cost
  components but no declared total). Surfaces the failure mode that let
  the boundary bug hide from the row-sum check.
- Data-driven gutter fallback in `detect_layout`: when header labels
  produce fewer than 8 cost column positions (e.g. 1994-era files with
  concatenated "Foreigncurrency" labels and word-wrapped 4th pairs),
  and the table has ≥6 data rows, the layout detector finds all-space
  gutter regions directly from the data and uses their starts as column
  boundaries. This recovers all 8 cost columns for 1994 tables with
  enough data, where labels alone could only find 7 (missing the 4th
  foreign-currency column whose label was lost to word-wrap).
- Partial period parser in `parse_period`: when the strict
  "BETWEEN ... AND ... <year>" regex fails (typically because the
  title line was truncated by the source's 193-char fixed-width limit),
  fall back through a stack of progressively more permissive regexes:
  - `PERIOD_PARTIAL_RE` captures whatever of the BETWEEN clause survived
    and infers the end date from the start month's quarter end (Jan→Mar
    31, Apr→Jun 30, Jul→Sep 30, Oct→Dec 31) and the period year from the
    source filename's filing year/quarter (a period in the same quarter
    as filing belongs to the prior year, else the filing year).
  - `PERIOD_ON_RE` handles "EXPENDED ON <date>" single-date trips.
  - `PERIOD_NO_BETWEEN_RE` handles titles missing the BETWEEN word.
  - `PERIOD_MD_RE` handles numeric M/D format ("BETWEEN 7/1 AND 9/30,
    2009") used by some Speaker reports.
  - `PERIOD_NO_END_MON_RE` handles "BETWEEN FEB. 3 AND 6, 2000" (end has
    a bare day, no end month) by inferring end_mon = start_mon.
  - `PERIOD_DASH_RANGE_RE` handles "BETWEEN FEB. 21-26, 2002"
    (dash-separated day range) by inferring end_mon = start_mon.
  - A tail fallback scans for the LAST "AND <mon> <day>, <year>" in the
    title and uses the preceding "<mon> <day>" as the start, recovering
    duplicated-AND titles like "BETWEEN ARMED SERVICES AND JAN. 1 AND
    MAR. 31, 2008" (a "COMMITTEE ON," typo let "ARMED SERVICES" leak in)
    and titles truncated to "REPRE 14 AND FEB. 22, 1998" (the start
    month "JAN." was lost; the start month is inferred from end_mon).
- Source typo tolerance: `BE?TWE+ENP?` matches "BTWEEN", "BETWEENP", and
  "BETWEEEN". A comma after the end month name ("DEC, 31") is accepted.
  `month_num` strips a leading "P" as a fallback for "PSEPT." typos.
  Invalid end days ("SEPT. 31", "JUNE 31") are clamped to the month's
  last day and flagged, not dropped -- dropping the end date loses year
  inference for every segment in the table. Recovered 79 of 92
  PERIOD_UNPARSEABLE reports and 1,805 of 1,895 NO_PERIOD_FOR_YEAR_INFERENCE
  segments in the full corpus.
- `PERIOD_END_INFERRED` report flag (the AND clause was truncated; end
  date was inferred from the quarter).
- `PERIOD_YEAR_INFERRED_FROM_FILENAME` report flag (no 4-digit year
  survived in the title; period year was inferred from the filing
  year/quarter).
- `PERIOD_END_DAY_CLAMPED` report flag (source typo like "SEPT. 31" was
  clamped to the month's last valid day).
- `PERIOD_START_MONTH_INFERRED` report flag (the truncated title lost
  the start month; start_mon was inferred from end_mon in the tail
  fallback).

### Changed

- `official_foreign_travel/parsing/costs.py`: `parse_cost_cell` now
  strips leading currency codes (FF, DM, SEK, L, HK, LE, D, etc.)
  and dollar signs before parsing, so foreign-currency amounts like
  `FF4,733.91` and `$315.00` are correctly parsed instead of flagged
  as `UNPARSEABLE_COST_CELL`. European thousands convention
  (`5.723.37` → 5723.37) is recognized when there are 2+ periods and
  the last group is exactly 2 digits. Dash-filled cells (`--` and a
  bare `-`) are treated as empty (no value), matching the dot-fill
  convention. Trailing dots that are fixed-width padding residue
  (e.g. `462.00  ..` → 462.00) are stripped. Corpus-wide, this
  recovered 2,569 foreign-currency amounts that were previously
  dropped. A second pass added handling for the source-specific
  patterns that dominated the residual `UNPARSEABLE_COST_CELL`
  population:

  - **Symbolic whole-cell footnote markers** `*`, `**`, `***`, and
    `(*)` are recorded as symbolic footnotes (source-defined: `*` =
    Delegation costs, `**` = Cancelled mission) and treated as empty
    cells, not flagged.
  - **Explicit-empty markers** `N/A`, `n/a`, `NA`, `None`, and `-0-`
    are treated as empty cells, not flagged.
  - **`Milair\3\`** (source shorthand for military air transport) is
    recognized as an empty cell with `military_air=True`, not flagged.
  - **Paren-wrapped footnote + value** (`(\3\) 496.1`, `(\3\)  ..`)
    — after the `\3\` marker is stripped, the residual empty parens
    are dropped so the trailing amount or dotfill parses. The bare
    `(3) 620.00` form (HTML-stripped) is handled the same way.
  - **Asterisk-prefix amounts** (`* 2,443.46`, `** 1,001.67`,
    `*2,944.00`) and **trailing-asterisk amounts** (`12,597.90*`):
    the symbolic marker is recorded as a footnote and the numeric
    part is parsed.
  - **Leading single-digit footnote** without backslashes
    (`4 6,912.00`): the digit is treated as a footnote marker and
    the amount is parsed.
  - **Long currency prefix** (`Euro237.80`) and **trailing currency
    code/name** (`191,590 CFA`, `722.55 euro`, `235.32Ls`): the
    currency token is stripped and the amount parsed. Bare currency
    names alone (`Euro`, `Zloty`, `Irish pound`) remain flagged as
    column-misalignment artifacts, not values.
  - **Source typos in the decimal part**: slash (`1,484/00` →
    1,484.00), stray space (`27,368. 74` → 27,368.74), lowercase-o
    (`394.oo` → 394.00), and stray trailing brace (`5,133.00}` →
    5,133.00).
  - **Accounting-style parenthesized amounts** (`(7.48)`) parse as
    negative.

  This second pass reduced `UNPARSEABLE_COST_CELL` from 1,408 to 439
  cells (69% reduction). The residual 439 are dominated by bare
  currency-name cells (column misalignment) plus a small tail of
  layout-slice artifacts (`..  287.`, `Xxxxxxxxxxx`) that are
  genuinely unparseable.
- `official_foreign_travel/parsing/dates.py`: `parse_month_day` now
  detects the European D/M convention — a first number > 12 with a
  second number in 1-12 is reinterpreted as day/month (e.g. `14/10`
  → Oct 14, `22/11` → Nov 22). The new helper `parse_month_day_swapped`
  also reports whether the swap was applied. `resolve_segment_dates`
  treats D/M as a table-level convention: if either date is
  unambiguous D/M, the other is reinterpreted as D/M too (so
  arrival `30/11` forces departure `2/12` to be read as Dec 2,
  not Feb 12 with a spurious `YEAR_ROLLOVER_APPLIED`). A new
  segment flag `DATE_DAY_MONTH_SWAPPED` records when the swap fired.
  Corpus-wide, this recovered 87 arrival cells and 6 departure cells
  (93 total); `ARRIVAL_DATE_INVALID` fell from 91 to 4 and
  `DEPARTURE_DATE_INVALID` from 34 to 28. The residual 4 arrivals and
  28 departures are genuine source-data errors (`3/39`, `64/12`,
  `2/29` in non-leap years like 1998 and 2005) that no interpretation
  can rescue.
- `official_foreign_travel/parsing/dates.py`: `resolve_segment_dates`
  now swaps arrival and departure when the source has a same-month day
  inversion (arrival day > departure day in the same month). All 54
  corpus cases that previously fired `DEPARTURE_BEFORE_ARRIVAL` were
  same-month inversions — overwhelmingly a source column swap (the
  arrival/departure columns reversed for one row), not a genuine
  backwards-in-time trip. The swap records the correction via a new
  segment flag `ARRIVAL_DEPARTURE_SWAPPED` and lets downstream
  consumers see sensible forward-time dates; `DEPARTURE_BEFORE_ARRIVAL`
  now fires only for cross-month inversions that survive year-rollover
  (none currently in the corpus). Cross-month inversions still get
  `YEAR_ROLLOVER_APPLIED` and aren't swapped.
- `official_foreign_travel/parsing/layout.py`: `_refine_boundary` now
  snaps to the nearest position that cuts no row's token (was: nearest
  position where ≥60% of rows start a token). `_is_token_start` is
  replaced by `_cuts_token`. `detect_layout` caps confidence to 0.5
  when refined positions are not unique. `FOREIGN_LABEL_RE` drops the
  trailing `\b` to match concatenated "Foreigncurrency" (1994 layout).
  `_merge_nearby` tolerance increased from 2 to 3 to merge "equivalent"
  and "currency" labels 3 chars apart (1994 "U.S.currency" layout).
  `_label_positions` filters cost label positions before `country_pos`
  to drop word-wrap artifacts. New `_detect_gutter_starts` provides the
  data-driven fallback.
- `official_foreign_travel/parsing/validate.py`: `validate_report`
  raises `ROW_TOTAL_MISSING` on segments with components but no total,
  and clears it on re-validation (idempotent).
- `official_foreign_travel/utils/text.py`: `strip_html_tags` now strips
  HTML-escaped `&lt;...&gt;` entities alongside raw `<...>` tags. Two
  source files had escaped `&lt;SUP&gt;` markup in cost cells that
  shifted the fixed-width grid and collided layout boundaries.
- `official_foreign_travel/parsing/header.py`: `PERIOD_RE` tolerates a
  comma after the end month name (source typo `DEC, 31`) and the
  `BETWEENP` / `BETWEEEN` typos via `BE?TWE+ENP?`. New
  `PERIOD_PARTIAL_RE`, `PERIOD_ON_RE`, `PERIOD_NO_BETWEEN_RE`,
  `PERIOD_MD_RE`, `PERIOD_NO_END_MON_RE`, `PERIOD_DASH_RANGE_RE`, and a
  tail-fallback pair (`PERIOD_TAIL_END_RE` / `PERIOD_TAIL_START_RE`)
  handle truncated titles, single-date "EXPENDED ON" trips, titles
  missing the BETWEEN word, numeric M/D format, bare-day ends,
  dash-separated day ranges, and duplicated/garbage AND clauses.
  `parse_period` and `parse_header` accept a `source_file` argument used
  to infer the period year from the filing year/quarter encoded in the
  filename. Invalid end days are clamped to the month's last day via
  `_build_date` rather than dropped.
- `official_foreign_travel/parsing/header.py`: `classify_sponsor` now
  recognizes the sponsor patterns that previously fell through to
  `SPONSOR_UNCLASSIFIED`:
  - **`TRAVEL TO <destination>`** and the truncated **`TO <destination>`**
    form (where "TRAVEL" was stripped along with the leading "FOREIGN
    TRAVEL" boilerplate) classify as `delegation` — the sponsor IS
    the trip.
  - **Interparliamentary assemblies**: `NATO PARLIAMENTARY`, `OSCE`,
    `TRANSATLANTIC LEGISLATORS`, `PARLIAMENTARY ASSEMBLY` (generic),
    and the truncated `NORTH ATLANTIC` (pre-NATO-PA name) classify as
    `interparliamentary`.
  - **`JOINT ECONOMIC COMMITTEE`** and other `JOINT ... COMMITTEE`
    patterns classify as `committee` (without requiring "COMMITTEE
    ON"). The `\bCOMMITTEE ON[A-Z]` typo (e.g. "COMMITTEE
    ONSTANDARDS") also classifies as committee.
  - **`SPEAKER`** anywhere in the sponsor text classifies as
    `speaker`.
  - **Bare personal names** (`DANIEL SILVERBERG`, `JENNIFER M.
    STEWART`, `KAY A. KING, PH.D.`, `MARIO DIAZ-BALART`,
    `PATRICK T. McHENRY`) classify as `individual`. The heuristic
    matches 2-5 uppercase words (with `.`/`'`/`-`/`,` allowed) after
    every committee/delegation/travel/interparliamentary pattern has
    been ruled out, and a stopword guard rejects phrases like "TRAVEL
    TO RUSSIA" that happen to look name-shaped.
  - **Honorifics** `HONORABLE`, `REV`, `FR`, `FATHER`, `MSGR`,
    `SIR`, `LADY` now join `HON`/`MR`/`MRS`/`MS`/`DR` as recognized
    individual prefixes.

  Corpus-wide, this reduced `SPONSOR_UNCLASSIFIED` from 268 to 4
  reports (98.5% reduction). The residual 4 are all `parse_header`
  issues — trailing `HOUSE OF REPRESENTATIVES, EXPENDED...` clauses
  that couldn't be stripped because the period parser rejected a
  typo (`20O2` for `2002`, doubled `BETWEEN`), or a boilerplate
  paragraph leaking into a sponsor slot — not classify_sponsor
  failures.
- `official_foreign_travel/parsing/rows.py`: `extract_rows` now
  handles single-date "US departure" / "US return" legs. These rows
  carry only a departure (or only an arrival) date because one end of
  the trip was domestic — the source leaves the unused cell
  dot-filled. Previously, requiring two date tokens skipped these
  rows entirely, losing the traveler's name on the first leg and
  orphaning every subsequent foreign leg below it under
  `SEGMENT_WITHOUT_TRAVELER_NAME`. Two new code paths recover them:
  - When a row carries exactly one date token, it's treated as a
    partial segment (the empty cell becomes an empty `arrival_raw` or
    `departure_raw`, flagged downstream as `ARRIVAL_CELL_EMPTY` /
    `DEPARTURE_CELL_EMPTY` by `resolve_segment_dates`).
  - When a row carries zero date tokens but has a name that
    `_looks_like_personal_name` accepts (rejecting sub-labels like
    "Commercial airfare" and multi-line sponsor headings), the name
    is carried forward as `pending_name` and attached to the next
    dated row. This recovers CODEL label-rows patterns (a traveler
    named on one row whose itinerary follows on the next) and rows
    with incomplete date text (`1/` with no day).
- `official_foreign_travel/parsing/dates.py`:
  `resolve_segment_dates` distinguishes a genuinely empty date cell
  from unparseable text. An empty `arrival_raw` now flags
  `ARRIVAL_CELL_EMPTY` (was: `ARRIVAL_DATE_UNPARSEABLE`), so
  downstream consumers can tell source-missing from parse-failure.
  Same for `departure_raw` → `DEPARTURE_CELL_EMPTY`.
- `official_foreign_travel/utils/text.py`: `clean_cell` strips
  trailing whitespace-and-dot interleavings (e.g.
  `"Sablan...............  ."`) in one pass, not just contiguous
  trailing dots. This was needed because a name column's dotfill
  often bleeds 1-2 chars into the adjacent column when layout
  refinement lands the boundary a hair too wide.
- `official_foreign_travel/parsing/months.py`: `month_num` strips a
  leading "P" as a fallback when the bare name doesn't resolve, so
  source typos like "PSEPT." parse as September.

  Corpus-wide, these row-extraction changes reduced
  `SEGMENT_WITHOUT_TRAVELER_NAME` from 77 reports to 1 (98.7%
  reduction). The 1 residual is a `--Continued` table whose first
  data row has no name at all — the name is in the *previous* block
  (the same committee split across two page-boundaried report
  blocks); recovering it requires a cross-block merge that's out of
  scope here. The new `ARRIVAL_CELL_EMPTY` / `DEPARTURE_CELL_EMPTY`
  flags (1,230 segments) record the partial legs that were
  previously invisible.

### Fixed - Committee-total row recognition (typo tolerance)

`TOTAL_ROW_RE` only matched the canonical spellings
`Committee total` / `Grand total`. The House Clerk corpus contains a
small but persistent zoo of source typos in both the prefix word and
the "total" token — these rows were silently ignored, the table's
`committee_total` stayed `None`, and the report was flagged
`MISSING_COMMITTEE_TOTAL` even though the total row was visibly
present in the source.

The regex now tolerates the typo variants found across the corpus:

- Prefix: `Commitee`, `Committe`, `Committeee`, `Committeel`,
  `Committtee`, `Commmittee`, `Committed`, `Grant` (for Grand),
  `Commercial` (for Committee), and `CODEL`. (The prefix is still
  optional, so a bare `Total for page 3` sub-total row also matches
  as before.)
- Token: `totals` (plural), `tota;` (semicolon for `l`), `tota:`,
  `Totals:` — any `tota` followed by up to 3 chars from
  `[a-z;:}]`.
- An optional `\N\` footnote marker between the token and the
  dot-fill (e.g. `Committee Total\3\........`).
- The trailing `(\s+for\s+|\s*\.)` anchor requires either dot-fill
  or a " for ..." continuation after the token, which excludes
  footnote lines like `\3\ Total cost of all commercial flights.` and
  `* Total air.` that begin with non-alphabetic chars and would
  otherwise match the bare `total` prefix-optional form.

Corpus-wide, this recovered `MISSING_COMMITTEE_TOTAL` on 88 reports
(133 → 45, 66% reduction). The 45 residuals are a mix of: layout
failures (`LAYOUT_UNDETECTED`), tables with no total row in source
(truncated reports), and the cross-block `Continued` shape where the
total lives in a different block from the data.

### Fixed - Cross-block merge for `--Continued` tables

When a committee's report spans a page break, the next page begins
with a new `REPORT OF EXPENDITURES...--Continued` header. The
segmenter treated each as a separate `TableBlock`, so the original
block held the data rows and the Continued block held the trailing
`Committee total` and any supplemental `Commercial airfare` rows.
The result: the report was flagged `MISSING_COMMITTEE_TOTAL` (the
total was in a block the parser never connected to the data), and
travelers whose first row was on the Continued page were orphaned
under `SEGMENT_WITHOUT_TRAVELER_NAME`.

`segment_tables` now post-processes its block list to fold each
`--Continued` block into the most recent earlier block with the
matching title (minus `--Continued`). The Continued block's lines are
appended verbatim to the original block's lines; the duplicated
column-header boilerplate (title, dashed separators, "Name of
Member" label row) is preserved because it's safely ignored
downstream: `extract_rows`'s no-token branch rejects the label row
via `_looks_like_personal_name` (it contains the stopword "of"), and
`detect_layout._find_header_window` only uses the *first* "Name of
Member" occurrence, so the second header section doesn't perturb
column-boundary detection. Subsequent blocks' `table_index` values
are renumbered to stay contiguous.

The merge is conservative: a `--Continued` block with no matching
earlier title (e.g. the previous block was a different committee) is
kept as its own block rather than silently dropped, so a malformed
source file can't lose data.

Corpus-wide, this recovered:
- `MISSING_COMMITTEE_TOTAL` on 11 reports (45 → 34). All 11
  `--Continued` blocks in the corpus had a matching earlier block,
  so every recoverable case was recovered.
- `SEGMENT_WITHOUT_TRAVELER_NAME` on 2 reports (2 → 0). Both were
  Continued-block residuals: the traveler's name was on the prior
  page, but the next page's first data row had no name and was
  flagged as an orphan segment.

The 34 remaining `MISSING_COMMITTEE_TOTAL` residuals are
genuinely-missing total rows in source -- the committee signed the
report without including a total row. Recovering those would require
synthesizing a computed total (sum of segment totals), which is a
different kind of fix and out of scope here.

### Fixed - Bare currency labels and cost-cell typo recovery

The `UNPARSEABLE_COST_CELL` flag was previously raised for any cost
cell whose text couldn't be coerced to a `Decimal`, including cells
that are legitimately non-numeric. The 3.0.0 design intent (documented
in the CHANGELOG entry for that release) was to flag bare currency
names as "column-misalignment artifacts, not values." That judgment
was too coarse for the corpus: 91% of the residual unparseable cells
were not misalignment but source conventions and OCR typos with
clearly-recoverable semantics.

`parse_cost_cell` now recognizes the following additional patterns
and returns an empty `CostCell` (no flag, no value) or a parsed
amount where appropriate:

- **Bare currency names** (`Euro`, `Zloty`, `Irish pound`, `English`,
  `Franc`, `Ruble`, `Hryvnia`, `Dinar`, `Krone`, `Koruna`, `Kuna`,
  `Forint`, `Lari`, `Leu`, `Pound`, `Baht`, `Lira`, `Manat`, `Naira`,
  `Rand`, `Shekel`, `Shilling`, `Tenge`, `Won`, `Yen`, `Som`, plus
  OCR-typo variants `Zolty`/`Rubble` and 3-letter ISO codes `DKK`,
  `ETB`, `KES`, `SEK`, `NOK`, `ISK`, `CZK`, `HUF`, `PLN`, `RON`,
  `BGN`, `HRK`, `RSD`): when only the U.S. dollar equivalent is
  reported, the foreign-currency cell carries just the currency label
  -- a labeling convention, not a parse error or a value to recover.
- **`Military` / `Military air`** as a standalone transportation-cell
  label: marks the leg as military-air-transported with no commercial
  cost (`military_air=True`, `amount=None`).
- **`\*\N` symbolic footnote marker** (star between backslashes,
  used for classified-travel omissions under title 22 USC 1754(b)(2)):
  stripped and recorded as footnote `*`, amount parsed.
- **`****` four-asterisk prefix** (variant of the cancelled-mission
  `**` marker): collected as a footnote, amount parsed.
- **Trailing-minus negative amounts** (`1,060.00-`): accounting-style
  negative written with a trailing dash, parsed as `-1060.00`.
- **Leading dots/whitespace before content** (`..       287.`,
  `...........  DKK`): the leading dot-fill residue is stripped,
  revealing either an amount or a bare currency label.
- **Leading `!` / `=` typos** (`!1,288.28`, `=129.00`): stripped
  before parsing.
- **Trailing bracket typo** (`41.00]`): stripped, amount parsed.

Corpus-wide, this reduced `UNPARSEABLE_COST_CELL` from 424 cells to
24 cells (94% reduction, segment-level: 34 residual flags). The
remaining 24 cells are genuinely unparseable: country names that
leaked from a column shift (`Haiti`, `Kyrgyzstan`), descriptive
label fragments (`Transport`, `Commercial`, `Air`, `(eurostar)`,
`Taxi/Bags`, `Misc. exp.`, `Conf/Rental`, `. No per diem`), phrase
fragments (`1 returning to`, `Jordan on s`, `ame day via mi`,
`ransportation`), a footnote-text fragment (`#10880).`), the
spaced-asterisk symbolic marker (`* * * 234.2`), and `\4\1A5,018.`
OCR cases where `A` is a comma typo. These would require
column-aware logic or OCR-error correction to recover and are left
flagged for review.

The two existing tests that documented the prior "bare currency
names stay flagged" design have been replaced with tests that
document the new "bare currency names are labels" behavior.

### Fixed - Multi-key lookup variants for member matching

`_match_member` previously attempted a single exact lookup against
`members.csv` (keyed as `HON. <NAME>`) and then fell through to the
honorific-gated fuzzy matcher. That single-key strategy left 31,089
travelers flagged `MEMBER_UNMATCHED` across the corpus even when the
source name was a clear congressional honorific (`Hon.` / `Rep.` /
`Sen.`) whose body just didn't exactly match the index form -- a
missing period after a first initial, a stripped middle initial, a
source-omitted suffix, a multi-token surname with a particle, etc.

`_member_lookup_variants` now generates an ordered set of lookup
keys for each honorific-prefixed name, and `_match_member` tries
each key for an exact match (and then for a disambiguation-index
match) before falling through to fuzzy. Variants in decreasing
specificity:

1. Full body with `HON.` prefix.
2. Period after a single-letter first initial
   (`E de la Garza` -> `E. de la Garza`).
3. First + last, dropping middle initials
   (`William D. Lipinski` -> `William Lipinski`).
4. Strip a leading single-letter initial
   (`Y. Tim Hutchinson` -> `Tim Hutchinson`).
5. Surname-only (`Hon. Glen Browder` -> `BROWDER`).
6. Multi-token surname with a Romance/Teutonic particle
   (`de la Garza` -> `DE LA GARZA`).
7. Appended suffix variants (`Donald Payne` -> `Donald Payne, JR` /
   `Donald Payne JR`) -- `members.csv` stores sons who share a name
   with their father with a suffix; the source often omits it.

**Safety gate.** Only congressional honorifics (`Hon.`, `Rep.`,
`Sen.`) trigger the full variant set. Bare names (no honorific) and
other honorifics (`Mr.`, `Ms.`, `Dr.`, `Rev.`, etc., which in this
corpus overwhelmingly prefix committee staff) fall back to the
source-form `name.upper()` lookup -- which doesn't match `HON. ...`
entries, so they fall through to the honorific-gated fuzzy matcher
unchanged. This preserves the original safety guarantee: bare names
are not matched to members by surname, because that produces
confident-looking but wrong bioguide IDs (e.g. multiple different
staffers all matched to the same member by surname).

**Measured result** (full corpus):

| flag                                       | before  | after   |
|--------------------------------------------|---------|---------|
| `MEMBER_UNMATCHED`                          | 31,089  | 17,912  |
| `MEMBER_DISAMBIGUATED_BY_COMMITTEE`         | 0       | 46      |

A 42% reduction in `MEMBER_UNMATCHED`. The 46 new
disambiguation-index matches are a side effect of the variant
strategy: variants now reach the disambiguation index for names
that previously failed the single-key exact lookup before
disambiguation was even tried.

Verified on a 50-report sample that all newly-matched travelers
resolve to the correct bioguide ID (e.g. `Hon. E de la Garza` ->
`D000203`, `Hon. Donald Payne` -> `P000604`, `Hon. Y. Tim
Hutchinson` -> `H001015`, `Hon. William D. Lipinski` -> `L000342`).

**Residual unresolved categories** (the remaining 17,912 flags)
are cases the variant strategy cannot recover by construction:

- OCR variants (`B.Rose` -> `Barbara-Rose`, `McMillian` ->
  `McMillan`, `Morril` -> `Morrill`).
- Nickname mismatches (`William` -> `Bill`, `Robert` -> `Bob`).
- Married-name suffixes (`Helen Chenoweth` -> `Helen Chenoweth Hage`).
- Ambiguous surnames (`Charles Wilson` -- multiple members share
  the name; the disambiguation CSV does not yet cover every
  committee permutation).
- Genuine non-members (staffers with an `Hon.` prefix, e.g.
  legislative directors traveling with the member).

These would require OCR-error correction, nickname expansion, or
further disambiguation CSV curation to recover.

### Fixed - Recovery of source-omitted segment totals (`ROW_TOTAL_COMPUTED`)

`ROW_TOTAL_MISSING` was added in 3.0.1 as a defense-in-depth flag to
surface the segment shape that let the layout-boundary collision bug
hide from the sum check: components present (per_diem / transportation /
other) but `total.us_dollar.amount` is None. With the layout bug fixed,
the residual 6,434 `ROW_TOTAL_MISSING` segments are no longer parser
errors -- they are source omissions.

Sampling the corpus showed the dominant pattern (78%, 5,057 of 6,434)
is "source declared only per_diem; the total cell is dot-filled" --
common in older reports where per_diem IS the total. The remaining 22%
have multiple components but no declared total. In every case the
total equals the sum of the non-null component amounts.

`validate_report` now recovers these totals: when
`total.us_dollar.amount` is None and the sum of the non-null component
amounts is greater than zero, the total is filled with that sum and
the segment is tagged `ROW_TOTAL_COMPUTED` (informational, replacing
the `ROW_TOTAL_MISSING` problem flag). The sum-mismatch check is
skipped for recovered totals -- they're correct by construction.
Source-declared totals (raw cell carries digits) are never
overwritten.

**Idempotency.** The empty-cell `raw` marker (dot-fill, whitespace, or
stray backslashes from `\1/6\`-style date-token residue) is what
distinguishes a recovered total from a source-declared one. On
revalidation (e.g. after a correction), `validate_report` clears
`ROW_TOTAL_COMPUTED` along with its other flags and re-adds it
whenever the total cell is still empty-looking -- so the flag survives
revalidation unchanged. If a correction step replaces the computed
total with a real source value (raw now carries digits), the flag is
cleared on revalidation.

**Measured result** (full corpus):

| flag                     | before  | after   |
|--------------------------|---------|---------|
| `ROW_TOTAL_MISSING`      | 6,434   | 0       |
| `ROW_TOTAL_COMPUTED`     | 0       | 6,434   |

Problem-flag count drops by 6,434; the data is recovered (every
previously-missing total is now populated); the audit trail is
preserved (the new informational flag marks every recovered total).

`TABLE_SUM_MISMATCH` (619) and `ROW_SUM_MISMATCH` (3,075) counts are
unchanged -- the recovered segments were not contributing to those
mismatches, and the recovered totals don't introduce new ones.

### Fixed - Recovery of supplement-outdated segment totals

`ROW_SUM_MISMATCH` fires when a segment's component amounts (per_diem
+ transportation + other) don't add up to the declared total. Sampling
the 3,075 cases revealed that 1,484 (48%) are a single recoverable
pattern: the segment has `COST_SUPPLEMENT_MERGED` (a supplement row like
"Commercial transportation" or "Delegation Expenses" was merged into
the components) and the source's declared total was never updated to
reflect the supplement.

Example (`report_text/1994q1feb10.txt`):

```
Hon. Glen Browder...............    12/10  12/16  Germany.....  ...........  1,050.00  ...........  ...  1,050.00
    Commercial transportation...  ........  .......  ............  ...........  ...........  ...........  731.25  ...........  ...........
```

The source declared total=1,050.00 (just per_diem). The supplement row
added transportation=731.25, but the source's total cell wasn't updated.
After parsing, the segment has per_diem=1,050.00 + transportation=
731.25 = 1,781.25, but the source-declared total is still 1,050.00 --
a 731.25 mismatch that's the supplement's contribution, not a parse
error.

`validate_report` now detects this pattern: when `ROW_SUM_MISMATCH`
would fire AND `COST_SUPPLEMENT_MERGED` is also set on the segment, the
source-declared total is overwritten with the computed component sum
and the segment is tagged `ROW_TOTAL_COMPUTED` (replacing
`ROW_SUM_MISMATCH`). The supplement flag is preserved as audit trail.

**Idempotency** is now handled by an explicit `computed: bool` field on
`CostCell` (default False). `validate_report` sets `computed=True`
whenever it fills in or overwrites a total -- for both the
source-omitted recovery (added in the prior fix) and this
supplement-outdated recovery. The flag is re-added on revalidation
whenever `computed=True`, replacing the prior raw-string sniffing
heuristic, which couldn't distinguish a recovered total from a
source-declared one when the source raw carried digits. A correction
that wants to replace a computed total with a real source value must
clear `computed=False` to signal "this is no longer computed."

**Residual `ROW_SUM_MISMATCH` (1,591)** -- the cases that aren't
supplement-related. Sampling shows:

- **Small diffs (≤ $1, 224 cases)**: source rounding typos (e.g.,
  source total is 50 cents off the component sum).
- **Large diffs (1,331 cases)**: OCR typos (`4,29495` parsed as
  429,495 instead of 4,294.95), missing digit (`33,036.35` vs
  `3,036.35`), military-air cost referenced in a footnote but not
  extracted (36 cases), and segments where the source has only a total
  and no components (parser issue or genuinely distinct row).

These would require OCR-error correction, footnote-cost extraction, or
component-row recovery to fix -- left flagged as genuine source errors.

**Measured result** (full corpus):

| flag                     | before  | after   | Δ       |
|--------------------------|---------|---------|---------|
| `ROW_SUM_MISMATCH`        | 3,075   | 1,591   | -1,484  |
| `ROW_TOTAL_COMPUTED`     | 6,434   | 7,918   | +1,484  |
| `TABLE_SUM_MISMATCH`     | 639     | 700     | +61     |

The 61 new `TABLE_SUM_MISMATCH` cases are real signal: recovered
per-segment totals now contribute to the table sum, exposing tables
where the source's committee total was inconsistent with the
per-segment data (e.g., the committee total was the sum of the
source-declared per-segment totals, not the recovered ones). These
were previously masked because the supplement-outdated per-segment
totals were understating the true sum.

### Fixed - Recovery of period headers (`PERIOD_UNPARSEABLE`)

`PERIOD_UNPARSEABLE` (13 reports) and its downstream
`NO_PERIOD_FOR_YEAR_INFERENCE` (90 segments with valid M/D dates that
couldn't be resolved to a year because the period was missing) came
from two distinct source shapes:

1. **Speaker / annual-summary wrappers (2 reports)**: titles like
   "... during the first quarter of 2008 ..." or "... during the
   first, second, third, and fourth quarters of 2018 ..." have no
   `EXPENDED BETWEEN` clause and no per-traveler rows -- they're
   wrapper text before the actual reports. New
   `PERIOD_DURING_QUARTERS_RE` parses the listed quarter(s) of the
   stated year. Single quarter -> `quarter=N`; all four quarters ->
   full-year period with `quarter=None`. Clean recovery, no flag.
2. **Truncated titles (11 reports)**: titles ending in
   "EXPENDED BETWEEN", "EXPENDED BETWEEN MAR. 5",
   "EXPENDED BETWEEN MAY 25 AND MA", "EXPENDED BE", "EXP 1997", etc.
   The title's fixed-width line was truncated before the dates
   survived. New `_build_period_from_filename` infers a quarter-wide
   period from the source filename's `YYYYqQmmmdd.txt` pattern: filed
   Q1 -> period Q4 of prior year, filed Q2 -> Q1 of filing year, etc.
   (The House Clerk files reports the quarter AFTER travel ended.)
   Tagged `PERIOD_INFERRED_FROM_FILENAME` (informational).

The truncated-title recovery also required unwinding a control-flow
bug: `_build_period_from_partial_match` could return `None` (e.g.
"EXPENDED BETWEEN MAR. 5" -- March isn't a standard quarter start, so
no quarter end can be inferred), but `parse_period` returned that
result directly instead of falling through to the new fallbacks. Now
a `None` partial result falls through to `during`, `tail`, and
filename inference.

The year-rollover logic in `dates.resolve_dates` handles cross-year
edge cases at the segment level: a Mar 28 - Apr 2 trip with an
inferred Q1 period will resolve the Apr 2 segment as Apr 2 of the
next year via year-rollover (flagged `YEAR_ROLLOVER_APPLIED`).

**Measured result** (full corpus):

| flag                                  | before | after | Δ    |
|---------------------------------------|--------|-------|------|
| `PERIOD_UNPARSEABLE`                  | 13     | 0     | -13  |
| `NO_PERIOD_FOR_YEAR_INFERENCE`        | 90     | 0     | -90  |
| `PERIOD_INFERRED_FROM_FILENAME`       | 0      | 11    | +11  |

All 13 reports now have a Period. All 90 previously-orphaned M/D
segment dates now resolve to full arrival/departure dates.

### Fixed - Recovery of source-double-counted component totals (`ROW_TOTAL_DOUBLE_COUNTED`)

A residual subset of `ROW_SUM_MISMATCH` segments had a source total that
exceeded the component sum by exactly one component amount -- the
source double-counted that component. The clearest example is the 1997
Korea trip in `1997q3sep03.txt`: every row has `per_diem=305.00` and
no other components, but the source declared `total=610.00` (= 2 *
per_diem). The source added `per_diem` to itself in both USD and the
foreign currency. 24 of these are the Korea pattern; 2 more are
similar double-counts in rows with multiple components
(`2001q2may23` Abigail Shannon: `per_diem=67, transport=6079.14,
declared=6213.14`; `2019q3aug23` Jaclyn Cahan: `per_diem=789,
transport=6124.23, other=94.09, declared=7796.32`).

`validate_report` now detects this pattern: when the source total
exceeds the component sum by an amount within tolerance of exactly
one component, the source is treated as having double-counted that
component. The total is overwritten with the computed sum, the
segment is tagged `ROW_TOTAL_DOUBLE_COUNTED` (informational, distinct
from `ROW_TOTAL_COMPUTED` which is also set), and
`costs.total.us_dollar.double_counted` is set as the idempotency marker
(mirroring `costs.total.us_dollar.computed`).

**Safety.** Only `diff > 0` (declared exceeds computed) qualifies. The
inverse -- `diff < 0`, source total excludes a component -- is
overwhelmingly an intentional source convention, not a source error:

- `|diff| = transport` (20 cases): military airfare listed in the
  transport column but excluded from the total (the source notes the
  flight was provided separately and shouldn't be in the reimbursement
  total).
- `|diff| = per_diem` (15 cases): per diem paid via a different
  reimbursement mechanism (e.g. the "Commercial airfare" supplement
  label rows where the source column structure puts a value in the
  per_diem slot but the total only reflects transport).
- `|diff| = other` (25 cases): "other" costs excluded from the total
  (separate reimbursement, e.g. a delegation expense billed elsewhere).

Overwriting those would replace a correct source convention with a
wrong computed value, so they stay flagged `ROW_SUM_MISMATCH`.

**Measured result** (full corpus):

| flag                                | before  | after   | Δ     |
|-------------------------------------|---------|---------|-------|
| `ROW_SUM_MISMATCH`                  | 1,591   | 1,565   | -26   |
| `ROW_TOTAL_COMPUTED`                | 7,918   | 7,944   | +26   |
| `ROW_TOTAL_DOUBLE_COUNTED` (new)    | 0       | 26      | +26   |
| `TABLE_SUM_MISMATCH`                | 700     | 702     | +2    |

26 cases recovered. The +2 `TABLE_SUM_MISMATCH` is the recovery
surfacing two tables whose declared committee total previously matched
the inflated (source-double-counted) segment sum: now that the segment
totals are corrected, the committee total no longer matches and is
correctly flagged as itself wrong.

**Residual `ROW_SUM_MISMATCH` (1,565 cases)** breaks down as:

- ~60 "source excluded a component" (intentional source convention,
  left flagged as designed).
- ~31 round-thousand diffs (e.g. `transport=33036.35` parsed when the
  source had `3036.35` -- a layout/parser error adding a leading `3`;
  or `declared=364` when source `per_diem=4364` -- a digit drop).
  These need layout-parser or source-curation fixes, not arithmetic
  recovery.
- ~103 cases with no components parsed but a declared total (a source
  convention, not layout errors -- see the `ROW_NO_COMPONENT_BREAKDOWN`
  fix below; now reclassified out of `ROW_SUM_MISMATCH`).
- ~1,365 other mismatches: source rounding typos, OCR errors, military
  airfare costs referenced only in footnotes, and genuine source
  arithmetic errors. Recovering these would require OCR-error
  correction, layout-parser improvements, or per-row curation.

### Fixed - Reclassify no-breakdown rows out of `ROW_SUM_MISMATCH` (`ROW_NO_COMPONENT_BREAKDOWN`)

A residual subset of `ROW_SUM_MISMATCH` segments had a declared total
but no per-component breakdown at all -- all three component cells
(per_diem, transportation, other) were empty/dot-filled. There is
nothing to arithmetically check in that shape: the flag was firing on
`abs(0 - declared_total) > tolerance`, which is a tautology, not a
mismatch. The largest single source is `2009q2may13.txt` (46 of 102
cases), where the source convention is to declare only a USD total for
every row with no breakdown -- the parser correctly extracted what's
there, the data isn't wrong, it's just unverifiable.

`validate_report` now detects this shape (computed component sum is
zero, a non-zero total is declared) and flags it
`ROW_NO_COMPONENT_BREAKDOWN` (informational) instead of
`ROW_SUM_MISMATCH` (error). This mirrors the supplement-merged and
double-count recoveries' philosophy of distinguishing "source didn't
provide the data to check" from "source provided data that doesn't add
up." The declared total is left in place and still counts toward the
table-level `TABLE_SUM_MISMATCH` check -- it's a real dollar amount,
just one we can't verify at the row level.

**Measured result** (full corpus, `--include-superseded`):

| flag                              | before  | after   | Δ     |
|-----------------------------------|---------|---------|-------|
| `ROW_SUM_MISMATCH`                | 1,565   | 1,462   | -103  |
| `ROW_NO_COMPONENT_BREAKDOWN` (new)| 0       | 116     | +116  |
| `ROW_TOTAL_COMPUTED`              | 7,944   | 7,931   | -13   |

116 cases reclassified. 103 came directly from `ROW_SUM_MISMATCH` --
rows that had a declared total and no components, previously flagged
as an arithmetic mismatch when there was nothing to mismatch. The
other 13 came from `ROW_TOTAL_COMPUTED`: those were
`COST_SUPPLEMENT_MERGED` rows with no components, where the supplement
recovery path previously overwrote the declared total with the computed
sum (zero) and tagged `ROW_TOTAL_COMPUTED` -- zeroing a real declared
total. The new no-breakdown check runs before the supplement recovery,
so those rows keep their declared total and are flagged informationally
instead. 46 of the 116 are from `2009q2may13.txt` (the known
source-convention file); the remaining 70 are scattered across ~25
files (`2007q1feb16`, `2006q2jun21`, `2013q2may08`, `2001q1feb08`,
etc.) -- the same shape at lower volume.

### Fixed - Disambiguation of ambiguous fuzzy member matches (`MEMBER_DISAMBIGUATED_BY_NAME`)

When `NameMatcher.search_by_name` returned `is_inconclusive` (two or more
candidates scored within the ambiguity threshold), the pipeline previously
left the traveler unmatched with `MEMBER_MATCH_INCONCLUSIVE` -- even when
the source name clearly identified one of the two candidates by first
name. The classic shape: `Hon. D. Payne` is ambiguous between Donald
Payne (`P000149`) and Lewis Payne (`P000152`) because the surname matches
both and the single initial `D.` doesn't break the tie in the fuzzy
score. 150 travelers were stuck in this state.

`_match_member` now runs a first-name + surname tiebreaker on the
inconclusive candidate set: it extracts the source name's first and last
token (stripping the honorific, parenthetical annotations like
`(Gilman Codel)`, suffix tokens, and fixed-width gunk), then checks each
fuzzy candidate's first and last name against those tokens. If exactly
one candidate's first name AND surname both plausibly match, that's the
member the source meant; the traveler is matched and tagged
`MEMBER_DISAMBIGUATED_BY_NAME`. If both or neither match, the ambiguity
is real (a staffer whose name coincidentally resembles two members, or a
true same-name collision needing committee context) and it stays
`MEMBER_MATCH_INCONCLUSIVE`.

**Safety -- the surname gate.** The first-name check alone is not safe:
`Hon. Tim Clancy` fuzzy-matches `Tim Holden` on first name, but `Clancy`
!= `Holden`, so the surname gate rejects it -- Clancy is a staffer, not
a typo of Holden. Both checks are required, and each is gated by a
relative edit-distance ratio (≤0.3) so a single edit on a very short
name doesn't count: `Jim`→`Tim` (1/3 = 0.33) is rejected as a staffer
coincidence, while `Partrick`→`Patrick` (1/8 = 0.125) is accepted as a
typo. The surname matcher also handles hyphenated compounds
(`Chenoweth`→`Chenoweth-Hage`) and 1-2 character typos (`Gilmor`→
`Gillmor`, `LoBionbo`→`LoBiondo`, `Ryan`→`Ryun`).

**Three cases where the tiebreaker overrode the fuzzy top candidate.**
The fuzzy matcher sometimes ranked the wrong candidate first, and the
tiebreaker corrected it by first-name + surname verification:
- `Hon. Helen Chenoweth` → fuzzy top was Stephen Horn (`H000789`);
  tiebreaker picked Helen Chenoweth-Hage (`C000345`) -- `Helen` matches
  `Helen`, `Chenoweth` matches the `Chenoweth-Hage` compound, and neither
  matches Horn.
- `Hon. JoAnn Davis` → fuzzy top was James Davis (`D000114`); tiebreaker
  picked Jo Ann Davis (`D000597`) -- `JoAnn` ~ `Jo Ann`, same surname.
- `Hon. Jim Ryan` → fuzzy top was Tim Ryan (`R000577`); tiebreaker
  picked Jim Ryun (`R000566`) -- `Jim` exactly matches Jim Ryun's first
  name (Tim is only a 1-edit match, gated out by the ratio), and
  `Ryan` ~ `Ryun` (1-edit surname).

**Measured result** (full corpus, `--include-superseded
--fuzzy-name-matching`):

| flag                                | before  | after   | Δ     |
|-------------------------------------|---------|---------|-------|
| `MEMBER_MATCH_INCONCLUSIVE`         | 231     | 200     | -31   |
| `MEMBER_DISAMBIGUATED_BY_NAME` (new)| 0       | 31      | +31   |
| Travelers matched                   | 14,007  | 14,038  | +31   |

31 travelers recovered. The residual 200 `MEMBER_MATCH_INCONCLUSIVE`
are: the 6 true `Mike Rogers` same-name collisions (need the
`disambiguation_index`, which already handles 4 of the 6 when a sponsor
code is present -- the other 2 have no sponsor code on the report), and
~194 staffers where neither candidate's first name AND surname both
match (the tiebreaker correctly declines to guess). All 31 recoveries
were hand-verified as real members.

### Changed - Fuzzy name matching is now the default

`--fuzzy-name-matching` used to be opt-in, on the assumption that exact
matching against `members.csv` was the safe default and fuzzy matching
(via the `unitedstates/congress-legislators` YAML) was a recovery path
for the residue. The reverse is true in practice: exact-only matching
leaves **803 travelers unmatched** that fuzzy matching recovers --
members whose source-row name spelling drifts from `members.csv`
(initials vs. full names, dropped suffixes, OCR typos, hyphenated
surnames, the `Hon`-without-period shape recovered by the
honorific fix above). The fuzzy path is also the only entry point for
the date-verified bare-name, disambiguation-by-committee, and
disambiguation-by-name recoveries, none of which fire when the
`NameMatcher` is absent.

**The change** flips the CLI default to `True` and reuses
`argparse.BooleanOptionalAction` so `--no-fuzzy-name-matching` is the
opt-out (useful for benchmarking exact-only behavior, or where the
legislator YAML files aren't available). The help text states the
~800-traveler cost of disabling. The underlying `--fuzzy-name-matching`
flag still parses for backward compatibility with existing scripts.

**Cost of disabling, measured on the full corpus:**

| run                       | travelers matched |
|---------------------------|-------------------|
| default (fuzzy on)        | 14,038            |
| `--no-fuzzy-name-matching`| 13,235            |

The legislator YAML files (`legislators-current.yaml`,
`legislators-historical.yaml`) are now required for a default parse --
`oft-download-legislators` fetches them. Without them, pass
`--no-fuzzy-name-matching` to fall back to exact-only matching against
`members.csv`.

### Fixed - Recognize no-expenditures forms and drop Speaker-Authorized wrapper intros

Two shapes in the corpus were producing false `LAYOUT_UNDETECTED` /
`LAYOUT_LOW_CONFIDENCE` flags on blocks that aren't parse failures at
all:

1. **No-expenditures checkbox forms.** The House Clerk's quarterly form
   includes a "Please Note: If there were no expenditures during the
   calendar quarter noted above, please check the box at right to so
   indicate and return. x" line, with the checkbox marked when a
   committee filed nothing for that quarter. These blocks have column
   headers but no data rows, so the layout detector returned `None` or
   low confidence. They are legitimate zero-expenditure filings -- the
   committee reported nothing, not a parse failure. Now flagged
   `NO_EXPENDITURES` (informational) instead of `LAYOUT_UNDETECTED` /
   `LAYOUT_LOW_CONFIDENCE`, and the report is still emitted with its
   sponsor and period intact so consumers can see the committee filed.
   `NO_EXPENDITURES` does not trigger `--llm-fallback` (there is nothing
   to extract).

2. **Speaker-Authorized wrapper intros.** Quarterly Congressional Record
   batches begin with an intro paragraph: "REPORT OF EXPENDITURES FOR
   OFFICIAL FOREIGN TRAVEL -- Reports concerning the foreign currencies
   and U.S. dollars utilized for Speaker-Authorized Official Travel
   during the first quarter of 2008, pursuant to Public Law 95-384 are
   as follows:". The segmenter split this as a table (it starts with
   the header phrase), but it's prose with no sponsor, period, or data
   -- the real tables follow. Now dropped entirely from the assembled
   reports so it doesn't produce a junk `LAYOUT_UNDETECTED` entry.

**Measured result** (full corpus, `--include-superseded`):

| flag                    | before | after | delta |
|-------------------------|--------|-------|-------|
| `NO_EXPENDITURES` (new) | 0      | 529   | +529  |
| `LAYOUT_UNDETECTED`     | 10     | 6     | -4    |
| `LAYOUT_LOW_CONFIDENCE` | 2      | 1     | -1    |
| total reports           | 3,269  | 3,267 | -2    |

529 no-expenditures filings are now correctly identified (all verified
to have zero travelers -- genuine zero-expenditure reports, not false
positives). These were previously either falsely flagged as layout
failures or sitting silently in the output with no data and no flag.
The 2 dropped reports are the Speaker-Authorized wrapper intros
(`2008q2apr23-000`, `2019q1jan10-000`). The 7 remaining
`LAYOUT_UNDETECTED`/`LAYOUT_LOW_CONFIDENCE` flags are the genuinely
hard cases: 3 tables with garbled headers but clean data rows
(recoverable with a data-row layout fallback), and 4 tables in
`2012q4dec11.txt` with fundamentally broken text extraction (data rows
wrapped across multiple lines).

### Fixed - Recognize `total = per_diem × days` source convention (`ROW_TOTAL_IS_PER_DIEM_X_DAYS`)

A segment whose declared total equals `per_diem × (departure -
arrival).days`, with only per_diem populated (transportation and other
both empty), follows a recognizable source convention: the "per_diem"
column is per-day, and the source multiplies it by the segment's
day-count to get the total, without breaking the multiplier into a
separate component. Gingrich's 1997 Asia trip is the clearest example:
Korea 3/24→3/26 (2d) per_diem=$305 total=$610, Hong Kong 3/26→3/27
(1d) per_diem=$394 total=$394, China 3/27→3/30 (3d) per_diem=$255
total=$765, Japan 3/30→4/2 (3d) per_diem=$304 total=$912. The
foreign-currency side follows the same convention (Korea 268,400 won ×
2 = 536,800 won). Transportation and other are empty because the
transport was DoD-provided, which is why no component breaks out the
extra amount.

This is NOT a double-count (the source didn't add per_diem to itself --
it multiplied by days) and NOT a mismatch (the declared total is
correct under the convention). But a 2-day segment of this shape
(per_diem=305, total=610) is arithmetic-indistinguishable from a
per_diem double-count: `delta = per_diem × (days-1) = per_diem × 1 =
per_diem`, exactly matching the double-count signal. The pre-fix
double-count recovery was misfiring on these 2-day segments,
overwriting the source's correct total (610) with just per_diem (305)
and tagging `ROW_TOTAL_DOUBLE_COUNTED`. The `double_counted` marker
only stored a boolean, so the original total was unrecoverable from
the output -- only a re-parse from source restored it.

**The fix** adds a `_per_diem_times_days_match` check that runs BEFORE
the double-count check in `validate_report`. When only per_diem is
populated, both arrival and departure dates are present, `days > 1`,
and `declared_total ≈ per_diem × days` (within tolerance), the segment
is flagged `ROW_TOTAL_IS_PER_DIEM_X_DAYS` (informational) and the
declared total is kept as-is. A foreign-currency defense-in-depth
check verifies the FX side also follows `per_diem × days` when both FX
cells are populated, ruling out a USD-side coincidence -- this caught
10 of the 81 USD-only scoping predictions as false positives (the
genuine double-count whose per_diem × days happened to hit the same
number didn't reproduce on the FX side).

**Measured result** (full corpus, `--include-superseded`):

| flag                                 | before | after | delta |
|--------------------------------------|--------|-------|-------|
| `ROW_TOTAL_IS_PER_DIEM_X_DAYS` (new) | 0      | 71    | +71   |
| `ROW_TOTAL_DOUBLE_COUNTED`           | 26     | 2     | -24   |
| `ROW_TOTAL_COMPUTED`                 | 7,931  | 7,907 | -24   |
| `ROW_SUM_MISMATCH`                   | 1,462  | 1,415 | -47   |
| `TABLE_SUM_MISMATCH`                 | 699    | 698   | -1    |

71 segments recovered: 24 were previously misclassified as
double-counts (with their correct totals overwritten and lost), 47
were previously flagged as mismatches. The 2 residual
`ROW_TOTAL_DOUBLE_COUNTED` are genuine double-counts whose FX side
didn't match the ×days pattern. The bonus `TABLE_SUM_MISMATCH` drop
came from one table whose row total had been wrongly overwritten,
making the table sum off by the same amount. The 24 recovered
segments' source totals (e.g. Gingrich Korea $610) are restored only
on a fresh re-parse -- an existing `output.json` from before this fix
still carries the wrong overwritten totals and the
`ROW_TOTAL_DOUBLE_COUNTED` flag, since the `double_counted` boolean
marker doesn't store the original value.

### Fixed - Bare-name date-verified matching and congressional honorific without period

Two residual sources of `MEMBER_UNMATCHED`:

1. **Source-omitted `Hon.` prefix.** A meaningful minority of 1990s
   reports write `Hon Charles Wilson` (no trailing period) rather than
   `Hon. Charles Wilson`. The original `get_honorific` required a
   period for every honorific, so these names were classified as bare
   and routed to the safety gate (`MEMBER_UNMATCHED`).
2. **Bare name that is actually a member.** The safety gate blocks
   bare names from fuzzy matching to prevent staffers being matched
   to members by surname. But some bare names *are* members whose
   source row just dropped the `Hon.` prefix. Without a recovery path,
   these stayed `MEMBER_UNMATCHED`.

**`get_honorific` now recognizes congressional honorifics (`Hon` /
`Rep` / `Sen`) with or without a trailing period.** Non-congressional
honorifics (`Mr` / `Ms` / `Dr` / `Rev` / `Adm` / etc.) still require
a period, because period-less forms of those (`Mr Ben McMakin`) are
rare in the corpus and overwhelmingly prefix committee staff -- promoting
a bare-looking staffer name into the non-congressional fuzzy path
produced confident-looking but wrong matches.

**`_bare_name_date_verified_match`** is a new recovery path for bare
names. It tries `HON.`-prefixed exact lookups (full name and first +
last, dropping middle initials) against `members.csv` and accepts the
match only if `NameMatcher.was_serving(bioguide, period.year, window=1)`
confirms the matched bioguide was actually serving during the report's
period (±1 year for the House's filed-next-quarter lag). Without the
date gate, a staffer named `Mark Walker` traveling in 2011 would match
`HON. MARK WALKER` -> `W000819` (who served 2015-2019).

**`NameMatcher.was_serving`** is a new helper that walks the
`(year, month) -> bioguide` index built at initialization. Returns True
if the bioguide is present in any month of any year in the ±window
range.

**Measured result** (full corpus):

| flag                                       | before  | after   | Δ      |
|--------------------------------------------|---------|---------|--------|
| `MEMBER_UNMATCHED`                          | 16,864  | 16,851  | -13    |
| `MEMBER_FUZZY_MATCHED`                      | 1,029   | 491     | -538   |
| `MEMBER_MATCHED_BY_NAME_DATE` (new)         | 0       | 312     | +312   |
| `MEMBER_MATCH_INCONCLUSIVE`                 | 238     | 231     | -7     |
| `MEMBER_DISAMBIGUATED_BY_COMMITTEE`         | 46      | 46      | 0      |
| Exact (no flag)                             | 12,937  | 13,183  | +246   |

The +312 new `MEMBER_MATCHED_BY_NAME_DATE` cases are bare names that
date-verified -- all real members (sample: `Pete Peterson`, `Bart
Gordon`, `Frank R. Wolf`, `Michael J. Kopetski`, `Gerald Solomon`,
`Gene Green`, `William Clay`, `Alcee Hastings`). The +246 exact
matches and -538 fuzzy are a quality improvement: many previously-
fuzzy matches now hit an exact lookup variant (e.g. `HON. WILLIAM
LIPINSKI` for source `Hon. William D. Lipinski`), and some previously-
fuzzy staffer names with honorifics no longer fuzzy-match (correctly
flagged `MEMBER_UNMATCHED`).

**Residual unresolved categories** (the remaining 16,851 flags) are
dominated by bare 2-token names (14,405) -- overwhelmingly committee
staff whose names have no member equivalent in `members.csv`. The
other 2,446 are bare 3+-token names (2,012) and honorific-prefixed
names that don't match any index entry (435). Recovering these would
require OCR-error correction, nickname expansion, or further
disambiguation CSV curation.

### Fixed - Recovery of empty arrival/departure cells

`ARRIVAL_CELL_EMPTY` (491 segments) and `DEPARTURE_CELL_EMPTY` (175
segments) came from three distinct source shapes, each with its own
recovery:

1. **US departure / return legs (461 segments)**: a segment whose
   country is the United States and that has only one date filled is a
   domestic departure (empty arrival -- no foreign arrival to record)
   or return (empty departure -- trip ends at home). The empty cell is
   intentionally blank in the source, not a parse failure. Reclassified
   to `US_DEPARTURE_LEG` or `US_RETURN_LEG` (informational); no date is
   inferred. US detection normalizes `United States`, `USA`, `U.S.`,
   `U.S.A.` by stripping trailing dot-fill and collapsing internal
   periods.
2. **Connecting flights (32 segments)**: a foreign segment whose
   adjacent sibling has the missing date filled (previous segment's
   departure, or next segment's arrival) -- connecting flights
   typically land and depart the same day. The missing date is inferred
   from the sibling. Reclassified to `DATE_INFERRED_FROM_SIBLING`.
3. **Same-day transits (112 segments)**: a foreign segment with no
   useful sibling but its own other date present (e.g. first segment
   with empty arrival and own departure filled). Inferred as a
   same-day arrival/departure. Reclassified to `DATE_INFERRED_SAME_DAY`.

Pairs of adjacent segments that each have one date empty (so neither
sibling has the missing date) are left flagged -- same-day inference
would manufacture a 0-day stay, and there is no other signal to use.
The 61 residual cases (9 `ARRIVAL_CELL_EMPTY`, 52 `DEPARTURE_CELL_EMPTY`)
are overwhelmingly this pattern.

`resolve_segment_dates` returns both dates as None when one is
unparseable (preserving the historical invariant tested in
`test_dates.py`), so the recovery re-resolves the present date via a
new `_resolve_single_date` helper that mirrors `resolve_segment_dates`'s
year-candidate logic but for one date in isolation. The recovery runs
as a post-pass in `validate_report` (`recover_empty_dates`), grouped
per-traveler so sibling segments are visible. Idempotent: re-derives
the recovery from the underlying empty-raw condition on each call, so
revalidation after a correction doesn't accumulate stale recovery tags.

**Measured result** (full corpus):

| flag                              | before | after | Δ     |
|-----------------------------------|--------|-------|-------|
| `ARRIVAL_CELL_EMPTY`              | 491    | 9     | -482  |
| `DEPARTURE_CELL_EMPTY`             | 175    | 52    | -123  |
| `US_DEPARTURE_LEG` (new, info)     | 0      | 398   | +398  |
| `US_RETURN_LEG` (new, info)        | 0      | 63    | +63   |
| `DATE_INFERRED_FROM_SIBLING` (new) | 0      | 32    | +32   |
| `DATE_INFERRED_SAME_DAY` (new)     | 0      | 112   | +112  |

605 of 666 cases (91%) recovered; 61 left flagged (adjacent-pairs-both-empty,
genuinely ambiguous in the source).

## [3.0.0] - 2026-07-06

### Rebuilt Parser - Layout-Aware Parsing, Costs, JSON

The v2 parser relied on one hardcoded set of fixed-width column offsets and
a start-of-table delimiter that ~6% of files never contained, silently
dropping about 12% of all records and corrupting dates in the published
CSV for dozens of files. This release replaces it with a layout-aware
pipeline that detects column boundaries per table, extracts costs (which
v2 didn't extract at all), and never silently drops a record.

**Measured result:** 62,503 travel segments extracted from the full
1994-2019 corpus (356 files), versus 55,093 in the previously-published
`travel_report_data.csv` -- and every single year now meets or exceeds
its old count (several years, and 22 whole files, previously produced
zero or badly undercounted records).

### Added

- **New `official_foreign_travel/parsing/` package**: `segmenter.py` (table
  boundaries from header lines, not the unreliable start delimiter),
  `header.py` (sponsor/period extraction), `layout.py` (per-table column
  detection, cross-checked against real data rows instead of trusted to
  label position), `costs.py` (per diem/transportation/other/total, foreign
  currency + USD, footnote markers, military-air detection), `dates.py`
  (year inference with December-to-January rollover handling), `rows.py`
  (traveler/segment extraction with continuation-line and supplemental-cost
  merging), `assemble.py` (orchestration + name matching), `validate.py`
  (arithmetic/date invariant flags), `dedup.py` (amended-report
  deduplication), `serialize.py` (JSON/CSV/JSONL), `llm_fallback.py`
  (optional).
- **`models/report.py`**: `Report`, `Sponsor`, `Period`, `Traveler`,
  `TravelSegment`, `Costs` Pydantic models -- the canonical structured
  representation (report -> sponsor/period -> travelers -> segments -> costs).
- **JSON output** (`--format json`, the new default): every report's
  sponsor, period, travelers, per-traveler segments with resolved dates and
  full cost breakdown (foreign currency + USD, footnotes, military-air
  flag), footnote definitions, and a `flags` list surfacing anything the
  deterministic pipeline couldn't fully resolve -- nothing is silently
  dropped or guessed.
- **Cost extraction**: per diem, transportation, other purposes, and total,
  each with foreign-currency and US-dollar-equivalent amounts as `Decimal`
  (serialized as strings in JSON to avoid float precision loss).
- **Amended-report deduplication** (`--include-superseded` to opt back in):
  keeps the latest publication per sponsor+period. Two same-sponsor,
  same-period reports are only merged if one is explicitly marked amended
  or their traveler rosters substantially overlap -- some committees
  (e.g. Appropriations subcommittees) file multiple genuinely distinct
  reports under the same generic sponsor label for one quarter, and an
  earlier version of this logic incorrectly discarded ~7,300 real segments
  by treating those as duplicates.
- **Validation invariants**: row-level cost-sum checks, table-total
  reconciliation, and missing-total detection, all recorded as flags rather
  than used to drop or "fix" data. Several genuine arithmetic errors in the
  1990s source documents were surfaced this way, not introduced.
- **Fuzzy name matching wired in**: exact match against `members.csv` first,
  falling back to the existing (previously unused) `NameMatcher` for names
  that don't exactly match (nicknames, missing middle initials, staff not
  in the roster). Ambiguous matches are left blank and flagged, never guessed.
- **Optional LLM fallback** (`--llm-fallback`, off by default): routes only
  tables that failed deterministic parsing to a model, and re-validates the
  result against the same arithmetic invariants before accepting it --
  amounts and dates in the deterministic path are never touched by this
  feature. Built on Simon Willison's `llm` library rather than a
  provider-specific SDK, so `--llm-model` can name any `llm`-registered
  model: an Anthropic model via `llm-anthropic` (default; needs
  `ANTHROPIC_API_KEY`) or a local/cloud Ollama model via `llm-ollama`
  (needs `OLLAMA_HOST`, and `OLLAMA_API_KEY` for Ollama's cloud models).
  Requires the `llm` extra, which is Python 3.10+ only (the rest of the
  package still supports 3.9). The extraction schema is embedded as prompt
  text rather than passed as a structured-output `schema=` constraint --
  confirmed empirically that Anthropic's constrained-decoding mode hangs
  indefinitely on schemas with as few as ~10 fields, well below what this
  extraction needs once nested.
- **`tests/fixtures/`**: six real report files spanning 1995-2019, chosen to
  cover the layout/format variants found during this rebuild, with
  regression tests locking in per-file and full-corpus coverage floors.

### Fixed

- Files with no dashed start delimiter (all of 1994-95, plus files like
  `2007q4nov13.txt`, `2012q2may29.txt`) produced zero records under v2;
  they now parse normally.
- ~39% of source files have leaked HTML/SGML markup (`<strong>`, `<SUP>`,
  even full `<html>`/`<body>` wrappers in ~140 files) that shifted
  fixed-width column alignment; markup is now stripped before parsing.
- Wrapped multi-line country lists (e.g. a delegation visiting 7 countries)
  were truncated to the first line; they're now merged.
- Supplemental cost rows ("Commercial airfare", "Delegation Expenses") were
  dropped entirely, losing real dollar amounts; they're now merged into the
  preceding segment's costs.
- Trips crossing a calendar year boundary (e.g. arrival 12/28, departure
  1/2) got the same year applied to both dates and were then rejected by a
  departure-before-arrival check; year rollover is now inferred correctly.
- `pyproject.toml`'s build backend was misconfigured
  (`setuptools.build_backend` instead of `setuptools.build_meta`), which
  broke `uv sync`/`pip install -e .` entirely.
- `cli/download_legislators.py` imported and called a `setup_logging`
  function that didn't exist, crashing `oft-download-legislators` on use.

### Changed

- **Tooling**: switched to [uv](https://docs.astral.sh/uv/) for dependency
  management (`uv sync`, `uv run ...`); `requirements.txt` and
  `requirements-dev.txt` are removed in favor of `pyproject.toml` +
  `uv.lock`. CI now uses `astral-sh/setup-uv`.
- **CLI** (`oft-parse`): now takes `--format {json,csv,jsonl}` (inferred
  from the output extension if omitted), `--include-superseded`,
  `--fuzzy-name-matching`, `--llm-fallback`, `--fail-report`. JSON is the
  new canonical format; CSV keeps the original column names/order and
  appends new cost/flag/report-id columns.
- **`travel_report_data.csv`**: regenerated. Corrected dates that were
  previously corrupted by column misalignment, removed duplicate
  amended-report rows, and added rows from the previously zero-yield files
  and years. New columns: `per_diem_usd`, `per_diem_fc`,
  `transportation_usd`, `transportation_fc`, `other_usd`, `other_fc`,
  `total_usd`, `total_fc`, `military_air`, `report_id`, `amended`, `flags`.
- **`scrapers/report_parser.py`**: kept as `ReportParser` for import-path
  compatibility, but is now a thin orchestrator over the new pipeline;
  `parse_file`/`parse_directory` return `Report` objects, not flat
  `TravelRecordInput` rows.

### Removed

- Deprecated Python 2-era scripts and their v2 wrapper shims: `scraper.py`,
  `scraper_new.py`, `scraper_report_text.py`, `scraper_report_text_new.py`,
  `name_search.py`, `name_search_test.py`, `name_search_test_new.py`, and
  the root-level `download_legislators.py` (use the `oft-*` CLI tools or
  the `official_foreign_travel` package directly).

## [2.0.0] - 2025-11-19

### Major Refactoring - Python 3 + Pydantic Upgrade

This release represents a complete rewrite of the codebase with modern Python practices.

### Added

- **Pydantic Models**: Comprehensive data validation using Pydantic v2
  - `TravelRecord`, `TravelRecordInput`, `TravelRecordOutput`
  - `Member`, `MemberInput`
  - `Committee`
  - `NameMatch`, `NameMatchResult`

- **Package Structure**: Proper Python package organization
  - `official_foreign_travel/models/` - Data models
  - `official_foreign_travel/scrapers/` - Web scraping and parsing
  - `official_foreign_travel/matchers/` - Name matching
  - `official_foreign_travel/utils/` - Utilities
  - `official_foreign_travel/cli/` - Command-line interfaces

- **Modern CLI Tools**:
  - `oft-download` - Download reports with retry logic
  - `oft-parse` - Parse reports with validation
  - `oft-test-matching` - Test name matching

- **Configuration Management**:
  - Centralized config with `Config` class
  - Environment variable support (prefix: `OFT_`)
  - `.env` file support

- **Comprehensive Logging**:
  - Structured logging throughout
  - Configurable log levels
  - Optional log file output

- **Error Handling**:
  - Retry logic with exponential backoff for HTTP requests
  - Comprehensive exception handling
  - Graceful degradation

- **Type Hints**: Full type hints throughout codebase

- **Documentation**:
  - `TECHNICAL_README.md` - Comprehensive technical documentation
  - `CHANGELOG.md` - Version history
  - Docstrings for all classes and functions

- **Development Tools**:
  - `pyproject.toml` - Modern Python packaging
  - `requirements.txt` - Dependency management
  - Updated `.gitignore` for Python projects

### Changed

- **Python 3 Compatibility**: Requires Python 3.9+
- **ReportDownloader** (formerly `scraper_report_text.py`):
  - Added retry logic with exponential backoff
  - Better error handling and logging
  - Progress tracking
  - Configurable timeout and retry attempts

- **ReportParser** (formerly `scraper.py`):
  - Uses Pydantic models for validation
  - Generator-based parsing for memory efficiency
  - Better error handling
  - Flexible input/output modes

- **NameMatcher** (formerly `name_search.py`):
  - Returns `NameMatchResult` objects with confidence flags
  - Improved caching mechanism
  - Better error messages
  - Configurable scoring thresholds

### Backward Compatibility

- Wrapper scripts provided for easy migration:
  - `scraper_report_text_new.py` - Replaces `scraper_report_text.py`
  - `scraper_new.py` - Replaces `scraper.py`
  - `name_search_test_new.py` - Replaces `name_search_test.py`

- CSV output format remains compatible with v1.0

### Migration Guide

1. Install new dependencies: `pip install -r requirements.txt`
2. Use wrapper scripts OR update to new CLI tools
3. Optional: Update code to use new Python API

### Testing

- All components tested and verified
- Parser tested on 2010 reports: ✓ 41 records parsed correctly
- Output matches original scraper

### Technical Improvements

- Memory-efficient generator-based processing
- Better Unicode handling
- Improved fixed-width column parsing
- Robust date parsing and validation
- Time-indexed legislator database for efficient matching
- Sophisticated fuzzy matching algorithm preserved

### Dependencies

- pydantic >= 2.0.0
- pydantic-settings >= 2.0.0
- requests >= 2.31.0
- beautifulsoup4 >= 4.12.0
- pyyaml >= 6.0.0
- typing-extensions >= 4.7.0

### Development Dependencies

- pytest >= 7.4.0
- black >= 23.0.0
- mypy >= 1.5.0
- ruff >= 0.0.290

---

## [1.0.0] - Original Version

Original implementation by @eric_bickel and @ryanes for Data for Democracy / ProPublica.

### Features

- Download quarterly foreign travel reports from clerk.house.gov
- Parse fixed-width text files
- Fuzzy name matching with temporal indexing
- Export to CSV format
