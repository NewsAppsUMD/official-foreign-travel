# Report Review UI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A local web UI (`oft-review`) that shows a flagged report's original fixed-width
source text and its extracted structured data side by side, lets a reviewer edit any field,
persists edits to a separate corrections overlay file, and merges those corrections back
into a fresh parse via `oft-parse --apply-corrections`.

**Architecture:** New `official_foreign_travel/review/` package: `corrections.py` (dotted-path
get/set into a JSON-shaped report dict, load/save the overlay file, merge corrections onto
`Report` objects), `source_lookup.py` (re-locate a report's raw lines by re-running the
existing segmenter), `server.py` (stdlib `http.server.ThreadingHTTPServer` + a request
handler serving static HTML/CSS/JS and a small JSON API), and `static/` (plain HTML/CSS/JS,
no build step). A new `oft-review` CLI entry point starts the server. `cli/parse.py` gains
`--apply-corrections` to merge the overlay back into a fresh parse.

**Tech Stack:** Python stdlib only (`http.server`, `json`, `pathlib`) on the backend;
vanilla HTML/CSS/JS on the frontend. No new dependencies. Builds on the existing
`official_foreign_travel.models.report.Report` Pydantic model and
`official_foreign_travel.parsing.segmenter.segment_tables`.

See `docs/plans/2026-07-08-report-review-ui-design.md` for the full design rationale
(corrections schema, API shape, UI/UX) — this plan implements that design.

---

## Task 1: Corrections path get/set

**Files:**
- Create: `official_foreign_travel/review/__init__.py`
- Create: `official_foreign_travel/review/corrections.py`
- Test: `tests/test_review_corrections.py`

**Step 1: Create the package init**

```python
# official_foreign_travel/review/__init__.py
"""Local review server for QA'ing flagged parser output."""
```

**Step 2: Write the failing tests for path parsing/get/set**

```python
# tests/test_review_corrections.py
"""Tests for the corrections overlay: dotted-path get/set, load/save, and merging."""

import pytest

from official_foreign_travel.review.corrections import get_path, set_path


class TestGetPath:
    def test_simple_field(self):
        assert get_path({"a": 1}, "a") == 1

    def test_nested_field(self):
        assert get_path({"a": {"b": 2}}, "a.b") == 2

    def test_list_index(self):
        assert get_path({"a": [1, 2, 3]}, "a[1]") == 2

    def test_nested_list_and_field(self):
        data = {"travelers": [{"name": "X"}]}
        assert get_path(data, "travelers[0].name") == "X"

    def test_deep_chain(self):
        data = {"travelers": [{"segments": [{"costs": {"total": {"us_dollar": {"amount": "5"}}}}]}]}
        assert get_path(data, "travelers[0].segments[0].costs.total.us_dollar.amount") == "5"

    def test_invalid_segment_raises(self):
        with pytest.raises(ValueError):
            get_path({"a": 1}, "a[")


class TestSetPath:
    def test_simple_field(self):
        data = {"a": 1}
        set_path(data, "a", 2)
        assert data == {"a": 2}

    def test_nested_field(self):
        data = {"a": {"b": 2}}
        set_path(data, "a.b", 3)
        assert data["a"]["b"] == 3

    def test_list_index_field(self):
        data = {"travelers": [{"name": "X"}]}
        set_path(data, "travelers[0].name", "Y")
        assert data["travelers"][0]["name"] == "Y"

    def test_deep_chain(self):
        data = {"travelers": [{"segments": [{"costs": {"total": {"us_dollar": {"amount": "5"}}}}]}]}
        set_path(data, "travelers[0].segments[0].costs.total.us_dollar.amount", "9.99")
        assert data["travelers"][0]["segments"][0]["costs"]["total"]["us_dollar"]["amount"] == "9.99"
```

**Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_review_corrections.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'official_foreign_travel.review.corrections'`

**Step 4: Implement `get_path`/`set_path`**

```python
# official_foreign_travel/review/corrections.py
"""Corrections overlay: dotted/indexed-path edits into a report dict, persisted to disk."""

import re
from typing import Any, List, Optional, Tuple

_TOKEN_RE = re.compile(r"^([^.\[\]]+)(\[(\d+)\])?$")


def _parse_path(path: str) -> List[Tuple[str, Optional[int]]]:
    """Parse 'travelers[2].segments[0].costs.total' into
    [("travelers", 2), ("segments", 0), ("costs", None), ("total", None)]."""
    tokens = []
    for part in path.split("."):
        match = _TOKEN_RE.match(part)
        if not match:
            raise ValueError(f"Invalid path segment: {part!r} in path {path!r}")
        key, _, index = match.groups()
        tokens.append((key, int(index) if index is not None else None))
    return tokens


def get_path(data: Any, path: str) -> Any:
    """Read a value out of a JSON-shaped dict using a dotted/indexed path."""
    current = data
    for key, index in _parse_path(path):
        current = current[key]
        if index is not None:
            current = current[index]
    return current


def set_path(data: Any, path: str, value: Any) -> None:
    """Write a value into a JSON-shaped dict using a dotted/indexed path, in place."""
    tokens = _parse_path(path)
    current = data
    for key, index in tokens[:-1]:
        current = current[key]
        if index is not None:
            current = current[index]
    last_key, last_index = tokens[-1]
    if last_index is not None:
        current[last_key][last_index] = value
    else:
        current[last_key] = value
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_review_corrections.py -v`
Expected: PASS (10 tests)

**Step 6: Commit**

```bash
git add official_foreign_travel/review/__init__.py official_foreign_travel/review/corrections.py tests/test_review_corrections.py
git commit -m "Add dotted-path get/set for the review corrections overlay"
```

---

## Task 2: Load/save the corrections overlay file

**Files:**
- Modify: `official_foreign_travel/review/corrections.py`
- Test: `tests/test_review_corrections.py`

**Step 1: Write the failing tests**

Append to `tests/test_review_corrections.py`:

```python
import json

from official_foreign_travel.review.corrections import load_corrections, save_report_correction


class TestLoadCorrections:
    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert load_corrections(tmp_path / "does-not-exist.json") == {}

    def test_loads_existing_file(self, tmp_path):
        path = tmp_path / "corrections.json"
        path.write_text(json.dumps({"r-1": {"status": "confirmed_ok", "edits": {}}}))
        assert load_corrections(path) == {"r-1": {"status": "confirmed_ok", "edits": {}}}


class TestSaveReportCorrection:
    def test_creates_file_if_missing(self, tmp_path):
        path = tmp_path / "corrections.json"
        entry = save_report_correction(path, "r-1", "edited", {"sponsor.name": "Fixed"})
        assert path.exists()
        assert entry["status"] == "edited"
        assert entry["edits"] == {"sponsor.name": "Fixed"}
        assert "reviewed_at" in entry

    def test_overwrites_existing_entry_for_same_report(self, tmp_path):
        path = tmp_path / "corrections.json"
        save_report_correction(path, "r-1", "edited", {"a": "1"})
        save_report_correction(path, "r-1", "edited", {"a": "2"})
        data = load_corrections(path)
        assert data["r-1"]["edits"] == {"a": "2"}

    def test_preserves_other_reports_entries(self, tmp_path):
        path = tmp_path / "corrections.json"
        save_report_correction(path, "r-1", "confirmed_ok", {})
        save_report_correction(path, "r-2", "edited", {"a": "1"})
        data = load_corrections(path)
        assert set(data.keys()) == {"r-1", "r-2"}
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_review_corrections.py -v -k "LoadCorrections or SaveReportCorrection"`
Expected: FAIL with `ImportError`

**Step 3: Implement**

Append to `official_foreign_travel/review/corrections.py`:

```python
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict


def load_corrections(path: Path) -> Dict[str, dict]:
    """Load the corrections overlay file, or return {} if it doesn't exist yet."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_report_correction(
    path: Path, report_id: str, status: str, edits: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Save (replacing) one report's correction entry, preserving all others.

    Returns:
        The entry that was just saved.
    """
    corrections = load_corrections(path)
    entry = {
        "status": status,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "edits": edits,
    }
    corrections[report_id] = entry
    path.write_text(json.dumps(corrections, indent=2), encoding="utf-8")
    return entry
```

Add `Any` to the existing `from typing import ...` line at the top of the file (it already
imports `Any` transitively? No -- add it explicitly): update the top import to
`from typing import Any, List, Optional, Tuple`.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_review_corrections.py -v`
Expected: PASS (14 tests total)

**Step 5: Commit**

```bash
git add official_foreign_travel/review/corrections.py tests/test_review_corrections.py
git commit -m "Add load/save for the review corrections overlay file"
```

---

## Task 3: Merge corrections onto Report objects

**Files:**
- Modify: `official_foreign_travel/review/corrections.py`
- Test: `tests/test_review_corrections.py`

**Step 1: Write the failing tests**

Append to `tests/test_review_corrections.py`:

```python
from datetime import date
from decimal import Decimal

from official_foreign_travel.models.report import (
    Costs,
    CostCell,
    CostGroup,
    Period,
    Report,
    Sponsor,
    Traveler,
    TravelSegment,
)
from official_foreign_travel.review.corrections import apply_corrections


def _cell(amount=None):
    return CostCell(amount=Decimal(amount) if amount is not None else None, raw="")


def _costs(total=None):
    empty = _cell()
    return Costs(per_diem=CostGroup(foreign_currency=empty, us_dollar=_cell(total)),
                 transportation=CostGroup(foreign_currency=empty, us_dollar=empty),
                 other=CostGroup(foreign_currency=empty, us_dollar=empty),
                 total=CostGroup(foreign_currency=empty, us_dollar=_cell(total)))


def _report(report_id, sponsor_name="COMMITTEE ON TEST"):
    segment = TravelSegment(
        arrival_date=date(2018, 1, 5), departure_date=date(2018, 1, 8),
        arrival_raw="1/5", departure_raw="1/8", country_raw="Testland",
        costs=_costs("100.00"),
    )
    return Report(
        report_id=report_id, source_file="x.txt", table_index=0,
        sponsor=Sponsor(type="committee", name=sponsor_name, raw=""),
        period=Period(start=date(2018, 1, 1), end=date(2018, 3, 31), year=2018, quarter=1),
        header_raw="", travelers=[Traveler(name="A", segments=[segment])],
    )


class TestApplyCorrections:
    def test_report_with_no_correction_entry_is_unchanged(self):
        report = _report("r-1")
        result = apply_corrections([report], {})
        assert result[0] is report
        assert "MANUALLY_CORRECTED" not in result[0].flags

    def test_confirmed_ok_with_no_edits_gets_flagged_and_unchanged(self):
        report = _report("r-1")
        corrections = {"r-1": {"status": "confirmed_ok", "edits": {}}}
        result = apply_corrections([report], corrections)
        assert result[0].sponsor.name == "COMMITTEE ON TEST"
        assert "HUMAN_CONFIRMED" in result[0].flags

    def test_edit_applies_and_flags_manually_corrected(self):
        report = _report("r-1")
        corrections = {"r-1": {"status": "edited", "edits": {"sponsor.name": "Fixed Name"}}}
        result = apply_corrections([report], corrections)
        assert result[0].sponsor.name == "Fixed Name"
        assert "MANUALLY_CORRECTED" in result[0].flags

    def test_edit_to_a_cost_amount_is_reflected_and_revalidated(self):
        report = _report("r-1")
        corrections = {
            "r-1": {
                "status": "edited",
                "edits": {
                    "travelers[0].segments[0].costs.total.us_dollar.amount": "999.00",
                },
            }
        }
        result = apply_corrections([report], corrections)
        segment = result[0].travelers[0].segments[0]
        assert segment.costs.total.us_dollar.amount == Decimal("999.00")
        # per_diem (100.00) no longer matches the corrected total (999.00) -> flagged
        assert "ROW_SUM_MISMATCH" in segment.flags
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_review_corrections.py -v -k ApplyCorrections`
Expected: FAIL with `ImportError`

**Step 3: Implement**

Append to `official_foreign_travel/review/corrections.py`:

```python
from typing import List

from ..models.report import Report
from ..parsing.validate import validate_report


def apply_corrections(reports: List[Report], corrections: Dict[str, dict]) -> List[Report]:
    """
    Merge saved human corrections onto assembled reports, in place (by replacement).

    A `confirmed_ok` entry with no edits just tags the report HUMAN_CONFIRMED. An
    `edited` entry applies each dotted-path edit onto a JSON dump of the report,
    re-parses it back into a validated Report, re-runs validate_report, and tags it
    MANUALLY_CORRECTED. Reports with no entry in `corrections` are left untouched.
    """
    for index, report in enumerate(reports):
        entry = corrections.get(report.report_id)
        if entry is None:
            continue

        edits = entry.get("edits") or {}
        if not edits:
            if entry.get("status") == "confirmed_ok" and "HUMAN_CONFIRMED" not in report.flags:
                report.flags.append("HUMAN_CONFIRMED")
            continue

        data = report.model_dump(mode="json")
        for path, value in edits.items():
            set_path(data, path, value)
        corrected = Report.model_validate(data)
        if "MANUALLY_CORRECTED" not in corrected.flags:
            corrected.flags.append("MANUALLY_CORRECTED")
        validate_report(corrected)
        reports[index] = corrected

    return reports
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_review_corrections.py -v`
Expected: PASS (18 tests total)

**Step 5: Check for circular imports**

Run: `uv run python3 -c "from official_foreign_travel.review import corrections; print('ok')"`
Expected: `ok` (no ImportError — `review` -> `parsing.validate` -> ... does not import back into `review`)

**Step 6: Commit**

```bash
git add official_foreign_travel/review/corrections.py tests/test_review_corrections.py
git commit -m "Merge human corrections back onto Report objects"
```

---

## Task 4: Locate a report's raw source lines

**Files:**
- Create: `official_foreign_travel/review/source_lookup.py`
- Test: `tests/test_review_source_lookup.py`

**Step 1: Write the failing tests**

```python
# tests/test_review_source_lookup.py
"""Tests for locating a report's raw source lines for the review UI."""

from pathlib import Path

from official_foreign_travel.parsing.assemble import assemble_file
from official_foreign_travel.review.source_lookup import get_raw_lines

FIXTURES = Path(__file__).parent / "fixtures"


class TestGetRawLines:
    def test_returns_lines_for_a_real_report(self):
        reports = assemble_file(FIXTURES / "2019q1jan29.txt")
        report = reports[0]
        lines = get_raw_lines(report, FIXTURES)
        assert lines is not None
        assert any("REPORT OF EXPENDITURES" in line for line in lines)

    def test_missing_source_file_returns_none(self, tmp_path):
        reports = assemble_file(FIXTURES / "2019q1jan29.txt")
        report = reports[0]
        report.source_file = "does-not-exist.txt"
        assert get_raw_lines(report, tmp_path) is None
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_review_source_lookup.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Implement**

```python
# official_foreign_travel/review/source_lookup.py
"""Re-locate a report's raw source lines, for the review UI's side-by-side view."""

from pathlib import Path
from typing import List, Optional

from ..models.report import Report
from ..parsing.llm_fallback import _load_block


def get_raw_lines(report: Report, report_text_dir: Path) -> Optional[List[str]]:
    """Return the raw lines of the table block a report was parsed from, or None."""
    block = _load_block(report, report_text_dir)
    return block.lines if block is not None else None
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_review_source_lookup.py -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add official_foreign_travel/review/source_lookup.py tests/test_review_source_lookup.py
git commit -m "Add raw source-line lookup for the review UI"
```

---

## Task 5: HTTP server -- static files and the reports list endpoint

**Files:**
- Create: `official_foreign_travel/review/server.py`
- Create: `official_foreign_travel/review/static/index.html` (placeholder, filled in Task 8)
- Test: `tests/test_review_server.py`

**Step 1: Create a placeholder static file so static-serving has something to serve**

```html
<!-- official_foreign_travel/review/static/index.html -->
<!DOCTYPE html>
<html><head><title>Report Review</title></head><body>placeholder</body></html>
```

**Step 2: Write the failing tests**

```python
# tests/test_review_server.py
"""Integration tests for the review server, driven in-process via http.client."""

import http.client
import json
import threading
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from official_foreign_travel.models.report import (
    Costs, CostCell, CostGroup, Period, Report, Sponsor, Traveler, TravelSegment,
)
from official_foreign_travel.review.server import make_handler
from http.server import ThreadingHTTPServer

FIXTURES = Path(__file__).parent / "fixtures"


def _cell(amount=None):
    return CostCell(amount=Decimal(amount) if amount is not None else None, raw="")


def _costs(total=None):
    empty = _cell()
    return Costs(
        per_diem=CostGroup(foreign_currency=empty, us_dollar=_cell(total)),
        transportation=CostGroup(foreign_currency=empty, us_dollar=empty),
        other=CostGroup(foreign_currency=empty, us_dollar=empty),
        total=CostGroup(foreign_currency=empty, us_dollar=_cell(total)),
    )


def _flagged_report(report_id):
    segment = TravelSegment(
        arrival_date=date(2018, 1, 5), departure_date=date(2018, 1, 8),
        arrival_raw="1/5", departure_raw="1/8", country_raw="Testland",
        costs=_costs("100.00"), source_lines=[1],
    )
    return Report(
        report_id=report_id, source_file="2019q1jan29.txt", table_index=0,
        sponsor=Sponsor(type="committee", name="COMMITTEE ON TEST", raw=""),
        period=Period(start=date(2018, 1, 1), end=date(2018, 3, 31), year=2018, quarter=1),
        header_raw="", flags=["LAYOUT_LOW_CONFIDENCE"],
        travelers=[Traveler(name="A", segments=[segment])],
    )


@pytest.fixture
def running_server(tmp_path):
    reports = [_flagged_report("r-1"), _flagged_report("r-2")]
    corrections_path = tmp_path / "corrections.json"
    handler_cls = make_handler(reports, FIXTURES, corrections_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, corrections_path
    server.shutdown()
    thread.join()


def _get(server, path):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    conn.request("GET", path)
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response.status, body


class TestListEndpoint:
    def test_returns_all_reports_as_unreviewed(self, running_server):
        server, _ = running_server
        status, body = _get(server, "/api/reports")
        assert status == 200
        data = json.loads(body)
        assert len(data) == 2
        assert {r["report_id"] for r in data} == {"r-1", "r-2"}
        assert all(r["status"] == "unreviewed" for r in data)


class TestStaticFiles:
    def test_serves_index_html(self, running_server):
        server, _ = running_server
        status, body = _get(server, "/")
        assert status == 200
        assert b"Report Review" in body

    def test_unknown_path_is_404(self, running_server):
        server, _ = running_server
        status, _ = _get(server, "/nonexistent")
        assert status == 404
```

**Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_review_server.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 4: Implement the server (list + static serving only for now)**

```python
# official_foreign_travel/review/server.py
"""Local review server: side-by-side raw/extracted view with editable corrections."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Type
from urllib.parse import urlparse

from ..models.report import Report
from .corrections import load_corrections
from .source_lookup import get_raw_lines

STATIC_DIR = Path(__file__).parent / "static"
CONTENT_TYPES = {".html": "text/html", ".css": "text/css", ".js": "application/javascript"}


def make_handler(
    reports: List[Report], report_text_dir: Path, corrections_path: Path
) -> Type[BaseHTTPRequestHandler]:
    """Build a request handler class closed over this run's reports and paths."""
    reports_by_id = {r.report_id: r for r in reports}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format_str: str, *args) -> None:
            pass  # keep test/server output quiet

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._serve_static("index.html")
            elif path in ("/report.html", "/app.css", "/app.js"):
                self._serve_static(path.lstrip("/"))
            elif path == "/api/reports":
                self._send_json(self._list_reports())
            elif path.startswith("/api/reports/"):
                self._send_report_detail(path[len("/api/reports/") :])
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path.startswith("/api/reports/") and path.endswith("/corrections"):
                report_id = path[len("/api/reports/") : -len("/corrections")]
                self._save_corrections(report_id)
            else:
                self.send_error(404)

        def _serve_static(self, name: str) -> None:
            file_path = STATIC_DIR / name
            if not file_path.is_file():
                self.send_error(404)
                return
            body = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPES.get(file_path.suffix, "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, data, status: int = 200) -> None:
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _list_reports(self) -> List[dict]:
            corrections = load_corrections(corrections_path)
            return [
                {
                    "report_id": r.report_id,
                    "sponsor": r.sponsor.name,
                    "source_file": r.source_file,
                    "flags": r.flags,
                    "traveler_count": len(r.travelers),
                    "status": corrections.get(r.report_id, {}).get("status", "unreviewed"),
                }
                for r in reports
            ]

        def _send_report_detail(self, report_id: str) -> None:
            report = reports_by_id.get(report_id)
            if report is None:
                self.send_error(404)
                return
            raw_lines = get_raw_lines(report, report_text_dir) or []
            corrections = load_corrections(corrections_path)
            self._send_json(
                {
                    "report": report.model_dump(mode="json"),
                    "raw_lines": raw_lines,
                    "correction": corrections.get(report_id, {"status": "unreviewed", "edits": {}}),
                }
            )

        def _save_corrections(self, report_id: str) -> None:
            if report_id not in reports_by_id:
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length))
            from .corrections import save_report_correction

            entry = save_report_correction(
                corrections_path, report_id, payload.get("status", "edited"), payload.get("edits", {})
            )
            self._send_json(entry)

    return Handler


def run_server(
    reports: List[Report],
    report_text_dir: Path,
    corrections_path: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Start the review server and block until interrupted (Ctrl-C)."""
    flagged = [r for r in reports if r.flags]
    handler_cls = make_handler(flagged, report_text_dir, corrections_path)
    server = ThreadingHTTPServer((host, port), handler_cls)
    print(f"Review server running at http://{host}:{port}/ ({len(flagged)} flagged reports)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_review_server.py -v`
Expected: PASS (3 tests)

**Step 6: Commit**

```bash
git add official_foreign_travel/review/server.py official_foreign_travel/review/static/index.html tests/test_review_server.py
git commit -m "Add review server: static file serving and reports list endpoint"
```

---

## Task 6: Report detail and save-corrections endpoints

**Files:**
- Modify: `tests/test_review_server.py` (server.py itself already has these handlers from Task 5 -- this task is tests-only, confirming the behavior)

**Step 1: Write the failing tests**

Append to `tests/test_review_server.py`:

```python
class TestDetailEndpoint:
    def test_returns_report_raw_lines_and_default_correction(self, running_server):
        server, _ = running_server
        status, body = _get(server, "/api/reports/r-1")
        assert status == 200
        data = json.loads(body)
        assert data["report"]["report_id"] == "r-1"
        assert isinstance(data["raw_lines"], list)
        assert data["correction"] == {"status": "unreviewed", "edits": {}}

    def test_unknown_report_id_is_404(self, running_server):
        server, _ = running_server
        status, _ = _get(server, "/api/reports/does-not-exist")
        assert status == 404


def _post(server, path, payload):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    body = json.dumps(payload).encode("utf-8")
    conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    result = response.read()
    conn.close()
    return response.status, result


class TestSaveCorrectionsEndpoint:
    def test_save_then_list_reflects_status(self, running_server):
        server, _ = running_server
        status, body = _post(
            server, "/api/reports/r-1/corrections",
            {"status": "edited", "edits": {"sponsor.name": "Fixed"}},
        )
        assert status == 200
        entry = json.loads(body)
        assert entry["status"] == "edited"
        assert entry["edits"] == {"sponsor.name": "Fixed"}

        _, list_body = _get(server, "/api/reports")
        reports = json.loads(list_body)
        r1 = next(r for r in reports if r["report_id"] == "r-1")
        assert r1["status"] == "edited"
        r2 = next(r for r in reports if r["report_id"] == "r-2")
        assert r2["status"] == "unreviewed"

    def test_confirm_ok_round_trip(self, running_server):
        server, _ = running_server
        self_status, _ = _post(server, "/api/reports/r-2/corrections", {"status": "confirmed_ok", "edits": {}})
        assert self_status == 200
        _, detail_body = _get(server, "/api/reports/r-2")
        detail = json.loads(detail_body)
        assert detail["correction"]["status"] == "confirmed_ok"

    def test_unknown_report_id_is_404(self, running_server):
        server, _ = running_server
        status, _ = _post(server, "/api/reports/does-not-exist/corrections", {"status": "edited", "edits": {}})
        assert status == 404
```

**Step 2: Run tests to verify they pass (server.py already implements this from Task 5)**

Run: `uv run pytest tests/test_review_server.py -v`
Expected: PASS (9 tests total). If anything fails, it means Task 5's implementation has a
bug in the detail/save handlers -- fix `server.py` until green.

**Step 3: Commit**

```bash
git add tests/test_review_server.py
git commit -m "Add tests for review server detail and save-corrections endpoints"
```

---

## Task 7: `oft-review` CLI entry point

**Files:**
- Create: `official_foreign_travel/cli/review.py`
- Modify: `pyproject.toml`

**Step 1: Implement the CLI**

```python
#!/usr/bin/env python3
# official_foreign_travel/cli/review.py
"""CLI for the local report review server."""

import argparse
import json
import sys
from pathlib import Path

from ..models.report import Report
from ..review.server import run_server


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Review flagged parser output side-by-side with the original source text"
    )
    parser.add_argument("report_text_dir", type=Path, help="Directory of original *.txt report files")
    parser.add_argument("parsed_json", type=Path, help="Parsed output JSON, from oft-parse")
    parser.add_argument(
        "--corrections",
        type=Path,
        default=Path("corrections.json"),
        help="Corrections overlay file to read/write (default: corrections.json)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765)")

    args = parser.parse_args()

    if not args.report_text_dir.is_dir():
        print(f"Error: not a directory: {args.report_text_dir}")
        return 1
    if not args.parsed_json.exists():
        print(f"Error: file not found: {args.parsed_json}")
        return 1

    payload = json.loads(args.parsed_json.read_text(encoding="utf-8"))
    reports = [Report.model_validate(r) for r in payload["reports"]]

    run_server(reports, args.report_text_dir, args.corrections, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Step 2: Register the entry point**

In `pyproject.toml`, in `[project.scripts]`, add:

```toml
oft-review = "official_foreign_travel.cli.review:main"
```

**Step 3: Re-sync so the entry point is installed**

Run: `uv sync`

**Step 4: Smoke-test the CLI wiring (no real browser yet)**

Run: `uv run oft-review --help`
Expected: prints the argparse help text without error.

**Step 5: Commit**

```bash
git add official_foreign_travel/cli/review.py pyproject.toml
git commit -m "Add oft-review CLI entry point"
```

---

## Task 8: Frontend -- list view

**Files:**
- Modify: `official_foreign_travel/review/static/index.html` (replace placeholder)
- Create: `official_foreign_travel/review/static/app.css`
- Create: `official_foreign_travel/review/static/app.js`

**Step 1: Write the real `index.html`**

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Report Review</title>
  <link rel="stylesheet" href="/app.css">
</head>
<body>
  <h1>Flagged Reports</h1>
  <div id="progress"></div>
  <select id="status-filter">
    <option value="">All statuses</option>
    <option value="unreviewed">Unreviewed</option>
    <option value="confirmed_ok">Confirmed OK</option>
    <option value="edited">Edited</option>
  </select>
  <table id="reports-table">
    <thead>
      <tr><th>Report ID</th><th>Sponsor</th><th>Source File</th><th>Flags</th><th>Travelers</th><th>Status</th></tr>
    </thead>
    <tbody id="reports-body"></tbody>
  </table>
  <script src="/app.js"></script>
  <script>renderList();</script>
</body>
</html>
```

**Step 2: Write `app.css`**

```css
body { font-family: sans-serif; margin: 1.5rem; }
table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
th, td { border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; font-size: 0.9rem; }
th { background: #f0f0f0; }
#progress { font-weight: bold; margin-bottom: 0.5rem; }

#toolbar { margin-bottom: 1rem; display: flex; gap: 0.5rem; align-items: center; }
#panes { display: flex; gap: 1rem; align-items: flex-start; }
#raw-pane {
  flex: 1; background: #1e1e1e; color: #ddd; padding: 1rem; overflow: auto;
  white-space: pre; font-family: "SF Mono", Menlo, monospace; font-size: 0.8rem;
  max-height: 85vh; border-radius: 4px;
}
#raw-pane mark { background: #ffd54f; color: #000; }
#form-pane { flex: 1; overflow: auto; max-height: 85vh; }
#form-pane h3 { margin-top: 1rem; border-top: 1px solid #ddd; padding-top: 0.5rem; }
#form-pane h4 { margin: 0.5rem 0 0.25rem; cursor: pointer; color: #0645ad; }
.field-row { display: flex; justify-content: space-between; gap: 0.5rem; margin-bottom: 0.25rem; font-size: 0.8rem; }
.field-row span { flex: 0 0 40%; color: #555; }
.field-row input { flex: 1; font-family: monospace; font-size: 0.8rem; }
button { cursor: pointer; }
```

**Step 3: Write `app.js` (list rendering only for this task)**

```javascript
const API = "/api/reports";

async function fetchReports() {
  const res = await fetch(API);
  return res.json();
}

async function renderList() {
  const reports = await fetchReports();
  const filterSelect = document.getElementById("status-filter");
  filterSelect.onchange = () => renderRows(reports, filterSelect.value);
  renderRows(reports, "");
}

function renderRows(reports, statusFilter) {
  const filtered = statusFilter ? reports.filter((r) => r.status === statusFilter) : reports;
  const reviewed = reports.filter((r) => r.status !== "unreviewed").length;
  document.getElementById("progress").textContent = `${reviewed}/${reports.length} reviewed`;

  const body = document.getElementById("reports-body");
  body.innerHTML = "";
  filtered.forEach((r) => {
    const tr = document.createElement("tr");
    const link = `/report.html?id=${encodeURIComponent(r.report_id)}`;
    tr.innerHTML = `
      <td><a href="${link}">${r.report_id}</a></td>
      <td>${r.sponsor}</td>
      <td>${r.source_file}</td>
      <td>${r.flags.join(", ")}</td>
      <td>${r.traveler_count}</td>
      <td>${r.status}</td>
    `;
    body.appendChild(tr);
  });
}
```

**Step 4: Manually verify in the browser**

Use the Preview tool: start `uv run oft-review tests/fixtures output/travel_reports.json`
(or a small real JSON output covering the fixtures), open the preview, confirm the list
renders with report rows, the status filter works, and the progress counter shows `0/N`.

**Step 5: Commit**

```bash
git add official_foreign_travel/review/static/index.html official_foreign_travel/review/static/app.css official_foreign_travel/review/static/app.js
git commit -m "Add review UI list view"
```

---

## Task 9: Frontend -- detail view (side-by-side, edit, save)

**Files:**
- Create: `official_foreign_travel/review/static/report.html`
- Modify: `official_foreign_travel/review/static/app.js`

**Step 1: Write `report.html`**

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Review Report</title>
  <link rel="stylesheet" href="/app.css">
</head>
<body>
  <div id="toolbar">
    <a href="/">&larr; List</a>
    <button id="prev-btn">&larr; Prev</button>
    <button id="next-btn">Next &rarr;</button>
    <button id="confirm-btn">Confirm OK</button>
    <button id="save-btn">Save</button>
    <span id="save-status"></span>
  </div>
  <div id="panes">
    <pre id="raw-pane"></pre>
    <div id="form-pane"></div>
  </div>
  <script src="/app.js"></script>
  <script>renderDetail();</script>
</body>
</html>
```

**Step 2: Append detail-view logic to `app.js`**

```javascript
function getReportIdFromUrl() {
  return new URLSearchParams(window.location.search).get("id");
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

let currentRawLines = [];

function highlightLines(range) {
  if (!range || !range.length) return;
  const pre = document.getElementById("raw-pane");
  pre.innerHTML = currentRawLines
    .map((line, i) => (range.includes(i + 1) ? `<mark>${escapeHtml(line)}</mark>` : escapeHtml(line)))
    .join("\n");
  const marked = pre.querySelector("mark");
  if (marked) marked.scrollIntoView({ block: "center" });
}

function fieldRow(path, value) {
  const row = document.createElement("label");
  row.className = "field-row";
  const label = document.createElement("span");
  label.textContent = path;
  const input = document.createElement("input");
  input.dataset.path = path;
  input.value = value ?? "";
  row.appendChild(label);
  row.appendChild(input);
  return row;
}

function renderForm(report, existingEdits) {
  const pane = document.getElementById("form-pane");
  pane.innerHTML = "";
  pane.appendChild(fieldRow("sponsor.type", report.sponsor.type));
  pane.appendChild(fieldRow("sponsor.name", report.sponsor.name));
  pane.appendChild(fieldRow("sponsor.code", report.sponsor.code));
  if (report.period) {
    pane.appendChild(fieldRow("period.start", report.period.start));
    pane.appendChild(fieldRow("period.end", report.period.end));
  }

  report.travelers.forEach((traveler, ti) => {
    const heading = document.createElement("h3");
    heading.textContent = `Traveler ${ti + 1}`;
    pane.appendChild(heading);
    pane.appendChild(fieldRow(`travelers[${ti}].name`, traveler.name));
    pane.appendChild(fieldRow(`travelers[${ti}].honorific`, traveler.honorific));
    pane.appendChild(fieldRow(`travelers[${ti}].bioguide_id`, traveler.bioguide_id));

    traveler.segments.forEach((segment, si) => {
      const segHeading = document.createElement("h4");
      segHeading.textContent = `Segment ${si + 1} (click to highlight source)`;
      segHeading.onclick = () => highlightLines(segment.source_lines);
      pane.appendChild(segHeading);

      const prefix = `travelers[${ti}].segments[${si}]`;
      pane.appendChild(fieldRow(`${prefix}.arrival_date`, segment.arrival_date));
      pane.appendChild(fieldRow(`${prefix}.departure_date`, segment.departure_date));
      pane.appendChild(fieldRow(`${prefix}.country_raw`, segment.country_raw));

      ["per_diem", "transportation", "other", "total"].forEach((category) => {
        ["foreign_currency", "us_dollar"].forEach((currency) => {
          const cell = segment.costs[category][currency];
          pane.appendChild(fieldRow(`${prefix}.costs.${category}.${currency}.amount`, cell.amount));
        });
      });
    });
  });

  Object.entries(existingEdits).forEach(([path, value]) => {
    const input = pane.querySelector(`[data-path="${CSS.escape(path)}"]`);
    if (input) input.value = value;
  });
}

function collectEdits() {
  const edits = {};
  document.querySelectorAll("#form-pane [data-path]").forEach((input) => {
    edits[input.dataset.path] = input.value;
  });
  return edits;
}

async function saveCorrection(reportId, status) {
  const edits = status === "confirmed_ok" ? {} : collectEdits();
  const res = await fetch(`/api/reports/${encodeURIComponent(reportId)}/corrections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, edits }),
  });
  document.getElementById("save-status").textContent = res.ok ? "Saved" : "Error saving";
}

async function setupPrevNext(currentId) {
  const reports = await fetchReports();
  const ids = reports.map((r) => r.report_id);
  const idx = ids.indexOf(currentId);
  document.getElementById("prev-btn").disabled = idx <= 0;
  document.getElementById("next-btn").disabled = idx < 0 || idx >= ids.length - 1;
  document.getElementById("prev-btn").onclick = () => {
    if (idx > 0) window.location.href = `/report.html?id=${encodeURIComponent(ids[idx - 1])}`;
  };
  document.getElementById("next-btn").onclick = () => {
    if (idx < ids.length - 1) window.location.href = `/report.html?id=${encodeURIComponent(ids[idx + 1])}`;
  };
}

async function renderDetail() {
  const reportId = getReportIdFromUrl();
  const res = await fetch(`/api/reports/${encodeURIComponent(reportId)}`);
  const data = await res.json();

  currentRawLines = data.raw_lines;
  document.getElementById("raw-pane").textContent = currentRawLines.join("\n");
  renderForm(data.report, data.correction.edits || {});

  document.getElementById("save-btn").onclick = () => saveCorrection(reportId, "edited");
  document.getElementById("confirm-btn").onclick = () => saveCorrection(reportId, "confirmed_ok");
  await setupPrevNext(reportId);
}
```

**Step 3: Manually verify in the browser**

Use the Preview tool against a real `oft-review` run:
1. Open the list view, click into a flagged report.
2. Confirm the raw text renders on the left and the form renders on the right with real
   values (sponsor, period, each traveler/segment/cost field).
3. Click a "Segment N" heading and confirm its source lines highlight on the left.
4. Edit a field (e.g. a traveler's `bioguide_id`), click Save, confirm "Saved" appears.
5. Reload the page and confirm the edited value persists (loaded from the corrections file).
6. Click Confirm OK on a different report, go back to the list, confirm its status shows
   `confirmed_ok` and the progress counter incremented.
7. Use Prev/Next to move between reports.

**Step 4: Commit**

```bash
git add official_foreign_travel/review/static/report.html official_foreign_travel/review/static/app.js
git commit -m "Add review UI detail view: side-by-side, highlighting, edit, save"
```

---

## Task 10: Wire `--apply-corrections` into `oft-parse`

**Files:**
- Modify: `official_foreign_travel/cli/parse.py`
- Test: `tests/test_cli_parse.py`

**Step 1: Write the failing test**

Append to `tests/test_cli_parse.py`:

```python
import json as json_module


class TestApplyCorrections:
    def test_correction_is_merged_into_output(self, tmp_path, monkeypatch):
        corrections_path = tmp_path / "corrections.json"
        # Real report_id for the first table in this fixture, from prior runs of oft-parse.
        out_first = tmp_path / "first.json"
        run_cli([str(FIXTURES / "2019q1jan29.txt"), str(out_first)], monkeypatch)
        first_data = json_module.loads(out_first.read_text())
        report_id = first_data["reports"][0]["report_id"]
        original_sponsor_name = first_data["reports"][0]["sponsor"]["name"]

        corrections_path.write_text(
            json_module.dumps(
                {report_id: {"status": "edited", "edits": {"sponsor.name": "Corrected Sponsor Name"}}}
            )
        )

        out_corrected = tmp_path / "corrected.json"
        code = run_cli(
            [
                str(FIXTURES / "2019q1jan29.txt"),
                str(out_corrected),
                "--apply-corrections",
                str(corrections_path),
            ],
            monkeypatch,
        )
        assert code == 0

        corrected_data = json_module.loads(out_corrected.read_text())
        corrected_report = next(r for r in corrected_data["reports"] if r["report_id"] == report_id)
        assert corrected_report["sponsor"]["name"] == "Corrected Sponsor Name"
        assert corrected_report["sponsor"]["name"] != original_sponsor_name
        assert "MANUALLY_CORRECTED" in corrected_report["flags"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_parse.py -v -k ApplyCorrections`
Expected: FAIL with `error: unrecognized arguments: --apply-corrections`

**Step 3: Implement the flag**

In `official_foreign_travel/cli/parse.py`, add the argument near `--fail-report`:

```python
    parser.add_argument(
        "--apply-corrections",
        type=Path,
        help="Merge human corrections from this file (written by oft-review) into the output",
    )
```

After the `--llm-fallback` block and before the format-dispatch (`if output_format == "json":`),
add:

```python
    if args.apply_corrections:
        from ..review.corrections import apply_corrections, load_corrections

        corrections = load_corrections(args.apply_corrections)
        reports = apply_corrections(reports, corrections)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_parse.py -v -k ApplyCorrections`
Expected: PASS

**Step 5: Run the full CLI test file to check nothing else broke**

Run: `uv run pytest tests/test_cli_parse.py -v`
Expected: PASS (all tests)

**Step 6: Commit**

```bash
git add official_foreign_travel/cli/parse.py tests/test_cli_parse.py
git commit -m "Add --apply-corrections to oft-parse"
```

---

## Task 11: Full verification pass

**Files:** none (verification only)

**Step 1: Run the full test suite**

Run: `uv run pytest tests/ -q`
Expected: all tests pass (only the 1 pre-existing `RUN_LLM_INTEGRATION_TESTS`-gated skip)

**Step 2: Run mypy on the new package**

Run: `uv run mypy official_foreign_travel/review/ official_foreign_travel/cli/review.py official_foreign_travel/cli/parse.py`
Expected: `Success: no issues found`. Fix any missing type annotations before moving on
(follow the same patterns used elsewhere in this codebase: explicit `Dict`/`List`/`Optional`
from `typing`, `-> None` / `-> int` return annotations on CLI `main()` functions).

**Step 3: Run black and ruff on the new files only**

Run:
```bash
uv run black official_foreign_travel/review/ official_foreign_travel/cli/review.py tests/test_review_corrections.py tests/test_review_server.py tests/test_review_source_lookup.py
uv run ruff check official_foreign_travel/review/ official_foreign_travel/cli/review.py
```
Fix anything flagged. Do not reformat unrelated pre-existing files.

**Step 4: End-to-end manual walkthrough with real data**

```bash
uv run oft-parse report_text/ output/travel_reports.json
uv run oft-review report_text/ output/travel_reports.json --corrections /tmp/corrections.json
```

Open the preview, review at least 2-3 real flagged reports end to end (edit + save one,
confirm-ok another), then:

```bash
uv run oft-parse report_text/ output/corrected.json --apply-corrections /tmp/corrections.json
```

Confirm the edited report in `output/corrected.json` reflects the correction and carries
`MANUALLY_CORRECTED`, and the confirmed-ok one carries `HUMAN_CONFIRMED`.

**Step 5: Update TECHNICAL_README.md**

Add a short "Reviewing flagged reports" section documenting `oft-review` and
`--apply-corrections`, following the existing doc style (see the `--llm-fallback`
documentation added earlier for the pattern to match).

**Step 6: Commit the docs update**

```bash
git add TECHNICAL_README.md
git commit -m "Document oft-review and --apply-corrections"
```
