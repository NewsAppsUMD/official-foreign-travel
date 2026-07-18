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
documentation — including `--llm-fallback`, `--no-fuzzy-name-matching` (fuzzy matching is
on by default), and merging human corrections back in with `--apply-corrections`.

## Reviewing Flagged Reports

Any table the parser can't fully resolve — a garbled date, a cost column that doesn't
add up, a traveler name it can't match — is kept and flagged rather than dropped.
`oft-review` is a local web tool for working through that flagged subset by hand:

```bash
uv run oft-parse report_text/ output.json
uv run oft-review report_text/ output.json --corrections corrections.json
```

Then open http://127.0.0.1:8765/ in a browser:

- The **list view** defaults to the flagged review queue, with a "Flagged only" toggle
  to browse every parsed report. It's filterable by review status and flag type,
  sortable by column, with a progress counter as you work through the queue.
- Clicking a report opens the **detail view**: the original fixed-width source text on
  the left, an editable form of every extracted field on the right. Clicking a
  segment's heading highlights the source lines it was parsed from.
- **Save** records your edits; **Confirm OK** marks a report reviewed with no changes
  needed. Use **Prev/Next** to move through the queue.

Corrections are written to the `--corrections` file (default `corrections.json`), never
to the parsed output itself, so your review work survives any re-parse. To fold the
corrections into a fresh parse:

```bash
uv run oft-parse report_text/ output.json --apply-corrections corrections.json
```

Corrected reports are tagged `MANUALLY_CORRECTED` (or `HUMAN_CONFIRMED` for
confirm-OKs) in the output. The server binds only to localhost and has no
authentication — it's meant for one local reviewer.

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
