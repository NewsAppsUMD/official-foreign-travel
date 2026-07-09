# Official Foreign Travel

A parser for the House of Representatives' "Official Foreign Travel" expenditure
reports — the quarterly filings published by the Office of the Clerk that list every
member and staffer who traveled abroad on official business, along with dates,
destinations, and costs.

The pipeline turns ~30 years of fixed-width text filings (1994–present) into structured
JSON: every trip is a `Report` with a sponsor, a reporting period, and a list of
travelers, each with their travel segments (arrival/departure dates, country, and the
four cost categories the Clerk publishes). Nothing is silently dropped — a row the
parser can't fully resolve is kept with a flag rather than guessed at or discarded, and
a local review tool (`oft-review`) lets a human work through the flagged subset by hand.

## Quick Start

```bash
uv sync --all-extras
uv run oft-download                         # fetch report_text/*.txt from clerk.house.gov
uv run oft-parse report_text/ output.json    # parse into structured JSON
uv run oft-review report_text/ output.json   # review flagged reports in a browser
```

See **[TECHNICAL_README.md](TECHNICAL_README.md)** for full installation, CLI, and API
documentation — including `--llm-fallback`, `--fuzzy-name-matching`, and merging human
corrections back in with `--apply-corrections`.

## Documentation

- **[TECHNICAL_README.md](TECHNICAL_README.md)** — installation, usage, CLI reference,
  data model, configuration, development, troubleshooting
- **[about_the_data.md](about_the_data.md)** — the source data, its quirks, and how the
  parsing pipeline handles them
- **[CHANGELOG.md](CHANGELOG.md)** — version history

## Contributing

Issues and pull requests are welcome. Run the test suite with `uv run pytest tests/`
before submitting; see [TECHNICAL_README.md](TECHNICAL_README.md#development) for
formatting/linting/type-checking commands.

## License

See [TECHNICAL_README.md](TECHNICAL_README.md#license).
