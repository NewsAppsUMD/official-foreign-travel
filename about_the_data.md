---
title: 'Foreign Travel: About the Data'
author: '@ryanes'
date: "February 17, 2017"
output: html_document
---

If you haven't already, please review our [readme](https://github.com/Data4Democracy/official-foreign-travel/blob/master/README.md) to learn more about this project and how to contribute.

The purpose of this document is to provide links to the original sources of data that are used in the [Data for Democracy/ProPublica repository](https://github.com/Data4Democracy/official-foreign-travel) `official-foreign-travel`. It also provides descriptions and context for each dataset, including information about the data cleaning methods used. This document will be updated as new datasets are introduced.

## Dataset: Foreign Travel Reports

The original datasets can be downloaded from the [Office of the Clerk](http://clerk.house.gov/public_disc/foreign/index.aspx).

_From Derek Willis of ProPublica:_ 

>House Official Foreign travel reports, which are published quarterly by the House Clerk, are produced either by committees or delegations that are not committee-sponsored. They contain the name of each traveler, arrival and departure dates, the destination, three spending categories (per diem, transportation and other) along with a grand total of money spent (usually in US dollars).

>For committee trips, the name of the committee is in the line beginning `REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL` in the files. Those without a committee might contain `DELEGATION` or an individual's name.

>Caveats: in some cases, the destination is a continent, not a country. This usually happens for trips paid for by the Intelligence Committee. Lawmakers are typically identified by the prefix "Hon" before their names. There could be amended reports, meaning substantially duplicative information would occur. To the extent we can identify those cases, we want to retain the most recent report.

## Processing pipeline (v3)

`oft-download` pulls the raw text files from the House Clerk site into `report_text/`.
`oft-parse` (backed by the `official_foreign_travel.parsing` package) turns them into
structured data:

1. **Segmentation**: each file is split into one table per
   `REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL` header, rather than relying on a
   dashed delimiter that a meaningful fraction of files never contain. Speaker-Authorized
   quarterly summary intros ("Reports concerning the foreign currencies... pursuant to
   Public Law 95-384 are as follows:") are dropped during assembly -- they start with the
   header phrase but are prose, not a table.
2. **Header parsing**: the sponsor (committee/delegation/commission/individual/etc.) and
   reporting period are extracted from the title line. Title lines are frequently
   truncated by the source's fixed-width limit (193 chars), so the parser tries a
   chain of regexes (strict `EXPENDED BETWEEN <mon> <day> AND <mon> <day>, <year>`,
   numeric M/D, "ON <date>", partial-match with quarter-end inference, "during the
   <quarter(s)> of <year>" wrapper-summary form, tail-AND capture) and finally falls
   back to inferring a quarter-wide period from the source filename
   (`PERIOD_INFERRED_FROM_FILENAME`) when no dates survived.
3. **Layout detection**: column boundaries (name, arrival, departure, country, and the
   eight cost subcolumns) are detected per table from its own column-header block and
   cross-checked against the real data rows, instead of trusting one fixed set of offsets
   for the whole corpus -- the column positions genuinely differ across the 1994-2019 span.
   A "no expenditures" checkbox form (the House Clerk's "Please Note: If there were no
   expenditures during the calendar quarter... check the box at right... x" with the box
   marked) is recognized as a legitimate zero-expenditure filing and flagged
   `NO_EXPENDITURES` (informational) rather than `LAYOUT_UNDETECTED` -- the committee
   reported nothing, the parser didn't fail. When the header label block is missing
   entirely or too garbled to parse (e.g. PDF extraction merged the labels onto the title
   line, or the "Arrival" label was dropped), the detector falls back to deriving the
   layout from the data rows' all-space gutters -- the standard 12-column layout produces
   exactly 11 gutters, and requiring that count rejects non-standard layouts (1994-era
   5-column) and garbled extractions (0-1 gutters) so they stay `LAYOUT_UNDETECTED`
   rather than getting a wrong layout. Recovered layouts are flagged
   `LAYOUT_INFERRED_FROM_DATA` (informational) so consumers can distinguish "header parsed
   cleanly" from "header was garbled, layout recovered from data rows."
4. **Row extraction**: travelers, their travel segments, and the four cost categories
   (per diem, transportation, other purposes, total -- each with a foreign-currency and a
   US-dollar-equivalent amount) are pulled out, with wrapped country lists and supplemental
   cost rows ("Commercial airfare," "Delegation Expenses") merged in rather than dropped.
5. **Validation**: each row's costs are checked against its own declared total, and each
   table's rows are checked against its declared committee total. Three recoverable
   mismatch shapes are fixed in place rather than left flagged: rows that have cost
   components but no declared total (the total cell is dot-filled -- common in older
   reports where per diem IS the total), rows where a supplemental cost row
   ("Commercial transportation," "Delegation Expenses") was merged into the components
   after the source declared its total and the source value wasn't updated, and rows
   where the source total exceeds the component sum by exactly one component amount
   (the source double-counted that component -- e.g. 1997 Korea trips where per_diem=305
   and total=610). In all three cases the total is filled in as the sum of the non-null
   component amounts and the segment is flagged `ROW_TOTAL_COMPUTED` (informational;
   `costs.total.us_dollar.computed` marks the recovery); the double-count recovery also
   carries `ROW_TOTAL_DOUBLE_COUNTED` (with `costs.total.us_dollar.double_counted` as its
   idempotency marker). Mismatches that aren't supplement-related or double-counts are
   recorded as `ROW_SUM_MISMATCH` flags, not corrected or hidden -- some are genuine
   errors in the original documents (rounding typos, OCR errors, military-air costs
   referenced only in footnotes), and some are intentional source conventions (military
   airfare listed in transport but excluded from total, per diem reimbursed separately).
   At the table level, `TABLE_SUM_MISMATCH` is downgraded to a specific
   informational flag when the mismatch matches a known source convention or
   recovery artifact: `TABLE_SUM_ROUNDING` (delta within $5 or 1% of the
   declared total), `TABLE_SUM_TRANSPORT_EXCLUDED` (declared = per_diem +
   other; DoD transport not counted), `TABLE_SUM_COMPONENT_DELTA` (|delta|
   exactly matches one segment's per_diem/transport/other/total), and
   `TABLE_SUM_EXPLAINED_BY_SUPPLEMENT` (the segment-level supplement merge
   inflated segment totals beyond the source-declared committee total; the
   pre-supplement sum — preserved in `CostCell.source_amount` — is verified to
   match the declared total exactly before downgrading). Three further
   downgrades catch source conventions the arithmetic classifiers can't
   verify (run after them so a specific arithmetic explanation wins when
   one applies): `TABLE_SUM_CT_NO_BREAKDOWN` (the ct total is the only cell
   populated, segments have costs; the ct total was entered as a single
   number with no per-component breakdown so the delta can't be
   arithmetically verified — 48 cases), `TABLE_SUM_NO_SEG_BREAKDOWN` (the
   ct has full breakdown but no segment has any cost cells populated, or
   there are no travelers at all; the source provided a ct breakdown but
   no per-traveler breakdown — 11 cases), and
   `TABLE_SUM_CT_HAS_UNBROKEN_COMPONENT` (one ct component cell is
   populated but the corresponding segment component sum is 0, and the
   rest of the ct components match the rest of the segment components;
   the ct broke out a component — often transportation — that wasn't
   broken out per-segment — 8 cases), and
   `TABLE_SUM_SEG_HAS_UNBROKEN_COMPONENT` (the mirror: one seg component
   sum is populated but the corresponding ct component is 0, and the rest
   of the seg components matches the rest of the ct components; a seg or
   per-traveler rollup broke out a component — often per-segment other
   costs — that wasn't broken out at the committee-total level — 1 case).
   Both `*_UNBROKEN_COMPONENT` downgrades require both sides to be
   internally consistent (ct_total == ct_components and seg_total_sum ==
   seg_components) so the unbroken component amount actually equals the
   table delta; otherwise the rest-match is a coincidence and the table
   delta is unexplained. A committee total cell with a
   comma-as-decimal typo (raw ends with `,NN` or `,NNN` with no decimal
   point, e.g. `3,312,32` parsed as 331232, intended `3,312.32`) is
   recovered: the declared total is divided by 100 (`,NN`) or 1000
   (`,NNN`) and the cell is overwritten when the result matches the
   segment total sum within tolerance; the source-declared (inflated)
   value is preserved in `source_amount`, and the report is flagged
   `COMMITTEE_TOTAL_COMMA_DECIMAL_TYPO` (3 cases). A mirror of the
   segment-level `ROW_COMPONENT_COMMA_DECIMAL_TYPO` at the committee-total
   level: one or more ct component cells (per_diem / transportation /
   other) used a comma where a decimal point should be (e.g. `37,347,86`
   parsed as 3734786, intended `37,347.86` = 37347.86; `1,123,000` parsed
   as 1123000, intended `1,123.000` = 1123). The ct total cell is correct;
   recovery overwrites each typo'd component with `amount/divisor`,
   preserves the source-declared (inflated) value in `source_amount`, and
   sets `computed=True` and `comma_decimal_typo=True` on the cell. Only
   fires when fixing the typo(s) makes the ct component sum exactly equal
   the declared ct total — the exact-match gate distinguishes a real typo
   from a genuine large component (the `,NNN` shape is otherwise ambiguous
   with a normal thousands separator). The report is flagged
   `COMMITTEE_COMPONENT_COMMA_DECIMAL_TYPO` (16 cases). Mismatches that
   don't match any of these patterns stay `TABLE_SUM_MISMATCH`.
   A segment that has a declared total but no per-component breakdown at all (all three
   component cells empty) has nothing to arithmetically check -- this is a source
   convention (e.g. `2009q2may13.txt` declares only USD totals with no breakdown for any
   row), not a mismatch, and is flagged `ROW_NO_COMPONENT_BREAKDOWN` (informational) so
   downstream consumers can distinguish "source didn't provide a breakdown" from "source
   provided a breakdown that doesn't add up." The declared total is left in place and
   still counts toward the table-level `TABLE_SUM_MISMATCH` check. When the US-dollar
   component cells are empty but the foreign-currency components sum to the declared
   US-dollar total, the source entered the per-category amounts in the foreign-currency
   column even though they are US-dollar figures (e.g. Kuwait `tr.fc=742.00` with
   `tot.us=742.00`; neither KWD nor the report's other currencies are 1:1 with USD, so the
   FC column is carrying US-dollar amounts). This is downgraded to
   `ROW_BREAKDOWN_IN_FC_COLUMN` (informational) to distinguish "no breakdown at all" from
   "breakdown in the FC column." 35 downgrades; 65 segments remain as genuine
   `ROW_NO_COMPONENT_BREAKDOWN` (total-only rows with no FC breakdown either). A segment whose
   declared total equals `per_diem × (departure - arrival).days` with only per_diem
   populated (transportation and other empty -- often DoD-provided transport) is the
   per-day per_diem convention: the source multiplies per_diem by the day count to get
   the segment total without breaking the multiplier into a component. Flagged
   `ROW_TOTAL_IS_PER_DIEM_X_DAYS` (informational) and the declared total is kept as-is;
   a foreign-currency defense-in-depth check verifies the FX side follows the same
   convention. This must be detected before the double-count check, otherwise a 2-day
   segment of this shape (where `per_diem × (days-1) = per_diem`) is
   arithmetic-indistinguishable from a per_diem double-count and the correct total
   would be overwritten.
   A segment whose declared total equals the sum of per_diems across all the
   traveler's segments, with only per_diem populated across all segments (no
   transport or other), follows the "trip total in one segment" convention:
   the source fills the cumulative per_diem across the whole trip into one
   segment's total cell (the first or last, depending on the report), rather
   than a per-segment total. E.g. `1995q4dec13-005` (NORTH ATLANTIC ASSEMBLY
   France/Belgium delegation): each traveler has France (per_diem=$834.46,
   no total) and Belgium (per_diem=$606.00, total=$1,440.46), and 834.46 +
   606.00 = 1,440.46. The other segments are already `ROW_TOTAL_COMPUTED`
   (their totals are dot-filled in the source); the segment carrying the
   trip total would otherwise be flagged `ROW_SUM_MISMATCH` because its own
   per_diem doesn't sum to the trip total. Recovery overwrites that
   segment's total with its own per_diem, preserves the source trip total
   in `CostCell.source_amount`, and flags `ROW_TOTAL_IS_TRIP_TOTAL`
   (informational) alongside `ROW_TOTAL_COMPUTED`; the `costs.total.us_dollar.trip_total`
   marker is the idempotency flag for revalidation. This must be detected
   before the per_diem × days check: a 2-segment traveler with equal
   per_diems where the last segment is a 2-day stay (per_diem × 2 = 2 ×
   per_diem = trip total) is arithmetic-indistinguishable from the per_diem
   × days convention, and trip-total is the more specific shape (the
   committee total equals the sum of trip totals, not the sum of per_diem ×
   days). The recovery also preempts what would otherwise be a false
   `ROW_TOTAL_DOUBLE_COUNTED`: the trip-total segment's delta (declared −
   per_diem = sum of OTHER segments' per_diems) can coincidentally equal
   this segment's own per_diem, which the double-count check would
   misinterpret as a self-double-count.
   A segment whose component sum is within a small threshold of the
   declared total -- source rounding or a small typo, not a genuine
   arithmetic error -- is downgraded from `ROW_SUM_MISMATCH` to
   `ROW_SUM_ROUNDING` (informational). The threshold is `min($5.00, 1%
   of the declared total)`, mirroring the `TABLE_SUM_ROUNDING` rule.
   The source-declared total is kept as-is (consumers care about the
   source's stated amount). Runs after the more specific recoveries
   (supplement-merge, trip-total, per_diem × days, double-count) so
   each of those patterns stays specific; a segment that fits one of
   them is never reclassified as rounding.
   A segment with exactly one populated cost component (per_diem,
   transportation, or other) whose amount doesn't match the declared
   total (delta exceeds rounding) is a recognized source convention --
   not a genuine arithmetic error. Three shapes:
   - `ROW_TOTAL_COMMA_DECIMAL_TYPO`: a recurring source typo where the
     writer used a comma where a decimal point should be (e.g. per_diem
     `1,204.00`, total `1,204,00` which the parser reads as 120400).
     Recovery: overwrite total with the single component amount and
     preserve the source-declared (100×) total in `source_amount`; set
     `costs.total.us_dollar.comma_decimal_typo = True` as the
     idempotency marker. Two shapes qualify:
     - Single-component segment where declared_total == component × 100
       exactly (the exact-100× gate is tight enough that coincidental
       hits are negligible). 12 cases in the corpus.
     - Multi-component segment (two or more populated components) where
       the total cell's raw has the comma-as-decimal shape (no decimal
       point, comma-separated, last group 2 or 3 digits -- e.g.
       `2,345,36` → divisor 100, `7,202,000` → divisor 1000) and
       declared_total / divisor exactly equals the component sum. The
       raw-shape gate (in addition to the exact-arithmetic gate)
       distinguishes the typo from a genuine large total in the
       multi-component case, where arithmetic alone is a weaker signal.
       21 cases in the corpus.
   - `ROW_TOTAL_INCLUDES_UNBROKEN_COSTS`: positive delta (declared >
     component). The source declared a total that includes costs not
     broken out into per_diem/transport/other -- often a shared
     commercial airfare charged to the delegation but not broken out
     per-traveler. Keep the source-declared total as-is. 142 cases.
   - `ROW_TOTAL_LESS_THAN_COMPONENT`: negative delta (declared <
     component). The source declared a total less than the single
     component -- often the per_diem column shows the full rate and
     the total reflects deductions (returned per-diem, host-provided
     meals). Keep the source-declared total as-is. 75 cases.
   - `ROW_COMPONENT_COMMA_DECIMAL_TYPO`: a mirror of the total-cell
     typo, but on a component cell. One or more component cells
     (per_diem / transportation / other) used a comma where a decimal
     point should be (e.g. `749,00` parsed as 74900, `1,123,000` parsed
     as 1123000). The declared total is correct; recovery overwrites
     each typo'd component with `amount/divisor` (100 for `,NN`,
     1000 for `,NNN`), preserves the source-declared (inflated) value
     in `source_amount`, and sets `computed=True` and
     `comma_decimal_typo=True` on the cell. Only fires when fixing the
     typo(s) makes comp_sum exactly equal the declared total — the
     exact-match gate distinguishes a real typo from a genuine large
     component (the `,NNN` shape is otherwise ambiguous with a normal
     thousands separator). 35 cases in the corpus.
   The two downgrades keep the source total; only the typo recovery
   overwrites it.
   A multi-component segment (two or more populated components) whose
   declared total equals the sum of a subset of its populated components
   is another recognized source convention -- not a genuine arithmetic
   error. Mirrors the table-level `TABLE_SUM_TRANSPORT_EXCLUDED` rule:
   the source excludes certain components from the declared total (e.g.
   DoD-provided transport not counted, per_diem reimbursed separately /
   returned). Three flags, one per excluded component:
   - `ROW_TOTAL_TRANSPORT_EXCLUDED`: declared = sum of non-transport
     components (per_diem and/or other). 21 cases.
   - `ROW_TOTAL_OTHER_EXCLUDED`: declared = sum of non-other components. 25
     cases.
   - `ROW_TOTAL_PER_DIEM_EXCLUDED`: declared = sum of non-per_diem
     components. 11 cases.
   Single-component exclusions are tried first (more common, less
   ambiguous). When two populated components have equal amounts and the
   total matches either single-component subset (a genuinely ambiguous
   case), the priority order is transport-excluded, then other-excluded,
   then per_diem-excluded -- matching the table-level convention
   precedent. A segment where declared equals exactly one component
   while two others are populated gets both of those components' excluded
   flags. The source-declared total is kept as-is in all cases; this is
   informational. 57 downgrades total.
   A multi-component segment whose declared total doesn't match any
   subset sum of its populated components (delta exceeds rounding) is
   the same source convention as the single-component downgrades above,
   extended to the multi-component case. The delta-sign classifier runs
   after the subset-exclusion check (more specific), so it only sees
   segments where no subset matched. Positive delta (declared > component
   sum): the source's total includes unbroken-out costs (shared airfare
   not broken out per-traveler) → `ROW_TOTAL_INCLUDES_UNBROKEN_COSTS`.
   Negative delta (declared < component sum): the source's total
   reflects deductions (returned per-diem, host-provided meals) →
   `ROW_TOTAL_LESS_THAN_COMPONENT`. The source-declared total is kept
   as-is. 268 downgrades total (181 positive, 87 negative), leaving
   only 3 segments as genuine `ROW_SUM_MISMATCH` (negative-per_diem
   refund shapes that no current recovery covers). An additional 35
   segments that previously fell here are now recovered as
   `ROW_COMPONENT_COMMA_DECIMAL_TYPO` (component-cell typo fix — see
   above).
   A segment with a negative per_diem (the source wrote the amount
   with a trailing minus, e.g. `1,060.00-` which the parser reads as
   -1060.00), no other populated components, and a declared total
   equal to the absolute value of per_diem, is a source convention:
   the total is the absolute value of the negatively-written per_diem.
   All 3 cases are in `2017q4dec06-000` (Janice Robinson). Flagged
   `ROW_TOTAL_NEGATIVE_PER_DIEM` (informational); the source-declared
   total is kept as-is. With this, no segments remain as genuine
   `ROW_SUM_MISMATCH`.
   Empty arrival/departure cells are also recovered where the source shape
   makes the missing date unambiguous: a United States segment with one date filled is a
   domestic departure/return leg (`US_DEPARTURE_LEG` / `US_RETURN_LEG`, no date inferred),
   a foreign segment whose adjacent sibling has the missing date is inferred from that
   sibling (`DATE_INFERRED_FROM_SIBLING`), and a foreign segment with no useful sibling
   but its own other date present is inferred as a same-day arrival/departure
   (`DATE_INFERRED_SAME_DAY`). The sibling inference resolves the sibling's
   date from its raw text (not its `arrival_date`/`departure_date`, which
   is `None` when the sibling has its own empty date cell), so a chain of
   consecutive segments with empty departures but valid arrivals (e.g.
   `1998q1mar11-004` Kevin Long: Jordan/Kuwait/Bahrain, all departures
   empty, all arrivals valid) recovers each departure from the next
   segment's arrival. Only pairs of adjacent segments where the sibling's
   *relevant* date is also empty (e.g. seg i has dep_empty, seg i+1 has
   arr_empty) stay flagged `ARRIVAL_CELL_EMPTY` / `DEPARTURE_CELL_EMPTY` --
   same-day inference would manufacture a 0-day stay, and there's no
   other signal to use. A departure date of `2/29` (Feb 29) in a non-leap
   year, when the arrival is also in February, is a source data error:
   the source wrote Feb 29 but the year doesn't have one. The departure
   is inferred as March 1 of the same year (the day after Feb 28, which
   is what Feb 29 would map to in a non-leap year) and flagged
   `DEPARTURE_DATE_INFERRED_LEAP_YEAR` (informational). E.g.
   `2005q2may16-027` Trinidad delegation: arr=2/26, dep=2/29, 2005 is
   not a leap year → departure inferred as 2005-03-01. 18 cases. A 2/29
   departure in a non-leap arrival year where `dep_month < arr_month`
   (year-rollover) and `arrival.year + 1` is a leap year rolls to Feb 29
   of the next year, flagged `YEAR_ROLLOVER_APPLIED`. E.g.
   `2019q2may20-001` Engel Colombia: arr=3/28, dep=2/29, 2019 not leap,
   2020 leap → 2020-02-29. (The year-rollover check no longer requires
   the departure to be a valid date in the arrival year -- Feb 29 is the
   only date that is invalid in one year but valid in the next, so the
   guard removal only affects leap-day year-rollover cases.) A source day
   that overshoots month-end by exactly one (e.g. `9/31` → `9/30`,
   `11/31` → `11/30`, `2/30` in a leap year → `2/29`) is a typo for the
   last day of the month; the day is clamped to `days_in_month` and the
   segment is flagged `DATE_DAY_CLAMPED_TO_MONTH_END` (3 cases:
   `2006q1mar07-018` dep=9/31, `2019q1feb07-005` dep=11/31,
   `2013q2may06-003` dep=2/30 in non-leap 2013 → Feb 28, the source
   treating Feb as a 30-day month). Feb 29 in a non-leap year is excluded
   from this rule -- the existing leap-year recovery handles that shape,
   and clamping it to Feb 28 would mask the year-rollover signal. Other
   invalid departure dates (Sep 32, month 13/18, or 2/29 with arrival
   not in February and next year not a leap year) stay
   `DEPARTURE_DATE_INVALID` -- they don't fit a clean recovery pattern.
   `UNPARSEABLE_COST_CELL` is similarly recovered for three layout-residue
   patterns that are empty
   cells or footnote-marked amounts in disguise: dot-fill cells with
   trailing whitespace, backslash, or asterisk residue (e.g.
   `...........  \\` -- a dot-fill cell whose trailing backslash is a
   footnote marker that lost its digit; `...........  *` -- a dot-fill
   cell with a symbolic `*` marker, recorded as a footnote); merged
   dot-fill chains from supplement lines (e.g.
   `........... + ........... + ...........`); and footnote markers
   merged with their amount by layout residue -- `\4\1A184.00` (footnote 4,
   `1A` is a layout-extraction artifact, amount `184.00`) and `3\ -700.00`
   (incomplete `\3\` marker, amount `-700.00`). 24 segments recovered (103
   cells). A further recovery handles source line-break wraps where the
   decimal part of an amount is split onto the next source line in the
   same cost column (`12,785.` on one line, `48` on the next, in the same
   column -- the prior parser summed these as `12,785 + 48 = 12,833.00`
   instead of reading `12,785.48`), and the parallel 1-digit case
   (`234.2` + `2` -> `234.22`). When a cell carrying multiple
   space-separated symbolic asterisk markers wraps this way
   (`* * * 234.2` + `2` -> `* * * 234.22`, 2005q3jul26 Freeman Thailand)
   each asterisk is now recorded as a separate footnote reference and the
   wrapped amount parses cleanly. 676 cells recovered. The 8 remaining
   `UNPARSEABLE_COST_CELL` segments are genuine column-misalignment
   artifacts where a country name or descriptive label leaked into a cost
   cell (`Haiti`, `Air`, `(eurostar)`, `Transport`, `Taxi/Bags`,
   `Conf/Rental`, `Commercial`, `Misc. exp.`, `Kyrgyzstan + ...........`)
   -- there is no numeric amount to recover, so the flag stays.
   `TABLE_SUM_MISMATCH` is recovered for one clean sub-pattern: a
   committee total row whose TOTAL cell doesn't match its own component
   cells (per_diem + transportation + other) but whose components DO
   sum to the segment components. The TOTAL cell is wrong (layout
   digit-shift like `71,882.24` parsed instead of `171,882.24`,
   comma-decimal typo like `40,135,73` parsed as 4013573 instead of
   40135.73, or a small source typo) while the components are intact.
   The total is overwritten with the computed component sum, the
   source-declared value is preserved in `source_amount`, and the
   report is flagged `COMMITTEE_TOTAL_COMPUTED` (informational, mirrors
   the segment-level `ROW_TOTAL_COMPUTED`). 50 reports recovered; 32
   fully clean, 18 retain `TABLE_SUM_MISMATCH` because the segment row
   totals independently don't sum to the corrected total (both flags
   accurate). A second recovery handles mismatches fully explained by
   row-level source conventions: when the table-level delta exactly
   equals the sum of per-segment `total - component_sum` residuals on
   segments flagged `ROW_TOTAL_INCLUDES_UNBROKEN_COSTS`,
   `ROW_TOTAL_LESS_THAN_COMPONENT`, `ROW_NO_COMPONENT_BREAKDOWN`,
   `ROW_TOTAL_TRANSPORT_EXCLUDED` / `ROW_TOTAL_OTHER_EXCLUDED` /
   `ROW_TOTAL_PER_DIEM_EXCLUDED`, `ROW_TOTAL_NEGATIVE_PER_DIEM`, or
   `ROW_BREAKDOWN_IN_FC_COLUMN`, the mismatch is downgraded to
   `TABLE_SUM_EXPLAINED_BY_ROW_TOTAL_FLAGS` (informational). 79 reports
   downgraded. A third refinement allows negative segment totals to be
   computed (a US_RETURN_LEG with a negative per_diem is a refund, not a
   zero-expenditure row) and loosens the ct-component-vs-segment-component
   match tolerance to the rounding threshold (catches cases with
   per-segment FX rounding noise). 3 more reports recovered. 292 reports
   remain flagged `TABLE_SUM_MISMATCH` -- mismatch patterns where the ct
   components don't match the segment components (so neither side is
   unambiguously correct), or multiple inconsistencies overlap that no
   single pattern explains.
   `MISSING_COMMITTEE_TOTAL` is similarly recovered for one
   typo'd-but-recognizable total-row shape: `Committee total \1\ \2\..........`
   (2008q4dec10 Science and Technology) -- two footnote markers between the
   "total" token and the dot-fill. The detector previously accepted at most
   one `\d+\` marker, so the row was missed; it now accepts any number. 28
   reports remain flagged -- genuine source omissions (classified Intelligence
   reports, individual trips with no committee-level aggregation by
   convention, and committee/delegation reports that simply didn't print a
   total row). A second `MISSING_COMMITTEE_TOTAL` recovery infers the
   committee total from the sum of segment costs when the source omitted the
   total row but every traveler's segments carry per-component US-dollar
   amounts. The inferred `Costs` is built with `computed=True` cells (so
   downstream consumers can distinguish it from a source-declared total) and
   the report is flagged `COMMITTEE_TOTAL_INFERRED_FROM_SEGMENTS`
   (informational, mirrors `COMMITTEE_TOTAL_COMPUTED`). 27 reports
   recovered; 1 remains flagged (`2010q3sep15-016`) because no segment in
   that report has any US-dollar cost data to sum. Member matching also
   recovers two residual unmatched shapes: a congressional honorific written without a
   trailing period (`Hon Charles Wilson` is detected as `Hon.` rather than treated as a
   bare name), and a bare name whose source row omitted the `Hon.` prefix but matches a
   member who was actually serving during the report's period (`MEMBER_MATCHED_BY_NAME_DATE`,
   gated by `NameMatcher.was_serving` with a ±1-year window for filing lag). Non-
   congressional honorifics (`Mr`, `Ms`, `Dr`) still require a period to avoid promoting
   bare-looking staffer names into the fuzzy path. When fuzzy matching returns an
   ambiguous result (two candidates within the ambiguity threshold), a first-name +
   surname tiebreaker picks the candidate whose first name and surname both match the
   source (`MEMBER_DISAMBIGUATED_BY_NAME` -- e.g. `Hon. D. Payne` is ambiguous between
   Donald and Lewis Payne, but `D.` matches Donald only). The surname gate is what makes
   this safe: a staffer like `Hon. Tim Clancy` whose first name matches a member
   (`Tim Holden`) is rejected because `Clancy` != `Holden`. If both or neither
   candidate's first and last name match, the ambiguity is real and the traveler stays
   `MEMBER_MATCH_INCONCLUSIVE`. When fuzzy matching returns a below-threshold
   single top match (neither confident nor inconclusive), a maiden-name-prefix
   recovery checks if the source name is the maiden form of the member's
   married compound surname -- first name matches exactly, source surname is
   a strict prefix of the member's compound surname with a separator (space
   or hyphen) at the boundary, and the member was serving during the report's
   period (`MEMBER_MATCHED_BY_MAIDEN_NAME` -- e.g. `Hon. Stephanie Herseth`
   -> `HON. STEPHANIE HERSETH SANDLIN` (H001037), where the member married
   after the source report was filed). The strict-prefix + separator gate
   blocks same-surname staffers (`Hon. Bob Smith` vs `Bob Smith` has equal
   surnames, not a prefix) and same-prefix non-marriage names (`Hon. Bob
   Smith` vs `Bob Smithers` has no separator at the boundary, so it's a
   different name, not a marriage extension). Names that share a
   congressional name with two simultaneously-serving members (e.g. `Hon.
   Mike Rogers` -- R000572 Michigan and R000575 Alabama, both serving
   2003-2015) are disambiguated by the report's sponsoring committee via a
   hand-curated `(uppercase name, sponsor_code) -> bioguide` index
   (`member_disambiguation.csv`, producing `MEMBER_DISAMBIGUATED_BY_COMMITTEE`).
   The lookup keys strip trailing parenthetical annotations like `(AL)`
   state tags or `(Codel)` delegation notes before the index lookup, so
   `Hon. Mike Rogers (AL)` resolves the same as `Hon. Mike Rogers` when
   the sponsor code identifies the committee.
6. **Deduplication**: reports for the same sponsor and period are treated as the same
   underlying report (keeping the latest publication, per the amended-report caveat above)
   only when one is explicitly marked amended or their traveler rosters substantially
   overlap. Some committees file more than one genuinely distinct report under the same
   generic sponsor label for a single quarter (e.g. separate Appropriations subcommittee
   delegations), so sponsor+period alone isn't a reliable duplicate signal.

The canonical output is JSON (`travel_reports.json`, generated with `oft-parse report_text/
travel_reports.json`): one entry per report, each with its sponsor, period, and travelers,
each traveler with their segments, each segment with resolved dates and the full cost
breakdown. It is not committed to this repository because of its size; regenerate it
locally or via `oft-parse report_text/ output.json --include-superseded` (add
`--include-superseded` to also get amended-report duplicates that would otherwise be
excluded). `travel_report_data.csv` (generated with `oft-parse report_text/
travel_report_data.csv`) is a flattened, one-row-per-segment export in the same column
layout the pre-v3 CSV used, with additional cost/flag columns appended for backward
compatibility. Like the JSON output, it's not committed here -- regenerate it locally.

An optional `--llm-fallback` flag (requires the `llm` extra, Python 3.10+, and whichever
model's credentials `--llm-model` needs -- an Anthropic model by default, or a local/cloud
Ollama model) routes only the small number of tables that fail deterministic parsing to a
model, and re-validates its output against the same arithmetic checks before accepting it --
it is never used for the happy path, and never given a free pass on the invariants. See
[TECHNICAL_README.md](TECHNICAL_README.md) for the full CLI reference.