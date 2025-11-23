# Upgrade Guide - Version 2.1

This document outlines the improvements made in version 2.1 and how to use them.

## What's New in v2.1

### Critical Fixes

1. **Security Fix**: Fixed YAML loading vulnerability
   - Changed `yaml.load()` to `yaml.safe_load()` to prevent arbitrary code execution
   - Affects: `name_search.py` (legacy)

2. **Dependencies**: Added `requirements.txt` and `requirements-dev.txt`
   - Production dependencies in `requirements.txt`
   - Development dependencies (testing, linting) in `requirements-dev.txt`

### New Features

1. **Automated Legislator Data Download**
   ```bash
   # Standalone script
   python download_legislators.py

   # Or CLI tool (after pip install -e .)
   oft-download-legislators

   # Download to specific directory
   oft-download-legislators --output-dir data/
   ```

2. **Environment Configuration Template**
   - Copy `.env.example` to `.env` and customize
   - All configuration options documented with examples

3. **Comprehensive Test Suite**
   ```bash
   # Install dev dependencies
   pip install -r requirements-dev.txt

   # Run tests
   pytest

   # Run with coverage
   pytest --cov=official_foreign_travel --cov-report=html

   # View coverage report
   open htmlcov/index.html
   ```

4. **CI/CD Pipeline**
   - Automated testing on Python 3.9, 3.10, 3.11, 3.12
   - Code quality checks (black, ruff, mypy)
   - Package build verification
   - See `.github/workflows/ci.yml`

5. **Improved Type Checking**
   - Enabled strict type checking for new code
   - Legacy code excluded for gradual migration
   - Run: `mypy official_foreign_travel/`

6. **Enhanced Linting**
   - Configured ruff with multiple rule sets
   - Run: `ruff check official_foreign_travel/`

### Deprecation Warnings

The following scripts now display deprecation warnings:
- `scraper.py` → Use `oft-parse` or `scraper_new.py`
- `scraper_report_text.py` → Use `oft-download` or `scraper_report_text_new.py`
- `name_search.py` → Use `from official_foreign_travel.matchers import NameMatcher`
- `name_search_test.py` → Use `oft-test-matching` or `name_search_test_new.py`

## Installation

### Fresh Install

```bash
# Clone repository
git clone <repository-url>
cd official-foreign-travel

# Install production dependencies
pip install -r requirements.txt

# Or install package in development mode
pip install -e .

# Download legislator data
python download_legislators.py

# Verify installation
oft-download --help
oft-parse --help
```

### Upgrading from v2.0

```bash
# Pull latest changes
git pull

# Install new dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Download legislator data (automated now!)
python download_legislators.py

# Run tests to verify
pytest
```

## Configuration

### Using .env File

1. Copy the example:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` to customize:
   ```bash
   # Example customizations
   OFT_START_YEAR=2015
   OFT_END_YEAR=2023
   OFT_LOG_LEVEL=DEBUG
   ```

### Available Configuration Options

See `.env.example` for all available options:
- Directory paths (data, output, reports)
- Data file locations
- Scraping parameters (years, timeouts, retries)
- Name matching thresholds
- Logging configuration

## Development Workflow

### Setting Up Development Environment

```bash
# Install with development dependencies
pip install -e ".[dev]"

# Or use requirements-dev.txt
pip install -r requirements-dev.txt

# Download legislator data
python download_legislators.py
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=official_foreign_travel

# Run specific test file
pytest tests/test_models.py

# Run specific test
pytest tests/test_models.py::TestTravelRecord::test_from_input
```

### Code Quality Checks

```bash
# Format code
black official_foreign_travel/ tests/

# Lint code
ruff check official_foreign_travel/ tests/

# Type check
mypy official_foreign_travel/

# Run all checks (like CI)
black --check official_foreign_travel/ tests/
ruff check official_foreign_travel/ tests/
mypy official_foreign_travel/
pytest
```

## Migration from Legacy Scripts

### Downloading Reports

**Old way:**
```bash
python scraper_report_text.py
```

**New way:**
```bash
# Using modern CLI
oft-download --start-year 2015 --end-year 2020

# Or wrapper (compatible interface)
python scraper_report_text_new.py
```

### Parsing Reports

**Old way:**
```bash
python scraper.py report_text/ output.csv
```

**New way:**
```bash
# Using modern CLI
oft-parse report_text/ output.csv

# Or wrapper (compatible interface)
python scraper_new.py report_text/ output.csv
```

### Name Matching

**Old way:**
```python
import name_search
charset, members_list, members_dict, members_index = name_search.initialize()
results = name_search.search_by_name("Hon. John Doe", "1/15/2019", "1/20/2019",
                                     members_index, charset)
```

**New way:**
```python
from official_foreign_travel.matchers import NameMatcher

matcher = NameMatcher()
matcher.initialize()
result = matcher.search_by_name("Hon. John Doe", "1/15/2019", "1/20/2019")

# Access results
print(f"Best match: {result.best_bioguide_id}")
print(f"Confident: {result.is_confident}")
print(f"Score: {result.top_match.score}")
```

## Testing Your Changes

After upgrading, verify everything works:

```bash
# 1. Download legislator data
python download_legislators.py

# 2. Run tests
pytest

# 3. Try downloading a few reports
oft-download --start-year 2019 --end-year 2019

# 4. Parse the reports
oft-parse report_text/ test_output.csv

# 5. Test name matching
oft-test-matching report_text/ matching_issues.txt
```

## Troubleshooting

### Missing legislator data

**Error:** `FileNotFoundError: legislators-current.yaml`

**Solution:**
```bash
python download_legislators.py
```

### Import errors

**Error:** `ModuleNotFoundError: No module named 'official_foreign_travel'`

**Solution:**
```bash
pip install -e .
```

### Test failures

**Error:** Tests fail with import errors

**Solution:**
```bash
pip install -r requirements-dev.txt
```

### Type checking errors

**Error:** Many mypy errors in old code

**Solution:** This is expected. Old scripts are excluded from strict type checking. Only new code in `official_foreign_travel/` needs to pass type checks.

## Breaking Changes

None. This is a backward-compatible upgrade. All old scripts still work but show deprecation warnings.

## Getting Help

- Check the [TECHNICAL_README.md](TECHNICAL_README.md) for detailed usage
- Review [README.md](README.md) for project overview
- Open an issue on GitHub for bugs or questions
