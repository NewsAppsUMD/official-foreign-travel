# Design: Report Review UI

## Context

The parsing pipeline flags reports it couldn't fully resolve deterministically or via
`--llm-fallback` (`LAYOUT_LOW_CONFIDENCE`, `LAYOUT_UNDETECTED`, `TABLE_SUM_MISMATCH`,
`LLM_PARSED`, `MEMBER_UNMATCHED`, etc.) — currently around 64 tables in the full corpus,
listed in `output/llm_fallback_candidates.json`. There's no way to look at one of these
side-by-side with the original source text and correct it; a human has to cross-reference
the raw `report_text/*.txt` file and the JSON output by hand. This adds a local review tool
for that.

## Goals

- Show the original fixed-width table text and the extracted structured data side by side.
- Let a reviewer edit any field and persist the correction without touching the pipeline's
  regenerable output.
- Let corrections be merged back into a fresh parse of the whole corpus at any time.
- Zero new runtime dependencies.

## Non-goals

- Multi-user / concurrent-editing support (single local reviewer).
- Reviewing the entire corpus (~3,000 reports) — only the flagged subset.
- Editing the raw source text itself.

## Architecture

New `official_foreign_travel/review/` package:

```
review/
  server.py       # http.server.ThreadingHTTPServer + request handler, API endpoints
  corrections.py  # load/save/merge the corrections overlay; dotted-path get/set on Report
  static/
    index.html    # list view
    report.html   # detail view (side-by-side)
    app.css
    app.js        # fetch API calls, form rendering, highlight-on-focus
cli/
  review.py       # `oft-review` entry point: argparse, starts the server
```

Built on Python's stdlib `http.server` (a `ThreadingHTTPServer` with a custom
`BaseHTTPRequestHandler` subclass) rather than Flask/FastAPI — no new dependency. Binds to
`127.0.0.1` only.

```
oft-review report_text/ output/travel_reports.json --corrections corrections.json --port 8765
```

The server loads the parsed JSON once at startup and holds it in memory (report list is
small — the flagged subset, not the full corpus). For each flagged report's raw text, it
re-locates the source block on demand via `report_text/<source_file>` + `table_index`,
reusing `segment_tables()` (the same lookup `llm_fallback._load_block` already does —
factor that into a shared helper in `segmenter.py` or a small `review/source_lookup.py` to
avoid duplicating it). Corrections are read/written directly to the `--corrections` JSON
file on each save — no database, no in-memory-only state that could be lost.

## Corrections overlay schema

A separate JSON file, keyed by `report_id`, sitting alongside the pipeline's output:

```json
{
  "2007q4nov13-006": {
    "status": "edited",
    "reviewed_at": "2026-07-08T12:00:00Z",
    "edits": {
      "sponsor.name": "Committee on Foreign Affairs",
      "travelers[2].bioguide_id": "S001153",
      "travelers[2].segments[0].costs.total.us_dollar.amount": "2439.00"
    }
  },
  "1998q1mar11-001": {
    "status": "confirmed_ok",
    "reviewed_at": "2026-07-08T12:05:00Z",
    "edits": {}
  }
}
```

- `status`: `unreviewed` (implicit — no entry in the file), `confirmed_ok` (reviewed, no
  changes needed), or `edited` (has one or more corrections).
- `edits`: dotted/indexed paths into the `Report` JSON structure, mapping only the changed
  leaf fields to their corrected values. Path grammar: `field`, `field.subfield`,
  `list[N]`, `list[N].field`, chained arbitrarily (e.g.
  `travelers[2].segments[0].costs.total.us_dollar.amount`).
- Saving a report's corrections replaces that report's entire entry (not a deep merge) —
  simpler semantics, and the whole form is always resubmitted together.

`review/corrections.py` provides:
- `load_corrections(path) -> dict`
- `save_report_correction(path, report_id, status, edits) -> None`
- `get_path(report_dict, path) -> Any` / `set_path(report_dict, path, value) -> None` —
  parse and apply one dotted/indexed path against a plain dict (the JSON-decoded report,
  not the Pydantic model, to avoid re-validating on every keystroke).
- `apply_corrections(reports: List[Report], corrections: dict) -> List[Report]` — used by
  `oft-parse --apply-corrections`, applies each report's `edits` onto the Pydantic model
  (via `model_dump()` -> apply paths -> `Report.model_validate()`), tags touched reports
  with `MANUALLY_CORRECTED`, and re-runs `validate_report` on them.

## API endpoints

- `GET /` — list view (static HTML, fetches data via JS)
- `GET /api/reports` — JSON array of flagged reports: `{report_id, sponsor, source_file,
  flags, traveler_count, status}` (status joined in from the corrections file)
- `GET /api/reports/<report_id>` — JSON: `{report: {...}, raw_lines: [...], corrections:
  {...}}` (raw_lines includes per-segment source_line ranges for highlighting)
- `POST /api/reports/<report_id>/corrections` — body `{"status": "edited"|"confirmed_ok",
  "edits": {...}}`, writes to the corrections file, returns the updated entry

## UI/UX

**List view**: table of flagged reports (ID, sponsor, source file, flags, traveler count,
status), filterable by flag type and status, sortable, with a progress counter ("42/64
reviewed"). Row click -> detail view.

**Detail view**: two panes.
- Left: raw fixed-width table text, monospace, with the focused traveler/segment's
  `source_lines` highlighted.
- Right: a form generated from the report structure — sponsor (type/name/code), period
  (start/end), then one block per traveler (name/honorific/bioguide_id) with nested blocks
  per segment (arrival/departure date, country, all 8 cost cells: 4 categories x
  foreign-currency/US-dollar). Focusing a traveler/segment block highlights its lines on
  the left.
- Actions: Save (status -> `edited`), Confirm OK (status -> `confirmed_ok`, no edits),
  Prev/Next through the flagged queue.

## Applying corrections back into output

`oft-parse report_text/ output.json --apply-corrections corrections.json`: after the
normal parse -> validate -> dedup pipeline and before serialization, looks up each
assembled report by `report_id` in the corrections file, applies any `edits`, re-validates
touched reports, and tags them `MANUALLY_CORRECTED`. This makes the corrections file the
durable source of truth for human judgment — a full re-parse of the corpus from scratch
never loses a correction.

## Testing

- Unit tests for `corrections.py`: dotted-path get/set (including list-index paths and
  nested chains), `apply_corrections` merging onto a `Report`, the flagged-report filter.
- Integration tests driving the `http.server` handler in-process (via `http.client`)
  against a temp corrections file: list endpoint, detail endpoint, save-then-reload round
  trip, confirm-ok round trip.
- No JS test framework. The actual browser UI (list view, detail view, highlighting,
  save/confirm flow) is verified manually via the preview tool before considering this
  done, per the project's UI-testing convention.
