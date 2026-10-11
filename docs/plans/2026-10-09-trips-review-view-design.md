# Trips View in the Review Browser — Design

Date: 2026-10-09
Status: approved (user-approved 2026-10-09; implementation plan to follow)

> **Post-implementation note:** built via `2026-10-09-trips-review-view.md`, which
> supersedes three details here — separate Start/End sort columns (not one combined
> column), a client-ready `_trip_row` in `review/server.py` (not reuse of
> `_flatten_trip_row`, whose joined-string flags and `%m/%d/%Y` dates don't suit the
> UI), and the progress line's "conflicted · merged" field order (merged first in the
> shipped code).

## Context

`oft-parse --trips` (see the `[Unreleased]` CHANGELOG entry and `about_the_data.md`
"Canonical trip records") derives one trip record per person-journey, merging the
same journey across filings by dates and destinations. The trip flags —
`TRIP_DATE_DESTINATION_CONFLICT` above all — are a triage list pointing back at
filings worth reviewing, but today the only way to act on one is to grep
`trips.csv`, find the `source_report_ids`, and open those filings in `oft-review`
by hand. This design adds a read-only trips view to the review browser so that loop
is clickable.

The review browser today (`review/server.py` + `review/static/`): a closure-over-
reports `ThreadingHTTPServer` serving `/api/reports` (flat list), `/api/reports/{id}`
(detail + raw text + correction), and POST `/api/reports/{id}/corrections`; a
vanilla-JS two-page frontend (sortable filings list with status/flag filters and a
"Flagged only" default; detail page with editable form + raw pane). In-flight
uncommitted work applies corrections in `cli/review.py` at startup, before
`run_server` — which makes startup-time trip derivation always in sync with the
session's corrections.

## Approved decisions

1. **Purpose**: a triage queue first. The trips view defaults to the "needs
   attention" subset; a name/bioguide search box covers the lookup use case
   secondarily.
2. **Data source**: derive at `oft-review` startup (Approach A) —
   `build_trips` runs in `cli/review.py` immediately after `apply_corrections`.
   Measured: ~8.3s for the full corpus (26,763 trips from 3,606 reports; report
   load ~6s). Loading a pre-generated trips file (B) was rejected for drift against
   the session's freshly applied corrections; lazy first-request derivation (C)
   was rejected for lock complexity — boot cost is once per session either way.
3. **Read-only**: trips are not correctable entities. Fixes happen in the filings
   a trip links to; the trip view is a lens, not a second edit surface.
4. **`conflict_with` joins the outputs**: the new pairing field (below) is exposed
   in the review UI *and* as a final `conflict_with` CSV column plus a
   JSONL/JSON field, so conflict triage works from the CLI too. Schema churn is
   free while the trips outputs are still uncommitted.
5. **`docs/review-glossary.md` gets the `TRIP_*` flags** — the earlier
   "intentionally untouched" decision reverses because the flags now surface in
   the review UI.

## Data flow

```
load reports (file or per-year dir)
  → apply_corrections(reports, corrections)          [existing, in-flight]
  → build_trips(visible_reports(reports))            [new — non-superseded only]
  → run_server(reports, report_text_dir, corrections, trips=trips)
```

- Superseded filings are excluded from derivation (matching `oft-parse --trips`
  defaults) but remain browsable as filings in the report list/detail.
- No new CLI argument.
- Boot line grows a suffix:
  `(3606 reports, 3494 flagged · 26763 trips, 1108 need attention)`.
- Staleness: corrections saved mid-session do not re-derive trips (same staleness
  as the reports list itself, which is captured at server start). Restart
  `oft-review` — or re-run `oft-parse --trips` — to see refreshed trips. The
  trips page carries a small hint line saying so.

## Backend (`review/server.py`)

Two new GET routes inside the existing `make_handler` closure (which gains the
`trips` list); no new POST.

- **`/api/trips`** — flat list, one object per trip, rows built with
  `serialize._flatten_trip_row` (same shape as `trips.csv` rows, private-import
  precedent: `llm_fallback.py` imports `_match_member`) plus `conflict_with`.
  Sent in `build_trips` order (chronological, undated last). ~26.7k rows ≈ 4–5 MB
  on localhost — consistent with the client-filters-everything pattern
  `/api/reports` already uses.
- **`/api/trips/{trip_id}`** — per-trip detail: `legs` (arrival/departure dates,
  countries, `country_raw`, `report_id`, `source_file`), `filings` (report_id,
  source_file, sponsor type/name/code, publication_date, amended — newest first),
  plus the flat row fields and `conflict_with`. Fetched lazily when a row expands.
  404 for unknown ids, mirroring `_send_report_detail`.

## `conflict_with` (the one change inside `parsing/trips.py`)

Phase 2 already records conflict pairs (candidate index, candidate index) when
spans overlap but destinations disagree. After Phase 4 assigns trip ids, a
post-pass cross-links: each trip in a conflict pair gains
`conflict_with: list[str]` — the partner trips' ids, sorted, deduped. A
EUROPE/DENMARK pair yields two rows that link to each other.

Surfaced as:
- `Trip.conflict_with` (new field, default `[]`),
- final `conflict_with` column in `TRIP_CSV_FIELDNAMES` ("; "-joined),
- a `conflict_with` field in the JSONL and nested-JSON trip rows.

## Frontend (`review/static/`)

A third page, `trips.html`, with a `renderTrips()` in `app.js` mirroring
`renderList()`:

- **Nav**: "Trips →" link on the filings index; "← Filings" on trips.html.
- **Progress line**: `1108 need attention · 527 merged · 505 conflicted · 26763
  trips total`.
- **Filters**:
  - "Needs attention only" checkbox, default ON. Attention set:
    `TRIP_DATE_DESTINATION_CONFLICT`, `TRIP_MATCHED_NAME_ONLY`,
    `TRIP_MERGED_MIXED_IDENTITY`, `TRIP_DATES_MISSING`. Deliberately *not*
    `TRIP_SPLIT_BY_DATE_GAP` (informational; 4.2k rows) and not
    `TRIP_MERGED_ACROSS_FILINGS` alone (the feature working as intended).
  - Flag dropdown built from the distinct `TRIP_*` flags present (existing
    `populateFlagFilter` pattern).
  - Client-side text search on traveler name / bioguide / trip id.
- **Table** (sortable, existing `th[data-sort]` pattern): Traveler (name +
  bioguide), Dates (start–end), Countries, Legs, Filings — each
  `source_report_id` a link to `/report.html?id=…` — and flag badges
  (existing `flagBadges`).
- **Row expand**: clicking a row fetches `/api/trips/{trip_id}` and renders an
  inline detail — legs table (arrival, departure, countries, source-filing
  link), filing provenance, and `conflict_with` partners as links.
  `trips.html?expand=<trip_id>` auto-expands and scrolls to that trip.
- **No save/confirm buttons.** A hint line notes trips refresh on
  `oft-review` restart.

Filter/sort state persists via `localStorage` under a new key
(`oft-review-trips-filters`), mirroring the filings list.

## Edge cases

- Single-file / small parses: trips derive normally (one report's trips; possibly
  few or none — empty traveler tables yield "No trips").
- Superseded filings: excluded from trips, browsable as filings.
- Corrections saved mid-session: filings detail re-fetches edits as today; the
  trips list is a startup snapshot — hint line covers it.
- Corrupted `corrections.json`: unchanged request-scoped 500 path.
- Unknown trip id in `/api/trips/{id}` or `?expand=`: 404 / ignored.

## Testing

- `tests/test_cli_review.py` (extends the existing `fake_run_server` capture
  pattern): the `trips` kwarg passed to `run_server` reflects corrections applied
  *before* derivation (fix a date via correction → derived trip's date changes);
  superseded reports are excluded from the derived trips.
- `tests/test_trips.py`: `conflict_with` populated symmetrically on a
  EUROPE-vs-DENMARK pair (and only on conflict pairs); empty for clean merges;
  CSV header gains `conflict_with` last; JSONL/JSON rows carry it.
- Server endpoints stay untested at the HTTP level (parity with today — no
  `make_handler` tests exist); row-building reuses `serialize` helpers already
  covered by `tests/test_serialize.py`-style tests. UI verified manually.
- Full-suite, black/ruff/mypy scoped to touched files, corpus smoke
  (`oft-review` boot message + `--trips` outputs with `conflict_with`).

## Docs

- `TECHNICAL_README.md` "Reviewing Flagged Reports": trips-view paragraph +
  example.
- `about_the_data.md`: mention `conflict_with` in the trips output prose.
- `CHANGELOG.md` `[Unreleased]`: extend the trips entry.
- `docs/review-glossary.md`: add the seven `TRIP_*` flags.