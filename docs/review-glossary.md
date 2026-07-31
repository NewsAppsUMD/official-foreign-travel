# Review app glossary

This is a plain-language glossary for the local review app (`oft-review`) that shows parsed
"Report of Expenditures for Official Foreign Travel" data next to the original scanned text.
It covers two things: the general terms and fields you'll see in the UI, and every colored
badge ("flag") the parser can attach to a report or a travel segment, in plain English.

If you just want to know what a specific badge means, jump to
[Badges (flags)](#badges-flags).

## How the app is organized

- **Report** — one parsed table: a single committee/delegation's filing for one time
  period. Each report has its own page (`report.html?id=...`).
- **Traveler** — one named person listed in a report — a member of Congress or a staffer —
  along with their trip(s).
- **Segment** — one leg of a traveler's trip: one arrival date, one departure date, one
  destination, and a set of costs. A traveler with a multi-stop trip has multiple segments.
- **Sponsor** — who authorized/is filing the report (a committee, commission, delegation,
  interparliamentary group, the Speaker, or an individual).
- **Period** — the date range (and, when it's a clean calendar quarter, the quarter number)
  that a report's trips fall within.
- **Committee total** — the total-cost row printed at the bottom of a table, summing every
  traveler's costs for the whole report — separate from any individual traveler's total.
- **Footnotes** — numbered notes (like `\3\`) printed below a table and referenced by a
  matching marker next to a specific cost figure (most commonly used to mark military-air
  travel — see [The "military air" concept](#the-military-air-concept) below).
- **Flag / badge** — a short, all-caps code (like `MEMBER_UNMATCHED`) the parser automatically
  attaches to a report or segment when it noticed something uncertain, inferred, corrected, or
  otherwise worth a second look. Shown in the app as a colored pill. See the full list below.
- **"no flags" badge** — the green pill shown when a report or segment has *no* flags at all —
  i.e., nothing the parser wants to call out.

### Fields you'll see in the detail view

- **report_id** — a unique ID for one parsed table, built from the source filename plus that
  table's position within the file (one source file can contain several tables/reports).
- **source_file** — the name of the original text file this report came from.
- **table_index** — this table's position (starting at 0) within its source file.
- **parse_method** — `deterministic` means the ordinary rule-based parser produced this report;
  `llm` means the rule-based parser failed and an AI model read the raw text instead (see
  [`LLM_PARSED`](#llm_parsed) below).
- **amended** — true when the source document itself marks this report as a correction/amendment
  to an earlier filing for the same sponsor and period.
- **superseded_by** — when a report is a duplicate that a later, corrected filing replaces, this
  holds the `report_id` of that newer report. Nothing is deleted; the older report just gets
  marked as replaced.
- **header_raw** — the table's full title line, exactly as printed, before it was split into
  sponsor and period.
- **signature_raw** — the best-effort text grabbed from the signature block that typically
  follows a table (e.g. "HON. JODEY C. ARRINGTON, Feb. 10, 2025.").
- **sponsor.type / sponsor.name / sponsor.code / sponsor.raw** — `type` is a category
  (committee, delegation, commission, interparliamentary, speaker, individual, or other);
  `name` is the cleaned sponsor name; `code` is a matched internal committee code (when known);
  `raw` is the sponsor text exactly as printed, before cleanup.
- **honorific** — a title like "Hon.", "Mr.", or "Dr." prefixing a traveler's name, used as a
  hint for whether that traveler is likely a member of Congress versus staff.
- **bioguide_id** — the ID number Congress's official Biographical Directory assigns to a
  specific member, linking a traveler to a real, identifiable person when matched.
- **match_confidence** — how sure the system is that a traveler was correctly matched to that
  member: `1.0` means an exact name match; lower values mean an approximate/fuzzy match.
- **arrival_raw / departure_raw** vs. **arrival_date / departure_date** — the `_raw` fields are
  the date text exactly as printed (e.g. `"3/14"`); the non-`_raw` fields are the cleaned-up,
  full calendar dates (with year filled in) computed from that text.
- **country_raw** vs. **countries** — `country_raw` is the destination text exactly as printed
  (which may list several countries together); `countries` is that text split into a clean list.
- **Cost categories: per_diem, transportation, other, total** — the four cost categories the
  House form itself asks for: daily living allowance, transportation, all other expenses, and
  the summed total.
- **foreign_currency vs. us_dollar** — within each cost category, the two parallel amounts the
  form reports: the cost as paid in local foreign currency, and its converted U.S.-dollar
  equivalent.
- **layout_confidence** — a 0–1 score for how sure the parser is that it correctly identified
  where each column starts and ends in this table.
- **layout_fingerprint** — an internal numeric signature describing a table's detected column
  positions, used to characterize and compare table layouts.
- **source_lines / "click to highlight source"** — the exact line numbers in the original raw
  text that a segment's data came from. Clicking a segment's heading highlights those lines in
  the raw-text pane on the left.

### The review workflow

- **corrections.json / edits overlay** — the file the app saves your changes to. Instead of
  editing the parsed data directly, each action (confirming a report, or editing specific
  fields) is recorded here, keyed by report ID, so it can be re-applied after a fresh re-parse.
- **Review status** — every report is in one of three states, shown as a badge on the list page:
  - `unreviewed` — the default; nobody has looked at it yet.
  - `confirmed_ok` — a reviewer looked at it and clicked **Confirm OK** without changing anything.
  - `edited` — a reviewer changed one or more fields and clicked **Save**.
- **Flagged-only toggle** — a checkbox on the list page (checked by default) that hides every
  report with no flags, showing only the subset that plausibly needs review.
- **Flag filter dropdown** — narrows the list down to reports carrying one specific flag, useful
  for working through every instance of the same issue at once.

### The "military air" concept

Some trips were flown on a U.S. military aircraft instead of a commercial airline, which means
there's no commercial dollar amount to report for that leg's transportation cost — an empty
transportation cell is *expected and documented*, not missing data. The source documents signal
this three different ways, and the parser recognizes all three:

1. A small footnote marker (like `\3\` or `(3)`) inside the transportation cost cell that, when
   looked up, reads something like "military air transportation."
2. The literal shorthand "Milair" typed directly into the cost cell in place of a number.
3. A whole separate row of text reading "Military" or "Military Air" placed below the
   traveler's row instead of a dollar figure — this is what the
   [`MILITARY_AIR_LABEL_ROW`](#military_air_label_row) flag marks.

Every individual cost cell carries a `military_air` true/false marker recording this. It isn't a
badge by itself — it's what the review app's `MILITARY_AIR` badge (below) is built from.

---

## Badges (flags)

Badges are grouped by what they're about. Each one is followed by what situation in the source
document triggers it, and what it implies about whether you should double-check the scan.
**Bold attach point** shows whether the badge appears on a whole report or on one segment.

### ⚠️ One badge is not stored data

- **MILITARY_AIR** — **segment.** This badge is *not* one of the flags below — it's generated
  live by the review app itself, not saved in the underlying data. It appears whenever a segment
  has a cost cell marked `military_air: true` but doesn't already carry a
  [`MILITARY_AIR_LABEL_ROW`](#military_air_label_row) flag — i.e., the inline-footnote case (#1
  and #2 above), which the parser tracks on the cost cell but never turned into a flag. It means
  the same thing `MILITARY_AIR_LABEL_ROW` means: this leg was flown on a military aircraft, so a
  blank transportation cost here is expected, not an error.

### Traveler identity matching

These record how (or whether) a traveler's printed name was matched to a specific member of
Congress in the official roster.

- **BARE_NAME_MEMBER_MATCH** — **report and every segment of that traveler.** A traveler with no
  title at all ("Hon.", "Dr.", etc.) was nonetheless assigned a bioguide ID. This fires on *every*
  such match, whether or not it's correct — the source gave no indication this row was a member,
  so any match here deserves a second look regardless of the confidence score. (This is what
  caught a real false match: a Speaker's-office staffer named "William Johnson" was briefly
  matched to Bill Johnson (R-OH), an unrelated former member who'd resigned seven months before
  the trip — see `MEMBER_MATCHED_BY_NAME_DATE` below.)
- **MEMBER_DISAMBIGUATED_BY_COMMITTEE** — **report.** Two or more members share the exact same
  name and served at the same time, so a hand-curated list matching sponsoring-committee
  membership was used to pick the right one. Fairly trustworthy since it's backed by curated
  data, but worth a sanity check when it matters.
- **MEMBER_DISAMBIGUATED_BY_NAME** — **report.** A name was ambiguous between two or more
  similarly-scored members, but comparing the exact first/last name in the source narrowed it to
  one. A reasonably solid match, but double-check the traveler's identity against the source.
- **MEMBER_FUZZY_MATCHED** — **report.** The printed name didn't exactly match the roster (a
  typo, abbreviation, or nickname), so it was matched by approximate name similarity plus that
  member's dates in office. Usually correct, but not an exact-text match — worth a quick glance.
- **MEMBER_MATCHED_BY_MAIDEN_NAME** — **report.** The source used a maiden name that no longer
  matches the member's current surname in the roster; confirmed by checking that person was
  actually in office at the time. Fairly reliable, but worth confirming the name change.
- **MEMBER_MATCHED_BY_NAME_DATE** — **report.** The source line was missing the usual "Hon."
  title, but the bare name still exact-matched a member who was serving within a month of the
  traveler's actual travel dates (not just "serving sometime that year" — a member who resigns in
  January would otherwise still count as serving for the rest of the year). Relies on a weaker
  signal than usual — worth a second look to rule out a staffer sharing a member's name. Always
  paired with `BARE_NAME_MEMBER_MATCH`.
- **MEMBER_MATCH_INCONCLUSIVE** — **report.** The name plausibly matches two or more members and
  there wasn't enough information to tell which. No ID was assigned — needs manual research.
- **MEMBER_UNMATCHED** — **report.** The name carried a title ("Hon.", "Dr.", etc.) — a real
  attempt was made to match it (exact, fuzzy, disambiguation, date-verified, maiden-name) — and
  every one of those attempts still failed. Genuinely worth a look: the source is telling you
  this should be a member, and the roster still doesn't have them.
- **STAFF_UNMATCHED** — **report.** The name had no title at all, which in this corpus means
  staff the overwhelming majority of the time — so no match was even attempted (fuzzy-matching
  bare names produces confident-looking but wrong results). This is the expected, default
  outcome for staff travelers, not a sign anything went wrong. Only worth checking if the name
  looks like it should belong to a member who was printed without their usual title.

### Report/table structure, sponsor, and period

These fire while the parser is figuring out the table's basic shape and the header information
(who sponsored the trip, and what dates the report covers) before it even gets to individual rows.

- **LAYOUT_INFERRED_FROM_DATA** — **report.** The table's column layout couldn't be read from a
  header row, so it was guessed from the pattern of the data rows themselves. Usually fine, but
  skim the source to make sure values didn't land in the wrong column.
- **LAYOUT_LOW_CONFIDENCE** — **report.** The parser's best guess at the column layout scored
  below its own confidence threshold — this table's row structure was unusually messy. Higher
  chance of misaligned/wrong values; check against the source text.
- **LAYOUT_UNDETECTED** — **report.** The column layout couldn't be figured out at all, so no
  traveler rows were extracted. This report is essentially empty and needs manual attention (or
  the AI fallback) to fill in from the source text.
- **NO_EXPENDITURES** — **report.** This isn't a data table — it's the standard "no expenditures
  this quarter" checkbox form. A legitimate, deliberate zero-data filing, not a parsing failure.
- **NO_TRAVELERS_EXTRACTED** — **report.** The parser found what looked like a real data table
  but extracted zero traveler rows from it. Strongly suggests something's being missed — check
  the source text.
- **PERIOD_END_DATE_INVALID** / **PERIOD_START_DATE_INVALID** — **report.** The period's end (or
  start) date couldn't be built as a real calendar date, even after trying common-typo fixes. That
  date is simply missing here and should be filled in by hand if needed.
- **PERIOD_END_DAY_CLAMPED** / **PERIOD_START_DAY_CLAMPED** — **report.** The source stated a day
  of the month that doesn't exist (e.g. "SEPT. 31" or "JUNE 31" — neither month has that many
  days), so the day was rounded down to that month's last real day. A reasonable stand-in, not
  literally what was printed — worth a glance if the exact date matters.
- **PERIOD_END_INFERRED** — **report.** The title was cut off before stating when the period
  ended, so the end date was filled in based on the calendar quarter the known start date
  belongs to. An educated guess, not a value read from the document.
- **PERIOD_INFERRED_FROM_FILENAME** — **report.** No usable period text survived in the title at
  all, so the whole period (a full calendar quarter) was guessed from the filing date encoded in
  the source filename. The weakest level of inference here — treat with real skepticism.
- **PERIOD_START_DAY_ASSUMED** — **report.** The title gave a start month (and often year) but no
  specific day, so the 1st of that month was assumed. A filler value, not a fact from the
  document — treat the exact start date as unknown.
- **PERIOD_START_MONTH_INFERRED** — **report.** The end date was found, but the text just before
  it (where the start month should be) was itself garbled, so the start month was assumed to
  match the end month. A guess to keep the period usable — check if the trip likely spanned
  more than one month.
- **PERIOD_START_MONTH_UNPARSEABLE** — **report.** The title has the expected "between ... and
  ..." phrasing, but the start-month word isn't a recognizable month name, so the end month was
  substituted as a fallback. A guess standing in for garbled text, not a confirmed fact.
- **PERIOD_UNPARSEABLE** — **report.** No strategy — including guessing from the filename —
  could extract any period at all. This report has no period information, which also weakens
  anything else that depends on it (like guessing years for travel dates). Needs manual attention.
- **PERIOD_YEAR_INFERRED_FROM_FILENAME** — **report.** The title named a start month/day but the
  year was missing, so the year was inferred from the report's House Clerk filing date (encoded
  in the source filename). A reasonable inference, still a guess — confirm if the year matters.
- **SPONSOR_EMPTY** — **report.** After stripping the standard boilerplate wording, no sponsor
  text was left at all. The sponsor field is blank and needs to be filled in by hand.
- **SPONSOR_UNCLASSIFIED** — **report.** Sponsor text was found but didn't match any of the
  known patterns (committee, commission, delegation, interparliamentary group, Speaker,
  individual), so it was labeled generically "other." The raw text is preserved and probably
  still readable — check whether it actually fits a standard category.
- **TITLE_PREFIX_UNPARSEABLE** — **report.** The title line didn't start with the standard
  "REPORT(S) OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL" boilerplate the parser expects, so it
  couldn't confirm the title's structure before pulling sponsor/period from it. Treat everything
  else parsed from this title with extra caution.

### Row and date extraction

These fire while reading individual traveler/segment rows and their arrival/departure dates.

- **ARRIVAL_CELL_EMPTY** / **DEPARTURE_CELL_EMPTY** — **segment.** The arrival (or departure)
  date field was left blank in the source. This may get explained away by
  `US_DEPARTURE_LEG`/`US_RETURN_LEG` or the `DATE_INFERRED_*` flags below — if it's still
  present on its own, that date is genuinely missing from the source.
- **ARRIVAL_DATE_UNPARSEABLE** / **DEPARTURE_DATE_UNPARSEABLE** — **segment.** The date cell had
  text in it, but it didn't look like a valid "month/day" pattern (garbled OCR, stray
  characters), so the parser gave up reading it. No date is shown — check the scan.
- **ARRIVAL_DATE_INVALID** / **DEPARTURE_DATE_INVALID** — **segment.** The month/day written
  couldn't be resolved to a plausible date at all, even after trying corrections. Both dates end
  up blank for this segment — check the scan to recover them by hand.
- **ARRIVAL_DEPARTURE_SWAPPED** — **segment.** The printed dates would put departure before
  arrival within the same month, which almost always means the two columns were transposed in
  the source row — so the parser swapped them back. A judgment call, not a literal
  transcription; worth a quick look to confirm the swap makes sense.
- **DEPARTURE_BEFORE_ARRIVAL** — **segment.** As parsed, departure comes before arrival and the
  gap spans different months, so it can't be explained as a simple swap or year rollover. Both
  dates are kept as printed but flagged as contradictory — check the scan for a misreading.
- **CONTINUATION_MERGED** — **segment.** A destination name was too long for one printed line
  and wrapped onto the next; the parser stitched that overflow text back onto the segment
  above. The country name is a two-line reconstruction, not a single verbatim cell — confirm it
  reads correctly.
- **COST_SUPPLEMENT_MERGED** — **segment.** An extra labeled line below a traveler's row (e.g.
  "Commercial airfare," "Delegation Expenses") carried an additional dollar amount that got
  folded into that segment's costs. The displayed figures are a sum of two source lines — verify
  the addition if the total looks off.
- **DATE_DAY_CLAMPED_TO_MONTH_END** — **segment.** The source wrote a day that doesn't exist in
  that month (e.g. Feb. 30th), treated as a typo and rounded down to the month's last real day.
  A guess correcting an apparent typo, not what's printed verbatim.
- **DATE_DAY_MONTH_SWAPPED** — **segment.** The date was written day/month order (European
  style, e.g. "14/10" meaning October 14) instead of month/day, and the parser corrected it. The
  month and day have been reinterpreted, not read literally — confirm if it matters.
- **DATE_INFERRED_FROM_SIBLING** — **segment.** One end of this leg's date was blank, but a
  neighboring row's date matched, so the parser filled the gap in assuming a same-day connecting
  leg. This date isn't printed on this row at all — it's inferred from a neighbor.
- **DATE_INFERRED_SAME_DAY** — **segment.** One end was blank and no neighboring row offered a
  usable date, so the parser assumed arrival and departure happened the same day. This is a
  guess, the weakest form of inference here — check against the scan.
- **DEPARTURE_DATE_INFERRED_LEAP_YEAR** — **segment.** The source listed a departure of Feb.
  29th in a non-leap year (so it can't exist), and since arrival was also in February, the
  parser inferred the traveler actually departed March 1st. A calculated substitution for an
  impossible printed date — worth double-checking.
- **MILITARY_AIR_LABEL_ROW** — **segment.** A separate row of text reading "Military Air" (with
  no dollar figures) marked this leg as flown on a military aircraft rather than commercial. See
  [The "military air" concept](#the-military-air-concept) — this reflects a real note in the
  source, not a guess.
- **NO_PERIOD_FOR_YEAR_INFERENCE** — **segment.** This row's dates couldn't be assigned a
  calendar year because the table's overall reporting period (normally used to figure out which
  year "3/14" means) itself couldn't be read. No dates are shown; points to a bigger problem
  with the table's header worth investigating.
- **STAFFDEL_GROUP_EXPENSE** — **segment.** The row's name is "STAFFDEL Expense(s)" — a real
  arrival/departure date and country matching the delegation's leg, but the cost is shared across
  the whole staff delegation, not any one traveler. Kept as its own record (nothing is dropped)
  but flagged so it isn't mistaken for a person when counting travelers.
- **SEGMENT_WITHOUT_TRAVELER_NAME** — **report** (recorded at the table level, though it points
  at one specific row). A cost row appeared in the source with no traveler's name the parser
  could find or infer. Dates/costs on that row may be accurate, but it's not known whose trip it
  is — check the scan to identify the missing name.
- **UNPARSEABLE_COST_CELL** — **segment** (one specific cost cell). A dollar-amount cell had
  non-blank text that couldn't be interpreted as a number after cleanup (stray symbols, OCR
  garbling). That figure shows as missing rather than a value — check the scan.
- **US_DEPARTURE_LEG** — **segment.** The arrival-date field was intentionally blank because the
  leg began within the U.S. (domestic), not a foreign arrival — a normal, expected gap, not
  missing data.
- **US_RETURN_LEG** — **segment.** The departure-date field was intentionally blank because the
  leg ended with the traveler returning to the U.S. — a normal, expected gap.
- **YEAR_ROLLOVER_APPLIED** — **segment.** Arrival and departure months go backwards (e.g.
  arriving in December, departing in January), recognized as a trip crossing New Year's, so the
  departure was assigned to the following year. Calculated rather than printed — a reasonable
  but inferred adjustment.

### Cost math within a row

These check whether a segment's own per-diem/transportation/other line items add up to its
own printed total.

- **ROW_SUM_MISMATCH** — **segment.** The line items don't add up to the printed total, and the
  gap doesn't fit any of the recognized, forgivable patterns below (rounding, exclusions,
  typos). The printed total is kept, but this is the plainest "something doesn't add up" flag —
  check the scan closely.
- **ROW_SUM_ROUNDING** — **segment.** Line items add up to slightly more/less than the total —
  a gap small enough (the smaller of $5 or 1%) to look like ordinary rounding noise. Usually
  safe to trust.
- **ROW_TOTAL_COMPUTED** — **segment.** The printed total wasn't reliably usable as-is (blank,
  or overridden by one of the fixes below), so the parser calculated/substituted it from the
  line items. The number shown was computed by software, not read off the page — verify it.
- **ROW_TOTAL_COMMA_DECIMAL_TYPO** / **ROW_COMPONENT_COMMA_DECIMAL_TYPO** — **segment.** A total
  (or a line item) was printed with a comma where a decimal point belonged (e.g. "1,204,00"
  misread as $120,400 instead of $1,204.00), inflating it ~100–1000x. The parser divided out the
  error and kept the original misread figure for reference — confirm the correction against the
  scan.
- **ROW_TOTAL_DOUBLE_COUNTED** — **segment** (always with `ROW_TOTAL_COMPUTED`). The printed
  total appears to have added one cost category in twice — it exceeds the line-item sum by
  exactly one of those amounts. Replaced with the corrected sum — confirm the interpretation.
- **ROW_TOTAL_IS_TRIP_TOTAL** — **segment** (always with `ROW_TOTAL_COMPUTED`). A multi-segment
  traveler had one segment's "total" field actually hold the combined per-diem for the *whole*
  trip, not just that segment. Replaced with just this segment's own per-diem — confirm the
  recovered split against the source.
- **ROW_TOTAL_IS_PER_DIEM_X_DAYS** — **segment.** Only a per-diem rate is listed, and the
  printed total exactly equals that rate times the number of days — a recognizable convention
  (often when transportation was government-provided and not itemized). Informational only.
- **ROW_TOTAL_NEGATIVE_PER_DIEM** — **segment.** The per-diem figure was printed with a trailing
  minus sign (e.g. "1,060.00-"), read as negative, while the total is the positive version with
  no other line items. A recognized formatting convention — just confirm it isn't really a
  refund/credit.
- **ROW_BREAKDOWN_IN_FC_COLUMN** — **segment.** A total is shown with no dollar-column
  breakdown, but the *foreign-currency* columns happen to add up to that dollar total —
  suggesting dollar figures were typed into the wrong column. The total is kept as printed —
  check whether those "foreign currency" numbers are really misplaced dollar amounts.
- **ROW_NO_COMPONENT_BREAKDOWN** — **segment.** A total is shown but none of the per-diem/
  transportation/other line items were filled in at all, so there's nothing to check the total
  against. It's trusted on faith alone here.
- **ROW_TOTAL_INCLUDES_UNBROKEN_COSTS** — **segment.** The total is larger than its own line
  items add up to — commonly because it includes some cost (like shared group airfare) not
  broken out per-traveler. A known reporting pattern, not an error; the total can generally be
  trusted, but the line-item breakdown here is incomplete.
- **ROW_TOTAL_LESS_THAN_COMPONENT** — **segment.** The total is *smaller* than its line items add
  up to — commonly a deduction (returned per-diem, host-provided meals) not shown in the line
  items. A known pattern, not an error.
- **ROW_TOTAL_OTHER_EXCLUDED** / **ROW_TOTAL_PER_DIEM_EXCLUDED** / **ROW_TOTAL_TRANSPORT_EXCLUDED**
  — **segment.** The total only reconciles with the line items once "other" (or per-diem, or
  transportation) is left out — meaning that category was accounted for separately from this
  total (transportation exclusion is often because it was government/military-provided).
  Informational; the total is kept as printed.

### Table-level cost math

These compare the committee's stated grand total for the whole table against the sum of every
segment's total.

- **TABLE_SUM_MISMATCH** — **report.** The sum of every segment's total doesn't match the
  committee's printed grand total, and the gap doesn't fit any of the recognized patterns below.
  This is the plainest "the two totals genuinely disagree" flag — check for a missed row or a
  garbled total figure.
- **TABLE_SUM_ROUNDING** — **report.** The two totals differ by only a small amount (the smaller
  of $5 or 1%) — ordinary rounding noise, not a real discrepancy.
- **TABLE_SUM_COMPONENT_DELTA** — **report.** The gap between the two totals exactly matches one
  specific line-item amount somewhere in the segments, suggesting the committee total
  intentionally excluded (or double-counted) that one component. Informational.
- **TABLE_SUM_TRANSPORT_EXCLUDED** — **report.** The committee's total matches the segment sum
  only if transportation is left out entirely — the committee total, by convention, doesn't
  include transportation (often because it was government-provided).
- **TABLE_SUM_EXPLAINED_BY_SUPPLEMENT** — **report.** Some segment totals were bumped up by a
  later "supplement" line merged in after the committee had already printed its total; backing
  those additions out makes the committee's original total match. The committee total is still
  considered valid for the pre-supplement period.
- **TABLE_SUM_EXPLAINED_BY_ROW_TOTAL_FLAGS** — **report.** The gap between the two totals is
  fully accounted for by row-level quirks already flagged on individual segments (unbroken
  costs, exclusions, etc.) — not a new problem, just the cumulative effect of already-explained
  rows.
- **TABLE_SUM_CT_NO_BREAKDOWN** — **report.** The committee's total is a single lump number with
  no per-diem/transportation/other breakdown, even though individual segments do have costs, so
  there's no way to verify it arithmetically against its own components.
- **TABLE_SUM_NO_SEG_BREAKDOWN** — **report.** The committee's total has a full breakdown, but
  not a single segment has any cost figures at all — so it can't be checked against per-traveler
  detail because none exists.
- **TABLE_SUM_CT_HAS_UNBROKEN_COMPONENT** — **report.** The committee table breaks out a cost
  category (often transportation) as nonzero, but no segment shows any amount in that category —
  and that one category fully explains the gap. That category was apparently tallied only at the
  committee level, not per traveler.
- **TABLE_SUM_SEG_HAS_UNBROKEN_COMPONENT** — **report.** The mirror image: segments show a
  nonzero amount in some category, but the committee table shows zero for it, and that gap fully
  explains the mismatch. That category was tallied per-segment but never rolled up into the
  committee's breakdown.
- **COMMITTEE_TOTAL_INFERRED_FROM_SEGMENTS** — **report.** The table never had a committee-total
  row at all, so one was built by adding up every segment's cost. Entirely synthetic — nothing
  was printed for it in the source.
- **COMMITTEE_TOTAL_COMPUTED** — **report.** The printed committee total didn't match its own
  subtotals, so it was replaced with the sum of the subtotals. No longer the number printed in
  the document — verify against the scan.
- **COMMITTEE_TOTAL_COMMA_DECIMAL_TYPO** / **COMMITTEE_COMPONENT_COMMA_DECIMAL_TYPO** —
  **report.** The committee's grand total (or one of its category subtotals) had the same
  comma-for-decimal-point misprint described under the row-level typo flags above. Corrected;
  the original misread figure is kept for reference.
- **MISSING_COMMITTEE_TOTAL** — **report.** No committee-total row exists, and no individual
  segment has any cost figures either — there's nothing from which to even estimate a total.
  Check the source pages for a missed total line.

### Automated (AI) fallback parsing

- **LLM_PARSED** — **report.** The rule-based parser failed badly enough on this report that an
  AI model read the raw text and extracted the data instead. AI-extracted data is inherently
  less certain than rule-based parsing, even after passing the same consistency checks — verify
  against the original scan.
- **LLM_UNVERIFIED** — **report.** The report failed normal parsing, was sent to the AI fallback,
  and the AI's attempt *also* failed basic consistency checks, so the original failed extraction
  was kept instead. This report is known to be broken and needs manual correction from the
  source text.

### Review actions (things a human did)

- **HUMAN_CONFIRMED** — **report.** A reviewer clicked **Confirm OK** in the app without changing
  anything — a person has vouched for the data as-is.
- **MANUALLY_CORRECTED** — **report.** A reviewer edited one or more fields by hand and saved.
  These fields reflect direct human correction and can generally be trusted more than the
  original automated extraction.

---

*Generated for the `oft-review` local review app. If a flag you see in the app isn't listed
here, check `official_foreign_travel/parsing/` for where it's appended in code — the file and
function name will usually make the trigger condition clear even without a comment.*
