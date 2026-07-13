# Changelog

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

**Measured result after the fix** (same corpus, 55,992 segments -- no
drop):

| category        | before | after |
|-----------------|--------|-------|
| per_diem        | 74.3%  | 90.8% |
| transportation  | 18.0%  | 24.4% |
| other           | 6.2%   | 9.1%  |
| total           | 60.2%  | 82.2% |

Collided cost-column boundaries in the corpus fell from 571/2700 to
2/2700, and both residuals are caught by the collision guard
(`confidence = 0.5`).

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

### Changed

- `official_foreign_travel/parsing/layout.py`: `_refine_boundary` now
  snaps to the nearest position that cuts no row's token (was: nearest
  position where ≥60% of rows start a token). `_is_token_start` is
  replaced by `_cuts_token`. `detect_layout` caps confidence to 0.5
  when refined positions are not unique.
- `official_foreign_travel/parsing/validate.py`: `validate_report`
  raises `ROW_TOTAL_MISSING` on segments with components but no total,
  and clears it on re-validation (idempotent).

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
