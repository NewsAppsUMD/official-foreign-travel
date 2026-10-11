# Trips Review View Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a read-only trips triage page to `oft-review` — derived at startup from the same corrected reports the session shows — with `/api/trips` endpoints and a new `conflict_with` pairing field on trip records and in the trips outputs.

**Architecture:** `cli/review.py` calls `build_trips` after `apply_corrections` and passes the trips into `run_server`, which gains two read-only GET routes (`/api/trips`, `/api/trips/{trip_id}`). `parsing/trips.py` cross-links the date/destination conflict pairs it already detects into a new `Trip.conflict_with` field, which also lands in the CSV/JSONL/JSON outputs. A new vanilla-JS `trips.html` page reuses the filings list's filter/sort/badge patterns.

**Tech stack:** Python 3.9 + Pydantic v2, `http.server` (stdlib), vanilla JS/CSS (no frontend framework, no build step), pytest. All commands via `uv run`.

---

## Standing constraints (read before Task 1)

1. **Nothing gets committed.** The user's rule for this working tree: no `git commit` unless they ask. Tasks below have **no commit steps** — do not add any.
2. **Work in the current tree**, not a worktree (user's explicit choice — a worktree would lose their in-flight uncommitted changes).
3. **The tree holds the user's uncommitted in-flight work** (`cli/review.py` corrections-at-startup, `models/report.py`, `parsing/dedup.py`, `review/static/app.css`, `review/static/app.js`, several tests). Every edit in this plan is additive to those files — never revert or reformat their lines. In particular: `cli/review.py`'s corrections block (lines 96–106) stays; we append after it.
4. **Scratch outputs go to `/tmp`**, never the repo's `output.json` (which belongs to the user).
5. Repo-wide `ruff`/`mypy` fail on pre-existing HEAD-era code (~45 ruff findings, 47 mypy errors in files this plan does not touch). **Only the files this plan touches must be clean.** If a lint tool reformats files outside your task list, revert those files.
6. A parse of the full corpus already exists at `/tmp/oft-trips-verify/output.json` (3,606 reports) from the previous feature's verification. If it's gone, regenerate it once (Task 7, step 1).

Reference docs: the approved design is `docs/plans/2026-10-09-trips-review-view-design.md`; the trip module is `official_foreign_travel/parsing/trips.py`.

---

### Task 1: `conflict_with` cross-linking + attention-flag set in `parsing/trips.py`

**Files:**
- Modify: `official_foreign_travel/parsing/trips.py`
- Test: `tests/test_trips.py`

**Step 1: Write the failing tests**

Add to the imports at the top of `tests/test_trips.py` (extend the existing `from official_foreign_travel.parsing.trips import (...)` block):

```python
    TRIP_ATTENTION_FLAGS,
```

Add this test class anywhere after `TestCrossFilingMatching` (it uses the file's existing builders `make_report`, `make_traveler`, `make_segment`, and helper `trips_for`):

```python
class TestConflictCrossLinking:
    def _conflicting_filings(self):
        # Same member, overlapping dates, disjoint destinations: two filings
        # that describe the same person in the same window but disagree on
        # where they went.
        older = make_report(
            report_id="f-000",
            source_file="2019q1jan02.txt",
            travelers=[
                make_traveler(
                    "Hon. Jane Smith",
                    bioguide="S001",
                    segments=[
                        make_segment(date(2019, 3, 1), date(2019, 3, 5), ["EUROPE"])
                    ],
                )
            ],
        )
        newer = make_report(
            report_id="g-001",
            source_file="2019q2apr30.txt",
            travelers=[
                make_traveler(
                    "Hon. Jane Smith",
                    bioguide="S001",
                    segments=[
                        make_segment(date(2019, 3, 2), date(2019, 3, 6), ["DENMARK"])
                    ],
                )
            ],
        )
        return older, newer

    def test_conflict_pairs_link_both_ways(self):
        older, newer = self._conflicting_filings()
        trips = build_trips([older, newer])
        assert len(trips) == 2
        assert all(TRIP_DATE_DESTINATION_CONFLICT in t.flags for t in trips)
        assert sorted(trips[0].conflict_with) == [trips[1].trip_id]
        assert sorted(trips[1].conflict_with) == [trips[0].trip_id]

    def test_merged_trip_has_no_conflict_with(self):
        # A clean cross-filing merge never carries conflict partners.
        traveler = make_traveler(
            "Hon. Jane Smith",
            bioguide="S001",
            segments=[make_segment(date(2019, 3, 1), date(2019, 3, 5), ["FRANCE"])],
        )
        other = make_traveler(
            "Hon. Jane Smith",
            bioguide="S001",
            segments=[make_segment(date(2019, 3, 1), date(2019, 3, 5), ["FRANCE"])],
        )
        trips = build_trips(
            [
                make_report("f-000", "2019q1jan02.txt", [traveler]),
                make_report("g-001", "2019q2apr30.txt", [other]),
            ]
        )
        assert len(trips) == 1
        assert trips[0].conflict_with == []

    def test_transient_conflict_merged_via_third_filing_no_self_link(self):
        # EUROPE and DENMARK filings conflict with each other, but a third
        # filing reporting both overlaps both -- all three merge into one
        # trip. That trip keeps the conflict flag but must not link to itself.
        europe = make_report(
            report_id="f-000",
            source_file="2019q1jan02.txt",
            travelers=[
                make_traveler(
                    "Hon. Jane Smith",
                    bioguide="S001",
                    segments=[
                        make_segment(date(2019, 3, 1), date(2019, 3, 5), ["EUROPE"])
                    ],
                )
            ],
        )
        denmark = make_report(
            report_id="g-001",
            source_file="2019q2apr30.txt",
            travelers=[
                make_traveler(
                    "Hon. Jane Smith",
                    bioguide="S001",
                    segments=[
                        make_segment(date(2019, 3, 2), date(2019, 3, 6), ["DENMARK"])
                    ],
                )
            ],
        )
        both = make_report(
            report_id="h-000",
            source_file="2019q3jul29.txt",
            travelers=[
                make_traveler(
                    "Hon. Jane Smith",
                    bioguide="S001",
                    segments=[
                        make_segment(date(2019, 3, 1), date(2019, 3, 6), ["EUROPE", "DENMARK"])
                    ],
                )
            ],
        )
        trips = build_trips([europe, denmark, both])
        assert len(trips) == 1
        assert TRIP_DATE_DESTINATION_CONFLICT in trips[0].flags
        assert trips[0].conflict_with == []

    def test_attention_flag_set(self):
        # Exactly the four flags that make a trip worth a reviewer's time.
        assert TRIP_ATTENTION_FLAGS == frozenset(
            {
                TRIP_DATE_DESTINATION_CONFLICT,
                TRIP_MATCHED_NAME_ONLY,
                TRIP_MERGED_MIXED_IDENTITY,
                TRIP_DATES_MISSING,
            }
        )
```

**Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_trips.py -k TestConflictCrossLinking -x`
Expected: FAIL — `ImportError: cannot import name 'TRIP_ATTENTION_FLAGS'` (the constant doesn't exist yet).

**Step 3: Implement**

In `official_foreign_travel/parsing/trips.py`:

3a. Import `field` alongside `dataclass` (line 21):

```python
from dataclasses import dataclass, field
```

3b. Add the attention set right after `NON_PERSON_SEGMENT_FLAGS` (after line 55):

```python
# The flags that mark a trip as worth a human look in the review app's
# trips queue: matches resting on name alone or on a bare identity whose
# name uniquely resolved to one member, journeys whose dates could not be
# recovered (and which therefore never merged), and unmerged
# date-overlapping destination disagreements. TRIP_SPLIT_BY_DATE_GAP is
# informational (a filing legitimately reporting two separate journeys),
# and TRIP_MERGED_ACROSS_FILINGS alone is the merge working as intended,
# so neither is "attention".
TRIP_ATTENTION_FLAGS = frozenset(
    {
        TRIP_DATE_DESTINATION_CONFLICT,
        TRIP_MATCHED_NAME_ONLY,
        TRIP_MERGED_MIXED_IDENTITY,
        TRIP_DATES_MISSING,
    }
)
```

3c. Add the field to the `Trip` dataclass, as the last field (after `notes: list[str]`, before the `n_legs` property — defaulted fields must come last):

```python
    flags: list[str]
    notes: list[str]
    # Trip ids of the other side(s) of any TRIP_DATE_DESTINATION_CONFLICT.
    conflict_with: list[str] = field(default_factory=list)
```

3d. In `build_trips`, Phase 2: record pairs, not just the candidate set. Change (around line 407):

```python
    conflict_candidates: set[int] = set()
```

to:

```python
    conflict_candidates: set[int] = set()
    conflict_pairs: list[tuple[int, int]] = []
```

and in the `elif` that records the conflict (around line 416), append the pair:

```python
                elif a.countries_set and b.countries_set:
                    conflict_candidates.add(a.index)
                    conflict_candidates.add(b.index)
                    conflict_pairs.append((a.index, b.index))
```

3e. Phase 3: map each cluster root to its trip index. Next to the declarations (around line 450):

```python
    trips: list[Trip] = []
    # (person_key, name_tokens) of each trip's winning candidate, for ids.
    id_meta: list[tuple[str, tuple[str, str]]] = []
```

add:

```python
    # Cluster root -> index into `trips`, for conflict cross-linking.
    trip_of_root: dict[int, int] = {}
```

and inside the `for members in clusters.values():` loop, right before `trips.append(`:

```python
        trip_of_root[find(winner.index)] = len(trips)
```

3f. Phase 4: after the trip-id assignment loop (after the `for trip, (person_key, name_tokens) in zip(trips, id_meta):` block ends, before `trips.sort(...)`), insert:

```python
    # Cross-link conflict pairs so each side can name the other. A pair whose
    # members ended up in one cluster via a third candidate (e.g. a merged
    # EUROPE+DENMARK filing overlapping both sides) is a transient
    # disagreement -- that single trip keeps the conflict flag but gets no
    # self-link.
    conflict_with: dict[int, set[str]] = {}
    for a, b in conflict_pairs:
        root_a, root_b = find(a), find(b)
        if root_a == root_b:
            continue
        trip_a, trip_b = trip_of_root[root_a], trip_of_root[root_b]
        conflict_with.setdefault(trip_a, set()).add(trips[trip_b].trip_id)
        conflict_with.setdefault(trip_b, set()).add(trips[trip_a].trip_id)
    for trip_index, partners in conflict_with.items():
        trips[trip_index].conflict_with = sorted(partners)
```

**Step 4: Run the tests**

Run: `uv run pytest tests/test_trips.py -x`
Expected: PASS (all 29 pre-existing tests + 5 new).

**Step 5: Lint scope check**

Run: `uv run black --check official_foreign_travel/parsing/trips.py && uv run ruff check official_foreign_travel/parsing/trips.py && uv run mypy official_foreign_travel/parsing/trips.py 2>&1 | grep "trips.py" ; echo done`
Expected: black "All done", ruff "All checks passed", no mypy lines naming trips.py (imported modules may report pre-existing errors — only trips.py itself must be clean).

---

### Task 2: `conflict_with` in the trips outputs (`parsing/serialize.py`)

**Files:**
- Modify: `official_foreign_travel/parsing/serialize.py`
- Test: `tests/test_trips.py` (serialization class already exists there)

**Step 1: Write the failing test**

Add to the existing serialization test class in `tests/test_trips.py` (`TestTripsSerialization` — find it with `grep -n "class TestTripsSerialization" tests/test_trips.py`):

```python
    def test_conflict_with_in_all_output_formats(self, tmp_path):
        older = make_report(
            report_id="f-000",
            source_file="2019q1jan02.txt",
            travelers=[
                make_traveler(
                    "Hon. Jane Smith",
                    bioguide="S001",
                    segments=[
                        make_segment(date(2019, 3, 1), date(2019, 3, 5), ["EUROPE"])
                    ],
                )
            ],
        )
        newer = make_report(
            report_id="g-001",
            source_file="2019q2apr30.txt",
            travelers=[
                make_traveler(
                    "Hon. Jane Smith",
                    bioguide="S001",
                    segments=[
                        make_segment(date(2019, 3, 2), date(2019, 3, 6), ["DENMARK"])
                    ],
                )
            ],
        )
        write_trips_csv([older, newer], tmp_path / "trips.csv")
        with open(tmp_path / "trips.csv", newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert sorted(r["conflict_with"] for r in rows) == sorted(
            [rows[1]["trip_id"], rows[0]["trip_id"]]
        )

        write_trips_jsonl([older, newer], tmp_path / "trips.jsonl")
        lines = [
            json.loads(line)
            for line in (tmp_path / "trips.jsonl").read_text().splitlines()
        ]
        assert all(line["conflict_with"] == "" for line in lines) is False
        assert sorted(line["conflict_with"] for line in lines) == sorted(
            [lines[1]["trip_id"], lines[0]["trip_id"]]
        )

        write_trips_json([older, newer], tmp_path / "trips.json")
        payload = json.loads((tmp_path / "trips.json").read_text())
        assert sorted(t["conflict_with"] for t in payload["trips"]) == sorted(
            [payload["trips"][1]["trip_id"], payload["trips"][0]["trip_id"]]
        )

    def test_conflict_with_is_final_csv_column(self):
        assert TRIP_CSV_FIELDNAMES[-1] == "conflict_with"
```

**Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_trips.py -k conflict_with_in_all_output -x`
Expected: FAIL — `KeyError: 'conflict_with'` (the CSV row doesn't have the column).

**Step 3: Implement**

In `official_foreign_travel/parsing/serialize.py`:

3a. Append the column to `TRIP_CSV_FIELDNAMES` (after `"notes",`):

```python
    "flags",
    "notes",
    "conflict_with",
]
```

3b. In `_flatten_trip_row`, add the row value after `"notes"`:

```python
        "flags": ";".join(trip.flags),
        "notes": ";".join(trip.notes),
        "conflict_with": "; ".join(trip.conflict_with),
    }
```

(`write_trips_jsonl` and `write_trips_json` both build on `_flatten_trip_row`, so they gain the field automatically. The nested JSON's `legs`/`filings` come from the `Trip` object and need no change.)

**Step 4: Run the tests**

Run: `uv run pytest tests/test_trips.py -x`
Expected: PASS. Also `uv run pytest tests/test_serialize.py` — expected PASS (its trips coverage, if any, uses `TRIP_CSV_FIELDNAMES` rather than a hard-coded header list).

**Step 5: Lint scope check**

Run: `uv run black --check official_foreign_travel/parsing/serialize.py && uv run ruff check official_foreign_travel/parsing/serialize.py`
Expected: clean.

---

### Task 3: Review server — trips parameter, payload builders, routes (`review/server.py`)

**Files:**
- Modify: `official_foreign_travel/review/server.py`
- Create: `tests/test_review_server.py`

**Step 1: Write the failing tests**

Create `tests/test_review_server.py` (self-contained builders — mirrors how `test_cli_review.py` doesn't import from other test files):

```python
"""Tests for the review server's trips payload builders (pure functions)."""

import json  # noqa: F401  (kept for parity with sibling test files)
from datetime import date
from decimal import Decimal

from official_foreign_travel.models.report import (
    CostCell,
    CostGroup,
    Costs,
    Report,
    Sponsor,
    Traveler,
    TravelSegment,
)
from official_foreign_travel.parsing.trips import build_trips
from official_foreign_travel.review.server import _trip_detail, _trip_row


def make_cell(amount=None):
    return CostCell(amount=amount, raw=str(amount) if amount is not None else "")


def make_costs(total=None):
    return Costs(
        per_diem=CostGroup(foreign_currency=make_cell(), us_dollar=make_cell()),
        transportation=CostGroup(foreign_currency=make_cell(), us_dollar=make_cell()),
        other=CostGroup(foreign_currency=make_cell(), us_dollar=make_cell()),
        total=CostGroup(foreign_currency=make_cell(), us_dollar=make_cell(Decimal("100"))),
    )


def _trip():
    traveler = Traveler(
        name="Hon. Jane Smith",
        bioguide_id="S001",
        segments=[
            TravelSegment(
                arrival_date=date(2019, 3, 1),
                departure_date=date(2019, 3, 5),
                country_raw="FRANCE",
                countries=["FRANCE"],
                costs=make_costs(),
            ),
            TravelSegment(
                arrival_date=date(2019, 3, 5),
                departure_date=date(2019, 3, 8),
                country_raw="GERMANY",
                countries=["GERMANY"],
                costs=make_costs(),
            ),
        ],
    )
    report = Report(
        report_id="f-000",
        source_file="2019q1jan02.txt",
        table_index=0,
        sponsor=Sponsor(type="committee", name="COMMITTEE ON TEST", code="HS00"),
        travelers=[traveler],
    )
    return build_trips([report])[0]


class TestTripRow:
    def test_row_fields_are_client_ready(self):
        # Lists (not joined strings) and ISO dates (string sorting in the
        # table is chronological) -- the CSV row shape is for flat files,
        # this one is for the browser.
        row = _trip_row(_trip())
        assert row["trip_id"]
        assert row["name"] == "Hon. Jane Smith"
        assert row["bioguide_id"] == "S001"
        assert row["start_date"] == "2019-03-01"
        assert row["end_date"] == "2019-03-08"
        assert row["days"] == 8
        assert row["countries"] == ["FRANCE", "GERMANY"]
        assert row["n_legs"] == 2
        assert row["n_filings"] == 1
        assert row["source_report_ids"] == ["f-000"]
        assert row["source_files"] == ["2019q1jan02.txt"]
        assert row["flags"] == []
        assert row["conflict_with"] == []
        assert row["total_usd"] is None

    def test_undated_row_has_empty_dates_and_none_days(self):
        trip = _trip()
        trip.start_date = None
        trip.end_date = None
        row = _trip_row(trip)
        assert row["start_date"] == ""
        assert row["end_date"] == ""
        assert row["days"] is None


class TestTripDetail:
    def test_detail_extends_row_with_legs_and_filings(self):
        trip = _trip()
        detail = _trip_detail(trip)
        assert detail["trip_id"] == trip.trip_id
        assert [leg["arrival_date"] for leg in detail["legs"]] == [
            "2019-03-01",
            "2019-03-05",
        ]
        assert detail["legs"][0]["country_raw"] == "FRANCE"
        assert detail["legs"][0]["report_id"] == "f-000"
        assert detail["filings"][0]["report_id"] == "f-000"
        assert detail["filings"][0]["sponsor_name"] == "COMMITTEE ON TEST"
        assert detail["filings"][0]["sponsor_code"] == "HS00"
        assert detail["filings"][0]["publication_date"] == "2019-01-02"
        assert detail["filings"][0]["amended"] is False
        assert detail["conflict_with"] == []
```

**Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_review_server.py -x`
Expected: FAIL — `ImportError: cannot import name '_trip_row'`.

**Step 3: Implement**

In `official_foreign_travel/review/server.py`:

3a. Imports — add after the existing `from ..models.report import Report`:

```python
from ..parsing.trips import TRIP_ATTENTION_FLAGS, Trip
```

3b. Payload builders — add module-level functions after the `CONTENT_TYPES` constant and before `make_handler`:

```python
def _trip_row(trip: Trip) -> dict:
    """Flat JSON row for the trips list page. Arrays rather than joined
    strings (the client filters and links without splitting) and ISO dates
    (string sorting in the table is chronological)."""
    days = (
        (trip.end_date - trip.start_date).days + 1
        if trip.start_date is not None and trip.end_date is not None
        else None
    )
    return {
        "trip_id": trip.trip_id,
        "name": trip.name,
        "bioguide_id": trip.bioguide_id,
        "sponsor_name": trip.sponsor_name,
        "committee_code": trip.sponsor_code or "",
        "start_date": trip.start_date.isoformat() if trip.start_date else "",
        "end_date": trip.end_date.isoformat() if trip.end_date else "",
        "days": days,
        "countries": list(trip.countries),
        "n_legs": trip.n_legs,
        "n_filings": trip.n_filings,
        "source_report_ids": list(trip.source_report_ids),
        "source_files": list(trip.source_files),
        "total_usd": str(trip.total_usd) if trip.total_usd is not None else None,
        "flags": list(trip.flags),
        "conflict_with": list(trip.conflict_with),
    }


def _trip_detail(trip: Trip) -> dict:
    """The list row plus legs and per-filing provenance, for the expand view."""
    row = _trip_row(trip)
    row.update(
        legs=[
            {
                "arrival_date": (
                    leg.segment.arrival_date.isoformat() if leg.segment.arrival_date else ""
                ),
                "departure_date": (
                    leg.segment.departure_date.isoformat() if leg.segment.departure_date else ""
                ),
                "countries": list(leg.segment.countries),
                "country_raw": leg.segment.country_raw,
                "notes": list(leg.segment.notes),
                "report_id": leg.report_id,
                "source_file": leg.source_file,
            }
            for leg in trip.legs
        ],
        filings=[
            {
                "report_id": filing.report_id,
                "source_file": filing.source_file,
                "sponsor_type": filing.sponsor_type,
                "sponsor_name": filing.sponsor_name,
                "sponsor_code": filing.sponsor_code,
                "publication_date": (
                    filing.publication_date.isoformat() if filing.publication_date else ""
                ),
                "amended": filing.amended,
            }
            for filing in trip.filings
        ],
        per_diem_usd=str(trip.per_diem_usd) if trip.per_diem_usd is not None else None,
        transportation_usd=(
            str(trip.transportation_usd) if trip.transportation_usd is not None else None
        ),
        other_usd=str(trip.other_usd) if trip.other_usd is not None else None,
        military_air=trip.military_air,
        publication_date=(
            trip.publication_date.isoformat() if trip.publication_date else ""
        ),
        amended=trip.amended,
        notes=list(trip.notes),
    )
    return row
```

3c. `make_handler` gains the trips list — change the signature and first line:

```python
def make_handler(
    reports: list[Report],
    report_text_dir: Path,
    corrections_path: Path,
    trips: list[Trip],
) -> type[BaseHTTPRequestHandler]:
    """Build a request handler class closed over this run's reports and trips."""
    reports_by_id = {r.report_id: r for r in reports}
    trips_by_id = {t.trip_id: t for t in trips}
```

3d. In `do_GET`, extend the static whitelist (so `trips.html` serves) and add the routes. Change:

```python
                elif path in ("/report.html", "/app.css", "/app.js"):
                    self._serve_static(path.lstrip("/"))
```

to:

```python
                elif path in ("/report.html", "/trips.html", "/app.css", "/app.js"):
                    self._serve_static(path.lstrip("/"))
```

and after the `/api/reports/` branch, add:

```python
                elif path == "/api/trips":
                    self._send_json([_trip_row(t) for t in trips])
                elif path.startswith("/api/trips/"):
                    self._send_trip_detail(path[len("/api/trips/") :])
```

3e. Add the detail method next to `_send_report_detail` (mirrors its 404 handling):

```python
        def _send_trip_detail(self, trip_id: str) -> None:
            trip = trips_by_id.get(trip_id)
            if trip is None:
                self.send_error(404)
                return
            self._send_json(_trip_detail(trip))
```

3f. `run_server` gains the trips parameter and an attention count in the boot line:

```python
def run_server(
    reports: list[Report],
    report_text_dir: Path,
    corrections_path: Path,
    trips: list[Trip],
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Start the review server and block until interrupted (Ctrl-C).

    Every parsed report is browsable; the list view defaults to showing only
    the flagged subset (the review queue), with a toggle for everything else.
    ``trips`` (from ``parsing.trips.build_trips`` over the same reports)
    powers the read-only trips view at /trips.html.
    """
    flagged = sum(1 for r in reports if r.flags)
    attention = sum(1 for t in trips if TRIP_ATTENTION_FLAGS & set(t.flags))
    handler_cls = make_handler(reports, report_text_dir, corrections_path, trips)
    server = ThreadingHTTPServer((host, port), handler_cls)
    print(
        f"Review server running at http://{host}:{port}/ "
        f"({len(reports)} reports, {flagged} flagged · "
        f"{len(trips)} trips, {attention} need attention)"
    )
```

(keep the rest of the function — `serve_forever`/`KeyboardInterrupt`/`finally` — unchanged).

**Step 4: Run the tests**

Run: `uv run pytest tests/test_review_server.py tests/test_cli_review.py -x`
Expected: `test_review_server.py` PASSES. `test_cli_review.py` **FAILS** — `run_server()` missing 1 required positional argument: 'trips' (the CLI hasn't been updated yet; that's Task 4). This is the expected state: run `uv run pytest tests/test_review_server.py -x` alone to confirm the new file passes, then proceed.

**Step 5: Lint scope check**

Run: `uv run black --check official_foreign_travel/review/server.py && uv run ruff check official_foreign_travel/review/server.py && uv run mypy official_foreign_travel/review/server.py 2>&1 | grep "server.py" ; echo done`
Expected: clean (no lines naming server.py).

---

### Task 4: Derive trips in the review CLI (`cli/review.py`)

**Files:**
- Modify: `official_foreign_travel/cli/review.py` (additive — the user's in-flight corrections block stays untouched)
- Test: `tests/test_cli_review.py`

**Step 1: Write the failing tests**

Append to `tests/test_cli_review.py` (it already imports `date`, `json`, and the report builders; the file's existing `fake_run_server` tests use `*_args, **_kwargs` — the new fakes spell out the signature so they capture `trips`):

```python
class TestReviewCliTrips:
    def test_derives_trips_after_applying_corrections(self, tmp_path, monkeypatch):
        # A correction that fixes a segment's arrival date must change the
        # derived trip: corrections are applied BEFORE build_trips runs.
        parsed_json = tmp_path / "output.json"
        parsed_json.write_text(json.dumps({"reports": [_report_with_segment().model_dump(mode="json")]}))
        corrections = tmp_path / "corrections.json"
        corrections.write_text(
            json.dumps(
                {
                    "r-1": {
                        "status": "edited",
                        "edits": {"travelers[0].segments[0].arrival_date": "2018-02-01"},
                    }
                }
            )
        )
        captured = {}

        def fake_run_server(reports, _report_text_dir, _corrections_path, trips, **_kwargs):
            captured["trips"] = trips

        monkeypatch.setattr("official_foreign_travel.cli.review.run_server", fake_run_server)

        code = run_cli(
            [str(tmp_path), str(parsed_json), "--corrections", str(corrections)], monkeypatch
        )

        assert code == 0
        assert [t.start_date for t in captured["trips"]] == [date(2018, 2, 1)]

    def test_excludes_superseded_reports_from_trips(self, tmp_path, monkeypatch):
        superseded = _report_with_segment()
        superseded.superseded_by = "r-2"
        parsed_json = tmp_path / "output.json"
        parsed_json.write_text(
            json.dumps({"reports": [superseded.model_dump(mode="json")]})
        )
        captured = {}

        def fake_run_server(reports, _report_text_dir, _corrections_path, trips, **_kwargs):
            captured["trips"] = trips
            captured["reports"] = reports

        monkeypatch.setattr("official_foreign_travel.cli.review.run_server", fake_run_server)

        code = run_cli([str(tmp_path), str(parsed_json)], monkeypatch)

        assert code == 0
        # Superseded filings contribute no trips...
        assert captured["trips"] == []
        # ...but stay browsable as filings in the report list.
        assert len(captured["reports"]) == 1
```

**Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cli_review.py -k TestReviewCliTrips -x`
Expected: FAIL — `TypeError: run_server() missing 1 required positional argument: 'trips'` or `fake_run_server() missing ...` (the CLI doesn't pass trips yet).

**Step 3: Implement**

In `official_foreign_travel/cli/review.py`:

3a. Add imports with the existing `from ..review.corrections import ...` block:

```python
from ..parsing.serialize import visible_reports
from ..parsing.trips import build_trips
```

3b. Change the end of `main()` from:

```python
    reports = apply_corrections(reports, corrections)

    run_server(reports, args.report_text_dir, args.corrections, host=args.host, port=args.port)
    return 0
```

to:

```python
    reports = apply_corrections(reports, corrections)

    # Trips are derived once, after corrections: the trips view always
    # reflects this session's reports, with no separate file to keep in
    # sync. Superseded filings are excluded (matching `oft-parse --trips`)
    # but remain browsable in the filings list. Corrections saved during
    # the session don't re-derive trips -- restart to refresh.
    trips = build_trips(visible_reports(reports, include_superseded=False))

    run_server(
        reports, args.report_text_dir, args.corrections, trips, host=args.host, port=args.port
    )
    return 0
```

**Step 4: Run the tests**

Run: `uv run pytest tests/test_cli_review.py tests/test_review_server.py -x`
Expected: PASS (both files, all tests — including the pre-existing ones whose `fake_run_server(reports, *_args, **_kwargs)` still works because `trips` is positional).

**Step 5: Lint scope check**

Run: `uv run black --check official_foreign_travel/cli/review.py && uv run ruff check official_foreign_travel/cli/review.py && uv run mypy official_foreign_travel/cli/review.py 2>&1 | grep "review.py" ; echo done`
Expected: clean.

---

### Task 5: Frontend — trips page (`trips.html`, `app.js`, `index.html`, `app.css`)

No JS test harness exists (parity with the rest of the review UI: server payload builders are tested in Task 3; the page is verified by booting the server in Task 7). Write carefully — this code has no automated safety net.

**Files:**
- Create: `official_foreign_travel/review/static/trips.html`
- Modify: `official_foreign_travel/review/static/app.js` (append)
- Modify: `official_foreign_travel/review/static/index.html` (one line)
- Modify: `official_foreign_travel/review/static/app.css` (append)

**Step 1: Create `trips.html`**

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Trip Review</title>
  <link rel="stylesheet" href="/app.css">
</head>
<body>
  <h1>Trip Review <a class="nav-link" href="/">&larr; Filings</a></h1>
  <div id="progress"></div>
  <select id="trip-flag-filter">
    <option value="">All flags</option>
  </select>
  <label id="attention-only-label">
    <input type="checkbox" id="attention-only" checked> Needs attention only
  </label>
  <input type="search" id="trip-search" placeholder="Search name, bioguide, or trip id">
  <p class="hint">Trips are derived from the loaded reports when the server starts --
    corrections saved during this session appear here after restarting oft-review.</p>
  <table id="trips-table">
    <thead>
      <tr>
        <th data-sort="name">Traveler</th>
        <th data-sort="start_date">Start</th>
        <th data-sort="end_date">End</th>
        <th>Countries</th>
        <th data-sort="n_legs">Legs</th>
        <th data-sort="n_filings">Filings</th>
        <th>Flags</th>
      </tr>
    </thead>
    <tbody id="trips-body"></tbody>
  </table>
  <script src="/app.js"></script>
  <script>renderTrips();</script>
</body>
</html>
```

**Step 2: Add the nav link to `index.html`**

Change:

```html
  <h1>Report Review</h1>
```

to:

```html
  <h1>Report Review <a class="nav-link" href="/trips.html">Trips &rarr;</a></h1>
```

**Step 3: Append the trips view to `app.js`**

Append at the end of the file (it reuses `escapeHtml`, `sortReports`, and `flagBadges` defined earlier in the file):

```javascript
// --- Trips view -----------------------------------------------------------

const TRIPS_API = "/api/trips";

// Mirror of parsing/trips.py TRIP_ATTENTION_FLAGS. The flags that make a
// trip worth a second look; TRIP_SPLIT_BY_DATE_GAP is informational (a
// filing legitimately reporting two separate journeys) and a plain
// TRIP_MERGED_ACROSS_FILINGS is the merge working as intended.
const ATTENTION_FLAGS = [
  "TRIP_DATE_DESTINATION_CONFLICT",
  "TRIP_MATCHED_NAME_ONLY",
  "TRIP_MERGED_MIXED_IDENTITY",
  "TRIP_DATES_MISSING",
];

const tripSort = { column: null, ascending: true };
const TRIPS_FILTER_STORAGE_KEY = "oft-review-trips-filters";
const tripDetailCache = new Map();

function saveTripsFilters(state) {
  try {
    localStorage.setItem(TRIPS_FILTER_STORAGE_KEY, JSON.stringify(state));
  } catch (e) {
    // localStorage unavailable -- filters just won't persist
  }
}

function loadTripsFilters() {
  try {
    return JSON.parse(localStorage.getItem(TRIPS_FILTER_STORAGE_KEY)) || {};
  } catch (e) {
    return {};
  }
}

function tripNeedsAttention(trip) {
  return trip.flags.some((flag) => ATTENTION_FLAGS.includes(flag));
}

function populateTripFlagFilter(trips) {
  const select = document.getElementById("trip-flag-filter");
  const flags = Array.from(new Set(trips.flatMap((t) => t.flags))).sort();
  flags.forEach((flag) => {
    const option = document.createElement("option");
    option.value = flag;
    option.textContent = flag;
    select.appendChild(option);
  });
}

function updateTripSortIndicators() {
  document.querySelectorAll("#trips-table th[data-sort]").forEach((th) => {
    const isActive = th.dataset.sort === tripSort.column;
    th.classList.toggle("sorted", isActive);
    th.dataset.sortArrow = isActive ? (tripSort.ascending ? "▲" : "▼") : "";
  });
}

async function renderTrips() {
  const res = await fetch(TRIPS_API);
  const trips = await res.json();
  populateTripFlagFilter(trips);

  const flagSelect = document.getElementById("trip-flag-filter");
  const attentionOnly = document.getElementById("attention-only");
  const search = document.getElementById("trip-search");

  const saved = loadTripsFilters();
  if (saved.attentionOnly !== undefined) attentionOnly.checked = saved.attentionOnly;
  if (
    saved.flag !== undefined &&
    Array.from(flagSelect.options).some((o) => o.value === saved.flag)
  ) {
    flagSelect.value = saved.flag;
  }
  if (saved.search !== undefined) search.value = saved.search;
  if (saved.sortColumn) {
    tripSort.column = saved.sortColumn;
    tripSort.ascending = saved.sortAscending !== false;
  }

  const rerender = () => {
    saveTripsFilters({
      attentionOnly: attentionOnly.checked,
      flag: flagSelect.value,
      search: search.value,
      sortColumn: tripSort.column,
      sortAscending: tripSort.ascending,
    });
    renderTripRows(trips, attentionOnly.checked, flagSelect.value, search.value);
  };
  flagSelect.onchange = rerender;
  attentionOnly.onchange = rerender;
  search.oninput = rerender;

  document.querySelectorAll("#trips-table th[data-sort]").forEach((th) => {
    th.onclick = () => {
      const column = th.dataset.sort;
      tripSort.ascending = tripSort.column === column ? !tripSort.ascending : true;
      tripSort.column = column;
      rerender();
    };
  });

  const attention = trips.filter(tripNeedsAttention).length;
  const conflicted = trips.filter((t) => t.flags.includes("TRIP_DATE_DESTINATION_CONFLICT")).length;
  const merged = trips.filter((t) => t.flags.includes("TRIP_MERGED_ACROSS_FILINGS")).length;
  document.getElementById("progress").textContent =
    `${attention} need attention · ${conflicted} conflicted · ${merged} merged · ${trips.length} trips total`;

  rerender();

  // ?expand=<trip_id> deep link (e.g. from a conflict-partner link): open
  // that trip if the current filters leave it visible.
  const expand = new URLSearchParams(window.location.search).get("expand");
  if (expand) {
    const row = document.querySelector(`tr[data-trip-id="${CSS.escape(expand)}"]`);
    if (row) {
      expandTrip(expand);
      row.scrollIntoView({ block: "center" });
    }
  }
}

function renderTripRows(trips, attentionOnly, flagFilter, search) {
  let filtered = attentionOnly ? trips.filter(tripNeedsAttention) : trips;
  if (flagFilter) filtered = filtered.filter((t) => t.flags.includes(flagFilter));
  if (search) {
    const needle = search.trim().toLowerCase();
    filtered = filtered.filter(
      (t) =>
        t.name.toLowerCase().includes(needle) ||
        (t.bioguide_id || "").toLowerCase().includes(needle) ||
        t.trip_id.toLowerCase().includes(needle)
    );
  }
  filtered = sortReports(filtered, tripSort.column, tripSort.ascending);
  updateTripSortIndicators();

  const body = document.getElementById("trips-body");
  body.innerHTML = "";
  if (!filtered.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="7">No trips match.</td>';
    body.appendChild(tr);
    return;
  }
  filtered.forEach((t) => {
    const tr = document.createElement("tr");
    tr.dataset.tripId = t.trip_id;
    const filingLinks = t.source_report_ids
      .map((id) => `<a href="/report.html?id=${encodeURIComponent(id)}">${escapeHtml(id)}</a>`)
      .join(", ");
    tr.innerHTML = `
      <td class="trip-traveler">${escapeHtml(t.name)}<div class="trip-sub">${escapeHtml(t.bioguide_id || "")}</div></td>
      <td>${escapeHtml(t.start_date || "")}</td>
      <td>${escapeHtml(t.end_date || "")}</td>
      <td class="countries-cell">${escapeHtml(t.countries.join("; "))}</td>
      <td>${t.n_legs}</td>
      <td class="filings-cell">${filingLinks}</td>
      <td></td>
    `;
    tr.querySelector("td:last-child").appendChild(flagBadges(t.flags));
    // Clicking anywhere in the row expands it -- except on a filing link.
    tr.onclick = (event) => {
      if (event.target.closest("a")) return;
      expandTrip(t.trip_id);
    };
    body.appendChild(tr);
  });
}

async function expandTrip(tripId) {
  const tr = document.querySelector(`tr[data-trip-id="${CSS.escape(tripId)}"]`);
  if (!tr) return;
  const existing = tr.nextElementSibling;
  if (existing && existing.classList.contains("trip-detail-row")) {
    existing.remove(); // clicking again collapses
    return;
  }
  let detail = tripDetailCache.get(tripId);
  if (!detail) {
    const res = await fetch(`${TRIPS_API}/${encodeURIComponent(tripId)}`);
    if (!res.ok) return;
    detail = await res.json();
    tripDetailCache.set(tripId, detail);
  }

  const legsRows = detail.legs
    .map(
      (leg) => `
      <tr>
        <td>${escapeHtml(leg.country_raw || leg.countries.join(", "))}</td>
        <td>${escapeHtml(leg.arrival_date || "")}</td>
        <td>${escapeHtml(leg.departure_date || "")}</td>
        <td><a href="/report.html?id=${encodeURIComponent(leg.report_id)}">${escapeHtml(leg.report_id)}</a></td>
      </tr>`
    )
    .join("");
  const filingRows = detail.filings
    .map(
      (f) => `
      <tr>
        <td><a href="/report.html?id=${encodeURIComponent(f.report_id)}">${escapeHtml(f.report_id)}</a></td>
        <td>${escapeHtml(f.sponsor_name)}</td>
        <td>${escapeHtml(f.source_file)}</td>
        <td>${escapeHtml(f.publication_date || "")}${f.amended ? " (amended)" : ""}</td>
      </tr>`
    )
    .join("");
  const conflictLinks = detail.conflict_with.length
    ? detail.conflict_with
        .map(
          (id) => `<a href="/trips.html?expand=${encodeURIComponent(id)}">${escapeHtml(id)}</a>`
        )
        .join(", ")
    : "none";

  const detailRow = document.createElement("tr");
  detailRow.className = "trip-detail-row";
  const cell = document.createElement("td");
  cell.colSpan = 7; // keep in sync with the trips-table header count
  cell.innerHTML = `
    <div class="trip-detail">
      <h4>Legs (${detail.n_legs})</h4>
      <table class="trip-legs">
        <thead><tr><th>Destination</th><th>Arrival</th><th>Departure</th><th>Filing</th></tr></thead>
        <tbody>${legsRows}</tbody>
      </table>
      <h4>Filings (${detail.n_filings}, newest first)</h4>
      <table class="trip-legs">
        <thead><tr><th>Report</th><th>Sponsor</th><th>Source file</th><th>Published</th></tr></thead>
        <tbody>${filingRows}</tbody>
      </table>
      <h4>Conflicts with</h4>
      <div>${conflictLinks}</div>
    </div>`;
  detailRow.appendChild(cell);
  tr.after(detailRow);
}
```

**Step 4: Append styles to `app.css`**

```css
/* --- Trips view --- */

.nav-link { font-size: 0.85rem; font-weight: 500; color: var(--accent); text-decoration: none; margin-left: 0.75rem; }
.nav-link:hover { text-decoration: underline; }

#trip-search {
  padding: 0.35rem 0.5rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  margin-left: 0.5rem;
  font: inherit;
}
#attention-only-label { font-size: 0.9rem; margin-left: 0.5rem; user-select: none; }

.hint { color: var(--muted); font-size: 0.78rem; margin: 0.5rem 0 0; }

.trip-traveler { font-weight: 600; }
.trip-sub { color: var(--muted); font-weight: 400; font-size: 0.72rem; }
.countries-cell, .filings-cell { color: var(--muted); font-size: 0.78rem; }

.trip-detail-row td { background: #eef1f5; }
.trip-detail { padding: 0.4rem 0.6rem; }
.trip-detail h4 {
  margin: 0.5rem 0 0.25rem;
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--muted);
}
.trip-legs { margin-top: 0; margin-bottom: 0.4rem; }
.trip-legs th { position: static; background: none; text-transform: none; letter-spacing: 0; font-size: 0.7rem; }
```

**Step 5: Sanity-check without a browser**

Run: `uv run python -c "import pathlib; js = pathlib.Path('official_foreign_travel/review/static/app.js').read_text(); assert js.count('{') == js.count('}'), 'unbalanced braces'; assert 'renderTrips' in js and 'expandTrip' in js; print('js ok:', len(js.splitlines()), 'lines')"`
Expected: `js ok: <N> lines`.

Also confirm nothing regressed: `uv run pytest tests/test_cli_review.py tests/test_review_server.py -q`
Expected: all pass.

---

### Task 6: Documentation

**Files:**
- Modify: `TECHNICAL_README.md` (review section)
- Modify: `about_the_data.md` (trips paragraph)
- Modify: `CHANGELOG.md` (`[Unreleased]`)
- Modify: `docs/review-glossary.md` (Trip term + seven `TRIP_*` badges)

**Step 1: `TECHNICAL_README.md`** — in "### Reviewing Flagged Reports", insert after the paragraph that ends "...never tied to one specific run's output." (i.e. before the `--port`/`--host` paragraph):

```markdown
The trips view (`/trips.html`, "Trips" link at the top of the filings list) shows
the canonical trip records derived from the same loaded reports: one row per
person-journey, defaulting to the "needs attention" subset (date/destination
conflicts, name-only merges, mixed-identity merges, undated trips). Clicking a
row expands its legs and per-filing provenance; each filing links into the
familiar report detail page, so a conflict is fixed by correcting the underlying
filing and restarting. Trip records are read-only -- corrections are still made
on filings, never on trips.
```

**Step 2: `about_the_data.md`** — in the trips output paragraph, change:

```markdown
pairs are flagged `TRIP_DATE_DESTINATION_CONFLICT`. Like the other outputs it is not
```

to:

```markdown
pairs are flagged `TRIP_DATE_DESTINATION_CONFLICT`; each side's `conflict_with`
column names the other side's `trip_id`, so the pairs are self-describing. Like
the other outputs it is not
```

**Step 3: `CHANGELOG.md`** — inside the existing `## [Unreleased]` trips entry, after the "On the full corpus:" line, add:

```markdown
Trip records also carry `conflict_with` (a final CSV column; a field in
JSONL/JSON): the trip ids of the other side of any date/destination
conflict, so conflict pairs are clickable/triageable outside the browser.

### Added - Trips view in the review browser

`oft-review` now derives trips once at startup, right after applying
corrections, and serves a read-only trips page at `/trips.html`:
one row per person-journey with in-page leg/filing detail, links into
the underlying filings, a "needs attention" default filter
(conflicts, name-only merges, mixed-identity merges, undated trips),
a flag filter, and a name/bioguide/trip-id search. Two new API routes
back it: `/api/trips` and `/api/trips/{trip_id}`. No new CLI flags;
restart `oft-review` to refresh trips after saving corrections.
```

**Step 4: `docs/review-glossary.md`** — two edits:

4a. In "How the app is organized", add after the **Segment** bullet:

```markdown
- **Trip** — one person's journey, built by stitching together that traveler's
  appearances across *multiple* reports (two committees co-sponsoring one
  delegation each file their own report; a trip spanning a quarter boundary is
  published twice). One traveler with a multi-stop itinerary is still one trip.
  Shown on the Trips page (`trips.html`), which is read-only: to fix something,
  open the filing the trip points to and correct it there.
```

4b. In "## Badges (flags)", add a new section (after the "Traveler identity matching" section, before "Report/table structure, sponsor, and period" — keep the established format of bold flag, bold attach point, then plain English):

```markdown
### Trip construction (across filings)

These badges appear on the Trips page, not on filings. They record how trip
records were stitched together across reports.

- **TRIP_MERGED_ACROSS_FILINGS** — **trip.** This journey was reported by more
  than one filing (e.g. two committees co-sponsoring one delegation, or a trip
  straddling a quarter boundary published twice), and the duplicates were
  combined into a single trip. The trip's dates, destinations, and costs come
  from the most recently published filing; costs are never added across
  filings. By itself this badge is good news — the merge worked.
- **TRIP_DATE_DESTINATION_CONFLICT** — **trip.** Two filings say the same
  person traveled in the same date window but disagree about where they went
  (a committee filing "EUROPE" and another filing "DENMARK"). The two records
  were deliberately *not* merged; each one's detail view lists the other under
  "Conflicts with". Worth checking which filing has the more specific
  destination, and correcting the vaguer one if it's wrong.
- **TRIP_MATCHED_NAME_ONLY** — **trip.** The filings describing this journey
  were combined by matching the traveler's name plus dates and destinations,
  with no member ID on any side — typically a spouse or staffer, or a member
  the roster lookup couldn't confirm. If the same person appears under
  slightly different names, this is how they were joined.
- **TRIP_MERGED_MIXED_IDENTITY** — **trip.** One filing listed this person
  with their member ID and another without, but the bare name is unique to
  exactly one member of Congress, so the two were combined. Safe unless two
  members share the name.
- **TRIP_DATES_MISSING** — **trip.** No dated leg could be recovered for this
  journey (the source shows no usable dates), so it was never merged across
  filings — matching requires dates on both sides.
- **TRIP_SPLIT_BY_DATE_GAP** — **trip.** Within one filing, this traveler's
  legs had a gap of more than three days between them, so they were treated
  as two separate journeys rather than one long trip. Informational: check
  the source if it looks like one continuous trip.
- **TRIP_GAP_FILLED_FROM_EARLIER_FILING** — **trip.** The winning filing left
  a leg's dates or destination blank and an older filing of the same journey
  supplied them. The detail view shows which filing each leg came from.
```

**Step 5: Verify docs references**

Run: `grep -c "TRIP_" docs/review-glossary.md && grep -n "trips.html" TECHNICAL_README.md CHANGELOG.md`
Expected: glossary count ≥ 7; both files mention `trips.html`.

---

### Task 7: Full verification

**Step 1: Full test suite**

Run: `uv run pytest`
Expected: all pass (824 pre-existing + ~9 new). One pre-existing skip is normal.

**Step 2: Lint/type scope**

Run:
```bash
uv run black --check official_foreign_travel/parsing/trips.py official_foreign_travel/parsing/serialize.py official_foreign_travel/cli/review.py official_foreign_travel/review/server.py
uv run ruff check official_foreign_travel/parsing/trips.py official_foreign_travel/parsing/serialize.py official_foreign_travel/cli/review.py official_foreign_travel/review/server.py
uv run mypy official_foreign_travel/parsing/trips.py official_foreign_travel/review/server.py official_foreign_travel/cli/review.py 2>&1 | grep -E "server.py|cli/review.py" ; echo "mypy-scoped-clean"
```
Expected: black clean, ruff clean, no mypy lines naming the touched files.

**Step 3: Corpus smoke — trips outputs with `conflict_with`**

If `/tmp/oft-trips-verify/` no longer exists, regenerate `output.json` first with
`uv run oft-parse report_text/ /tmp/oft-trips-verify/output.json --apply-corrections corrections.json`.

```bash
uv run oft-parse report_text/ /tmp/oft-trips-verify/smoke.json --trips /tmp/oft-trips-verify/smoke-trips.csv --apply-corrections corrections.json 2>&1 | tail -4
uv run python - <<'EOF'
import csv
rows = list(csv.DictReader(open('/tmp/oft-trips-verify/smoke-trips.csv', newline='')))
ids = {r["trip_id"] for r in rows}
conflict_rows = [r for r in rows if r["conflict_with"]]
flagged = [r for r in rows if "TRIP_DATE_DESTINATION_CONFLICT" in r["flags"]]
print(f"trips: {len(rows)}, conflict_with non-empty: {len(conflict_rows)}, flag: {len(flagged)}")
assert len(conflict_rows) == len(flagged), "conflict_with and the flag must cover the same rows"
assert all(pid in ids for r in conflict_rows for pid in r["conflict_with"].split("; ")), "conflict_with must reference real trip ids"
print("smoke-trips ok")
EOF
```
Expected: `trips: 26763, conflict_with non-empty: ~505, flag: ~505` and `smoke-trips ok` (conflict-with rows ≈ flagged rows; a tiny shortfall is acceptable if some conflicts self-resolved via a third filing — but the id-integrity assert must hold).

**Step 4: Corpus smoke — review server over HTTP**

```bash
uv run oft-review report_text/ /tmp/oft-trips-verify/smoke.json --corrections corrections.json --port 8799 > /tmp/oft-trips-verify/review-boot.log 2>&1 &
BOOT_PID=$!
sleep 25   # ~6s report load + ~8s build_trips + corrections, with headroom
cat /tmp/oft-trips-verify/review-boot.log
curl -s http://127.0.0.1:8799/api/trips | uv run python -c "import json,sys; d=json.load(sys.stdin); print('api/trips rows:', len(d)); print('first:', d[0]['trip_id'], d[0]['name'])"
TRIP_ID=$(curl -s http://127.0.0.1:8799/api/trips | uv run python -c "import json,sys; d=json.load(sys.stdin); print(next(t['trip_id'] for t in d if t['conflict_with']))")
curl -s "http://127.0.0.1:8799/api/trips/$TRIP_ID" | uv run python -c "import json,sys; d=json.load(sys.stdin); print('detail legs:', len(d['legs']), 'filings:', len(d['filings']), 'conflicts:', d['conflict_with'])"
curl -s -o /dev/null -w "trips.html: %{http_code}\n" http://127.0.0.1:8799/trips.html
curl -s -o /dev/null -w "unknown trip 404: %{http_code}\n" http://127.0.0.1:8799/api/trips/no-such-trip
kill $BOOT_PID
```
Expected:
- boot log line contains `(3606 reports, 3494 flagged · 26763 trips, ~1100 need attention)`
- `api/trips rows: 26763`
- detail shows legs/filings/conflicts for a conflicted trip
- `trips.html: 200`, `unknown trip 404: 404`

**Step 5: Report**

Report all verification output to the user, including the corpus numbers, anything that deviated from expectations, and a reminder that the browser rendering itself needs their eyes (open `http://127.0.0.1:8765/trips.html` in their own session). Do not commit anything.