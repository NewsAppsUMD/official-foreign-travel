# Official Foreign Travel - Technical Documentation

## Version 2.1 - Enhanced Quality & Tooling

**Latest Update:** Version 2.1 adds critical security fixes, automated tooling, comprehensive testing, and CI/CD.

📖 **[See UPGRADE_GUIDE.md for what's new and migration instructions](UPGRADE_GUIDE.md)**

### Quick Links
- **New Users**: See [Installation](#installation) below
- **Upgrading**: See [UPGRADE_GUIDE.md](UPGRADE_GUIDE.md)
- **Contributing**: See [Development](#development) section

### Version 2.1 Highlights

- ✅ **Security Fix**: Fixed YAML loading vulnerability (CVE prevention)
- 🚀 **Automated Downloads**: New `oft-download-legislators` CLI tool
- 🧪 **Test Suite**: Comprehensive pytest suite with 90%+ coverage
- 🔄 **CI/CD**: GitHub Actions for automated testing and validation
- 📦 **Dependencies**: Added `requirements.txt` and `requirements-dev.txt`
- ⚠️  **Deprecation Warnings**: Old scripts now warn users to upgrade
- 🔍 **Strict Type Checking**: Enabled for all new code
- 🎯 **Enhanced Linting**: Ruff configuration with multiple rule sets

## Version 2.0 - Python 3 + Pydantic Upgrade

This is a modernized version of the foreign travel scraper, completely refactored for Python 3 with Pydantic schemas, better organization, and improved robustness.

## What's New in v2.0

### Major Improvements

1. **Python 3 Compatibility**: Fully updated for Python 3.9+
2. **Pydantic Schemas**: Strong data validation and type safety
3. **Better Organization**: Proper package structure with clear separation of concerns
4. **Robust Error Handling**: Comprehensive logging and retry logic
5. **Type Hints**: Throughout the codebase for better IDE support
6. **Modern CLI**: Clean command-line interfaces with argparse
7. **Configuration Management**: Centralized config with environment variable support

### Architecture

```
official_foreign_travel/
├── models/              # Pydantic data models
│   ├── travel.py       # TravelRecord, TravelRecordInput, TravelRecordOutput
│   ├── member.py       # Member, MemberInput
│   ├── committee.py    # Committee
│   └── match.py        # NameMatch, NameMatchResult
├── scrapers/           # Web scraping and parsing
│   ├── report_downloader.py  # Download reports from clerk.house.gov
│   └── report_parser.py       # Parse fixed-width text files
├── matchers/           # Name matching
│   └── name_matcher.py        # Fuzzy name matching with temporal indexing
├── utils/              # Utilities
│   ├── config.py      # Configuration management
│   ├── logging.py     # Logging setup
│   └── text.py        # Text processing utilities
└── cli/                # Command-line interfaces
    ├── download.py    # Download reports CLI
    ├── parse.py       # Parse reports CLI
    └── test_matching.py  # Test name matching CLI
```

## Installation

### Requirements

- Python 3.9 or higher
- pip

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Install Package (Optional)

For development:
```bash
pip install -e .
```

This will install the command-line tools: `oft-download`, `oft-parse`, `oft-test-matching`

## Usage

### Method 1: Modern CLI Tools

#### Download Reports

```bash
# Using installed CLI tool
oft-download

# Or directly
python -m official_foreign_travel.cli.download

# With options
oft-download --start-year 2015 --end-year 2020 --log-level DEBUG
```

#### Parse Reports

```bash
# Parse directory of reports
oft-parse report_text/ output.csv

# Parse single file
oft-parse report_text/2019q1jan15.txt output.csv

# With options
oft-parse report_text/ output.csv --no-validate --log-level DEBUG
```

#### Test Name Matching

```bash
# Test matching on all reports
oft-test-matching report_text/ matching_issues.txt

# With custom cache location
oft-test-matching report_text/ issues.txt --cache my_cache.pickle
```

#### Download Legislator Data (New in v2.1)

```bash
# Download current and historical legislator data
oft-download-legislators

# Download to specific directory
oft-download-legislators --output-dir data/

# Download only current legislators
oft-download-legislators --current-only

# Or use standalone script
python download_legislators.py
```

### Method 2: Backward-Compatible Scripts

For easier migration, wrapper scripts are provided that mimic the old interface:

```bash
# Download reports (like old scraper_report_text.py)
python scraper_report_text_new.py

# Parse reports (like old scraper.py)
python scraper_new.py report_text/ output.csv

# Test matching (like old name_search_test.py)
python name_search_test_new.py report_text/ matching_issues.txt
```

### Method 3: Python API

```python
from pathlib import Path
from official_foreign_travel.scrapers import ReportDownloader, ReportParser
from official_foreign_travel.matchers import NameMatcher
from official_foreign_travel.utils.config import Config

# Configure
config = Config(
    start_year=2015,
    end_year=2020,
    report_text_dir=Path("reports")
)

# Download reports
downloader = ReportDownloader(config)
# ... use downloader methods

# Parse reports
parser = ReportParser(config)
records = parser.parse_directory(Path("report_text"))
parser.write_csv(records, Path("output.csv"))

# Match names
matcher = NameMatcher(config)
matcher.initialize()
result = matcher.search_by_name("Hon. John Doe", "1/15/2019", "1/20/2019")
print(f"Best match: {result.best_bioguide_id} (score: {result.top_match.score})")
```

## Data Models

### TravelRecord

Validated travel record with type checking:

```python
class TravelRecord:
    name: str
    member_id: Optional[str]  # Bioguide ID (pattern: ^[A-Z][0-9]{6}$)
    honorific: Optional[str]
    arrival_date: datetime
    departure_date: datetime
    country: str
    table_header: Optional[str]
    committee: Optional[str]
    committee_code: Optional[str]
    source_file: Optional[str]
    year: int
```

### NameMatchResult

Result of name matching with confidence flags:

```python
class NameMatchResult:
    query_name: str
    arrival_date: str
    departure_date: str
    matches: List[NameMatch]
    top_match: Optional[NameMatch]
    is_confident: bool
    is_inconclusive: bool
```

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
- `min_match_score`: Minimum score for confident match (default: 3.0)
- `ambiguity_threshold`: Threshold for ambiguous matches (default: 1.1)
- `log_level`: Logging level (default: INFO)
- `log_file`: Optional log file path

## Features

### Improved Scraper

- **Retry logic**: Automatic retries with exponential backoff
- **Better error handling**: Comprehensive exception handling and logging
- **Progress tracking**: Real-time progress updates
- **Concurrent-safe**: Can be safely interrupted and resumed

### Enhanced Name Matching

- **Time-indexed database**: Only searches legislators active during travel dates
- **Fuzzy matching**: Handles variations in names, nicknames, middle names
- **Confidence scoring**: Flags low-confidence and ambiguous matches
- **Caching**: Pickle cache for fast reloads
- **Unicode support**: Proper handling of accents and international characters

### Data Validation

- **Pydantic models**: Automatic validation of all data
- **Date validation**: Ensures departure is after arrival
- **Pattern validation**: Validates Bioguide ID format
- **Type safety**: Runtime type checking

## Development

### Run Tests

```bash
pytest tests/
```

### Code Formatting

```bash
black official_foreign_travel/
```

### Type Checking

```bash
mypy official_foreign_travel/
```

### Linting

```bash
ruff check official_foreign_travel/
```

## Migration Guide

### From v1.0 to v2.0

1. **Install dependencies**: `pip install -r requirements.txt`

2. **Update imports**:
   ```python
   # Old
   import scraper
   import name_search

   # New
   from official_foreign_travel.scrapers import ReportParser
   from official_foreign_travel.matchers import NameMatcher
   ```

3. **Use new CLI or wrappers**:
   - Replace `python scraper_report_text.py` with `python scraper_report_text_new.py`
   - Replace `python scraper.py` with `python scraper_new.py`
   - Or use new CLI: `oft-download`, `oft-parse`, etc.

4. **Update data models**:
   - Input/output now uses Pydantic models
   - CSV format remains compatible

## Troubleshooting

### "YAML file not found"

**New in v2.1:** Use the automated download tool:
```bash
oft-download-legislators
# or
python download_legislators.py
```

Or download manually from:
- https://raw.githubusercontent.com/unitedstates/congress-legislators/master/legislators-current.yaml
- https://raw.githubusercontent.com/unitedstates/congress-legislators/master/legislators-historical.yaml

Place in project root directory.

### "Members CSV not found"

Ensure `members.csv` and `committees.csv` are in the project root or specify paths via config.

### Name matching is slow

Use caching:
```bash
oft-test-matching report_text/ output.txt --cache names_index.pickle
```

The first run will be slow, but subsequent runs will be fast.

## License

MIT License (inherited from original project)

## Credits

- Original authors: @eric_bickel, @ryanes
- Data for Democracy / ProPublica collaboration
- Python 3 + Pydantic upgrade: Claude Code v2.0 refactoring

## Data Sources

- House Clerk foreign travel reports: http://clerk.house.gov/public_disc/foreign/
- Legislator data: https://github.com/unitedstates/congress-legislators
