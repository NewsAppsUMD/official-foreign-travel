# Changelog

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
