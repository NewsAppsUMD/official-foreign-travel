# Official Foreign Travel - Technical Documentation

## Version 3.0 - Layout-Aware Parser, Costs, JSON

**Latest update:** The parser has been rebuilt. The v2 parser used one hardcoded set of
fixed-width column offsets and a start-of-table delimiter that a meaningful fraction of
files never contained, silently dropping about 12% of all records and never extracting
costs at all. v3 detects column layout per table, extracts full cost data, deduplicates
amended reports, and outputs JSON by default (CSV is still available for compatibility).

See [CHANGELOG.md](CHANGELOG.md) for the full list of changes, and
[about_the_data.md](about_the_data.md) for a description of the parsing pipeline and the
source data's quirks.

### Quick Links
- **New Users**: See [Installation](#installation) below
- **Data pipeline**: See [about_the_data.md](about_the_data.md)
- **Contributing**: See [Development](#development) section

## Architecture

```
official_foreign_travel/
├── parsing/                    # The parser pipeline
│   ├── segmenter.py           # Split a file into per-table blocks
│   ├── header.py              # Extract sponsor + reporting period from a table's title
│   ├── layout.py              # Detect column boundaries per table
│   ├── costs.py                # Parse cost cells (amounts, footnotes, military-air)
│   ├── dates.py                # Resolve row dates against the table's period
│   ├── rows.py                  # Extract travelers/segments, merge continuations
│   ├── assemble.py             # Wire everything together into Report objects
│   ├── validate.py             # Arithmetic/date invariant checks (flags, never drops)
│   ├── dedup.py                 # Amended-report deduplication
│   ├── serialize.py            # JSON / CSV / JSONL output
│   └── llm_fallback.py         # Optional Anthropic-API repair pass (off by default)
├── models/
│   ├── report.py               # Report -> Sponsor/Period/Traveler/TravelSegment/Costs
│   ├── travel.py, member.py, committee.py, match.py   # Legacy flat models, still used
├── scrapers/
│   ├── report_downloader.py    # Download reports from clerk.house.gov
│   └── report_parser.py        # Thin orchestrator over parsing/ (kept for import compat)
├── matchers/
│   └── name_matcher.py         # Fuzzy name matching with temporal indexing
├── utils/
│   ├── config.py, logging.py, text.py
└── cli/
    ├── download.py, parse.py, test_matching.py, download_legislators.py
```

## Installation

### Requirements

- Python 3.9 or higher
- [uv](https://docs.astral.sh/uv/)

### Install Dependencies

```bash
uv sync --all-extras
```

This creates a `.venv` and installs the command-line tools: `oft-download`, `oft-parse`,
`oft-test-matching`, `oft-download-legislators`. Prefix all commands below with `uv run`
(e.g. `uv run oft-parse ...`), or activate `.venv` first.

The optional `llm` extra (Simon Willison's [`llm`](https://llm.datasette.io/) library,
plus the `llm-anthropic` and `llm-ollama` plugins) is installed by `--all-extras`; omit
it (`uv sync`) if you don't plan to use `--llm-fallback`. This extra requires Python
3.10+ (the rest of the package still supports 3.9) and is skipped automatically on 3.9.

## Usage

### Download Reports

```bash
oft-download
oft-download --start-year 2015 --end-year 2020 --log-level DEBUG
```

### Parse Reports

```bash
# JSON is the default (canonical) format, inferred from the .json extension
oft-parse report_text/ travel_reports.json

# Flat CSV, one row per traveler segment
oft-parse report_text/ travel_report_data.csv

# Single file
oft-parse report_text/2019q1jan29.txt output.json

# Force a format regardless of extension
oft-parse report_text/ output.txt --format jsonl

# Include amended-report duplicates that were superseded by a later publication
oft-parse report_text/ output.json --include-superseded

# Fall back to fuzzy name matching (requires legislator YAML data -- see below)
oft-parse report_text/ output.json --fuzzy-name-matching

# Route tables that fail deterministic parsing to a model, via `llm` (off by default)
export ANTHROPIC_API_KEY=...
oft-parse report_text/ output.json --llm-fallback --fail-report unresolved.json

# Or target a different model -- any `llm`-registered id works, e.g. an Ollama model
export OLLAMA_HOST=https://ollama.com OLLAMA_API_KEY=...
oft-parse report_text/ output.json --llm-fallback --llm-model llama3.1:70b-cloud

# Merge in human corrections from a prior review session (see "Reviewing Flagged
# Reports" below) before writing output
oft-parse report_text/ output.json --apply-corrections corrections.json
```

### Reviewing Flagged Reports

Any table the pipeline couldn't fully resolve keeps a `flags` entry rather than being
dropped or guessed at -- `oft-review` is a local, zero-dependency web UI for working
through that flagged subset by hand, side by side with the original source text.

```bash
oft-parse report_text/ output.json
oft-review report_text/ output.json --corrections corrections.json
```

Then open http://127.0.0.1:8765/ in a browser. The list view shows every flagged
report with its flags, traveler count, and review status; clicking one opens a
side-by-side view -- the original fixed-width table text on the left, an editable form
of the extracted fields on the right. Clicking a segment's heading highlights the source
lines it was parsed from. **Save** records any edits; **Confirm OK** marks a report
reviewed with no changes needed. Both write to the `--corrections` file (default
`corrections.json`), never to `output.json` itself, so a review session is never lost to
a re-parse.

To fold those corrections into a fresh parse of the whole corpus:

```bash
oft-parse report_text/ output.json --apply-corrections corrections.json
```

Corrected reports are tagged `MANUALLY_CORRECTED` (edited) or `HUMAN_CONFIRMED`
(confirmed with no changes) and re-validated against the same arithmetic checks as
everything else. Because corrections live in their own file keyed by `report_id`, this
works no matter how many times the corpus gets re-parsed from scratch -- a correction is
never tied to one specific run's output.

`--port`/`--host` control where the server binds (default `127.0.0.1:8765`; it only ever
binds to localhost). `oft-review` binds only to `127.0.0.1` and has no authentication --
it's meant for one local reviewer, not to be exposed on a network.

### Test Name Matching

```bash
oft-test-matching report_text/ matching_issues.txt
oft-test-matching report_text/ issues.txt --cache my_cache.pickle
```

### Download Legislator Data

Fetches `legislators-{current,historical}.yaml` and `committees-{current,historical}.yaml`
from [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators)
-- the legislator files are required for `--fuzzy-name-matching`; both are needed to
regenerate `members.csv`/`committees.csv` (below).

```bash
oft-download-legislators
oft-download-legislators --output-dir data/
oft-download-legislators --current-only
```

### Regenerate `members.csv`/`committees.csv`

`members.csv` and `committees.csv` are the exact-match lookups `oft-parse` tries before
falling back to fuzzy name matching (members) or leaving a report's `sponsor.code` unset
(committees, which has no fuzzy fallback at all). Committee names in particular change
across Congresses (e.g. "International Relations" -> "Foreign Affairs"), so these files
need periodic regeneration from the same congress-legislators source used above:

```bash
oft-download-legislators
oft-generate-reference-data
```

This overwrites `members.csv`/`committees.csv` in the current directory (override with
`--members-csv`/`--committees-csv`, and point at the 4 downloaded YAML files with
`--legislators-current`/`--legislators-historical`/`--committees-current`/
`--committees-historical` if they're not in the current directory). It reports how many
people/committees were considered, plus any exact-name collisions it had to resolve --
either by dropping an ambiguous name shared by two different people (never guesses), or
by preferring whichever YAML file was listed first (current before historical) when two
committees land on the same name.

### Python API

```python
from pathlib import Path
from official_foreign_travel.parsing.assemble import assemble_directory, load_name_index
from official_foreign_travel.parsing.dedup import dedup_reports
from official_foreign_travel.parsing.validate import validate_reports
from official_foreign_travel.parsing.serialize import write_json

member_index = load_name_index(Path("members.csv"))
committee_index = load_name_index(Path("committees.csv"))

reports = list(assemble_directory(Path("report_text"), member_index, committee_index))
validate_reports(reports)
dedup_reports(reports)
write_json(reports, Path("output.json"))
```

Or via the `ReportParser` orchestrator:

```python
from pathlib import Path
from official_foreign_travel.scrapers import ReportParser

parser = ReportParser()
reports = parser.parse_and_finalize(Path("report_text"))  # validated + deduplicated
parser.write_json(reports, Path("output.json"))
```

## Data Model

`Report` is the top-level unit (one per table): a sponsor, a reporting period, and a list
of travelers, each with their travel segments.

```python
class Report(BaseModel):
    report_id: str                 # "<source-file-stem>-<table-index>"
    source_file: str
    table_index: int
    amended: bool
    superseded_by: Optional[str]   # set if a later/duplicate report replaces this one
    parse_method: Literal["deterministic", "llm"]
    sponsor: Sponsor                # type, name, code, raw
    period: Optional[Period]        # start, end, year, quarter
    travelers: List[Traveler]
    committee_total: Optional[Costs]
    footnotes: Dict[str, str]
    flags: List[str]               # anything the pipeline couldn't fully resolve

class Traveler(BaseModel):
    name: str
    honorific: Optional[str]
    bioguide_id: Optional[str]     # exact match, then fuzzy fallback; else None + flagged
    match_confidence: Optional[float]
    segments: List[TravelSegment]

class TravelSegment(BaseModel):
    arrival_date: Optional[date]
    departure_date: Optional[date]
    country_raw: str
    countries: List[str]           # best-effort split of country_raw
    costs: Costs                   # per_diem/transportation/other/total, each FC + USD
    flags: List[str]

class Costs(BaseModel):
    per_diem: CostGroup
    transportation: CostGroup
    other: CostGroup
    total: CostGroup

class CostGroup(BaseModel):
    foreign_currency: CostCell
    us_dollar: CostCell

class CostCell(BaseModel):
    amount: Optional[Decimal]      # serialized as a string in JSON
    footnotes: List[str]
    military_air: bool
```

Nothing is ever silently dropped: a row that can't be fully resolved (an unparseable date,
a cost cell that doesn't look like a number, a table whose row costs don't sum to its
declared total) is kept with the relevant flag set, so it can be reviewed rather than lost.

## Configuration

Configuration can be set via:

1. **Environment variables** (prefix with `OFT_`):
   ```bash
   export OFT_START_YEAR=2015
   export OFT_END_YEAR=2020
   ```

2. **.env file**:
   ```
   OFT_START_YEAR=2015
   OFT_END_YEAR=2020
   OFT_LOG_LEVEL=DEBUG
   ```

3. **Python code**:
   ```python
   from official_foreign_travel.utils.config import Config
   config = Config(start_year=2015, end_year=2020)
   ```

### Configuration Options

- `data_dir`: Data directory (default: `data`)
- `report_text_dir`: Downloaded reports directory (default: `report_text`)
- `output_dir`: Output directory (default: `output`)
- `members_csv`: Members CSV file (default: `members.csv`)
- `committees_csv`: Committees CSV file (default: `committees.csv`)
- `legislators_current_yaml`: Current legislators YAML (default: `legislators-current.yaml`)
- `legislators_historical_yaml`: Historical legislators YAML (default: `legislators-historical.yaml`)
- `base_url`: House Clerk website base URL
- `start_year`: Start year for scraping (default: 1994)
- `end_year`: End year for scraping (default: 2020)
- `request_timeout`: HTTP timeout in seconds (default: 30)
- `retry_attempts`: Number of retry attempts (default: 3)
- `retry_delay`: Delay between retries (default: 2.0)
- `min_match_score`: Minimum score for a confident fuzzy name match (default: 3.0)
- `ambiguity_threshold`: Threshold for ambiguous fuzzy matches (default: 1.1)
- `log_level`: Logging level (default: INFO)
- `log_file`: Optional log file path

## Development

### Run Tests

```bash
uv run pytest tests/
```

`tests/test_corpus_regression.py` runs the full parser against `report_text/` and asserts
it never parses fewer records than the legacy parser did per year (`tests/baseline_counts.json`);
it's skipped automatically if `report_text/` isn't present.

### Code Formatting, Type Checking, Linting

```bash
uv run black official_foreign_travel/
uv run mypy official_foreign_travel/
uv run ruff check official_foreign_travel/
```

## Troubleshooting

### "YAML file not found" (only relevant to `--fuzzy-name-matching`)

```bash
oft-download-legislators
```

Or download manually from:
- https://raw.githubusercontent.com/unitedstates/congress-legislators/master/legislators-current.yaml
- https://raw.githubusercontent.com/unitedstates/congress-legislators/master/legislators-historical.yaml

Place in the project root, or point `OFT_LEGISLATORS_CURRENT_YAML` /
`OFT_LEGISLATORS_HISTORICAL_YAML` at them.

### "Members CSV not found"

Ensure `members.csv` and `committees.csv` are in the project root or specify paths via
`--members-csv`/`--committees-csv` or config.

### A table has a `LAYOUT_LOW_CONFIDENCE` or `LAYOUT_UNDETECTED` flag

The table's column-header block didn't match cleanly (garbled OCR, a genuinely unusual
layout). Either accept the gap, run with `--llm-fallback` (optionally `--llm-model` to
pick a different model) to route just those tables to a model for a second attempt (still
re-validated against the same arithmetic checks before being accepted), or review it by
hand with `oft-review` (see "Reviewing Flagged Reports" above). Larger tables can take a
few minutes per table with `--llm-fallback` -- this is normal generation time, not a hang.

## License

MIT License (inherited from original project)

## Credits

- Original authors: @eric_bickel, @ryanes
- Data for Democracy / ProPublica collaboration
- Python 3 + Pydantic upgrade (v2.0): Claude Code
- Layout-aware parser rebuild (v3.0): Claude Code

## Data Sources

- House Clerk foreign travel reports: http://clerk.house.gov/public_disc/foreign/
- Legislator data: https://github.com/unitedstates/congress-legislators
